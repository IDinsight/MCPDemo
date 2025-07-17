"""This module contains FastAPI routers for authentication endpoints.

Notes:

1. This authentication flow supports two OAuth2 grant types:
    - Password Grant (grant_type=password): User-based login with username/password.
    - Client Credentials Grant (grant_type=client_credentials): Machine-to-machine auth
        using client ID/secret.

   Both result in access tokens (JWTs), but the subject (sub) and claims differ:
        - `sub` is a user ID in password grant.
        - `sub` is a client ID in client_credentials grant.

   In OAuth2, each token is associated with a set of scopes (e.g., read, write, admin).
    These scopes are enforced by the authentication flow defined here. This is more
    granular and secure than traditional Bearer auth, which typically has
    all-or-nothing access. **However**, we also enforce scopes manually with the
    `scopes` package for Password Grant.
2. The authentication flow does NOT include Authorization-code + PKCE (Proof Key for
    Code Exchange). We would implement PKCE if we plan on supporting:
        - Single‑page web apps (React, Vue) that **can’t hide a client secret**
        - Native mobile apps (iOS/Android)
        - Any scenario where users log in through a browser pop‑up or redirect flow and
            we want maximum phishing/interception protection.

   Client‑credentials and password grants are fine for internal services and
   first‑party apps. PKCE is in addition to (not instead of) client‑credentials and the
   two serve different audiences.
3. Access tokens are:
    - Short-lived (e.g., 15 min)
    - Passed around often (in headers or cookies)
    - Stored in memory or in short-lived storage

   Refresh tokens are:
    - Long-lived (e.g., 30 days)
    - Stored securely
    - Only used at refresh endpoints, not sent with every request

   If an access token is leaked, it only works for 15 minutes (or less). The refresh
   token stays hidden and protected.

   Without refresh tokens:
    - Users/clients would need to re-login every 15 minutes.
    - Users/clients would be forced to:
        - Prompt for username/password (client ID/secret)
        - Or silently re-authenticate if possible (which still breaks UX)

   With refresh tokens:
    - The browser or client can:
    - Automatically call /token/refresh
    - Get a new access token without user input
    - End users never see interruptions

   This separation means:
    - APIs only need to verify access tokens.
    - The refresh endpoint handles session lifecycle, with stricter rules (IP lockout,
    rate limit, etc.).

   Thus, refresh tokens exist to allow short-lived access tokens to remain secure while
   keeping users authenticated for long periods, without repeatedly asking for
   passwords. Users still need the access token every time, but the refresh token is
   what keeps that access token renewable behind the scenes, without burdening the user.
4. Key rotation happens server-side (not client) and users do not need to manually
    generate new RSA passphrases. Clients do not need to rotate their own keys (unless
    we want to add such a feature in the future).
5. Clients should fetch the current JWKS periodically (or cache and re-fetch on
    signature failure).
6. The RSA passphrase is the single point of encryption for all private keys. Every new
    RSA private key used for JWT signing is encrypted on disk with the same passphrase
    (AUTH_RSA_PASSPHRASE). This protects the keys at rest---even if someone steals the
    `.pem` files, they can't read them without the passphrase.
7. If an attacker got access to the passphrase, then they could decrypt the `.pem`
    files and forge JWTs by signing tokens offline, backdate tokens with valid
    signatures, and bypass revocation systems using stateless tokens. Key rotation
    prevents this by periodically changing the passphrase and deleting old keys beyond
    `AUTH_ROTATION_KEEP_LAST_N` (so that older keys encrypted with the old passphrase
    are eventually purged). This makes forward secrecy stronger---even if the old
    passphrase leaks, those older keys are eventually deleted and cannot be used.
"""

# Standard Library
import hashlib

from typing import Any

# Third Party Library
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi_csrf_protect import CsrfProtect
from jose import JWTError, jwt
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.auth.schemas import (
    IntrospectionResponse,
    RefreshTokenRequestForm,
    RevokeTokenResponse,
    TokenResponse,
)
from mcp_demo.auth.utils import (
    ClientCredentialsRequestForm,
    _verify_caller,
    generate_refresh_token,
    get_cached_jwks,
    get_jwt_token,
    get_least_privileged_scopes,
    require_scopes,
    rotate_refresh_token,
)
from mcp_demo.clients.utils import verify_client
from mcp_demo.config import Settings
from mcp_demo.users.utils import get_user_scopes_by_id, verify_user
from mcp_demo.utils.database import get_async_session
from mcp_demo.utils.rate_limit import (
    is_locked_out,
    record_failed_login,
    reset_failed_login,
)

TAG_METADATA = {
    "description": "Endpoints for issuing and discovering JWTs",
    "name": "Authentication",
}
basic_auth = HTTPBasic(auto_error=False)  # RFC 7662 requires auth but we handle error
router = APIRouter(prefix="/auth", tags=[TAG_METADATA["name"]])

AUTH_RSA_PASSPHRASE = Settings.AUTH_RSA_PASSPHRASE
AUTH_TOKEN_TTL = Settings.AUTH_TOKEN_TTL
FASTAPI_ENV = Settings.FASTAPI_ENV
RATE_LIMIT_LOGIN_RATE = Settings.RATE_LIMIT_LOGIN_RATE
REDIS_CACHE_PREFIX_JTI = Settings.REDIS_CACHE_PREFIX_JTI
REDIS_CACHE_PREFIX_REFRESH_TOKEN = Settings.REDIS_CACHE_PREFIX_REFRESH_TOKEN
REDIS_URL = Settings.REDIS_URL

limiter = Limiter(key_func=get_remote_address, storage_uri=REDIS_URL)


@router.get(
    "/jwks.json",
    description=(
        "Returns the JSON Web Key Set (JWKS) used by this service to sign JWTs. "
        "FastMCP’s `BearerAuthProvider` and other OAuth2 clients automatically cache "
        "this URI based on its `Cache-Control` headers."
    ),
    include_in_schema=False,
    summary="JWKS endpoint",
)
async def get_jwks() -> JSONResponse:
    """Serve the public JWKS for verifying JWT signatures.

    What Happens Here
    -----------------

    1. Signing and token issuance
        - The auth server signs **every** JWT with the current private key
        (private_<kid>.pem).
        - That key’s kid is placed in the JWT header.
    2. Verification by clients/APIs
        - Verifier extracts kid and downloads (or has cached) one JWKS document.
        - Verifier chooses the JWK whose kid matches and checks the signature and also
            validates iss, aud, exp, scope, etc.
    3. Key rotation
        - Ops (or a scheduled job) creates a new keypair.
        - The public part is prepended to jwks.json; old key stays until all tokens
            signed with it expire.
        - Private files on disk now include private_<newKid>.pem (current) plus at
            most N‑1 older ones (optional).
        - Clients keep working: they’ll see the new JWK next time they refresh their
            JWKS cache.
    4. Multiple users or clients
        - Each login issues a fresh access token (15 min) and refresh token (30 days).
        - **Nothing** in jwks.json changes for those logins because the signing key
            hasn’t changed---only the token payload.

    With this setup, millions of users/clients can log in, tokens stay
    user/client‑specific, and the public‑key infrastructure remains simple, secure, and
    rotation‑friendly.

    NB: This endpoint should not be specific to individual subjects because it is meant
    to be used for **public** JWKS fetches (e.g., FastMCP or other OAuth2 clients). In
    other words, the `Settings.AUTH_JWKS_URI` must be a **fixed** URI for the
    authorization server at load time and, thus, there is no way for clients to inject
    things like `grant_type` or `sub` into the request. Although we could pass values
    in via query parameters, clients would not know how to do this.

    Returns
    -------
    JSONResponse
        The JWKS bundle containing all active public keys.
    """

    jwks = await get_cached_jwks()
    return JSONResponse({"keys": jwks["keys"]})  # Exclude internal metadata


@router.post("/introspect", response_model=IntrospectionResponse)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def introspect_token(
    request: Request,
    token: str,
    claims: dict = require_scopes(required_scopes={"admin"}),
) -> IntrospectionResponse:
    """RFC 7662-style token introspection.

    *Authenticated* callers can verify whether a JWT is active and view the scopes
    associated with its subject (user or client).

    The process is as follows:

    1. Extract the grant type and subject from the claims of the authenticated caller.
    2. The JWT is decoded and validated---signature, expiry, and replay (jti) checks
        are performed.
    3. The introspection response is built, indicating whether the token is active,
        its expiry, scopes, and subject/client ID.
    4. The caller's identity is set in the request state for auditing purposes.

    Parameters
    ----------
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    token
        The JWT token to introspect.
    claims
        The claims of the authenticated caller, used to verify scopes, grant type, and
        subject.

    Returns
    -------
    IntrospectionResponse
        An introspection response indicating whether the token is active, its scopes,
        and other metadata.

    Raises
    ------
    HTTPException
        If the caller is not authenticated, or if the token is invalid or expired.
        If the caller's credentials are invalid or not found in the database.
        If the token cannot be decoded or verified.
        If the token does not belong to the authenticated client or user.
    """

    # 1.
    grant_type = claims["gty"]
    subject = claims["sub"]
    client_id = subject if grant_type == "client_credentials" else None
    if grant_type == "client_credentials":
        options: dict[str, Any] = {
            "verify_aud": True,
            "verify_exp": True,
            "verify_iat": True,
            "verify_iss": True,
            "verify_nbf": True,
        }
    else:
        options = {"require": ["exp", "sub"]}

    # 2.
    try:
        claims = await _verify_caller(  # Introspection needs no scopes
            options=options, redis_client=request.app.state.redis, token=token
        )

    except HTTPException:
        return IntrospectionResponse(active=False)

    # 3.
    response = {
        "active": True,
        "client_id": client_id,
        "exp": claims["exp"],
        "scope": claims.get("scope", ""),
        "sub": subject,
        "token_type": "access_token",
    }

    # 4.
    request.state.audit_sub = subject  # Set subject in request state for auditing

    return IntrospectionResponse(**response)


@router.post(
    "/token",
    description=(
        "Authenticate via client credentials or username/password to receive an RS256 JWT.\n\n"
        "- **Request**: `application/x-www-form-urlencoded`\n"
        "- **Response**: JSON with `access_token`, `token_type`, `expires_in`\n"
        "- **Usage**: `Authorization: Bearer <token>` header"
    ),
    response_model=TokenResponse,
    summary="Issue JWT via Client-Credentials or Password grant",
)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def token_endpoint(
    request: Request,
    asession: AsyncSession = Depends(get_async_session),
    credentials: HTTPBasicCredentials = Depends(basic_auth),
    csrf_protect: CsrfProtect = Depends(),
    form: ClientCredentialsRequestForm = Depends(),
) -> JSONResponse | TokenResponse:
    """Issue a Bearer token via OAuth2 Client-Credentials **or** Password grant. Upon
    successful authentication (whether machine or human) a short‑lived RS256 JWT is
    issued in addition to a refresh token.

    NB: We cannot use the `require_scopes` dependency here because it requires a
    Bearer token to be present in the request, which is not the case for this
    endpoint. Instead, we handle the authentication and authorization logic manually
    based on the provided credentials and form data.

    The process is as follows:

    For the "client_credentials" grant type:

    1. Check if the client is locked out due to too many failed login attempts.
    2. Verify the client credentials against the database. If the credentials are
        invalid, record the failed login attempt.
    3. If the client is valid, reset the failed login attempts.
    4. Get the least privileged scopes for the client based on the allowed scopes and
        requested scopes.
    5. Get a JWT token using the client's scopes and the passphrase from settings.
    6. Generate a refresh token for the client, which can be used to obtain new access
        tokens without re-authenticating.
    7. Create a `TokenResponse` containing the access token, its expiration time,
        the refresh token, and its expiration time.

    For the "password" grant type:

    1. Check if the user is locked out due to too many failed login attempts.
    2. Verify the user credentials against the database. If the credentials are
        invalid, record the failed login attempt.
    3. If the user is valid, reset the failed login attempts.
    4. Get the least privileged scopes for the user based on their roles and
        permissions.
    5. Get a JWT token using the user's scopes and the passphrase from settings.
    6. Generate a refresh token for the user, which can be used to obtain new access
        tokens without re-authenticating.
    7. Create a `TokenResponse` containing the access token, its expiration time,
        the refresh token, and its expiration time.
    8. Set the access token as an HTTP-only cookie in the response, which is not
        accessible via JavaScript, enhancing security against XSS attacks (e.g., the
        access token is dropped into an HTTP-only cookie).

    Note on HTTP-only cookie
    ------------------------

    In the password grant flow, the end user is a human who is authenticating directly
    with a username and password. Their access token is usually returned in a
    browser-based client (e.g., a web app) and web apps may want to store the access
    token securely on the client so that it can be sent automatically with requests. In
    step 8 of the password grant flow, we set the access token as an HTTP-only cookie
    so that we can prevent JS access to the token (mitigates XSS attacks by preventing
    client-side JS from accessing document.cookie to read the token), allow the browser
    to automatically send the token with each request (cookie-based session), keep the
    UX clean (no localStorage or Authorization header management in JS), and allow
    pairing with CSRF tokens for extra security. In other words, the HTTP-only cookie
    acts like a traditional session token for the user's browser. When the browser
    receives the response with Set-Cookie, it stores the access token in a cookie named
    `access_token`. This cookie is then automatically sent by the browser on all future
    requests to the server (as long as the path/domain match), so users don’t need to
    manually include the token in request headers each time. Thus, in practice, after a
    user log ins (password or refresh), the API responds with a JSON payload and sets a
    secure, HTTP-only cookie containing the access token. Future API calls (e.g. to a
    protected endpoint) don’t need an "Authorization: Bearer ..." header-the browser
    will send the cookie automatically.

    In the client credentials grant flow, there is no user---it's machine-to-machine.
    A machine (or backend service) sends its client ID and secret to get an access
    token and that token is meant to be stored in memory by that machine, not a
    browser. Machines don't use cookies and HTTP-only cookies are not visible to the
    script calling the token endpoint---in other words, if you were to include the
    HTTP-only cookie in the response, you would be leaking sensitive credentials into
    an HTTP response that a machine doesn't even need/use.

    While these flags mitigate XSS risks, they don’t block CSRF (Cross-Site Request
    Forgery), since the cookie is still sent on cross-site POSTs. To fully protect
    endpoints, we'd also need either:
        1. A CSRF token (e.g., double-submit),
        2. Or use SameSite=Lax/Strict for cookies if it fits the flows,
        3. Or rely solely on Authorization header tokens instead of cookies.

    Note on Cross-Site Request Forgery (CSRF)
    -----------------------------------------

    CSRF is a type of attack where a malicious site tricks a user (not client) into
    making unintended requests to a different site where the user is authenticated.
    This can happen if the user is logged into a site and then visits a malicious site
    that sends requests to the authenticated site using the user's credentials (e.g.,
    via cookies).

    CSRF abuses the browser’s implicit credential sending (cookies, Basic auth,
    client certs). The client‑credentials grant is safe because machines read the token
    and put it in an Authorization header (i.e., machines don’t use cookies).

    However, the password grant sets `access_token` as an HTTP‑only cookie (good for
    XSS) but without extra defences that cookie is still sent on cross‑site POST,
    allowing CSRF attacks.

    Note on Swagger UI
    ------------------

    Swagger‑UI (FastAPI “Authorize” button) follows the OAuth specifications for the
    Client Credentials grant---it sends `client_id` and `client_secret` in the
    Authorization header as HTTP Basic credentials, not as
    application/x‑www‑form‑urlencoded fields. Thus, we need to handle this using
    credentials from the HTTP Basic auth header, not from the form body.

    Parameters
    ----------
    request
        The FastAPI request object.
    asession
        The SQLAlchemy async session to use for all database connections.
    credentials
        The HTTP Basic credentials provided by the client. This is used to verify
        the caller's identity if the grant type is "client_credentials". If the
        grant type is "password", this parameter is ignored and the username and
        password are taken from the form data.
    csrf_protect
        The CSRF protection dependency. This is used to generate and validate CSRF
        tokens for the password grant type. It is not used for the client credentials.
    form
        Form data covering both client_credentials and password grants:
            - `grant_type`: one of "client_credentials" or "password"
            - required fields vary by grant type

    Returns
    -------
    JSONResponse | TokenResponse
        A response containing the access token, its expiration time, and the token
        type. If the grant type is "client_credentials", it returns a `TokenResponse`
        with the access token and refresh token. If the grant type is "password", it
        returns a `JSONResponse` with the access token set as an HTTP-only cookie.

    Raises
    ------
    HTTPException
        If the user/client is locked out due to too many failed login attempts.
        If the user/client credentials are invalid or the user/client does not exist.
        If the grant type is unsupported.
    """

    assert request.client is not None, f"Request client is None: {request}"
    ip = request.client.host
    redis_client = request.app.state.redis

    match form.grant_type:
        case "client_credentials":
            client_id = form.client_id or (
                credentials.username if credentials else None
            )
            client_secret = form.client_secret or (
                credentials.password if credentials else None
            )

            # 1.
            if await is_locked_out(
                client_id=client_id, ip=ip, redis_client=redis_client
            ):
                raise HTTPException(
                    detail="Too many failed login attempts.",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            # 2.
            client_db = await verify_client(
                asession=asession, client_id=client_id, client_secret=client_secret
            )
            if client_db is None:
                await record_failed_login(
                    client_id=client_id, ip=ip, redis_client=redis_client
                )
                raise HTTPException(
                    detail="Invalid client credentials",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            # 3.
            await reset_failed_login(
                client_id=client_id, ip=ip, redis_client=redis_client
            )

            # 4.
            allowed_scopes = client_db.scopes
            requested_scopes = await get_least_privileged_scopes(
                allowed_scopes=list(allowed_scopes),
                requested_scopes=form.scopes,
                sub=client_db.client_id,
            )

            # 5.
            token = await get_jwt_token(
                grant_type=form.grant_type,
                passphrase=AUTH_RSA_PASSPHRASE.get_secret_value(),
                redis_client=redis_client,
                scopes=requested_scopes,
                sub=client_db.client_id,
            )

            # 6.
            refresh_token, refresh_token_expires_in = await generate_refresh_token(
                client_id=client_db.client_id,
                redis_client=request.app.state.redis,
                scopes=requested_scopes,
                sub=str(client_db.client_id),
            )

            # 7.
            return TokenResponse(
                access_token=token,
                expires_in=AUTH_TOKEN_TTL,
                refresh_token=refresh_token,
                refresh_token_expires_in=refresh_token_expires_in,
                scopes=requested_scopes,
                token_type="Bearer",
            )
        case "password":
            username, password = form.username, form.password

            # 1.
            if await is_locked_out(ip=ip, redis_client=redis_client, username=username):
                raise HTTPException(
                    detail="Too many failed login attempts.",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            # 2.
            user_db = await verify_user(
                asession=asession, password=password, username=username
            )
            if user_db is None:
                await record_failed_login(
                    ip=ip, redis_client=redis_client, username=username
                )
                raise HTTPException(
                    detail="Invalid user credentials.",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            # 3.
            await reset_failed_login(
                ip=ip, redis_client=redis_client, username=username
            )

            # 4.
            allowed_scopes = await get_user_scopes_by_id(
                asession=asession, user_id=user_db.user_id
            )
            requested_scopes = await get_least_privileged_scopes(
                allowed_scopes=list(allowed_scopes),
                requested_scopes=form.scopes,
                sub=user_db.user_id,
            )

            # 5.
            access_token = await get_jwt_token(
                grant_type=form.grant_type,
                passphrase=AUTH_RSA_PASSPHRASE.get_secret_value(),
                redis_client=redis_client,
                scopes=requested_scopes,
                sub=str(user_db.user_id),
            )

            # 6.
            refresh_token, refresh_token_expires_in = await generate_refresh_token(
                client_id=None,
                redis_client=request.app.state.redis,
                scopes=requested_scopes,
                sub=str(user_db.user_id),
            )

            # 7.
            token_response = TokenResponse(
                access_token=access_token,
                expires_in=AUTH_TOKEN_TTL,
                refresh_token=refresh_token,
                refresh_token_expires_in=refresh_token_expires_in,
                scopes=requested_scopes,
                token_type="Bearer",
            )

            # 8.
            csrf_token, signed_token = csrf_protect.generate_csrf_tokens()
            response = JSONResponse(content=token_response.model_dump())
            secure = FASTAPI_ENV in ["dev", "prod"]  # Sent only over HTTPS

            # Set HTTP-only cookie for access token.
            response.set_cookie(
                httponly=True,  # Not visible to JS
                key="access_token",
                max_age=AUTH_TOKEN_TTL,
                samesite="lax",
                secure=secure,  # Ensure cookie is only sent over HTTPS
                value=access_token,
            )

            # Set Non-HTTP-only cookie for CSRF cookie.
            csrf_protect.set_csrf_cookie(signed_token, response)

            # Set unsigned CSRF token in response headers for front-end SPA to use.
            response.headers["X-CSRF-Token"] = csrf_token

            return response
        case _:
            raise HTTPException(
                detail=f"Unsupported grant type: {form.grant_type}.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )


@router.post(
    "/token/rotate-refresh-token",
    response_model=JSONResponse,
    summary="Rotate refresh token",
)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def refresh_token_endpoint(
    form: RefreshTokenRequestForm,
    request: Request,
    csrf_protect: CsrfProtect = Depends(),
) -> JSONResponse:
    """Rotate a refresh token to issue a new access token.

    This endpoint allows **clients** (not users) to exchange a valid refresh token for
    a new access token and a new refresh token. The old refresh token is revoked in the
    process.

    The process is as follows:

    1. The CSRF token is validated to ensure the request is legitimate and not a CSRF
        attack.
    2. The refresh token is validated and rotated, generating a new access token and
         a new refresh token. If the `revoke_access` flag is set, the access token is
        also revoked.
    3. A `TokenResponse` is created containing the new access token, its expiration
        time, the new refresh token, and its expiration time.
    4. The access token is set as an HTTP-only cookie in the response, which is not
        accessible via JavaScript, enhancing security against XSS attacks (e.g., the
        access token is dropped into an HTTP-only cookie).
    5. Refresh the CSRF cookie for the next request cycle.

    Note on HTTP-only cookie:

    In the context of rotating refresh tokens, the access token is set as an HTTP-only
    cookie to prevent JavaScript access to the token, mitigating XSS attacks. This
    cookie is sent automatically with each request to the server, allowing the server
    to authenticate the user without requiring the client to manually include the
    access token in request headers. This is particularly useful for web applications
    where the access token is used to authenticate requests made by the user's browser.

    Note on CSRF dependency:

    The dependency automatically grabs the request, reads the signed cookie, compares
    it with the header echoed from the front‑end application, and raises
    `CsrfProtectError` on mismatch.

    Parameters
    ----------
    form
        The form data containing the refresh token to rotate and whether to revoke the
        access token.
    request
        The FastAPI request object.
    csrf_protect
        The CSRF protection dependency. This is used to validate the CSRF token
        provided in the request header.

    Returns
    -------
    JSONResponse
        A JSON response containing the new access token, its expiration time, the new
        refresh token, and its expiration time. The access token is also set as an
        HTTP-only cookie in the response.
    """

    # 1.
    await csrf_protect.validate_csrf(request)

    # 2.
    (
        access_token,
        new_refresh_token,
        access_token_expires_in,
        refresh_token_expires_in,
    ) = await rotate_refresh_token(
        redis_client=request.app.state.redis,
        refresh_token=form.refresh_token,
        revoke_access=form.revoke_access,
    )

    # 3.
    token_response = TokenResponse(
        access_token=access_token,
        expires_in=access_token_expires_in,
        refresh_token=new_refresh_token,
        refresh_token_expires_in=refresh_token_expires_in,
        token_type="Bearer",
    )

    # 4.
    response = JSONResponse(content=token_response.model_dump())
    secure = FASTAPI_ENV in ["dev", "prod"]  # Sent only over HTTPS
    response.set_cookie(
        httponly=True,  # Not visible to JS
        key="access_token",
        max_age=AUTH_TOKEN_TTL,
        samesite="lax",  # Lax is sufficient once CSRF token is validated
        secure=secure,  # Ensure cookie is only sent over HTTPS
        value=access_token,
    )

    # 5.
    _, new_csrf_signed = csrf_protect.generate_csrf_tokens()
    csrf_protect.set_csrf_cookie(new_csrf_signed, response)

    return response


@router.post(
    "/token/revoke-token",
    response_model=RevokeTokenResponse,
    summary="Revoke a refresh or access token",
)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def revoke_token(
    request: Request,
    claims: dict = require_scopes(required_scopes={"admin"}),
    token: str = Form(..., description="Access or refresh token to revoke"),
) -> RevokeTokenResponse:
    """Revoke a refresh or access token.

    This endpoint allows clients to revoke either an access token or a refresh token.
    If the token is an access token, it will be identified by its JTI (JWT ID) claim.
    If the token is a refresh token, it will be identified by its SHA-256 hash. The
    token is removed from the Redis cache, effectively invalidating it.

    NB: `jti` is a required part of the *access* token specification since all access
    tokens are stored in Redis with using `jti` as the key in order to support token
    validation and revocation. However, it is never included in *refresh* tokens,
    because refresh tokens are opaque---they are long, random base64url-encoded strings
    that are now JWTs. Thus if `jwt.get_unverified_claims(token)` succeeds and includes
    a `jti` field, then it's an access token. Otherwise, if that fails (e.g. invalid
    JWT format), or `jti` is missing, then we treat it as a refresh token.

    Parameters
    ----------
    request
        The FastAPI request object, used to access the Redis client.
    claims
        The claims of the authenticated caller, used to verify scopes and grant type.
        This is required to ensure that only authorized users can revoke tokens.
    token
        The JWT token to revoke, which can be either an access token or a refresh token.

    Returns
    -------
    RevokeTokenResponse
        A response indicating the revoked token.
    """

    redis_client = request.app.state.redis

    # Try access‑token path first.
    try:
        claims = jwt.get_unverified_claims(token)
        jti = claims.get("jti")
        if jti:
            await redis_client.delete(REDIS_CACHE_PREFIX_JTI.format(jti=jti))
            return RevokeTokenResponse(
                revoked_by=claims["sub"], revoked_token=token, type="access_token"
            )
    except JWTError:
        pass

    # Otherwise treat as refresh token.
    token_hash = hashlib.sha256(token.encode(), usedforsecurity=True).hexdigest()
    await redis_client.delete(
        REDIS_CACHE_PREFIX_REFRESH_TOKEN.format(token_hash=token_hash)
    )

    return RevokeTokenResponse(
        revoked_by=claims["sub"], revoked_token=token, type="refresh_token"
    )

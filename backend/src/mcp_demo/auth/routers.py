"""This module contains FastAPI routers for authentication endpoints.

This setup is optimized for machine-to-machine (service-to-service) use cases,
providing a lightweight OAuth2 “password grant” token-issuing endpoint (bypassing
refresh/consent flows), plus key discovery via JWKS.
"""

# Standard Library
from typing import Any, Optional

# Third Party Library
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.auth.schemas import IntrospectionResponse
from mcp_demo.auth.utils import (
    ClientCredentialsRequestForm,
    get_cached_jwks,
    get_jwt_token,
    sanitize_scopes,
    validate_token_and_get_claims,
)
from mcp_demo.clients.models import Oauth2ClientDB
from mcp_demo.clients.utils import Oauth2ClientNotFoundError, verify_client
from mcp_demo.config import Settings
from mcp_demo.schemas import TokenResponse
from mcp_demo.users.models import UserDB
from mcp_demo.users.utils import UserNotFoundError, get_user_scopes_by_id, verify_user
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
router = APIRouter(prefix="/auth", tags=[TAG_METADATA["name"]])

basic_auth = HTTPBasic(auto_error=False)  # RFC 7662 requires auth but we handle error
limiter = Limiter(key_func=get_remote_address, storage_uri=Settings.REDIS_URL)


@router.get(
    "/jwks.json",
    description=(
        "Returns the JSON Web Key Set (JWKS) used by this service to sign JWTs. "
        "FastMCP’s `BearerAuthProvider` and other OAuth2 clients automatically cache "
        "this URI based on its `Cache-Control` headers."
    ),
    include_in_schema=False,
    summary="JWKS Endpoint",
)
async def get_jwks() -> JSONResponse:
    """Serve the public JWKS for verifying JWT signatures.

    - Issuers sign tokens with private keys; clients must fetch public keys via this
        endpoint.
    - In production, the endpoint must be served over HTTPS.
    - JWKS is cached by clients and refreshed according to `Cache-Control` headers.

    Returns
    -------
    JSONResponse
        The JWKS bundle containing all active public keys.
    """

    jwks = await get_cached_jwks()
    print(f"{jwks = }")
    input()
    return JSONResponse(jwks)


@router.post("/introspect", response_model=IntrospectionResponse)
@limiter.limit(Settings.RATE_LIMIT_LOGIN_RATE)
async def introspect_token(
    request: Request,  # pylint: disable=W0613
    asession: AsyncSession = Depends(get_async_session),
    credentials: HTTPBasicCredentials | None = Depends(basic_auth),
    token: str = Form(..., description="Access or refresh token to introspect"),
) -> IntrospectionResponse:
    """RFC 7662-style token introspection.

    *Authenticated* callers can verify whether a JWT is active and view the scopes
    associated with its subject (user or client).

    The process is as follows:

    1. The caller must provide HTTP Basic credentials to authenticate.
    2. The caller's identity is verified against the database:
        - If a client ID is provided, it must match a registered client with a valid
            secret.
        - If a username is provided, it must match a registered user with a valid
            password.
    3. The JWT is decoded and validated:
        - Signature, expiry, and replay (jti) checks are performed.
    4. The introspection response is built, indicating whether the token is active,
        its expiry, scopes, and subject/client ID.
    5. The response is checked to ensure the token belongs to the authenticated client
        or user:

    Parameters
    ----------
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    asession
        The SQLAlchemy async session to use for all database connections.
    credentials
        Optional HTTP Basic credentials for client authentication. If provided, the
        caller must be a registered client with a valid secret.
    token
        The JWT token to introspect, provided as a form field.

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
    if credentials is None:
        raise HTTPException(
            detail="Basic authentication required",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # 2.
    caller_db, jwt_options = await check_introspection_call(
        asession=asession, credentials=credentials
    )

    # 3.
    try:
        claims = await validate_token_and_get_claims(options=jwt_options, token=token)
    except HTTPException:
        return IntrospectionResponse(active=False)

    # 4.
    is_client = claims.get("gty") == "client_credentials"
    response = {
        "active": True,
        "client_id": claims["sub"] if is_client else None,
        "exp": claims["exp"],
        "scope": claims.get("scope", ""),
        "sub": None if is_client else int(claims["sub"]),
        "token_type": "access_token",
    }

    # 5.
    if (
        isinstance(caller_db, Oauth2ClientDB)
        and response["client_id"] != caller_db.client_id
    ) or (isinstance(caller_db, UserDB) and response["sub"] != caller_db.user_id):
        return IntrospectionResponse(active=False)

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
@limiter.limit(Settings.RATE_LIMIT_LOGIN_RATE)
async def token_endpoint(
    request: Request,
    asession: AsyncSession = Depends(get_async_session),
    credentials: HTTPBasicCredentials | None = Depends(basic_auth),
    form: ClientCredentialsRequestForm = Depends(),
) -> TokenResponse:
    """Issue a Bearer token via OAuth2 Client-Credentials or Password grant.

    Grant types supported:

    1. client_credentials
    2. password

    Upon successful authentication—whether service or human—a short‑lived RS256 JWT is
    issued.

    Parameters
    ----------
    request
        The FastAPI request object.
    asession
        The SQLAlchemy async session to use for all database connections.
    credentials
        Optional HTTP Basic credentials for client authentication. If provided, the
        caller must be a registered client with a valid secret.
    form
        Form data covering both client_credentials and password grants:
            - `grant_type`: one of "client_credentials" or "password"
            - required fields vary by grant type

    Returns
    -------
    TokenResponse
        A Bearer token response including `access_token`, `token_type`, and
        `expires_in`.

    Raises
    ------
    HTTPException
        If the client ID does not exist, is inactive, or the client secret does not
            match the stored hash.
        If the user is locked out due to too many failed login attempts.
        If the user credentials are invalid or the user does not exist.
    """

    assert request.client is not None, f"Request client is None: {request}"
    ip = request.client.host
    redis_client = request.app.state.redis

    match form.grant_type:
        case "client_credentials":
            client_id = form.client_id if credentials is None else credentials.username
            client_secret = (
                form.client_secret if credentials is None else credentials.password
            )
            if not (client_id and client_secret):
                raise HTTPException(
                    detail="Client ID/Client Secret required.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

            client_db = await verify_client(
                asession=asession, client_id=client_id, client_secret=client_secret
            )
            if client_db is None:
                await record_failed_login(
                    ip=ip, redis_client=redis_client, user=client_id
                )
                raise HTTPException(
                    detail="Invalid client credentials",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            await reset_failed_login(ip=ip, redis_client=redis_client, user=client_id)

            token = await get_jwt_token(
                grant_type="client_credentials",
                redis_client=redis_client,
                scopes=sanitize_scopes(requested_scopes=client_db.scopes),
                sub=client_db.client_id,
            )

            return TokenResponse(
                access_token=token,
                expires_in=Settings.AUTH_TOKEN_TTL,
                token_type="Bearer",
            )
        case "password":
            username = form.username

            if await is_locked_out(ip=ip, redis_client=redis_client, user=username):
                raise HTTPException(
                    detail="Too many failed login attempts. Try again later.",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            user_db = await verify_user(
                asession=asession, password=form.password, username=username
            )
            if user_db is None:
                await record_failed_login(
                    ip=ip, redis_client=redis_client, user=username
                )
                raise HTTPException(
                    detail="Too many failed login attempts. Try again later.",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            await reset_failed_login(ip=ip, redis_client=redis_client, user=username)

            user_scopes = await get_user_scopes_by_id(
                asession=asession, user_id=user_db.user_id
            )
            token = await get_jwt_token(
                grant_type="password",
                passphrase=Settings.AUTH_RSA_PASSPHRASE.get_secret_value(),
                redis_client=redis_client,
                scopes=sanitize_scopes(requested_scopes=list(user_scopes)),
                sub=str(user_db.user_id),
            )

            return TokenResponse(
                access_token=token,
                expires_in=Settings.AUTH_TOKEN_TTL,
                token_type="bearer",
            )
        case _:
            raise HTTPException(
                detail=f"Unsupported grant type: {form.grant_type}.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )


async def check_introspection_call(
    *, asession: AsyncSession, credentials: HTTPBasicCredentials
) -> tuple[Oauth2ClientDB | UserDB, dict[str, Any]]:
    """Check if the caller is authenticated for introspection.

    This function attempts to authenticate the caller as either a client or a user. If
    the caller is authenticated, it returns the JWT options to use for decoding. If the
    caller is not authenticated, it raises an HTTPException.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    credentials
        The HTTP Basic credentials provided by the caller.

    Returns
    -------
    tuple[Oauth2ClientDB | UserDB, dict[str, Any]]
        A tuple containing the authenticated caller's database object and JWT options.

    Raises
    ------
    HTTPException
        If the caller is not authenticated, or if the credentials are invalid.
    """

    caller_db: Optional[Oauth2ClientDB | UserDB] = None
    options: dict[str, Any] = {}

    try:  # Try to authenticate as a client first.
        client_db = await verify_client(
            asession=asession,
            client_id=credentials.username,
            client_secret=credentials.password,
        )
        if client_db:
            caller_db = client_db
            options = {
                "verify_aud": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
                "verify_nbf": True,
            }
    except Oauth2ClientNotFoundError:
        pass

    if not caller_db:
        try:  # Try to authenticate as a user second.
            user_db = await verify_user(
                asession=asession,
                password=credentials.password,
                username=credentials.username,
            )
            if user_db:
                caller_db = user_db
                options = {"require": ["exp", "sub"]}
        except UserNotFoundError:
            pass

    # Fail if neither authenticated.
    if not caller_db:
        raise HTTPException(
            detail="Invalid credentials",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return caller_db, options

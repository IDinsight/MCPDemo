"""This module contains authentication utilities.

This module houses *all* key-management and token-generation logic used by the
authentication service and, indirectly, every other server that trusts its tokens
(e.g., the FastMCP server).

High-level flow
---------------
1. First start-up
   - `rotate_keys()` is invoked automatically by `get_jwt_token()` (via
    `get_latest_private_key_and_kid()`) when `jwks.json` is empty. It generates a fresh
    RSA-3072 key-pair, stores the private key (PKCS-8 + passphrase) in
    `$PATHS_SECRETS_DIR`, publishes the public key in `jwks.json`, and primes the
    in-memory JWKS cache.
2. Normal token issuance
   - `routers.issue_token` calls `get_jwt_token()`:
    2a. `get_latest_private_key_and_kid()` fetches the newest private key.
    2b. A short-lived RS256 JWT is signed (`kid` header set).
    2c. The JWT is returned to the caller.
3. Key rotation (scheduled task or manual call)
   - `rotate_keys()` generates a *new* key-pair, inserts its JWK at the head of
   `jwks.json`, prunes keys beyond `AUTH_ROTATION_KEEP_LAST_N`, deletes stale PEM
   files, and updates the global JWKS cache.
4. JWKS serving
   - API route `/auth/jwks.json` simply calls `load_jwks()`. Other servers (e.g.,
   FastMCP servers) periodically fetch this endpoint to verify future tokens.

Concurrency & Safety
--------------------
1. `process_lock()` wraps all file-system writes so multiple Uvicorn workers (or cron
    jobs) cannot rotate keys concurrently.
2. Atomic writes via `atomic_write()` guarantee that readers never observe partially
    written files.
3. In-memory cache (`_JWKS_CACHE` + `_JWKS_MTIME`) avoids repeated disk I/O and
    re-parses while still noticing on-disk changes made by other processes.

Environment contracts
---------------------
1. `$PATHS_SECRETS_DIR` (directory) must exist and be writable by the process.
2. `Settings.*` values (issuer, audience, time-outs, etc.) drive defaults.

All public helpers are asyncio-friendly; call them with `await` unless noted otherwise.
"""

# pylint: disable=W0603
# Standard Library
import asyncio
import base64
import json
import os
import time

from contextlib import asynccontextmanager
from pathlib import Path
from secrets import token_hex
from typing import Any, AsyncIterator, cast

# Third Party Library
import jwt

from authlib.jose import jwk
from authlib.oauth2.rfc6749 import AuthorizationServer, InvalidRequestError, grants
from authlib.oauth2.rfc6749.requests import BasicOAuth2Payload, OAuth2Request
from authlib.oauth2.rfc7636 import CodeChallenge
from authlib.oauth2.rfc9068 import JWTBearerTokenGenerator
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from filelock import FileLock
from loguru import logger
from sqlalchemy import select
from starlette.responses import JSONResponse, Response

# Package Library
from mcp_demo.auth.models import OAuth2AuthorizationCode, OAuth2Client, OAuth2Token
from mcp_demo.config import Settings
from mcp_demo.users.models import UserDB
from mcp_demo.utils.database import get_async_session_managed
from mcp_demo.utils.general import atomic_write, make_dir

_JWKS_CACHE: dict[str, Any] | None = None  # In-memory copy
_JWKS_MTIME: float | None = None  # Last os.stat mtime

PATHS_PROJECT_DIR = os.getenv("PATHS_PROJECT_DIR", None)
assert PATHS_PROJECT_DIR is not None
_SECRETS_DIR = Path(PATHS_PROJECT_DIR) / "secrets"
make_dir(_SECRETS_DIR, mode=0o700)
_LOCK = FileLock(str(_SECRETS_DIR / ".rotate.lock"), timeout=0)  # Non-blocking lock

AUTH_AUDIENCE = Settings.AUTH_AUDIENCE
AUTH_FILELOCK_TIMEOUT = Settings.AUTH_FILELOCK_TIMEOUT
AUTH_JWK_ALGORITHM = Settings.AUTH_JWK_ALGORITHM
AUTH_JWKS_FN = Settings.AUTH_JWKS_FN
AUTH_ROTATION_KEEP_LAST_N = Settings.AUTH_ROTATION_KEEP_LAST_N
AUTH_RSA_KEY_SIZE = Settings.AUTH_RSA_KEY_SIZE
AUTH_RSA_PUBLIC_EXPONENT = Settings.AUTH_RSA_PUBLIC_EXPONENT
AUTH_TOKEN_ISSUER = Settings.AUTH_TOKEN_ISSUER
AUTH_TOKEN_TTL = Settings.AUTH_TOKEN_TTL


class AuthorizationCodeGrantPKCE(grants.AuthorizationCodeGrant):
    """Authorization Code Grant with mandatory PKCE support for public clients.

    This subclass of Authlib's `AuthorizationCodeGrant` enforces the use of Proof Key
    for Code Exchange (PKCE) by requiring both `code_challenge` and
    `code_challenge_method` during the authorization request. It also implements
    persistence layer hooks for saving, querying, authenticating, and deleting the
    authorization codes in the database.

    Attributes inherited:
    - TOKEN_ENDPOINT_AUTH_METHODS = ["none"] makes this grant suitable for public
        clients (no client secret required).

    This class should be registered to `FastAPIAuthorizationServer` as follows:

        server.register_grant(
            AuthorizationCodeGrantPKCE, [CodeChallenge(required=True)]
        )

    The `CodeChallenge` extension ensures PKCE checks are enforced at runtime.
    """

    TOKEN_ENDPOINT_AUTH_METHODS = ["none"]

    async def authenticate_user(
        self, authorization_code: OAuth2AuthorizationCode
    ) -> UserDB:
        """Retrieve the UserDB who originally granted permission for this authorization
        code.

        Parameters
        ----------
        authorization_code
            The code record containing `user_id`.

        Returns
        -------
        UserDB
            The corresponding user object from the `UserDB` model.
        """

        async with get_async_session_managed() as db:
            return await db.get(UserDB, authorization_code.user_id)

    async def delete_authorization_code(
        self, authorization_code: OAuth2AuthorizationCode
    ) -> None:
        """Delete the authorization code once it is exchanged for tokens.

        Parameters
        ----------
        authorization_code
            The persisted authorization code record to be removed.
        """

        async with get_async_session_managed() as db:
            await db.delete(authorization_code)
            await db.commit()

    async def query_authorization_code(
        self, code: str, client: OAuth2Client
    ) -> OAuth2AuthorizationCode | None:
        """Fetch a valid, non-expired authorization code belonging to the specified
        client.

        PKCE values (`code_challenge`, `code_challenge_method`) are persisted with the
        code during `save_authorization_code`.

        Parameters
        ----------
        code
            The authorization code string provided by the client.
        client
            The client requesting token exchange; this code must belong to it.

        Returns
        -------
        OAuth2AuthorizationCode | None
            The matching code record or `None` if not found or expired.
        """

        async with get_async_session_managed() as db:
            stmt = (
                select(OAuth2AuthorizationCode)
                .where(OAuth2AuthorizationCode.code == code)
                .where(OAuth2AuthorizationCode.client_id == client.client_id)
            )
            return await db.scalar_one_or_none(stmt)

    async def save_authorization_code(self, code: str, request: OAuth2Request) -> None:
        """Persist the newly issued authorization code, including its PKCE fields. This
        method is invoked during the `/authorize` endpoint flow.

        Parameters
        ----------
        code
            Randomly generated string as the authorization code.
        request
            The incoming authorization request, containing `client`, `redirect_uri`,
            `scope`, `user`, and `data` that must include:
                - code_challenge
                - code_challenge_method

        Raises
        ------
        InvalidRequestError
            If the request does not contain the required PKCE fields.
        """

        code_challenge = request.data.get("code_challenge", None)
        if not code_challenge:
            raise InvalidRequestError("'code_challenge' is required for PKCE.")

        code_challenge_method = request.data.get("code_challenge_method", None)
        if not code_challenge_method:
            raise InvalidRequestError("'code_challenge_method' is required for PKCE.")

        async with get_async_session_managed() as db:
            item = OAuth2AuthorizationCode(
                code=code,
                code_challenge=code_challenge,
                code_challenge_method=code_challenge_method,
                client_id=request.client.client_id,
                redirect_uri=request.redirect_uri,
                scope=request.scope,
                user_id=request.user.id,
            )
            db.add(item)
            await db.commit()


class FastAPIAuthorizationServer(AuthorizationServer):
    """OAuth 2.1 Authorization Server tailored for FastAPI/Starlette requests.

    This class extends Authlib's core `AuthorizationServer` to integrate with FastAPI
    and Starlette, handling persistence hooks and request/response translation
    out-of-the-box.

    Features:
        - **DB-backed** client lookups via `query_client()`
        - **Token persistence** using `save_token()` into `OAuth2Token` table
        - **Graceful Starlette integration**: consumes `fastapi.Request` and emits
            `starlette.responses.Response` or `JSONResponse`
        - **PKCE-compatible** when used with `AuthorizationCodeGrantPKCE`
        - Log errors and wrap Authlib outcomes in HTTP-style objects
        - Access-token revocation hooks are not enabled; see RFC 7009 if you need them.

    Refer to Authlib docs for AuthorizationServer internals:
        - Registration of grants and token generators
        - Use of `CodeChallenge` extension for PKCE validation

    Security notes:
      - Requires HTTPS transport
      - Works with `AuthorizationCodeGrantPKCE` for public-client support
    """

    async def create_oauth2_request(self, request: Request) -> OAuth2Request:
        """Convert FastAPI/Starlette `Request` to Authlib `OAuth2Request`. This method
        extracts HTTP method, full URL, headers, query params, and form data.

        Parameters
        ----------
        request
            The incoming FastAPI/Starlette request object.

        Returns
        -------
        OAuth2Request
            An instance of `StarletteOAuth2Request` containing the request data.
        """

        args_dict = dict(request.query_params)
        form_dict = {}
        if request.method in ["PATCH", "POST", "PUT"]:
            form = await request.form()
            form_dict = {k: str(v) for k, v in form.items()}

        return StarletteOAuth2Request(
            args=args_dict,
            form=form_dict,
            headers=dict(request.headers),
            method=request.method,
            uri=str(request.url),
        )

    def handle_response(
        self,
        status: int,
        body: dict[str, Any] | list | str,
        headers: dict[str, str] | None = None,
    ) -> JSONResponse | Response:
        """Convert Authlib internal status/body/headers to FastAPI/Starlette Response.

        Parameters
        ----------
        status
            The HTTP status code to return.
        body
            The response body, which can be a dict, list, or string.
        headers
            Optional headers to include in the response.

        Returns
        -------
        JSONResponse | Response
            A FastAPI-compatible response object containing the status, body, and
            headers.
        """

        if status >= 400:
            logger.error(f"OAuth error {status}: {body}")
        headers = dict(headers or {})
        if isinstance(body, (dict, list)):
            return JSONResponse(content=body, status_code=status, headers=headers)

        # Token endpoint returns urlencoded string.
        return Response(content=body, status_code=status, headers=headers)

    async def query_client(self, client_id: str) -> OAuth2Client | None:
        """Lookup client by `client_id` from your DB.

        Parameters
        ----------
        client_id
            The unique identifier for the OAuth2 client.

        Returns
        -------
        OAuth2Client | None
            The client record if found, or `None` if not found.
        """

        async with get_async_session_managed() as db:
            stmt = select(OAuth2Client).where(OAuth2Client.client_id == client_id)
            return await db.scalar(stmt)

    async def save_token(self, token: dict[str, Any], request: OAuth2Request) -> None:
        """Persist issued tokens per Authlib requirements.

        Parameters
        ----------
        token
            The token data to be saved, typically containing `access_token`,
        request
            The OAuth2Request object containing client and user information.
        """

        async with get_async_session_managed() as db:
            db.add(
                OAuth2Token(
                    client_id=request.client.client_id, user_id=request.user.id, **token
                )
            )
            await db.commit()


class MCPJWTGenerator(JWTBearerTokenGenerator):
    """Issue RS256-signed JWT access tokens compliant with RFC 9068.

    This class extends Authlib's `JWTBearerTokenGenerator` to:
        - Sign access tokens with a given RSA private key and `kid`,
        - Embed public key metadata in JWKS format for resource server discovery.

    Notes
    -----
    1. Conforms to RFC 9068 (JSON Web Token (JWT) Profile for OAuth 2.0 Access Tokens).
    2. The JWKS provided allows downstream resource servers to validate the JWT
        signature.
    3. The `kid` ensures clients/resolvers can select the correct key when multiple are
        available.
    """

    def __init__(
        self, *, kid: str, private_key: rsa.RSAPrivateKey, **kwargs: Any
    ) -> None:
        """

        Parameters
        ----------
        kid
            Key ID to include in both the JWT header and JWKS entry.
        private_key
            An RSA private key instance supporting `alg` (usually from `cryptography`).
        **kwargs
            Additional keyword arguments.
        """

        super().__init__(**kwargs)

        self._jwk = jwk.dumps(private_key, alg=self.alg, kid=kid, kty="RSA", use="sig")

    def get_jwks(self) -> dict[str, Any]:
        """Return the JSON Web Key Set (JWKS) containing the public key. This enables
        resource servers to fetch dynamic public keys for verifying JWT signatures
        against rotating RSA key pairs.

        Returns
        -------
        dict[str, Any]
            A JWKS dict containing the public JWK, used by authorization servers to
            expose available signing keys for resource servers (like FastMCP) to
            validate tokens.
        """

        return {"keys": [self._jwk]}


class StarletteOAuth2Request(OAuth2Request):
    """FastAPI/Starlette adapter for Authlib's OAuth2Request model.

    Authlib requires a framework-agnostic `OAuth2Request` to represent an incoming
    OAuth token or authorization request. This subclass provides the minimal behavior
    needed by Authlib, wrapping Starlette’s `Request` specifics (query params and form).

    Notes
    -----
    1. This class is required by Authlib's AuthorizationServer integration. It enables
        granting and validating flows (e.g., PKCE).
    2. Must be created inside `FastAPIAuthorizationServer.create_oauth2_request()`, so
        that Grant classes can operate properly.
    """

    def __init__(
        self,
        *,
        args: dict[str, str],
        form: dict[str, str],
        headers: dict[str, str] | None,
        method: str,
        uri: str,
    ) -> None:
        """

        Parameters
        ----------
        args
            Query parameters (the `?` part of the URL).
        form
            Form-encoded body parameters (when `Content-Type` is
            `application/x-www-form-urlencoded`).
        headers
            HTTP headers as a simple string-to-string mapping.
        method
            HTTP method of the request, e.g., "GET" or "POST".
        uri
            Full request URI, including path and query string.
        """

        super().__init__(method, uri, headers)

        self._args = args
        self._form = form

        # Merge into a single dict for Authlib's BasicOAuth2Payload helper.
        self.payload = BasicOAuth2Payload({**args, **form})

    @property
    def args(self) -> dict[str, str]:
        """Return the query parameters of the request.

        Example: `{'client_id': 'abc', 'scope': 'read write'}` extracted from the URL.

        Returns
        -------
        dict[str, str]
            The query parameters as a dictionary, where keys are parameter names and
            values are their corresponding values.
        """

        return self._args

    @property
    def form(self) -> dict[str, str]:
        """Return the form-encoded body data of the request.

        Example: `{'grant_type': 'authorization_code', 'code': 'xyz', ...}` for token
        exchanges.

        Returns
        -------
        dict[str, str]
            The form data as a dictionary, where keys are parameter names and values
            are their corresponding values.
        """

        return self._form

    @property
    def scope(self) -> str | None:
        """Return the OAuth scope as a single space-delimited string. The value is
        looked up first in the form body (token exchange POST), then in the query
        string (initial /authorize request).

        Example: `read write` if the request included `scope=read write`.

        Returns
        -------
        str | None
            The requested scope string, e.g., `"read write"`, or `None` if no scope was
            specified in the request.
        """

        if "scope" in self._form:
            return self._form["scope"]
        if "scope" in self._args:
            return self._args["scope"]
        return None


def b64url(*, data: bytes) -> str:
    """Return RFC 7515 base64url without padding.

    Ref: https://datatracker.ietf.org/doc/html/rfc7515

    Parameters
    ----------
    data
        The data to be encoded in base64url format.

    Returns
    -------
    str
        The base64url encoded string without padding.
    """

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


async def create_auth_server() -> AuthorizationServer:
    """Build and configure the FastAPI OAuth 2.1 Authorization Server.

    The returned `AuthorizationServer` is ready to be integrated into a FastAPI
    application, offering:
        - Authorization Code Grant with PKCE support
        - RS256-signed JWT access tokens per RFC 9068
        - Dynamic JWKS publishing via embedded key generator

    Notes
    -----
    1. JWT access tokens conform to RFC 9068 and include the `kid` for key rotation.
    2. PKCE is enforced to protect public clients without client secrets.
    3. The `"default"` generator must be registered or Authlib will raise during login
        flows.

    The process is as follows:

    1. Instantiate `FastAPIAuthorizationServer`, a subclass of Authlib's
        `AuthorizationServer` designed for FastAPI.
    2. Register the `AuthorizationCodeGrantPKCE`, enforcing PKCE using
        `CodeChallenge(required=True)` for public-client security.
    3. Load the current RSA key pair using `get_latest_private_key_and_kid()`, ensuring
        integration with your existing key-rotation logic.
    4. Create `MCPJWTGenerator` with:
        - The private key and `kid`
        - Issuer claim set to `"https://auth.local"`
        - RS256 signing algorithm
        - A fixed TTL from `AUTH_TOKEN_TTL` settings (default 900s)
    5. Register the JWT generator under the `"default"` grant type, signifying it
        should be used for all authorization grants.

    Returns
    -------
    AuthorizationServer
        Configured Authlib server instance, typically used in FastAPI routes.
    """

    # 1.
    server = FastAPIAuthorizationServer()

    # 2.
    server.register_grant(AuthorizationCodeGrantPKCE, [CodeChallenge(required=True)])

    # 3.
    private_key, kid = await get_latest_private_key_and_kid(
        passphrase=Settings.AUTH_USER_PASSPHRASE.get_secret_value()
    )

    # 4.
    jwt_gen = MCPJWTGenerator(
        alg="RS256",
        expires_generator=lambda *_: AUTH_TOKEN_TTL,
        issuer="https://auth.local",
        private_key=private_key,
        kid=kid,
    )

    # 5.
    server.register_token_generator("default", jwt_gen)

    return server


def generate_rsa_keypair(
    *,
    key_size: int = AUTH_RSA_KEY_SIZE,
    public_exponent: int = AUTH_RSA_PUBLIC_EXPONENT,
) -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """Generate an RSA keypair.

    Parameters
    ----------
    key_size
        The size of the RSA key in bits. Use 2048 for most applications, or 4096 for
        longer-term certificates.
    public_exponent
        The public exponent for the RSA key.

    Returns
    -------
    tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
        A tuple containing the generated private key and its corresponding public key.
    """

    private_key = rsa.generate_private_key(
        public_exponent=public_exponent, key_size=key_size
    )

    return private_key, private_key.public_key()


async def get_jwt_token(
    *,
    jwks_fn: str = AUTH_JWKS_FN,
    passphrase: str,
    scopes: list[str],
    sub: str,
) -> str:
    """Generate a JWT token for the given subject and scopes.

    NB: We use RS256 for signing the JWT, which requires a private key. `kid` is used
    to identify the key in the JWKS (JSON Web Key Set) endpoint. The `iss` (issuer) and
    `aud` (audience) fields are set to the issuer and audience of the token,
    respectively, and should match the values expected by the server.

    The process is as follows:

    1. Grab the most-recent private key and its kid (auto-bootstraps on first run).
    2. Create a JWT payload with the subject, scopes, issuer, audience, issued at
         time, and expiration time.
    3. Sign with RS256 and embed the kid so verifiers find the right JWK.

    Parameters
    ----------
    jwks_fn
        The filename for the JWKS (JSON Web Key Set) file. This is only used for
        generating a new key if the JWKS file is empty.
    passphrase
        Passphrase to decrypt the private key.
    scopes
        A list of scopes to include in the JWT token.
    sub
        The subject for which the JWT token is being generated, typically a user ID.

    Returns
    -------
    str
        The generated JWT token as a string.
    """

    # 1.
    private_key, kid = await get_latest_private_key_and_kid(
        jwks_fn=jwks_fn, passphrase=passphrase
    )

    # 2.
    now = int(time.time())
    payload = {
        "aud": AUTH_AUDIENCE,
        "exp": now + AUTH_TOKEN_TTL,
        "iat": now,
        "iss": AUTH_TOKEN_ISSUER,
        "jti": token_hex(12),
        "nbf": now - 30,
        "scope": " ".join(scopes),
        "sub": sub,
        "typ": "JWT",
    }

    # 3.
    return cast(
        str,
        jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid}),
    )


async def get_latest_private_key_and_kid(
    *, jwks_fn: str = AUTH_JWKS_FN, passphrase: str
) -> tuple[rsa.RSAPrivateKey, str]:
    """Get the latest private key and its key ID (kid) for signing JWT tokens.

    Parameters
    ----------
    jwks_fn
        The filename for the JWKS (JSON Web Key Set) file. This is only used for
        generating a new key if the JWKS file is empty.
    passphrase
        Passphrase to decrypt the private key.

    Returns
    -------
    tuple[rsa.RSAPrivateKey, str]
        A tuple containing the loaded RSA private key and its key ID (kid).
    """

    jwks = await load_jwks(jwks_fn=jwks_fn)
    if not jwks["keys"]:
        # First run – create a pair automatically.
        kid = await rotate_keys(jwks_fn=jwks_fn, passphrase=passphrase)
    else:
        # Read the first (only) kid from jwks.json.
        kid = jwks["keys"][0]["kid"]

    private_key_fp = _SECRETS_DIR / f"private_{kid}.pem"

    return (
        load_private_key(passphrase=passphrase, private_key_fp=private_key_fp),
        kid,
    )


def jwk_from_public_key(
    *,
    alg: str = AUTH_JWK_ALGORITHM,
    kid: str,
    public_key: rsa.RSAPublicKey,
    use: str = "sig",
) -> dict[str, Any]:
    """Convert an RSA public key to a JWK payload suitable for a JWKS endpoint.

    Parameters
    ----------
    alg
        The algorithm used for the key, typically "RS256" for RSA keys.
    kid
        The key ID for the JWK, used to identify the key in a JWKS.
    public_key
        The RSA public key to be converted to JWK format.
    use
        The intended use of the key, typically "sig" for signature.

    Returns
    -------
    dict[str, Any]
        A dictionary representing the JWK (JSON Web Key) format of the public key.
    """

    numbers = public_key.public_numbers()
    return {
        "alg": alg,
        "e": b64url(data=numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
        "kid": kid,
        "kty": "RSA",
        "n": b64url(data=numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "use": use,
    }


async def load_jwks(*, jwks_fn: str = AUTH_JWKS_FN) -> dict[str, Any]:
    """Load the JSON Web Key Set (JWKS) from disk.

    NB: This function reads and parses the `jwks.json` file specified by
    `Settings.AUTH_JWKS_FN`. It caches the parsed keys in memory and tracks the file's
    last modification time. Subsequent calls only re-read and re-parse the file if its
    `mtime` has changed, making repeated invocations lightweight and efficient.

    Parameters
    ----------
    jwks_fn
        The filename for the JWKS (JSON Web Key Set) file.

    Returns
    -------
    dict[str, Any]
        The JWKS containing the keys, or an empty set if the file does not exist.
    """

    global _JWKS_CACHE, _JWKS_MTIME

    jwks_fp = _SECRETS_DIR / jwks_fn

    async with process_lock():
        if not jwks_fp.is_file():
            _JWKS_CACHE = {"keys": []}
            _JWKS_MTIME = None
            assert isinstance(_JWKS_CACHE, dict)
            return _JWKS_CACHE

        mtime = jwks_fp.stat().st_mtime
        if _JWKS_CACHE is None or _JWKS_MTIME != mtime:  # File changed or first read
            try:
                _JWKS_CACHE = json.loads(jwks_fp.read_text("utf-8"))
                _JWKS_MTIME = mtime
            except json.JSONDecodeError:
                logger.warning("Corrupted JWKS, regenerating")
                _JWKS_CACHE = {"keys": []}
        assert isinstance(_JWKS_CACHE, dict)
        return _JWKS_CACHE


def load_private_key(
    *, passphrase: str, private_key_fp: str | Path
) -> rsa.RSAPrivateKey:
    """Load a PEM-encoded private key from disk.

    Parameters
    ----------
    passphrase
        Passphrase to decrypt the private key.
    private_key_fp
        The path to the PEM file containing the private key.

    Returns
    -------
    rsa.RSAPrivateKey
        The loaded RSA private key.

    Raises
    ------
    TypeError
        If the loaded key is not an RSA private key.
    ValueError
        If the private key file is invalid or cannot be loaded.
    """

    private_key_fp = Path(private_key_fp)

    try:
        private_key = load_pem_private_key(
            private_key_fp.read_bytes(), password=passphrase.encode()
        )
    except ValueError as e:
        logger.error(f"Failed to load private key from {private_key_fp}: {e}")
        raise ValueError(f"Invalid private key file: {private_key_fp}") from e

    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise TypeError(f"Expected RSA private key, got {type(private_key).__name__}")

    return private_key


def load_public_key(*, public_key_fp: str | Path) -> rsa.RSAPublicKey:
    """Load a PEM-encoded public key from disk.

    Parameters
    ----------
    public_key_fp
        The path to the PEM file containing the public key.

    Returns
    -------
    rsa.RSAPublicKey
        The loaded RSA public key.

    Raises
    ------
    TypeError
        If the loaded key is not an RSA public key.
    """

    public_key_fp = Path(public_key_fp)
    public_key = load_pem_public_key(public_key_fp.read_bytes())

    if not isinstance(public_key, rsa.RSAPublicKey):
        raise TypeError(f"Expected RSA public key, got {type(public_key).__name__}")

    return public_key


@asynccontextmanager
async def process_lock() -> AsyncIterator[None]:
    """Context manager to acquire a process-wide lock.

    This is useful for ensuring that only one process can perform certain operations
    at a time, such as writing to a file or rotating keys. The lock is implemented
    using a file lock, which is suitable for cross-process synchronization.

    Notes:

    1. Non-blocking acquire (blocking=False) means the worker thread returns
        immediately.
    2. We poll inside the event loop with a tiny asyncio.sleep, so the loop stays
        responsive.
    3. We raise quickly if exceeding the filelock timeout instead of tying up the
        worker.

    Yields
    ------
    AsyncIterator[None]
        A context manager that acquires the lock before yielding and releases it
        after the block is executed.
    """

    deadline = time.monotonic() + AUTH_FILELOCK_TIMEOUT
    backoff = 0.05  # 50 ms – tweak if needed

    while True:
        acquired = await asyncio.to_thread(_LOCK.acquire, blocking=False)
        if acquired:
            break
        if time.monotonic() >= deadline:  # Hard ceiling
            raise TimeoutError(
                f"Could not obtain rotate.lock after {AUTH_FILELOCK_TIMEOUT}s"
            )
        await asyncio.sleep(backoff)
        backoff = min(backoff * 1.5, 0.5)  # Exponential back-off, cap at 500 ms

    try:
        yield
    finally:
        _LOCK.release()


async def rotate_keys(
    *,
    jwks_fn: str = AUTH_JWKS_FN,
    keep_last_n: int = AUTH_ROTATION_KEEP_LAST_N,
    key_size: int = AUTH_RSA_KEY_SIZE,
    passphrase: str,
) -> str:
    """Generate a new RSA key-pair, add it to `jwks.json`, delete keys older than
    `AUTH_ROTATION_KEEP_LAST_N`.

    The process is as follows:

    1. Generate a new kid (key ID) for the new key pair.
    2. Generate a new RSA key pair (private and public keys).
    3. Save the private and public keys to a PEM file, optionally encrypted with a
        passphrase.
    4. Load the existing JWKS (JSON Web Key Set) from `jwks.json`.
    5. Add the new key to the JWKS, ensuring it is at the top of the list. Prune the
        oldest keys, keeping only the most recent `AUTH_ROTATION_KEEP_LAST_N` keys.
    6. Save the updated JWKS back to `jwks.json`.
    7. Update the in-memory cache of the JWKS and its last modified time so that the
        current process can use the new key immediately.

    Parameters
    ----------
    jwks_fn
        The filename for the JWKS (JSON Web Key Set) file.
    keep_last_n
        The number of most recent keys to keep in the JWKS. Older keys will be deleted.
    key_size
        The size of the RSA key in bits. Use 2048 for most applications, or 4096 for
        longer-term certificates.
    passphrase
        Passphrase to encrypt the private key.

    Returns
    -------
    str
        The key ID (kid) of the newly generated key pair, which can be used to sign
        JWTs.
    """

    async with process_lock():
        # 1.
        kid = token_hex(8)  # 16-char random key ID

        # 2.
        private_key, public_key = await asyncio.to_thread(
            generate_rsa_keypair, key_size=key_size
        )

        # 3.
        await asyncio.to_thread(
            save_keypair,
            passphrase=passphrase,
            private_key=private_key,
            private_key_fp=_SECRETS_DIR / f"private_{kid}.pem",
            public_key=public_key,
            public_key_fp=_SECRETS_DIR / f"public_{kid}.pem",
        )

        # 4.
        jwks = await load_jwks(jwks_fn=jwks_fn)
        jwks["keys"].insert(0, jwk_from_public_key(kid=kid, public_key=public_key))

        # 5.
        while len(jwks["keys"]) > keep_last_n:
            # Remove **oldest** JWK.
            old = jwks["keys"].pop()
            old_kid = old["kid"]

            # Remove corresponding PEMs (ignore if already gone).
            await asyncio.to_thread(
                (_SECRETS_DIR / f"private_{old_kid}.pem").unlink, missing_ok=True
            )
            await asyncio.to_thread(
                (_SECRETS_DIR / f"public_{old_kid}.pem").unlink, missing_ok=True
            )

        # 6.
        await asyncio.to_thread(save_jwks, jwks=jwks, jwks_fn=jwks_fn)

        # 7.
        global _JWKS_CACHE, _JWKS_MTIME

        _JWKS_CACHE = jwks
        _JWKS_MTIME = (_SECRETS_DIR / jwks_fn).stat().st_mtime

        if kid in {key["kid"] for key in jwks["keys"][1:]}:
            logger.warning(f"Duplicate kid detected after rotation: {kid}")

        return kid


def save_jwks(*, jwks: dict[str, Any], jwks_fn: str = AUTH_JWKS_FN) -> None:
    """Save the JWKS (JSON Web Key Set) to a file.

    Parameters
    ----------
    jwks
        The JWKS to be saved, typically containing one or more JWKs (JSON Web Keys).
    jwks_fn
        The filename for the JWKS (JSON Web Key Set) file.
    """

    atomic_write(
        data=json.dumps(jwks, ensure_ascii=False, separators=(",", ":")).encode(),
        mode="wb",
        perm=0o640,
        target_fp=_SECRETS_DIR / jwks_fn,
    )


def save_keypair(
    *,
    passphrase: str,
    private_key: rsa.RSAPrivateKey,
    private_key_fp: Path,
    public_key: rsa.RSAPublicKey,
    public_key_fp: Path,
) -> tuple[Path, Path]:
    """Write PEM files to disk (PKCS-8 for the private key) under kid-specific
    filenames.

    Parameters
    ----------
    passphrase
        Passphrase to encrypt the private key.
    private_key
        The RSA private key to be saved.
    private_key_fp
        The file path where the private key PEM file will be saved.
    public_key
        The RSA public key to be saved.
    public_key_fp
        The file path where the public key PEM file will be saved.

    Returns
    -------
    tuple[Path, Path]
        A tuple containing the file paths of the saved private and public keys.
    """

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase.encode()),
        format=serialization.PrivateFormat.PKCS8,
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    atomic_write(data=private_pem, target_fp=private_key_fp, mode="wb", perm=0o600)
    atomic_write(data=public_pem, target_fp=public_key_fp, mode="wb", perm=0o640)

    return private_key_fp, public_key_fp


async def verify_user(*, form: OAuth2PasswordRequestForm = Depends()) -> dict[str, Any]:
    """Verify user credentials.

    TODO: Swap in a real DB lookup + salted hash check. At the moment, this only keeps
    track of a single user with a hardcoded username and passphrase from the .env file.

    Parameters
    ----------
    form
        The OAuth2 password request form containing the username, password, and scopes.

    Returns
    -------
    dict[str, Any]
        A dictionary containing the subject (username) and scopes of the authenticated
        user.

    Raises
    ------
    HTTPException
        If the username or password is incorrect, an HTTP 401 Unauthorized error is
        raised.
    """

    if not (
        form.username == Settings.AUTH_USER_NAME
        and form.password == Settings.AUTH_USER_PASSPHRASE.get_secret_value()
    ):
        raise HTTPException(
            detail="Bad credentials.",
            headers={"WWW-Authenticate": "Bearer"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    scopes = form.scopes or ["read"]  # Default for FastMCP

    return {"sub": form.username, "scopes": scopes}

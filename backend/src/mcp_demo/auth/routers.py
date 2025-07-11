"""This module contains FastAPI routers for authentication endpoints.

This setup is optimized for machine-to-machine (service-to-service) use cases,
providing a lightweight OAuth2 “password grant” token-issuing endpoint (bypassing
refresh/consent flows), plus key discovery via JWKS.
"""

# Third Party Library
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.auth.utils import (
    ClientCredentialsRequestForm,
    get_cached_jwks,
    get_jwt_token,
    sanitize_scopes,
)
from mcp_demo.clients.utils import verify_client
from mcp_demo.config import Settings
from mcp_demo.schemas import TokenResponse
from mcp_demo.users.utils import verify_user
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

    return JSONResponse(await get_cached_jwks())


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
            client_db = await verify_client(
                asession=asession,
                client_id=form.client_id,
                client_secret=form.client_secret,
            )
            if client_db is None:
                await record_failed_login(
                    ip=ip, redis_client=redis_client, user=form.client_id
                )
                raise HTTPException(
                    detail="Invalid client credentials",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )

            await reset_failed_login(
                ip=ip, redis_client=redis_client, user=form.client_id
            )

            token = await get_jwt_token(
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

            token = await get_jwt_token(
                passphrase=Settings.AUTH_RSA_PASSPHRASE.get_secret_value(),
                redis_client=redis_client,
                scopes=sanitize_scopes(
                    requested_scopes=form.scopes if user_db.is_admin else ["read"]
                ),
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

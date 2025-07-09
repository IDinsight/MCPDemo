"""This module contains FastAPI routers for authentication endpoints.

This module provides two key endpoints for issuing and distributing JWT bearer tokens:

1. **POST /auth/token** — Authenticates a user (via username/password), issues an
    RS256-signed JWT, and returns it in a Bearer format.
2. **GET  /auth/jwks.json** — Serves the JSON Web Key Set (JWKS) containing public keys
    used to verify the tokens. This can be used by servers or clients to validate the
    JWT signatures (e.g., the FastMCP `BearerAuthProvider`).

These endpoints allow any FastMCP server or client to:

1. Obtain a valid access token.
2. Discover and validate the token’s signature using JWKS.

This implements a lightweight OAuth2 “password grant” style contract, without full
flows like refresh or consent screens — ideal for service-to-service setups.
"""

# Third Party Library
from fastapi import APIRouter, Depends, status
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.auth.schemas import TokenResponse
from mcp_demo.auth.utils import get_jwt_token, load_jwks, sanitize_scopes, verify_user
from mcp_demo.config import Settings
from mcp_demo.utils.database import get_async_session

TAG_METADATA = {"description": "Handles authentication", "name": "Authentication"}
router = APIRouter(prefix="/auth", tags=[TAG_METADATA["name"]])


@router.get(
    "/jwks.json",
    description=(
        "Return the public JSON Web Key Set used by FastMCP servers to verify the "
        "signature of issued JWTs."
    ),
    include_in_schema=False,
    summary="JWKS Endpoint",
)
async def get_jwks() -> JSONResponse:
    """Publish the JSON-Web-Key-Set used by the token-issuer. FastMCP’s
    `BearerAuthProvider` will automatically download and cache this URI.

    The JWKS should be served over HTTPS in production. FastMCP clients fetch and
    cache this file at startup and refresh it based on `Cache-Control` headers from
    this endpoint.

    Returns
    -------
    JSONResponse
        The JWKS containing the public keys used to verify JWT tokens.
    """

    return JSONResponse(await load_jwks())


@router.post(
    "/token",
    description=(
        "Authenticate with username/password and receive an RS256 JWT.\n\n"
        "- **Request**: `application/x-www-form-urlencoded`\n"
        "- **Response**: JSON with `access_token`, `token_type`, `expires_in`\n"
        "- **Usage**: Header `Authorization: Bearer <token>` for subsequent requests"
    ),
    response_model=TokenResponse,
    summary="Issue Bearer Token",
)
async def issue_token(
    asession: AsyncSession = Depends(get_async_session),
    form: OAuth2PasswordRequestForm = Depends(),
) -> TokenResponse:
    """Issue a JWT token for the authenticated user.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    form
        The OAuth2 password request form.

    Returns
    -------
    TokenResponse
        The token response containing the access token, expiration time, and token type.

    Raises
    ------
    HTTPException
        If the user credentials are invalid or the user does not exist.
    """

    user_db = await verify_user(
        asession=asession, password=form.password, username=form.username
    )
    if user_db is None:
        raise HTTPException(
            detail="Bad credentials", status_code=status.HTTP_401_UNAUTHORIZED
        )

    token = await get_jwt_token(
        passphrase=Settings.AUTH_RSA_PASSPHRASE.get_secret_value(),
        scopes=sanitize_scopes(requested_scopes=form.scopes),
        sub=form.username,
    )

    return TokenResponse(
        access_token=token, expires_in=Settings.AUTH_TOKEN_TTL, token_type="bearer"
    )

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

# Standard Library
import os

from pathlib import Path
from typing import Any

# Third Party Library
from fastapi import APIRouter, Depends

# Package Library
from mcp_demo.auth.schemas import TokenResponse
from mcp_demo.auth.utils import get_jwt_token, load_jwks, verify_user
from mcp_demo.config import Settings

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
async def get_jwks() -> dict[str, Any]:
    """Publish the JSON-Web-Key-Set used by the token-issuer. FastMCP’s
    `BearerAuthProvider` will automatically download and cache this URI.

    The JWKS should be served over HTTPS in production. FastMCP clients fetch and
    cache this file at startup and refresh it based on `Cache-Control` headers from
    this endpoint.

    Returns
    -------
    \n\tdict[str, Any]
    \t\tThe JWKS containing the public keys used to verify JWT tokens.
    """

    return await load_jwks()


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
async def issue_token(user: dict[str, Any] = Depends(verify_user)) -> TokenResponse:
    """Issue a JWT token for the authenticated user.

    Parameters
    ----------
    \n\tuser
    \t\tThe authenticated user. This is obtained from the `verify_user` dependency,

    Returns
    -------
    \n\tTokenResponse
    \t\tThe token response containing the access token, expiration time, and token type.
    """

    token = await get_jwt_token(
        passphrase=Settings.AUTH_USER_PASSPHRASE.get_secret_value(),
        scopes=user["scopes"],
        sub=user["sub"],
    )

    # Write the token to disk for usage in other shells, applications, etc. DO NOT DO
    # THIS IN PRODUCTION! This is just for demonstration purposes.
    token_fp = Path("/tmp") / "mcp_demo_token.txt"
    with token_fp.open("w") as f:
        f.write(token)
    os.chmod(token_fp, 0o600)

    return TokenResponse(
        access_token=token, expires_in=Settings.AUTH_TOKEN_TTL, token_type="bearer"
    )

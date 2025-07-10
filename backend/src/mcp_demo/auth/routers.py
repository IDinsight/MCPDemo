"""This module contains FastAPI routers for authentication endpoints.

This module provides the following endpoints:

1. **GET  /auth/jwks.json** — Serves the JSON Web Key Set (JWKS) containing public keys
    used to verify the tokens. This can be used by servers or clients to validate the
    JWT signatures (e.g., the FastMCP `BearerAuthProvider`).

These endpoints allow any FastMCP server or client to:

1. Obtain a valid access token.
2. Discover and validate the token’s signature using JWKS.

This implements a lightweight OAuth2 “password grant” style contract, without full
flows like refresh or consent screens — ideal for service-to-service setups.
"""

# Third Party Library
from fastapi import APIRouter
from fastapi.responses import JSONResponse

# Package Library
from mcp_demo.auth.utils import get_cached_jwks

TAG_METADATA = {"description": "Handles user authentication", "name": "Authentication"}
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

    return JSONResponse(await get_cached_jwks())

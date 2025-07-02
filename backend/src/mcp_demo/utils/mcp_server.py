"""This module contains MCP server utilities."""

# Third Party Library
from fastmcp.server.auth import BearerAuthProvider

# Package Library
from mcp_demo.config import Settings

AUTH_AUDIENCE = Settings.AUTH_AUDIENCE
AUTH_JWKS_URI = Settings.AUTH_JWKS_URI
AUTH_TOKEN_ISSUER = Settings.AUTH_TOKEN_ISSUER


def get_bearer_auth_provider() -> BearerAuthProvider:
    """Get the BearerAuthProvider for the MCP server.

    Returns
    -------
    BearerAuthProvider
        The BearerAuthProvider instance.
    """

    auth = BearerAuthProvider(
        audience=AUTH_AUDIENCE,
        issuer=AUTH_TOKEN_ISSUER,
        jwks_uri=AUTH_JWKS_URI,
        required_scopes=["read"],
    )

    return auth

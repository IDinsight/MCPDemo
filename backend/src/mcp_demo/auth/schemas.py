"""This module contains Pydantic models for authentication."""

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field

# Package Library
from mcp_demo.config import Settings

AUTH_TOKEN_TTL = Settings.AUTH_TOKEN_TTL


class TokenResponse(BaseModel):
    """Pydantic model for token response."""

    access_token: str = Field(
        ...,
        description="RS256 JWT with `sub`, `iss`, `aud`, `exp`, and optionally `scope`",
    )
    expires_in: int = Field(
        AUTH_TOKEN_TTL, description="Lifetime of the token in seconds"
    )
    token_type: str = Field("bearer", description="Type of the token, always 'bearer'")

    model_config = ConfigDict(from_attributes=True)

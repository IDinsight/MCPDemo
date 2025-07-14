"""This module contains Pydantic models for auth."""

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field

# Package Library
from mcp_demo.config import Settings


class IntrospectionResponse(BaseModel):
    """Pydantic model for introspection response."""

    active: bool | None = None
    client_id: str | None = None  # `client_id` if client_credentials grant
    exp: int | None = None
    scope: str | None = None
    sub: int | None = None  # `user_id` if password grant
    token_type: str | None = None

    model_config = ConfigDict(from_attributes=True)


class RefreshTokenRequestForm(BaseModel):
    """Pydantic model for refresh token request."""

    refresh_token: str = Field(..., min_length=80)
    revoke_access: bool = False  # Set to True to revoke all old access tokens

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    """Pydantic model for token response."""

    access_token: str = Field(
        ...,
        description="RS256 JWT with `sub`, `iss`, `aud`, `exp`, and optionally `scope`",
    )
    expires_in: int = Field(
        Settings.AUTH_TOKEN_TTL, description="Lifetime of the token in seconds"
    )
    refresh_token: str
    refresh_token_expires_in: int
    token_type: str = Field("Bearer", description="Type of the token, always 'bearer'")

    model_config = ConfigDict(from_attributes=True)

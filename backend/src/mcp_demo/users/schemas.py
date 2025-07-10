"""This module contains Pydantic models for users."""

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field

# Package Library
from mcp_demo.config import Settings


# Token responses.
class TokenResponse(BaseModel):
    """Pydantic model for token response."""

    access_token: str = Field(
        ...,
        description="RS256 JWT with `sub`, `iss`, `aud`, `exp`, and optionally `scope`",
    )
    expires_in: int = Field(
        Settings.AUTH_TOKEN_TTL, description="Lifetime of the token in seconds"
    )
    token_type: str = Field("bearer", description="Type of the token, always 'bearer'")

    model_config = ConfigDict(from_attributes=True)


# Users.
class User(BaseModel):
    """Pydantic model for users."""

    username: str

    model_config = ConfigDict(from_attributes=True)


class UserCreate(User):
    """Pydantic model for user creation."""

    user_id: int


class UserCreateWithPassword(User):
    """Pydantic model for user creation with a password."""

    is_admin: bool = False
    password: str


class UserCreateWithRecoveryCodes(UserCreate):
    """Pydantic model for user creation with recovery codes for user account
    recovery.
    """

    recovery_codes: list[str]


class UserDeleteResponse(User):
    """Pydantic model for user deletion response."""

    user_id: int

    model_config = ConfigDict(from_attributes=True)

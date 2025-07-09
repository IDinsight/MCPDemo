"""This module contains Pydantic models for users."""

# Third Party Library
from pydantic import BaseModel, ConfigDict


# Users.
class User(BaseModel):
    """Pydantic model for users."""

    username: str

    model_config = ConfigDict(from_attributes=True)


class UserCreateWithRecoveryCodes(User):
    """Pydantic model for user creation with recovery codes for user account
    recovery.
    """

    recovery_codes: list[str]


class UserCreateWithPassword(User):
    """Pydantic model for user creation with a password."""

    password: str


class UserDeleteResponse(BaseModel):
    """Pydantic model for user deletion response."""

    user_id: int

    model_config = ConfigDict(from_attributes=True)

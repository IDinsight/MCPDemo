"""This module contains Pydantic models for users."""

# Standard Library
from datetime import datetime

# Third Party Library
from pydantic import BaseModel, ConfigDict


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

    password: str


class UserCreateWithRecoveryCodes(UserCreate):
    """Pydantic model for user creation with recovery codes for user account
    recovery.
    """

    created_by: int
    recovery_codes: list[str]
    scopes: list[str] = ["read"]


class UserDeleteResponse(User):
    """Pydantic model for user deletion response."""

    user_id: int

    model_config = ConfigDict(from_attributes=True)


class UserResetPassword(BaseModel):
    """Pydantic model for user password reset."""

    password: str
    recovery_code: str
    username: str

    model_config = ConfigDict(from_attributes=True)


class UserRetrieve(BaseModel):
    """Pydantic model for user retrieval."""

    created_datetime_utc: datetime
    is_active: bool
    scopes: list[str]
    updated_datetime_utc: datetime
    user_id: int
    username: str

    model_config = ConfigDict(from_attributes=True)

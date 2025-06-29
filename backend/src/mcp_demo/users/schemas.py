"""This module contains Pydantic models for users."""

# Third Party Library
from pydantic import BaseModel, ConfigDict


# Users.
class User(BaseModel):
    """Pydantic model for users."""

    user_id: str

    model_config = ConfigDict(from_attributes=True)


class UserDeleteResponse(BaseModel):
    """Pydantic model for user deletion response."""

    user_id: str

    model_config = ConfigDict(from_attributes=True)

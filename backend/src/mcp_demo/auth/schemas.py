"""This module contains Pydantic models for auth."""

# Third Party Library
from pydantic import BaseModel, ConfigDict


class IntrospectionResponse(BaseModel):
    """Pydantic model for introspection response."""

    active: bool | None = None
    client_id: str | None = None  # `client_id` if client_credentials grant
    exp: int | None = None
    scope: str | None = None
    sub: int | None = None  # `user_id` if password grant
    token_type: str | None = None

    model_config = ConfigDict(from_attributes=True)

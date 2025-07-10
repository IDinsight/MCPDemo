"""This module contains Pydantic models for clients."""

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field


# Clients.
class ServiceClientCreate(BaseModel):
    """Pydantic model for creating a service client."""

    client_id: str = Field(
        ..., max_length=64, min_length=2, description="Public client identifier"
    )
    is_active: bool = True
    scopes: list[str] = ["read"]
    secret: str = Field(
        ..., max_length=128, min_length=4, description="Plaintext secret"
    )

    model_config = ConfigDict(from_attributes=True)


class ServiceClientResponse(BaseModel):
    """Pydantic model for service client response."""

    client_id: str
    is_active: bool
    scopes: list[str]

    model_config = ConfigDict(from_attributes=True)

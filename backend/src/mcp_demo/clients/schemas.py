"""This module contains Pydantic models for clients."""

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field


# Clients.
class OAuth2Client(BaseModel):
    """Pydantic model for OAuth2 clients."""

    client_id: str = Field(
        ..., max_length=64, min_length=2, description="Public client identifier"
    )

    model_config = ConfigDict(from_attributes=True)


class OAuth2ClientCreate(OAuth2Client):
    """Pydantic model for creating an OAuth2 client."""

    is_active: bool = True
    scopes: list[str] = ["read"]
    secret: str = Field(
        ..., max_length=128, min_length=4, description="Plaintext secret"
    )

    model_config = ConfigDict(from_attributes=True)


class OAuth2ClientDeleteResponse(OAuth2Client):
    """Pydantic model for OAuth2 client deletion response."""


class OAuth2ClientResponse(BaseModel):
    """Pydantic model for OAuth2 client response."""

    client_id: str
    is_active: bool
    scopes: list[str]

    model_config = ConfigDict(from_attributes=True)

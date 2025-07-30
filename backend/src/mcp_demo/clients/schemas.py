"""This module contains Pydantic models for clients."""

# Standard Library
import os

from datetime import datetime

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

# Package Library
from mcp_demo.config import Settings

CADDY_DOMAIN_NAME = os.getenv("CADDY_DOMAIN_NAME", "localhost")
FASTAPI_PORT = Settings.FASTAPI_PORT


# Clients.
class OAuth2Client(BaseModel):
    """Pydantic model for OAuth2 clients."""

    client_id: str = Field(
        ...,
        description="Public identifier issued by the auth‑server",
        examples=["client1"],
        max_length=64,
        min_length=2,
    )

    model_config = ConfigDict(from_attributes=True)


class OAuth2ClientCreate(OAuth2Client):
    """Pydantic model for creating an OAuth2 client."""

    allowed_code_challenge_methods: list[str] = Field(
        default_factory=lambda: ["S256"],
        description="Accepted PKCE `code_challenge_method` values",
        examples=[["S256"]],
    )
    is_active: bool = Field(
        default=True,
        description="Whether the client is enabled immediately after creation",
    )
    pkce_enforced: bool = Field(
        default=True,
        description="Specifies whether PKCE is required on every authorization‑code request",
    )
    redirect_uris: list[HttpUrl] = Field(
        ...,
        description="Allowed redirect URIs for the authorization‑code flow",
        examples=[
            [
                f"http://{CADDY_DOMAIN_NAME}:{FASTAPI_PORT}/docs/oauth2-redirect",
                "https://api.example.com/docs/oauth2-redirect",
            ]
        ],
        min_length=1,
    )
    scopes: list[str] = Field(
        default_factory=lambda: ["read"],
        description="Default scopes implicitly granted to the client",
    )
    secret: str = Field(
        ..., description="Plaintext secret", max_length=128, min_length=4
    )


class OAuth2ClientDeleteResponse(OAuth2Client):
    """Pydantic model for OAuth2 client deletion response."""

    deleted_by: str


class OAuth2ClientListResponse(OAuth2Client):
    """Pydantic model for OAuth2 client list response."""

    is_active: bool
    scopes: list[str]


class OAuth2ClientResetSecret(OAuth2Client):
    """Pydantic model for client secret reset."""

    client_secret: str = Field(
        ...,
        description="New plain‑text secret",
        max_length=128,
        min_length=4,
    )


class OAuth2ClientResponse(OAuth2Client):
    """Pydantic model for OAuth2 client response."""

    allowed_code_challenge_methods: list[str]
    created_by: str | None = Field(
        default=None,
        description="Administrator who registered the client (nullable)",
    )
    created_datetime_utc: datetime
    is_active: bool
    pkce_enforced: bool
    redirect_uris: list[HttpUrl | str]
    scopes: list[str]
    updated_datetime_utc: datetime

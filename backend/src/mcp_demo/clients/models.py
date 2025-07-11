"""This module contains the ORM for managing OAuth2 clients."""

# Future Library
from __future__ import annotations

# Standard Library
from datetime import datetime, timezone

# Third Party Library
from sqlalchemy import ARRAY, Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

# Package Library
from mcp_demo.utils.database import Base


class Oauth2ClientDB(Base):
    """ORM for managing OAuth2 clients.

    Attributes
    ----------
    client_id
        Unique identifier for the client (primary key).
    is_active
        Whether the client is currently active and allowed to authenticate.
    scopes
        List of permitted OAuth2 scopes for this client.
    secret_hash
        Secure hash of the client's secret, stored for authentication.
    """

    __tablename__ = "client"

    client_id: Mapped[str] = mapped_column(
        String(64), doc="The client’s unique identifier", primary_key=True
    )
    created_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, doc="Whether this client is enabled"
    )
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=[], doc="OAuth2 scopes granted to this client"
    )
    secret_hash: Mapped[str] = mapped_column(
        String(256), doc="Hashed client secret for authentication", nullable=False
    )
    updated_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        onupdate=func.now(),  # On every UPDATE  # pylint: disable=E1102
        server_default=func.now(),  # First INSERT  # pylint: disable=E1102
    )

    def __repr__(self) -> str:
        """Return an unambiguous string representation of the `Oauth2ClientDB`
        instance, useful for logging and debugging.

        Returns
        -------
        str
            A string representation of the `Oauth2ClientDB` class.
        """

        return (
            f"<Client '{self.client_id}' active={self.is_active} scopes={self.scopes}>"
        )

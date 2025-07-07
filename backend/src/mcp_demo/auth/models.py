"""This module contains the ORM for managing authentication."""

# Third Party Library
from authlib.integrations.sqla_oauth2 import (
    OAuth2AuthorizationCodeMixin,
    OAuth2ClientMixin,
    OAuth2TokenMixin,
)
from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Package Library
from mcp_demo.users.models import UserDB
from mcp_demo.utils.database import Base


class OAuth2AuthorizationCode(OAuth2AuthorizationCodeMixin, Base):
    """ORM for managing OAuth2 authorization codes."""

    __tablename__ = "oauth2_code"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user = relationship("UserDB")
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.user_id"))


class OAuth2Client(OAuth2ClientMixin, Base):
    """ORM for managing OAuth2 clients."""

    __tablename__ = "oauth2_client"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user = relationship("UserDB")
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.user_id"))


class OAuth2Token(OAuth2TokenMixin, Base):
    """ORM for managing OAuth2 tokens."""

    __tablename__ = "oauth2_token"

    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("oauth2_client.id"))
    client = relationship("OAuth2Client")
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user = relationship("UserDB")
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.user_id"))

    def get_client(self) -> OAuth2Client:
        """Return the client that this token was issued to.

        Returns
        -------
        OAuth2Client
            The client associated with this token.
        """

        return self.client

    def get_user(self) -> UserDB:
        """Return the resource-owner associated with this token.

        Returns
        -------
        UserDB
            The user associated with this token.
        """

        return self.user

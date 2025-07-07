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
from mcp_demo.utils.database import Base


class OAuth2AuthorizationCode(OAuth2AuthorizationCodeMixin, Base):
    """ORM for managing OAuth2 authorization codes."""

    __tablename__ = "oauth2_code"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.user_id"))
    user = relationship("UserDB")


class OAuth2Client(OAuth2ClientMixin, Base):
    """ORM for managing OAuth2 clients."""

    __tablename__ = "oauth2_client"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.user_id"))
    user = relationship("UserDB")


class OAuth2Token(OAuth2TokenMixin, Base):
    """ORM for managing OAuth2 tokens."""

    __tablename__ = "oauth2_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.user_id"))
    user = relationship("UserDB")

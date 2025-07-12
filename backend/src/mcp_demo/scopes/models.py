"""This module contains the ORM for managing scopes."""

# Future Library
from __future__ import annotations

# Standard Library
from typing import TYPE_CHECKING

# Third Party Library
from sqlalchemy import Column, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Package Library
from mcp_demo.utils.database import Base

if TYPE_CHECKING:
    # Package Library
    from mcp_demo.users.models import UserDB

user_scope_table = Table(
    "user_scope",
    Base.metadata,
    Column("scope_name", ForeignKey("scope.name"), primary_key=True),
    Column("user_id", ForeignKey("user.user_id"), primary_key=True),
)


class ScopeDB(Base):
    """ORM for managing scopes.

    Attributes
    ----------
    name
        Unique scope name, used to control access to resources.
    """

    __tablename__ = "scope"

    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    users: Mapped[list[UserDB]] = relationship(
        "UserDB", back_populates="scopes", secondary=user_scope_table
    )

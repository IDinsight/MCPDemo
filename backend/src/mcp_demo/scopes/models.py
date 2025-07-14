"""This module contains the ORM for managing scopes."""

# Future Library
from __future__ import annotations

# Standard Library
from datetime import datetime, timezone
from typing import TYPE_CHECKING

# Third Party Library
from sqlalchemy import Column, DateTime, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

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

    created_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False
    )
    name: Mapped[str] = mapped_column(String(50), primary_key=True)
    updated_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        onupdate=func.now(),  # On every UPDATE  # pylint: disable=E1102
        server_default=func.now(),  # First INSERT  # pylint: disable=E1102
    )
    users: Mapped[list[UserDB]] = relationship(
        "UserDB", back_populates="scopes", secondary=user_scope_table
    )

    def __repr__(self) -> str:
        """Return an unambiguous string representation of the `ScopeDB` instance,
        useful for logging and debugging.

        Returns
        -------
        str
            A string representation of the `ScopeDB` class.
        """

        return f"<Scope: {self.name!r}"

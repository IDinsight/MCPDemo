"""This module contains the ORM for managing users."""

# pylint: disable=E1136
# Standard Library
from datetime import datetime, timezone

# Third Party Library
from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, Integer, String
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

# Package Library
from mcp_demo.scopes.models import ScopeDB, user_scope_table
from mcp_demo.utils.database import Base


class UserDB(Base):
    """ORM for managing users.

    Attributes
    ----------
    created_datetime_utc
        Row creation timestamp (server default: ``NOW() AT TIME ZONE 'UTC'``).
    is_active
        Soft-delete flag; inactive users cannot log in.
    password_hash
        *Argon2id* hash of the user’s password.
    recovery_codes_hash
        One-time recovery codes, individually hashed. ``None`` until 2-factor auth is
        enabled.
    scopes
        Scopes associated with the user, allowing access to specific resources.
    updated_datetime_utc
        Automatically updated on each ``UPDATE`` via `func.now()`.
    user_id
        Surrogate primary key.
    username
        Unique login name; indexed and case-sensitive.
    """

    # Reject any password hash that is too short
    __table_args__ = (
        CheckConstraint(
            "length(password_hash) > 50", name="chk_user_password_hash_len"
        ),
    )
    __tablename__ = "user"

    created_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    password_hash: Mapped[str] = mapped_column(String(), nullable=False)
    recovery_codes_hash: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), default=list, nullable=False
    )
    scopes: Mapped[list[ScopeDB]] = relationship(
        back_populates="users", cascade="all,delete", secondary=user_scope_table
    )
    updated_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        onupdate=func.now(),  # On every UPDATE  # pylint: disable=E1102
        server_default=func.now(),  # First INSERT  # pylint: disable=E1102
    )
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    def __repr__(self) -> str:
        """Define the string representation for the `UserDB` class.

        Returns
        -------
        str
            A string representation of the `UserDB` class.
        """

        return f"<Username '{self.username}' mapped to user ID {self.user_id}>"

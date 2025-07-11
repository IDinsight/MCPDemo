"""This module contains the ORM for managing users."""

# Future Library
from __future__ import annotations

# Standard Library
from datetime import datetime, timezone

# Third Party Library
from sqlalchemy import ARRAY, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

# Package Library
from mcp_demo.utils.database import Base


class UserDB(Base):
    """ORM for managing users.

    Notes
    -----
    1. `scopes` is runtime-only: populated from the access-token payload and never
        persisted to the database.

    Attributes
    ----------
    created_datetime_utc
        Row creation timestamp (server default: ``NOW() AT TIME ZONE 'UTC'``).
    is_active
        Soft-delete flag; inactive users cannot log in.
    is_admin
        Grants elevated privileges enforced by the router guards.
    password_hash
        *Argon2id* hash of the user’s password.
    recovery_codes_hash
        One-time recovery codes, individually hashed. ``None`` until 2-factor auth is
        enabled.
    scopes
        Injected at request time; not stored in the database.
    updated_datetime_utc
        Automatically updated on each ``UPDATE`` via `func.now()`.
    user_id
        Surrogate primary key.
    username
        Unique login name; indexed and case-sensitive.
    """

    __allow_unmapped__ = True
    __tablename__ = "user"

    created_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    password_hash: Mapped[str] = mapped_column(String(), nullable=False)
    recovery_codes_hash: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=True)
    scopes: set[str] | None = None  # Runtime-only, not persisted
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

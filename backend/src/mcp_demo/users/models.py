"""This module contains the ORM for managing users."""

# Future Library
from __future__ import annotations

# Standard Library
from datetime import datetime, timezone

# Third Party Library
from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

# Package Library
from mcp_demo.utils.database import Base


class UserDB(Base):
    """ORM for managing users."""

    __tablename__ = "user"

    created_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    password_hash: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    updated_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
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

        return f"User ID: {self.user_id}"

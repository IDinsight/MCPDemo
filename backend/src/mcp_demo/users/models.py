"""This module contains the ORM for managing users."""

# Future Library
from __future__ import annotations

# Standard Library
from datetime import datetime, timezone

# Third Party Library
from passlib.hash import bcrypt
from sqlalchemy import Boolean, DateTime, Integer, String
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
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
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

    @classmethod
    def create(cls, *, username: str, password: str) -> UserDB:
        """Create a new user with the given username and password.

        Parameters
        ----------
        username
            The username for the new user.
        password
            The password for the new user.

        Returns
        -------
        UserDB
            An instance of the `UserDB` class with the provided username and a hashed
            password.
        """

        return cls(username=username, password_hash=bcrypt.hash(password))

    def verify_password(self, *, password: str) -> bool:
        """Verify the password against the stored password hash.

        Parameters
        ----------
        password
            The password to verify.

        Returns
        -------
        bool
            True if the password matches the stored hash, False otherwise.
        """

        return bcrypt.verify(password, self.password_hash)

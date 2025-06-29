"""This module contains the ORM for managing users."""

# Standard Library
from datetime import datetime

# Third Party Library
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

# Package Library
from mcp_demo.utils.database import Base


class UserDB(Base):
    """ORM for managing users."""

    __tablename__ = "user"

    created_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String, primary_key=True, nullable=False)

    def __repr__(self) -> str:
        """Define the string representation for the `UserDB` class.

        Returns
        -------
        str
            A string representation of the `UserDB` class.
        """

        return f"User ID: {self.user_id}"

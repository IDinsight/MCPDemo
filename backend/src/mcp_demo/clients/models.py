"""This module contains the ORM for managing clients."""

# Future Library
from __future__ import annotations

# Third Party Library
from sqlalchemy import ARRAY, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

# Package Library
from mcp_demo.utils.database import Base


class ServiceClientDB(Base):
    """ORM for managing service clients."""

    __tablename__ = "service_client"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), default=[])
    secret_hash: Mapped[str] = mapped_column(String(256), nullable=False)

    def __repr__(self) -> str:
        """Define the string representation for the `ServiceClientDB` class.

        Returns
        -------
        str
            A string representation of the `ServiceClientDB` class.
        """

        return f"<Client '{self.client_id}' status {self.is_active} with scopes {self.scopes}>"

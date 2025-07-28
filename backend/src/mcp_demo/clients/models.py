"""This module contains the ORM for managing OAuth2 clients."""

# Future Library
from __future__ import annotations

# Standard Library
from datetime import datetime, timezone

# Third Party Library
from sqlalchemy import ARRAY, Boolean, CheckConstraint, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

# Package Library
from mcp_demo.utils.database import Base


class Oauth2ClientDB(Base):
    """ORM for managing OAuth2 clients.

    This model also supports OAuth 2.1/PKCE by tracking:
        - `redirect_uris`: one or more absolute URIs that the client is allowed to use
            in the *authorization code* flow
        - `pkce_enforced`: specifies whether Proof‑Key‑for‑Code‑Exchange is mandatory
            for this client (best‑practice, should be always True).
        - `allowed_code_challenge_methods`: list of PKCE transformation methods
            permitted for this client (OAuth 2.1 restricts to a single value `S256`).

    A single record models the configuration and credentials of an external application
    allowed to request tokens from this authorization server.

    Attributes
    ----------
    allowed_code_challenge_methods
        List of accepted PKCE transformation methods (e.g. `["S256"]`).
    client_id
        Public identifier issued during client registration (primary‑key).
    created_datetime_utc
        Timestamp of when the client was registered, in UTC.
    is_active
        If False the client is suspended and any token request must be denied.
    pkce_enforced
        Specifies whether to require PKCE for **all** authorization‑code requests from
        this client.
    redirect_uris
        Collection of pre‑registered redirect URIs (MUST NOT be empty).
    scopes
        List of permitted OAuth2 scopes for this client.
    secret_hash
        Secure hash of the client's secret, stored for authentication.
    updated_datetime_utc
        Timestamp of the last update to the client record, in UTC.
    """

    __table_args__ = (
        CheckConstraint(
            "array_length(redirect_uris, 1) >= 1", name="chk_client_redirect_not_empty"
        ),
        CheckConstraint("length(secret_hash) > 50", name="chk_client_secret_hash_len"),
    )
    __tablename__ = "client"

    allowed_code_challenge_methods: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        default=lambda: ["S256"],
        doc="Accepted PKCE code challenge methods",
        nullable=False,
        server_default="{S256}",
    )
    client_id: Mapped[str] = mapped_column(
        String(64),
        doc="Public client identifier issued by the auth‑server",
        primary_key=True,
    )
    created_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.now(timezone.utc), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, doc="Whether the client may currently authenticate"
    )
    pkce_enforced: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        doc="If True the client MUST send PKCE parameters with every code request",
        nullable=False,
        server_default=text("true"),
    )
    redirect_uris: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        doc="Whitelisted redirect URIs for the authorization code flow",
        nullable=False,
    )
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=[], doc="OAuth2 scopes granted to this client"
    )
    secret_hash: Mapped[str] = mapped_column(
        String(256), doc="Hashed client secret for authentication", nullable=False
    )
    updated_datetime_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        onupdate=func.now(),  # On every UPDATE  # pylint: disable=E1102
        server_default=func.now(),  # First INSERT  # pylint: disable=E1102
    )

    def __repr__(self) -> str:
        """Return an unambiguous string representation of the `Oauth2ClientDB`
        instance, useful for logging and debugging.

        Returns
        -------
        str
            A string representation of the `Oauth2ClientDB` class.
        """

        return (
            f"<Oauth2ClientDB id={self.client_id!r} active={self.is_active} "
            f"pkce={self.pkce_enforced} scopes={self.scopes}>"
        )

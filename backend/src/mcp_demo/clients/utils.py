"""This module contains utilities for clients."""

# Third Party Library
from fastapi import Form, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.clients.models import ServiceClientDB
from mcp_demo.clients.schemas import ServiceClientCreate
from mcp_demo.config import Settings
from mcp_demo.utils.general import generate_hash, verify_hash


class ClientAlreadyExistsError(Exception):
    """Custom exception raised when a client already exists in the database."""

    def __init__(self, *, error_msg: str) -> None:
        """

        Parameters
        ----------
        error_msg
            The error message.
        """

        super().__init__(f"Client already exists: {error_msg}")

        self.error_msg = error_msg


class OAuth2ClientCredentialsRequestForm:
    """Form parameters for the OAuth2 ‘client_credentials’ grant.

    NB: FastAPI does not provide this out-of-the-box.
    """

    def __init__(
        self,
        grant_type: str = Form(
            default="client_credentials", regex="client_credentials"
        ),
        scope: str = Form(default=""),
        client_id: str = Form(..., min_length=1),
        client_secret: str = Form(..., min_length=1),
    ) -> None:
        """

        Parameters
        ----------
        grant_type
            The OAuth2 grant type, must be 'client_credentials'.
        scope
            Space-separated list of scopes requested by the client.
        client_id
            The client identifier issued to the client during registration.
        client_secret
            The client secret issued to the client during registration. This is used to
            authenticate the client and should be kept confidential.
        """

        self.grant_type = grant_type
        self.scopes: list[str] = scope.split()
        self.client_id = client_id
        self.client_secret = client_secret


async def check_if_client_exists(
    *, asession: AsyncSession, client: ServiceClientCreate
) -> ServiceClientDB | None:
    """Check if a client exists in the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    client
        The service client object to check, containing `client_id`.

    Returns
    -------
    ServiceClientDB | None
        The ServiceClientDB instance if the client exists, otherwise `None`.
    """

    stmt = select(ServiceClientDB).where(ServiceClientDB.client_id == client.client_id)
    result = await asession.execute(stmt)
    client_db = result.scalar_one_or_none()
    return client_db


def get_service_scopes(*, client_db: ServiceClientDB) -> list[str]:
    """Restrict scopes to what the service is allowed — no self-escalation.

    Parameters
    ----------
    client_db
        The ServiceClientDB instance representing the client for which scopes are
        being retrieved.

    Returns
    -------
    list[str]
        A list of scopes that the client is allowed to use, filtered against the
        allowed scopes defined in the settings.
    """

    return list(set(client_db.scopes) & Settings.AUTH_ALLOWED_SCOPES)


async def save_client_to_db(
    *, asession: AsyncSession, client: ServiceClientCreate
) -> ServiceClientDB:
    """Save a client in the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    client
        The service client object to save in the database.

    Returns
    -------
    ServiceClientDB
        The client object saved in the database.

    Raises
    ------
    ClientAlreadyExistsError
        If a client with the same client ID already exists in the database.
    """

    existing_client = await check_if_client_exists(asession=asession, client=client)

    if existing_client is not None:
        raise ClientAlreadyExistsError(
            error_msg=f"Client ID already exists: {existing_client.client_id}"
        )

    client_db = ServiceClientDB(
        client_id=client.client_id,
        is_active=client.is_active,
        scopes=list(set(client.scopes)),  # Dedup
        secret_hash=generate_hash(text=client.secret),
    )

    asession.add(client_db)
    await asession.commit()
    await asession.refresh(client_db)

    return client_db


async def verify_client_secret(
    *, asession: AsyncSession, client_id: str, client_secret: str
) -> ServiceClientDB:
    """Return the DB row IFF id exists, active, and secret matches.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    client_id
        The client ID to verify against the database.
    client_secret
        The client secret to verify against the stored hash in the database.

    Returns
    -------
    ServiceClientDB
        The ServiceClientDB instance if the client ID and secret are valid.

    Raises
    ------
    HTTPException
        If the client ID does not exist, is inactive, or the client secret does not
        match the stored hash.
    """

    stmt = select(ServiceClientDB).where(
        ServiceClientDB.client_id == client_id, ServiceClientDB.is_active.is_(True)
    )
    result = await asession.execute(stmt)
    row: ServiceClientDB | None = result.scalar_one_or_none()

    if not row or not verify_hash(text=client_secret, hashed=str(row.secret_hash))[0]:
        raise HTTPException(
            detail="Invalid client credentials",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return row

"""This module contains utilities for managaing OAuth2 clients and credentials."""

# Standard Library
from typing import Any

# Third Party Library
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import SecurityScopes
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.auth.utils import _verify_caller, oauth_2_multi_scheme
from mcp_demo.clients.models import Oauth2ClientDB
from mcp_demo.clients.schemas import OAuth2ClientCreate, OAuth2ClientResetSecret
from mcp_demo.config import Settings
from mcp_demo.utils.database import get_async_session
from mcp_demo.utils.general import generate_hash, verify_hash

AUTH_ALLOWED_SCOPES = Settings.AUTH_ALLOWED_SCOPES


class Oauth2ClientDBClientAlreadyExistsError(Exception):
    """Custom exception raised when an OAuth2 client already exists in the database."""

    def __init__(self, *, error_msg: str) -> None:
        """

        Parameters
        ----------
        error_msg
            A human-readable description of the error condition.
        """

        super().__init__(f"OAuth2 client already exists: {error_msg}")

        self.error_msg = error_msg


class Oauth2ClientDBInvalidScopesError(Exception):
    """Custom exception raised when the provided scopes are not allowed."""

    def __init__(self, *, error_msg: str) -> None:
        """

        Parameters
        ----------
        error_msg
            A human-readable description of the error condition.
        """

        super().__init__(f"Invalid scopes: {error_msg}")

        self.error_msg = error_msg


class Oauth2ClientNotFoundError(Exception):
    """Custom exception raised when a client is not found in the database."""

    def __init__(self, *, error_msg: str) -> None:
        """

        Parameters
        ----------
        error_msg
            The error message.
        """

        super().__init__(f"Client not found: {error_msg}")

        self.error_msg = error_msg


async def check_if_client_exists(
    *, asession: AsyncSession, client: OAuth2ClientCreate | OAuth2ClientResetSecret
) -> Oauth2ClientDB | None:
    """Check if a client exists in the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    client
        The service client object to check, containing `client_id`.

    Returns
    -------
    Oauth2ClientDB | None
        The `Oauth2ClientDB` instance if the client exists, otherwise `None`.
    """

    stmt = select(Oauth2ClientDB).where(Oauth2ClientDB.client_id == client.client_id)
    result = await asession.execute(stmt)
    client_db = result.scalar_one_or_none()
    return client_db


async def check_if_clients_exist(*, asession: AsyncSession) -> bool:
    """Check if clients exist in the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    bool
        Specifies whether clients exists in the database.
    """

    stmt = select(Oauth2ClientDB.client_id).limit(1)
    result = await asession.scalars(stmt)
    return result.first() is not None


async def delete_client_from_db(*, asession: AsyncSession, client_id: str) -> None:
    """Delete a client from the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    client_id
        The unique identifier of the client to delete.
    """

    stmt = select(Oauth2ClientDB).where(Oauth2ClientDB.client_id == client_id)
    result = await asession.execute(stmt)
    client_db = result.scalar_one_or_none()

    if client_db is None:
        logger.warning(f"Client ID does not exist: {client_id}")
        return

    await asession.delete(client_db)
    await asession.commit()


async def get_client_by_id(*, asession: AsyncSession, client_id: str) -> Oauth2ClientDB:
    """Retrieve a client by client ID.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    client_id
        The unique identifier of the client to retrieve.

    Returns
    -------
    Oauth2ClientDB
        The client object retrieved from the database.

    Raises
    ------
    Oauth2ClientNotFoundError
        If the client with the specified client ID does not exist.
    """

    stmt = select(Oauth2ClientDB).where(Oauth2ClientDB.client_id == client_id)
    result = await asession.execute(stmt)

    try:
        client = result.scalar_one()
        return client
    except NoResultFound as err:
        raise Oauth2ClientNotFoundError(
            error_msg=f"Client ID does not exist: {client_id} "
        ) from err


async def get_current_client(
    request: Request,
    asession: AsyncSession = Depends(get_async_session),
    security_scopes: SecurityScopes = SecurityScopes(),
    token: str = Depends(oauth_2_multi_scheme),
) -> Oauth2ClientDB:
    """Authenticate a JWT issued via client credentials and return the matching
    `Oauth2ClientDB`.

    The token’s `sub` claim must equal the `client_id`. A revoked or replayed JTI is
    rejected via Redis lookup.

    Parameters
    ----------
    request
        The FastAPI request object, used to access the Redis client.
    asession
        The SQLAlchemy async session to use for all database connections.
    security_scopes
        The security scopes required for the operation, used to check if the token
        has the necessary permissions.
    token
        The JWT token to decode and verify.

    Returns
    -------
    Oauth2ClientDB
        The client database object representing the authenticated client.

    Raises
    ------
    HTTPException
        If the client is not found or is inactive.
    """

    payload = await _verify_caller(
        redis_client=request.app.state.redis,
        required_scopes=set(security_scopes.scopes),
        token=token,
    )

    try:
        client_db = await get_client_by_id(asession=asession, client_id=payload["sub"])
    except Oauth2ClientNotFoundError as exc:
        raise HTTPException(
            detail="Could not validate credentials",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc

    if not client_db.is_active:
        raise HTTPException(
            detail="Could not validate credentials",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return client_db


async def reset_client_secret(
    *,
    asession: AsyncSession,
    client: OAuth2ClientResetSecret,
    client_db: Oauth2ClientDB,
) -> Oauth2ClientDB:
    """Hash the new secret and persist the changes.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    client
        The client object containing the new secret to reset.
    client_db
        The existing client object from the database to update.

    Returns
    -------
    Oauth2ClientDB
        The updated client object saved in the database.
    """

    client_db.secret_hash = generate_hash(text=client.client_secret)

    await asession.commit()
    await asession.refresh(client_db)

    return client_db


async def save_client_to_db(
    *, asession: AsyncSession, client: OAuth2ClientCreate
) -> Oauth2ClientDB:
    """Saves a new service client in the database and ensures that `client_id` is
    unique and hashes the `client_secret`.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    client
        The service client object to save in the database.

    Returns
    -------
    Oauth2ClientDB
        The client object saved in the database.

    Raises
    ------
    Oauth2ClientDBClientAlreadyExistsError
        If a client with the same client ID already exists in the database.
    Oauth2ClientDBInvalidScopesError
        If the provided scopes are not allowed.
    """

    existing_client = await check_if_client_exists(asession=asession, client=client)

    if existing_client is not None:
        raise Oauth2ClientDBClientAlreadyExistsError(
            error_msg=f"OAuth2 client ID already exists: {existing_client.client_id}"
        )

    if not all(s in AUTH_ALLOWED_SCOPES for s in client.scopes):
        raise Oauth2ClientDBInvalidScopesError(
            error_msg=f"Invalid scopes: {client.scopes}."
        )

    client_db = Oauth2ClientDB(
        client_id=client.client_id,
        is_active=client.is_active,
        scopes=list(set(client.scopes)),  # Dedup
        secret_hash=generate_hash(text=client.secret),
    )

    asession.add(client_db)
    await asession.commit()
    await asession.refresh(client_db)

    return client_db


async def update_client_in_db(
    *,
    asession: AsyncSession,
    client: Oauth2ClientDB,
    **kwargs: Any,
) -> Oauth2ClientDB:
    """Update a client in the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    client
        The client object to update in the database.
    kwargs
        Additional keyword arguments to update the client object in the database.

    Returns
    -------
    Oauth2ClientDB
        The client object saved in the database after update.
    """

    client_db = Oauth2ClientDB(
        client_id=client.client_id,
        is_active=client.is_active,
        scopes=client.scopes,
        **kwargs,
    )
    client_db = await asession.merge(client_db)

    await asession.commit()
    await asession.refresh(client_db)

    return client_db


async def verify_client(
    *, asession: AsyncSession, client_id: str, client_secret: str
) -> Oauth2ClientDB | None:
    """Verify client credentials.

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
    Oauth2ClientDB | None
        The ClientDB instance if the client ID and secret are valid; otherwise, `None`.
    """

    try:
        client_db = await get_client_by_id(asession=asession, client_id=client_id)
    except Oauth2ClientNotFoundError:
        return None

    if not client_db.is_active:
        return None

    verified, hashed_secret = verify_hash(
        text=client_secret, hashed=client_db.secret_hash
    )

    if not verified:
        return None

    if hashed_secret == client_db.secret_hash:
        return client_db

    # Update hashed secret.
    client_db = await update_client_in_db(
        asession=asession,
        client=Oauth2ClientDB(
            client_id=client_db.client_id,
            is_active=client_db.is_active,
            scopes=client_db.scopes,
        ),
        secret_hash=hashed_secret,
    )

    return client_db

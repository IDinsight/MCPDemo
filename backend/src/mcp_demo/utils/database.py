"""This module contains utilities for databases."""

# pylint: disable=global-statement
# Standard Library
import contextlib

from collections.abc import AsyncGenerator
from typing import AsyncIterator, ContextManager, Generator

# Third Party Library
from sqlalchemy import URL, Engine, MetaData, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

# Package Library
from mcp_demo.config import Settings

POSTGRES_ASYNC_API = Settings.POSTGRES_ASYNC_API
POSTGRES_DB = Settings.POSTGRES_DB
POSTGRES_DB_POOL_SIZE = Settings.POSTGRES_DB_POOL_SIZE
POSTGRES_HOST = Settings.POSTGRES_HOST
POSTGRES_PASSWORD = Settings.POSTGRES_PASSWORD
POSTGRES_PORT = Settings.POSTGRES_PORT
POSTGRES_SYNC_API = Settings.POSTGRES_SYNC_API
POSTGRES_USER = Settings.POSTGRES_USER

# Global so we don't create more than one engine per process. Outside of being best
# practice, this is needed so we can properly pool connections and not create a new
# pool on every request.
_ASYNC_ENGINE: AsyncEngine | None = None
_SYNC_ENGINE: Engine | None = None


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def get_async_engine() -> AsyncEngine:
    """Return a SQLAlchemy async engine generator.

    Returns
    -------
    AsyncEngine
        A SQLAlchemy async engine.
    """

    global _ASYNC_ENGINE
    if _ASYNC_ENGINE is None:
        connection_string = get_connection_url()
        _ASYNC_ENGINE = create_async_engine(
            connection_string, pool_size=POSTGRES_DB_POOL_SIZE
        )
    return _ASYNC_ENGINE


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a SQLAlchemy async session.

    Yields
    ------
    AsyncGenerator[AsyncSession, None]
        An async session generator.
    """

    async with AsyncSession(
        get_async_engine(), expire_on_commit=False
    ) as async_session:
        yield async_session


@contextlib.asynccontextmanager
async def get_async_session_managed() -> AsyncIterator[AsyncSession]:
    """Yield a managed SQLAlchemy async session generator.

    Yields
    ------
    AsyncIterator[AsyncSession]
        A managed async session generator.
    """

    async with AsyncSession(
        get_async_engine(), expire_on_commit=False
    ) as async_session:
        yield async_session


def get_connection_url(
    *,
    db: str = POSTGRES_DB,
    db_api: str = POSTGRES_ASYNC_API,
    host: str = POSTGRES_HOST,
    password: str = POSTGRES_PASSWORD,
    port: int | str = POSTGRES_PORT,
    user: str = POSTGRES_USER,
) -> URL:
    """Return a connection string for the given database.

    Parameters
    ----------
    db
        The database name.
    db_api
        The database API.
    host
        The database host.
    password
        The database password.
    port
        The database port.
    user
        The database user.

    Returns
    -------
    URL
        A connection string for the given database.
    """

    return URL.create(
        database=db,
        drivername="postgresql+" + db_api,
        host=host,
        password=password,
        port=int(port),
        username=user,
    )


def get_engine() -> Engine:
    """Return a SQLAlchemy engine.

    Returns
    -------
    Engine
        A SQLAlchemy engine.
    """

    global _SYNC_ENGINE
    if _SYNC_ENGINE is None:
        connection_string = get_connection_url(db_api=POSTGRES_SYNC_API)
        _SYNC_ENGINE = create_engine(connection_string)
    return _SYNC_ENGINE


def get_session() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session generator.

    Yields
    ------
    Generator[Session, None, None]
        A SQLAlchemy session generator.
    """

    with Session(get_engine(), expire_on_commit=False) as session:
        yield session


def get_session_context_manager() -> ContextManager[Session]:
    """Return a SQLAlchemy session context manager.

    Returns
    -------
    ContextManager[Session]
        A SQLAlchemy session context manager.
    """

    return contextlib.contextmanager(get_session)()

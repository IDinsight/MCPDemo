"""This module contains fixtures for backend tests."""

# Standard Library
import os

from typing import AsyncGenerator, Generator

# Third Party Library
import pytest

from fastapi.testclient import TestClient
from pytest_alembic.config import Config
from redis import asyncio as aioredis
from sqlalchemy.engine import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import Session

# Package Library
from mcp_demo.config import Settings
from mcp_demo.utils.database import get_connection_url, get_session_managed
from mcp_demo.utils.fastapi_ import create_fastapi_app

POSTGRES_SYNC_API = Settings.POSTGRES_SYNC_API

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6381")


# Fixtures.
@pytest.fixture(scope="session")
def alembic_config() -> Config:
    """`alembic_config` is the primary point of entry for configurable options for the
    alembic runner for `pytest-alembic`.

    Returns
    -------
    Config
        A configuration object used by `pytest-alembic`.
    """

    return Config({"file": "alembic.ini"})


@pytest.fixture(scope="function")
def alembic_engine() -> Engine:
    """`alembic_engine` is where you specify the engine with which the alembic_runner
    should execute your tests.

    NB: The engine should point to a database that must be empty. It is out of scope
    for `pytest-alembic` to manage the database state.

    Returns
    -------
    Engine
        A SQLAlchemy engine object.
    """

    return create_engine(get_connection_url(db_api=POSTGRES_SYNC_API))


@pytest.fixture(scope="function")
async def asession(async_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Create an async session for testing.

    Parameters
    ----------
    async_engine
        Async engine for testing.

    Yields
    ------
    AsyncGenerator[AsyncSession, None]
        Async session for testing.
    """

    async with AsyncSession(async_engine, expire_on_commit=False) as async_session:
        yield async_session


@pytest.fixture(scope="function")
async def async_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create an async engine for testing.

    NB: We recreate engine and session to ensure it is in the same event loop as the
    test. Without this we get "Future attached to different loop" error. See:
    https://docs.sqlalchemy.org/en/14/orm/extensions/asyncio.html#using-multiple-asyncio-event-loops

    Yields
    ------
    Generator[AsyncEngine, None, None]
        Async engine for testing.
    """  # noqa: E501

    connection_string = get_connection_url()
    engine = create_async_engine(connection_string, pool_size=20)
    yield engine
    await engine.dispose()


@pytest.fixture(scope="session")
def client() -> Generator[TestClient, None, None]:
    """A **session-scoped** FastAPI `TestClient`.

    Yields
    ------
    Generator[TestClient, None, None]
        Test client.
    """

    app = create_fastapi_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def db_session() -> Generator[Session, None, None]:
    """Create a test database session.

    Yields
    ------
    Generator[Session, None, None]
        Test database session.
    """

    with get_session_managed() as session:
        yield session


@pytest.fixture(scope="function")
async def redis_client() -> AsyncGenerator[aioredis.Redis, None]:
    """Create a redis client for testing.

    Yields
    ------
    Generator[aioredis.Redis, None, None]
        Redis client for testing.
    """

    rclient = await aioredis.from_url(REDIS_URL, decode_responses=True)

    await rclient.flushdb()

    yield rclient

    await rclient.close()

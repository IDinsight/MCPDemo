"""This module contains utilities for users."""

# Standard Library
from datetime import datetime, timezone
from typing import Any

# Third Party Library
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.users.models import UserDB
from mcp_demo.users.schemas import User, UserCreateWithPassword
from mcp_demo.utils.general import generate_random_string, hash_password


class UserAlreadyExistsError(Exception):
    """Custom exception raised when a user already exists in the database."""

    def __init__(self, *, error_msg: str) -> None:
        """

        Parameters
        ----------
        error_msg
            The error message.
        """

        super().__init__(f"User already exists: {error_msg}")

        self.error_msg = error_msg


class UserNotFoundError(Exception):
    """Custom exception raised when a user is not found in the database."""

    def __init__(self, *, error_msg: str) -> None:
        """

        Parameters
        ----------
        error_msg
            The error message.
        """

        super().__init__(f"User not found: {error_msg}")

        self.error_msg = error_msg


async def check_if_user_exists(*, asession: AsyncSession, user: User) -> UserDB | None:
    """Check if a user exists in the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    user
        The user object to check in the database.

    Returns
    -------
    UserDB | None
        The user object if it exists in the database, otherwise `None`.
    """

    stmt = select(UserDB).where(UserDB.username == user.username)
    result = await asession.execute(stmt)
    user_db = result.scalar_one_or_none()
    return user_db


async def check_if_users_exist(*, asession: AsyncSession) -> bool:
    """Check if users exist in the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    bool
        Specifies whether users exists in the database.
    """

    stmt = select(UserDB.user_id).limit(1)
    result = await asession.scalars(stmt)
    return result.first() is not None


async def delete_user_from_db(*, asession: AsyncSession, user_id: int) -> None:
    """Delete a user from the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    user_id
        The ID of the user to remove from the database.
    """

    stmt = select(UserDB).where(UserDB.user_id == user_id)
    result = await asession.execute(stmt)
    user_db = result.scalar_one_or_none()

    if user_db is None:
        logger.warning(f"User ID does not exist: {user_id}")
        return

    await asession.delete(user_db)
    await asession.commit()


async def get_user_by_id(*, asession: AsyncSession, user_id: int) -> UserDB:
    """Retrieve a user by user ID.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    user_id
        The user ID to use for the query.

    Returns
    -------
    UserDB
        The user object retrieved from the database.

    Raises
    ------
    UserNotFoundError
        If the user with the specified user ID does not exist.
    """

    stmt = select(UserDB).where(UserDB.user_id == user_id)
    result = await asession.execute(stmt)

    try:
        user = result.scalar_one()
        return user
    except NoResultFound as err:
        raise UserNotFoundError(
            error_msg=f"User ID does not exist: {user_id} "
        ) from err


async def get_user_by_username(*, asession: AsyncSession, username: str) -> UserDB:
    """Retrieve a user by username.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    username
        The username to use for the query.

    Returns
    -------
    UserDB
        The user object retrieved from the database.

    Raises
    ------
    UserNotFoundError
        If the user with the specified username does not exist.
    """

    stmt = select(UserDB).where(UserDB.username == username)
    result = await asession.execute(stmt)

    try:
        user_db = result.scalar_one()
        return user_db
    except NoResultFound as err:
        raise UserNotFoundError(
            error_msg=f"User with username {username} does not exist."
        ) from err


async def save_user_to_db(
    *,
    asession: AsyncSession,
    recovery_codes: list[str] | None = None,
    user: User | UserCreateWithPassword,
) -> UserDB:
    """Save a user in the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    recovery_codes
        The recovery codes for the user account recovery.
    user
        The user object to save in the database.

    Returns
    -------
    UserDB
        The user object saved in the database.

    Raises
    ------
    UserAlreadyExistsError
        If a user with the same user ID already exists in the database.
    """

    existing_user = await check_if_user_exists(asession=asession, user=user)

    if existing_user is not None:
        raise UserAlreadyExistsError(
            error_msg=f"User ID already exists: {existing_user.user_id}"
        )

    password = (
        user.password
        if isinstance(user, UserCreateWithPassword)
        else generate_random_string(size=12)
    )

    user_db = UserDB(
        created_datetime_utc=datetime.now(timezone.utc),
        is_active=True,
        password_hash=hash_password(password=password),
        recovery_codes=recovery_codes,
        updated_datetime_utc=datetime.now(timezone.utc),
        username=user.username,
    )
    asession.add(user_db)
    await asession.commit()
    await asession.refresh(user_db)

    return user_db


async def update_user_in_db(
    *,
    asession: AsyncSession,
    user: User,
    user_id: int,
    **kwargs: Any,
) -> UserDB:
    """Update a user in the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    user
        The user object to update in the database.
    user_id
        The user ID to use for the query.
    kwargs
        Additional keyword arguments to update the user object in the database.

    Returns
    -------
    UserDB
        The user object saved in the database after update.
    """

    user_db = UserDB(
        updated_datetime_utc=datetime.now(timezone.utc),
        user_id=user_id,
        username=user.username,
        **kwargs,
    )
    user_db = await asession.merge(user_db)

    await asession.commit()
    await asession.refresh(user_db)

    return user_db

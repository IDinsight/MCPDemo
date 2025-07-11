"""This module contains utilities for users."""

# Standard Library
from datetime import datetime, timezone
from typing import Any

# Third Party Library
from fastapi import Depends, HTTPException, status
from fastapi.security import SecurityScopes
from loguru import logger
from redis import asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.auth.utils import _verify_caller, oauth2_scheme
from mcp_demo.users.models import UserDB
from mcp_demo.users.schemas import User, UserCreateWithPassword, UserResetPassword
from mcp_demo.utils.database import get_async_session
from mcp_demo.utils.general import (
    generate_hash,
    generate_random_string,
    get_redis_client,
    verify_hash,
)


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


async def check_if_user_exists(
    *, asession: AsyncSession, user: User | UserResetPassword
) -> UserDB | None:
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


async def get_current_user(
    asession: AsyncSession = Depends(get_async_session),
    redis_client: aioredis.Redis = Depends(get_redis_client),
    security_scopes: SecurityScopes = SecurityScopes(),
    token: str = Depends(oauth2_scheme),
) -> UserDB:
    """Verify the current user from the JWT token.

    This function decodes the JWT token, verifies its signature using the public key
    from the JWKS, and checks if the user exists in the database. It also verifies that
    the token has the required scopes for the requested operation.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    redis_client
        The Redis client used to check the JTI (JWT ID) for replay attacks.
    security_scopes
        The security scopes required for the operation, used to check if the token
        has the necessary permissions.
    token
        The JWT token to decode and verify.

    Returns
    -------
    UserDB
        The user database object representing the authenticated user.

    Raises
    ------
    HTTPException
        If the user is not found or is inactive.
    """

    payload = await _verify_caller(
        options={"require": ["exp", "sub"]},
        redis_client=redis_client,
        required_scopes=set(security_scopes.scopes),
        token=token,
    )

    try:
        user_db = await get_user_by_id(asession=asession, user_id=int(payload["sub"]))
    except UserNotFoundError as exc:
        raise HTTPException(
            detail="Could not validate credentials",
            status_code=status.HTTP_401_UNAUTHORIZED,
        ) from exc

    if not user_db.is_active:
        raise HTTPException(
            detail="Could not validate credentials",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return user_db


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


async def reset_user_password(
    *,
    asession: AsyncSession,
    user: UserResetPassword,
    user_db: UserDB,
) -> UserDB:
    """Hash the new password, optionally regenerate recovery codes, and persist the
    changes **on the existing row**.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    user
        The user object to reset the password.
    user_db
        The user database object containing the user ID and recovery code.

    Returns
    -------
    UserDB
        The user object saved in the database after password reset.
    """

    user_db.password_hash = generate_hash(text=user.password)

    await asession.commit()
    await asession.refresh(user_db)

    return user_db


async def save_user_to_db(
    *,
    asession: AsyncSession,
    recovery_codes: list[str],
    user: UserCreateWithPassword,
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
        is_admin=user.is_admin,
        password_hash=generate_hash(text=password),
        recovery_codes_hash=[
            generate_hash(text=recovery_code) for recovery_code in recovery_codes
        ],
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
        user_id=user_id,
        username=user.username,
        **kwargs,
    )
    user_db = await asession.merge(user_db)

    await asession.commit()
    await asession.refresh(user_db)

    return user_db


async def verify_recovery_code(
    *, asession: AsyncSession, user_db: UserDB, recovery_code: str
) -> bool:
    """Verify a recovery code for a user.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    user_db
        The user database object containing the recovery codes.
    recovery_code
        The recovery code to verify.

    Returns
    -------
    bool
        True if the recovery code is valid and has not been used; otherwise, False.
    """

    for recovery_code_hash in user_db.recovery_codes_hash:
        verified, _ = verify_hash(text=recovery_code, hashed=recovery_code_hash)
        if verified:
            user_db.recovery_codes_hash.remove(recovery_code_hash)  # One-time use
            await asession.commit()
            return True
    return False


async def verify_user(
    *, asession: AsyncSession, password: str, username: str
) -> UserDB | None:
    """Verify user credentials.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    password
        The password provided by the user for authentication.
    username
        The username provided by the user for authentication.

    Returns
    -------
    UserDB | None
        The user object if the credentials are valid and the user is active; otherwise,
        `None`.
    """

    try:
        user_db = await get_user_by_username(asession=asession, username=username)
    except UserNotFoundError:
        return None

    if not user_db.is_active:
        return None

    verified, hashed_password = verify_hash(text=password, hashed=user_db.password_hash)

    if not verified:
        return None

    if hashed_password == user_db.password_hash:
        return user_db

    # Update hashed password.
    user_db = await update_user_in_db(
        asession=asession,
        is_active=user_db.is_active,
        is_admin=user_db.is_admin,
        password_hash=hashed_password,
        user=User(
            username=user_db.username,
        ),
        user_id=user_db.user_id,
    )

    return user_db

"""This module contains utilities for users."""

# Standard Library
from datetime import datetime, timezone
from typing import Any

# Third Party Library
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import SecurityScopes
from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Package Library
from mcp_demo.auth.utils import _verify_caller, oauth_2_multi_scheme
from mcp_demo.config import Settings
from mcp_demo.scopes.models import ScopeDB, user_scope_table
from mcp_demo.users.models import UserDB
from mcp_demo.users.schemas import User, UserCreateWithPassword, UserResetPassword
from mcp_demo.utils.database import get_async_session
from mcp_demo.utils.general import generate_hash, generate_random_string, verify_hash

AUTH_ALLOWED_SCOPES = Settings.AUTH_ALLOWED_SCOPES


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


async def add_scopes_to_user(
    *, asession: AsyncSession, scopes: list[str], user_db: UserDB
) -> list[str]:
    """Add scopes to a user.

    The process is as follows:

    1. Validate scope names against the allowed scopes defined in the settings.
    2. Check if the scopes exist in the database.
    3. Append only missing scopes.
    4. Commit the changes to the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    scopes
        A list of scope names to be added to the user.
    user_db
        The user database object to which the scopes will be added.

    Returns
    -------
    list[str]
        A list of scope names that were successfully added to the user.

    Raises
    ------
    HTTPException
        If any of the provided scopes are not allowed.
        If any of the provided scopes are not found in the database.
    """

    # 1.
    allowed = set(AUTH_ALLOWED_SCOPES)
    unknown_scopes = [s for s in scopes if s not in allowed]
    if unknown_scopes:
        raise HTTPException(
            detail=f"Unknown scopes: {', '.join(unknown_scopes)}",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 2.
    scope_rows = (
        await asession.scalars(select(ScopeDB).where(ScopeDB.name.in_(scopes)))
    ).all()
    missing_in_db = {s for s in scopes if s not in {row.name for row in scope_rows}}
    if missing_in_db:
        raise HTTPException(
            detail=f"Scope(s) not seeded in DB: {', '.join(missing_in_db)}",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # 3.
    stmt = (
        select(UserDB)
        .options(selectinload(UserDB.scopes))
        .where(UserDB.user_id == user_db.user_id)
    )
    user_db = await asession.scalar(stmt)
    added_scopes: list[str] = []
    existing = {s.name for s in user_db.scopes}
    for scope_row in scope_rows:
        if scope_row.name not in existing:
            user_db.scopes.append(scope_row)
            added_scopes.append(scope_row.name)

    # 4.
    asession.add(user_db)
    await asession.commit()
    await asession.refresh(user_db)

    return added_scopes


async def check_if_user_exists(
    *, asession: AsyncSession, user: User | UserCreateWithPassword | UserResetPassword
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


async def delete_scope_from_user(
    *, asession: AsyncSession, scope_name: str, user_id: int
) -> UserDB | None:
    """Delete `scope_name` from the user identified by `user_id`.

    The process is as follows:

    1. Check if the scope is associated with the user.
    2. If the scope is not associated, return `None`.
    3. If the scope is associated, delete the association and commit the changes.
    4. Return the user object with the updated scopes.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    scope_name
        The name of the scope to remove from the user.
    user_id
        The user ID from which the scope association should be removed.

    Returns
    -------
    UserDB | None
        The user object if the scope was successfully removed, otherwise `None`.
    """

    # 1.
    stmt = select(user_scope_table).where(
        user_scope_table.c.scope_name == scope_name,
        user_scope_table.c.user_id == user_id,
    )
    result = await asession.execute(stmt)
    association = result.first()

    # 2.
    if not association:
        return None  # Scope not assigned

    # 3.
    await asession.execute(
        delete(user_scope_table).where(
            user_scope_table.c.user_id == user_id,
            user_scope_table.c.scope_name == scope_name,
        )
    )
    await asession.commit()
    await asession.flush()

    # 4.
    user_db = await asession.scalar(
        select(UserDB)
        .options(selectinload(UserDB.scopes))
        .where(UserDB.user_id == user_id)
    )

    return user_db


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
    request: Request,
    asession: AsyncSession = Depends(get_async_session),
    security_scopes: SecurityScopes = SecurityScopes(),
    token: str = Depends(oauth_2_multi_scheme),
) -> UserDB:
    """Verify the current user from the JWT token.

    This function decodes the JWT token, verifies its signature using the public key
    from the JWKS, and checks if the user exists in the database. It also verifies that
    the token has the required scopes for the requested operation.

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
    UserDB
        The user database object representing the authenticated user.

    Raises
    ------
    HTTPException
        If the user is not found or is inactive.
    """

    payload = await _verify_caller(
        redis_client=request.app.state.redis,
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

    request.state.audit_sub = user_db.user_id  # Expose caller ID for auditing

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


async def get_user_scopes_by_id(*, asession: AsyncSession, user_id: int) -> set[str]:
    """Get the scopes of a user.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    user_id
        The ID of the user whose scopes are to be retrieved.

    Returns
    -------
    set[str]
        A set of scope names associated with the user.
    """

    stmt = (
        select(UserDB)
        .options(selectinload(UserDB.scopes))
        .where(UserDB.user_id == user_id)
    )
    result = await asession.execute(stmt)
    user = result.scalar_one()
    return {s.name for s in user.scopes}


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

    existing_user_db = await check_if_user_exists(asession=asession, user=user)

    if existing_user_db is not None:
        raise UserAlreadyExistsError(
            error_msg=f"User ID already exists: {existing_user_db.user_id}"
        )

    password = (
        user.password
        if isinstance(user, UserCreateWithPassword)
        else generate_random_string(size=12)
    )

    user_db = UserDB(
        created_datetime_utc=datetime.now(timezone.utc),
        is_active=True,
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
    user_id: int,
    username: str,
    **kwargs: Any,
) -> UserDB:
    """Update a user in the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    user_id
        The user ID to use for the query.
    username
        The username to update in the database.
    kwargs
        Additional keyword arguments to update the user object in the database.

    Returns
    -------
    UserDB
        The user object saved in the database after update.
    """

    user_db = UserDB(user_id=user_id, username=username, **kwargs)
    user_db = await asession.merge(user_db)

    await asession.commit()
    await asession.refresh(user_db)

    return user_db


async def validate_user_scopes(*, scopes: list[str]) -> bool:
    """Validate that the provided scopes are allowed.

    Parameters
    ----------
    scopes
        A list of scope names to validate.
    """

    return set(scopes).issubset(AUTH_ALLOWED_SCOPES)


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
        verified, hashed_recovery_code = verify_hash(
            text=recovery_code, hashed=recovery_code_hash
        )
        if verified:
            user_db.recovery_codes_hash.remove(recovery_code_hash)  # One-time use
            await asession.commit()
            return True
        index = user_db.recovery_codes_hash.index(recovery_code_hash)
        user_db.recovery_codes_hash[index] = hashed_recovery_code
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
        password_hash=hashed_password,
        username=user_db.username,
        user_id=user_db.user_id,
    )

    return user_db

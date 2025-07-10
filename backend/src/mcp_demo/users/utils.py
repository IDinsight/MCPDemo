"""This module contains utilities for users."""

# Standard Library
from datetime import datetime, timezone
from typing import Annotated, Any

# Third Party Library
import jwt

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, SecurityScopes
from jose import JWTError, jwk
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.auth.utils import load_jwks
from mcp_demo.config import Settings
from mcp_demo.users.models import UserDB
from mcp_demo.users.schemas import User, UserCreateWithPassword
from mcp_demo.utils.database import get_async_session_managed
from mcp_demo.utils.general import (
    generate_hash,
    generate_random_string,
    verify_password,
)

AUTH_AUDIENCE = Settings.AUTH_AUDIENCE
AUTH_JWK_ALGORITHM = Settings.AUTH_JWK_ALGORITHM
AUTH_TOKEN_ISSUER = Settings.AUTH_TOKEN_ISSUER

oauth2_scheme = OAuth2PasswordBearer(
    scopes={"user": "Regular user", "admin": "Site administrator"},
    tokenUrl="/user/token",
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


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    security_scopes: SecurityScopes = SecurityScopes(),
) -> UserDB:
    """Get the current user from the JWT token.

    This function decodes the JWT token, verifies its signature using the public key
    from the JWKS, and checks if the user exists in the database. It also verifies that
    the token has the required scopes for the requested operation.

    Parameters
    ----------
    token
        The JWT token to decode and verify.
    security_scopes
        The security scopes required for the operation, used to check if the token
        has the necessary permissions.

    Returns
    -------
    UserDB
        The user database object representing the authenticated user.

    Raises
    ------
    HTTPException
        If the token is invalid, expired, or does not have the required scopes. This
        exception is raised with a 401 Unauthorized status code if the token cannot be
        validated, or a 403 Forbidden status code if the token lacks sufficient
        permissions.
    """

    credentials_exception = HTTPException(
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )

    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise credentials_exception
        last_jwks = await load_jwks()
        key = next(k for k in last_jwks["keys"] if k["kid"] == kid)
        if not key:
            raise credentials_exception
        public_key = jwk.construct(key).to_pem().decode()
        payload = jwt.decode(
            token,
            public_key,
            algorithms=[AUTH_JWK_ALGORITHM],
            audience=AUTH_AUDIENCE,
            issuer=AUTH_TOKEN_ISSUER,
            options={"require": ["exp", "sub"]},
        )
    except (StopIteration, JWTError) as exc:
        raise credentials_exception from exc

    user_id = payload.get("sub", None)
    if not user_id:
        raise credentials_exception

    token_scopes = {s for s in payload.get("scope", "").split() if s}
    required_scopes = set(security_scopes.scopes)
    if not required_scopes.issubset(token_scopes):
        raise HTTPException(
            detail="Not enough permissions", status_code=status.HTTP_403_FORBIDDEN
        )

    async with get_async_session_managed() as asession:
        try:
            user_db = await get_user_by_id(asession=asession, user_id=int(user_id))
            user_db.scopes = token_scopes
            return user_db
        except UserNotFoundError as exc:
            raise credentials_exception from exc


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
    recovery_codes: list[str],
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
        password_hash=generate_hash(text=password),
        recovery_codes_hash=[
            generate_hash(text=recovery_code) for recovery_code in recovery_codes
        ],
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


async def verify_user(
    *, asession: AsyncSession, password: str, username: str
) -> UserDB | None:
    """Verify user credentials.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    password
        The password povided by the user for authentication.
    username
        The username provided by the user for authentication.

    Returns
    -------
    UserDB | None
        The user object if the credentials are valid and the user is active; otherwise,
        None.
    """

    try:
        user_db = await get_user_by_username(asession=asession, username=username)
    except UserNotFoundError:
        return None

    if not user_db.is_active:
        return None

    verified, hashed_password = verify_password(
        plain_password=password, hashed_password=user_db.password_hash
    )

    if not verified:
        return None

    if hashed_password == user_db.password_hash:
        return user_db

    # Update hashed password.
    user_db = await update_user_in_db(
        asession=asession,
        password_hash=hashed_password,
        user=User(username=user_db.username),
        user_id=user_db.user_id,
    )

    return user_db

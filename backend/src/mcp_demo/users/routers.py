"""This module contains FastAPI routers for user endpoints."""

# Standard Library
from typing import Annotated

# Third Party Library
import sqlalchemy

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.config import Settings
from mcp_demo.users.models import UserDB
from mcp_demo.users.schemas import (
    UserCreateWithPassword,
    UserCreateWithRecoveryCodes,
    UserDeleteResponse,
    UserResetPassword,
    UserRetrieve,
)
from mcp_demo.users.utils import (
    UserNotFoundError,
    check_if_user_exists,
    delete_user_from_db,
    get_current_user,
    get_user_by_id,
    reset_user_password,
    save_user_to_db,
    verify_recovery_code,
)
from mcp_demo.utils.chat import AsyncChatSessionManager, get_chat_session_manager
from mcp_demo.utils.database import get_async_session
from mcp_demo.utils.general import generate_recovery_codes

TAG_METADATA = {"description": "Manages users", "name": "User"}
router = APIRouter(prefix="/user", tags=[TAG_METADATA["name"]])

limiter = Limiter(key_func=get_remote_address, storage_uri=Settings.REDIS_URL)

REDIS_CACHE_PREFIX_CHAT = Settings.REDIS_CACHE_PREFIX_CHAT


@router.post("/register", response_model=UserCreateWithRecoveryCodes)
@limiter.limit(Settings.RATE_LIMIT_LOGIN_RATE)
async def register(
    request: Request,  # pylint: disable=W0613
    user: UserCreateWithPassword,
    asession: AsyncSession = Depends(get_async_session),
) -> UserCreateWithRecoveryCodes:
    """Register a new user and issue recovery codes.

    The process is as follows:

    1. If the username already exists, then raise a 400 error.
    2. Generate recovery codes for the new user.
    3. Save the user to the database with the recovery codes.

    Parameters
    ----------
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    user
        The user object to create.
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    UserCreateWithRecoveryCodes
        The user object with the recovery codes.

    Raises
    ------
    HTTPException
        If the username already exists.
    """

    # 1.
    if await check_if_user_exists(asession=asession, user=user):
        raise HTTPException(
            detail="User name already exists.", status_code=status.HTTP_400_BAD_REQUEST
        )

    # 2.
    recovery_codes = generate_recovery_codes()

    # 3.
    user_db = await save_user_to_db(
        asession=asession, recovery_codes=recovery_codes, user=user
    )

    return UserCreateWithRecoveryCodes(
        recovery_codes=recovery_codes,
        user_id=user_db.user_id,
        username=user_db.username,
    )


@router.delete("/{user_id}", response_model=UserDeleteResponse)
@limiter.limit(Settings.RATE_LIMIT_LOGIN_RATE)
async def delete_user(
    calling_user_db: Annotated[UserDB, Depends(get_current_user)],
    request: Request,  # pylint: disable=W0613
    user_id: int,
    asession: AsyncSession = Depends(get_async_session),
    csm: AsyncChatSessionManager = Depends(get_chat_session_manager),
) -> UserDeleteResponse:
    """Delete user by ID from database and Redis caches.

    The process is as follows:

    1. Check if the authenticated user has permission to delete the user.
    2. Delete the user from the database.
    3. Delete the chat history from the chat session manager.

    NB: To prevent inference attacks, we also raise a 404 error if the calling user ID
    is not the same as the user ID to delete.

    Parameters
    ----------
    calling_user_db
        The user database object of the authenticated user, used to verify permissions.
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    user_id
        The user ID to delete.
    asession
        The SQLAlchemy async session to use for all database connections.
    csm
        An async chat session manager that manages the chat sessions for each user.

    Returns
    -------
    UserDeleteResponse
        The user deletion response.

    Raises
    ------
    HTTPException
        If the authenticated user does not have permission to delete the user.
        If the user ID does not exist in the database.
        If there is an error deleting the user from the database.
    """

    # 1.
    if calling_user_db.user_id != user_id and "admin" not in (
        calling_user_db.scopes or []
    ):
        raise HTTPException(
            detail=f"User ID not found: {user_id}.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # 2.
    try:
        user_db = await get_user_by_id(asession=asession, user_id=user_id)
    except UserNotFoundError as exc:
        raise HTTPException(
            detail=f"User ID not found: {user_id}.",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc

    try:
        await delete_user_from_db(asession=asession, user_id=user_db.user_id)
    except sqlalchemy.exc.IntegrityError as e:
        raise HTTPException(
            detail="Error deleting user.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from e

    # 3.
    chat_session_exists, session_id = await csm.check_if_chat_session_exists(
        namespace=REDIS_CACHE_PREFIX_CHAT, signed=True, user_id=f"{user_id}"
    )
    if chat_session_exists:
        await csm.delete_chat_history(session_id=session_id)

    return UserDeleteResponse(user_id=user_id, username=user_db.username)


@router.put("/reset-password", response_model=UserRetrieve)
@limiter.limit(Settings.RATE_LIMIT_LOGIN_RATE)
async def reset_password(
    request: Request,  # pylint: disable=W0613
    user: UserResetPassword,
    asession: AsyncSession = Depends(get_async_session),
) -> UserRetrieve:
    """Reset user password using a one-time recovery code.

    NB: When this endpoint is called, the assumption is that the calling user is the
    user that is requesting to reset their own password. This is because a user's
    password is universal and belongs to the user. Thus, only a user can reset their
    own password.

    Parameters
    ----------
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    user
        The user object with the new password and recovery code.
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    UserRetrieve
        The updated user object.

    Raises
    ------
    HTTPException
        If the user does not exist in the database.
        If the recovery code is invalid.
    """

    user_to_update = await check_if_user_exists(asession=asession, user=user)

    if user_to_update is None:
        raise HTTPException(
            detail="User not found.", status_code=status.HTTP_404_NOT_FOUND
        )

    verified_recovery_code = await verify_recovery_code(
        asession=asession, user_db=user_to_update, recovery_code=user.recovery_code
    )

    if not verified_recovery_code:
        raise HTTPException(
            detail="Invalid recovery code.", status_code=status.HTTP_400_BAD_REQUEST
        )

    updated_user_db = await reset_user_password(
        asession=asession, user=user, user_db=user_to_update
    )

    return UserRetrieve(
        created_datetime_utc=updated_user_db.created_datetime_utc,
        is_active=updated_user_db.is_active,
        is_admin=updated_user_db.is_admin,
        updated_datetime_utc=updated_user_db.updated_datetime_utc,
        user_id=updated_user_db.user_id,
        username=updated_user_db.username,
    )

"""This module contains FastAPI routers for user endpoints."""

# Third Party Library
import sqlalchemy

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.config import Settings
from mcp_demo.users.schemas import (
    UserCreateWithPassword,
    UserCreateWithRecoveryCodes,
    UserDeleteResponse,
)
from mcp_demo.users.utils import (
    UserNotFoundError,
    check_if_user_exists,
    delete_user_from_db,
    get_user_by_id,
    save_user_to_db,
)
from mcp_demo.utils.chat import AsyncChatSessionManager, get_chat_session_manager
from mcp_demo.utils.database import get_async_session
from mcp_demo.utils.general import generate_recovery_codes

TAG_METADATA = {"description": "Manage users", "name": "User"}
router = APIRouter(prefix="/user", tags=[TAG_METADATA["name"]])

REDIS_CACHE_PREFIX_CHAT = Settings.REDIS_CACHE_PREFIX_CHAT


@router.post("/register", response_model=UserCreateWithRecoveryCodes)
async def register(
    user: UserCreateWithPassword,
    asession: AsyncSession = Depends(get_async_session),
) -> UserCreateWithRecoveryCodes:
    """

    The process is as follows:

    1. If the username already exists, then raise a 400 error.
    2. Generate recovery codes for the new user.
    3. Save the user to the database with the recovery codes.

    Parameters
    ----------
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
        recovery_codes=recovery_codes, username=user_db.username
    )


@router.delete("/{user_id}", response_model=UserDeleteResponse)
async def delete_user(
    user_id: int,
    asession: AsyncSession = Depends(get_async_session),
    csm: AsyncChatSessionManager = Depends(get_chat_session_manager),
) -> UserDeleteResponse:
    """Delete user by ID from database and Redis caches.

    The process is as follows:

    1. Delete the user from the database.
    2. Delete the chat history from the chat session manager.

    Parameters
    ----------
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
        If the user does not exist, if the user ID does not match the authenticated
        user, or if there is an error deleting the user.
    """

    # 1.
    try:
        user_db = await get_user_by_id(asession=asession, user_id=user_id)
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User ID `{user_id}` not found.",
        ) from exc

    if user_db.user_id != user_id:
        raise HTTPException(
            detail="User ID does not match the authenticated user.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    try:
        await delete_user_from_db(asession=asession, user_id=user_db.user_id)
    except sqlalchemy.exc.IntegrityError as e:
        raise HTTPException(
            detail="Error deleting user.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from e

    # 2.
    chat_session_exists, session_id = await csm.check_if_chat_session_exists(
        namespace=REDIS_CACHE_PREFIX_CHAT, signed=True, user_id=f"{user_id}"
    )
    if chat_session_exists:
        await csm.delete_chat_history(session_id=session_id)

    return UserDeleteResponse(user_id=user_id)

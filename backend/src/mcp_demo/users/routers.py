"""This module contains FastAPI routers for user endpoints."""

# Third Party Library
import sqlalchemy

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.config import Settings
from mcp_demo.users.schemas import UserDeleteResponse
from mcp_demo.users.utils import UserNotFoundError, delete_user_from_db, get_user_by_id
from mcp_demo.utils.chat import AsyncChatSessionManager, get_chat_session_manager
from mcp_demo.utils.database import get_async_session

TAG_METADATA = {"description": "Manage users", "name": "User"}
router = APIRouter(prefix="/user", tags=[TAG_METADATA["name"]])

REDIS_CACHE_PREFIX_CHAT = Settings.REDIS_CACHE_PREFIX_CHAT


@router.delete("/{user_id}", response_model=UserDeleteResponse)
async def delete_user(
    user_id: str,
    asession: AsyncSession = Depends(get_async_session),
    csm: AsyncChatSessionManager = Depends(get_chat_session_manager),
) -> UserDeleteResponse:
    """Delete user by ID from database and Redis caches.

    The process is as follows:

    1. Delete the user from the database.
    2. Delete the chat history from the chat session manager.

    Parameters
    ----------
    \n\tuser_id
    \t\tThe user ID to delete.
    \n\tasession
    \t\tThe SQLAlchemy async session to use for all database connections.
    \n\tcsm
    \t\tAn async chat session manager that manages the chat sessions for each user.

    Returns
    -------
    \n\tUserDeleteResponse
    \t\tThe user deletion response.

    Raises
    ------
    \n\tHTTPException
    \t\tIf the user in the database does not match the authenticated user or if there
    \t\tis an error deleting the user.
    """

    # 1.
    try:
        user_db = await get_user_by_id(asession=asession, user_id=user_id)
    except UserNotFoundError:
        pass
    else:
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

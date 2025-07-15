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
from mcp_demo.auth.utils import require_scopes
from mcp_demo.config import Settings
from mcp_demo.scopes.schemas import ScopeAssign, ScopeCreate, ScopeResponse
from mcp_demo.scopes.utils import add_scope_to_db, check_if_scope_exists
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
    add_scopes_to_user,
    check_if_user_exists,
    check_if_users_exist,
    delete_scope_from_user,
    delete_user_from_db,
    get_current_user,
    get_user_by_id,
    get_user_scopes_by_id,
    reset_user_password,
    save_user_to_db,
    verify_recovery_code,
)
from mcp_demo.utils.chat import AsyncChatSessionManager, get_chat_session_manager
from mcp_demo.utils.database import get_async_session
from mcp_demo.utils.general import generate_recovery_codes

TAG_METADATA = {"description": "Manages users", "name": "User"}
router = APIRouter(prefix="/user", tags=[TAG_METADATA["name"]])

RATE_LIMIT_LOGIN_RATE = Settings.RATE_LIMIT_LOGIN_RATE
REDIS_CACHE_PREFIX_CHAT = Settings.REDIS_CACHE_PREFIX_CHAT
REDIS_URL = Settings.REDIS_URL

limiter = Limiter(key_func=get_remote_address, storage_uri=REDIS_URL)


@router.get("/admin-panel")
async def admin_panel(
    claims: dict = require_scopes(required_scopes={"admin"}),  # pylint: disable=W0613
) -> dict[str, str]:
    """Admin panel view for users with admin scope.

    This endpoint is protected and can only be accessed by users with the 'admin' scope.
    It returns a simple message indicating that the user has access to the admin panel.

    Parameters
    ----------
    claims
        The claims of the authenticated user, used to verify scopes.

    Returns
    -------
    dict[str, str]
        A message indicating access to the admin panel.
    """

    return {"message": f"Welcome to the admin panel {claims['sub']}!"}


@router.post("/", response_model=UserCreateWithRecoveryCodes)
async def add_new_user(
    calling_user_db: Annotated[UserDB, Depends(get_current_user)],
    user: UserCreateWithPassword,
    asession: AsyncSession = Depends(get_async_session),
) -> UserCreateWithRecoveryCodes:
    """Create a new user.

    The process is as follows:

    1. Check if the authenticated user has permission to add a new user.
    2. Check if the username already exists in the database.
    3. Generate recovery codes for the new user.
    4. Save the new user to the database with the generated recovery codes.
    5. Add the 'read' scope to the scope database and assign it to the new user.

    Parameters
    ----------
    calling_user_db
        The user object associated with the user that is creating a new user.
    user
        The user object to create.
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    UserCreateWithCode
        The user object with the recovery codes.

    Raises
    ------
    HTTPException
        If the authenticated user does not have permission to add a new user.
        If the username already exists.
    """

    # 1.
    calling_user_scopes = await get_user_scopes_by_id(
        asession=asession, user_id=calling_user_db.user_id
    )
    if "admin" not in calling_user_scopes:
        raise HTTPException(
            detail="Insufficient permission to add new user.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # 2.
    if await check_if_user_exists(asession=asession, user=user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Username already exists."
        )

    # 3.
    recovery_codes = generate_recovery_codes()

    # 4.
    user_db = await save_user_to_db(
        asession=asession, recovery_codes=recovery_codes, user=user
    )

    # 5.
    await add_scope_to_db(asession=asession, scope=ScopeCreate(name="read"))
    added_scopes = await add_scopes_to_user(
        asession=asession, scopes=["read"], user_db=user_db
    )

    return UserCreateWithRecoveryCodes(
        recovery_codes=recovery_codes,
        scopes=added_scopes,
        user_id=user_db.user_id,
        username=user_db.username,
    )


@router.post("/{user_id}/scope", response_model=ScopeResponse)
async def add_user_scope(
    calling_user_db: Annotated[UserDB, Depends(get_current_user)],
    scope_assign: ScopeAssign,
    user_id: int,
    asession: AsyncSession = Depends(get_async_session),
) -> ScopeResponse:
    """Add scopes to a user.

    Parameters
    ----------
    calling_user_db
        The user who is making the request. This is used to check if the user has the
        required scope to assign scopes.
    scope_assign
        The scopes to assign to the user.
    user_id
        The ID of the user to whom the scopes will be assigned.
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    ScopeResponse
        The response containing the user ID and the list of scopes assigned to the user.

    Raises
    ------
    HTTPException
        If the calling user does not have the 'admin' scope.
    """

    calling_user_scopes = await get_user_scopes_by_id(
        asession=asession, user_id=calling_user_db.user_id
    )
    if "admin" not in calling_user_scopes:
        raise HTTPException(
            detail="Insufficient permission to assign scopes.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    user_db = await get_user_by_id(asession=asession, user_id=user_id)

    added_scopes = await add_scopes_to_user(
        asession=asession, scopes=scope_assign.scopes, user_db=user_db
    )

    return ScopeResponse(scopes=added_scopes, user_id=user_db.user_id)


@router.delete("/{user_id}/{scope_name}", response_model=ScopeResponse)
async def delete_user_scope(
    calling_user_db: Annotated[UserDB, Depends(get_current_user)],
    scope_name: str,
    user_id: int,
    asession: AsyncSession = Depends(get_async_session),
) -> ScopeResponse:
    """Delete a scope from a user.

    The process is as follows:

    1. Check if the authenticated user has permission to delete scopes.
    2. Check if the scope exists in the database.
    3. Check if the user exists in the database.
    4. Delete the scope from the user.
    5. If the scope is not assigned to the user, raise an error.

    Parameters
    ----------
    calling_user_db
        The user who is making the request. This is used to check if the user has the
        required scope to delete scopes.
    scope_name
        The name of the scope to delete from the user.
    user_id
        The ID of the user from whom the scope will be deleted.
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    ScopeResponse
        The response containing the user ID and the list of scopes remaining for the
        user.

    Raises
    ------
    HTTPException
        If the calling user does not have the 'admin' scope.
        If the scope does not exist.
        If the scope is not assigned to the user.
    """

    # 1.
    calling_user_scopes = await get_user_scopes_by_id(
        asession=asession, user_id=calling_user_db.user_id
    )
    if "admin" not in calling_user_scopes:
        raise HTTPException(
            detail="Insufficient permission to delete scopes.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # 2.
    if not await check_if_scope_exists(
        asession=asession, scope=ScopeCreate(name=scope_name)
    ):
        raise HTTPException(
            detail=f"Scope '{scope_name}' does not exist.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # 3.
    try:
        user_db = await get_user_by_id(asession=asession, user_id=user_id)
    except UserNotFoundError as exc:
        raise HTTPException(
            detail=f"User ID not found: {user_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc

    # 4.
    user_db = await delete_scope_from_user(
        asession=asession, scope_name=scope_name, user_id=user_db.user_id
    )

    # 5.
    if not user_db:
        raise HTTPException(
            detail=f"Scope '{scope_name}' not found for user ID {user_id}.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return ScopeResponse(
        user_id=user_db.user_id, scopes=[s.name for s in user_db.scopes]
    )


@router.post("/register-first-user", response_model=UserCreateWithRecoveryCodes)
async def register_first_user(
    user: UserCreateWithPassword,
    asession: AsyncSession = Depends(get_async_session),
) -> UserCreateWithRecoveryCodes:
    """Register the first user and issue recovery codes.

    The process is as follows:

    1. Check if any users already exist in the database. If so, raise an error.
    2. Generate recovery codes for the first user.
    3. Save the first user to the database with the generated recovery codes.
    4. Add the 'admin' scope to the scope database and assign it to the first user.

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
        If the authenticated user does not have permission to create users.
    """

    # 1.
    if await check_if_users_exist(asession=asession):
        raise HTTPException(
            detail="Users already exist. Cannot register the first user again.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 2.
    recovery_codes = generate_recovery_codes()

    # 3.
    user_db = await save_user_to_db(
        asession=asession, recovery_codes=recovery_codes, user=user
    )

    # 4.
    await add_scope_to_db(asession=asession, scope=ScopeCreate(name="admin"))
    added_scopes = await add_scopes_to_user(
        asession=asession, scopes=["admin"], user_db=user_db
    )

    return UserCreateWithRecoveryCodes(
        recovery_codes=recovery_codes,
        scopes=added_scopes,
        user_id=user_db.user_id,
        username=user_db.username,
    )


@router.get("/{user_id}", response_model=UserRetrieve)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def get_user(
    calling_user_db: Annotated[UserDB, Depends(get_current_user)],
    request: Request,  # pylint: disable=W0613
    user_id: int,
    asession: AsyncSession = Depends(get_async_session),
) -> UserRetrieve:
    """Return a user profile iff the caller is the same `sub` *or* carries the `admin`
    scope.

    Parameters
    ----------
    calling_user_db
        The user database object of the authenticated user, used to verify permissions.
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    user_id
        The user ID to retrieve.
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    UserRetrieve
        The user profile object containing user details.

    Raises
    ------
    HTTPException
        If the authenticated user does not have permission to view the user profile.
        If the user ID does not exist in the database.
    """

    if calling_user_db.user_id != user_id:
        caller_scopes = await get_user_scopes_by_id(
            asession=asession, user_id=calling_user_db.user_id
        )
        if "admin" not in caller_scopes:
            raise HTTPException(
                detail=f"User ID not found: {user_id}.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

    try:
        target_user_db = await get_user_by_id(asession=asession, user_id=user_id)
    except UserNotFoundError as exc:
        raise HTTPException(
            detail=f"User ID not found: {user_id}.",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc

    target_user_scopes = await get_user_scopes_by_id(
        asession=asession, user_id=target_user_db.user_id
    )
    return UserRetrieve(
        created_datetime_utc=target_user_db.created_datetime_utc,
        is_active=target_user_db.is_active,
        scopes=list(target_user_scopes),
        updated_datetime_utc=target_user_db.updated_datetime_utc,
        user_id=target_user_db.user_id,
        username=target_user_db.username,
    )


@router.delete("/{user_id}", response_model=UserDeleteResponse)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
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
    calling_user_scopes = await get_user_scopes_by_id(
        asession=asession, user_id=calling_user_db.user_id
    )

    if calling_user_db.user_id != user_id and "admin" not in (
        calling_user_scopes or []
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
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
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
    updated_user_scopes = await get_user_scopes_by_id(
        asession=asession, user_id=updated_user_db.user_id
    )
    return UserRetrieve(
        created_datetime_utc=updated_user_db.created_datetime_utc,
        is_active=updated_user_db.is_active,
        scopes=list(updated_user_scopes),
        updated_datetime_utc=updated_user_db.updated_datetime_utc,
        user_id=updated_user_db.user_id,
        username=updated_user_db.username,
    )

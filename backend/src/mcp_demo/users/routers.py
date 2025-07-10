"""This module contains FastAPI routers for user endpoints."""

# Standard Library
from typing import Annotated

# Third Party Library
import sqlalchemy

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.auth.utils import get_jwt_token, sanitize_scopes
from mcp_demo.config import Settings
from mcp_demo.users.models import UserDB
from mcp_demo.users.schemas import (
    TokenResponse,
    UserCreateWithPassword,
    UserCreateWithRecoveryCodes,
    UserDeleteResponse,
)
from mcp_demo.users.utils import (
    UserNotFoundError,
    check_if_user_exists,
    delete_user_from_db,
    get_current_user,
    get_user_by_id,
    save_user_to_db,
    verify_user,
)
from mcp_demo.utils.chat import AsyncChatSessionManager, get_chat_session_manager
from mcp_demo.utils.database import get_async_session
from mcp_demo.utils.general import generate_recovery_codes
from mcp_demo.utils.rate_limit import (
    is_locked_out,
    record_failed_login,
    reset_failed_login,
)

TAG_METADATA = {"description": "_Requires user login._ Manage users", "name": "User"}
router = APIRouter(prefix="/user", tags=[TAG_METADATA["name"]])

limiter = Limiter(key_func=get_remote_address, storage_uri=Settings.REDIS_URL)

REDIS_CACHE_PREFIX_CHAT = Settings.REDIS_CACHE_PREFIX_CHAT


@router.post(
    "/token",
    description=(
        "Authenticate with username/password and receive an RS256 JWT.\n\n"
        "- **Request**: `application/x-www-form-urlencoded`\n"
        "- **Response**: JSON with `access_token`, `token_type`, `expires_in`\n"
        "- **Usage**: Header `Authorization: Bearer <token>` for subsequent requests"
    ),
    response_model=TokenResponse,
    summary="Issue Bearer Token",
)
@limiter.limit(Settings.RATE_LIMIT_LOGIN_RATE)
async def login(
    request: Request,
    asession: AsyncSession = Depends(get_async_session),
    form: OAuth2PasswordRequestForm = Depends(),
) -> TokenResponse:
    """Issue a JWT token for the authenticated user.

    Parameters
    ----------
    request
        The FastAPI request object, used to access the requested scopes.
    asession
        The SQLAlchemy async session to use for all database connections.
    form
        The OAuth2 password request form.

    Returns
    -------
    TokenResponse
        The token response containing the access token, expiration time, and token type.

    Raises
    ------
    HTTPException
        If the user is locked out due to too many failed login attempts.
        If the user credentials are invalid or the user does not exist.
    """

    assert request.client is not None, f"Request client is None: {request}"
    ip = request.client.host
    redis_client = request.app.state.redis
    username = form.username

    if await is_locked_out(ip=ip, redis_client=redis_client, user=username):
        raise HTTPException(
            detail="Too many failed login attempts. Try again later.",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    user_db = await verify_user(
        asession=asession, password=form.password, username=username
    )
    if user_db is None:
        await record_failed_login(ip=ip, redis_client=redis_client, user=username)
        raise HTTPException(
            detail="Bad credentials", status_code=status.HTTP_401_UNAUTHORIZED
        )

    await reset_failed_login(ip=ip, redis_client=redis_client, user=username)

    user_scopes = ["read", "write"]
    if user_db.is_admin:
        user_scopes.append("admin")

    token = await get_jwt_token(
        passphrase=Settings.AUTH_RSA_PASSPHRASE.get_secret_value(),
        scopes=sanitize_scopes(requested_scopes=user_scopes),
        sub=str(user_db.user_id),
    )

    return TokenResponse(
        access_token=token, expires_in=Settings.AUTH_TOKEN_TTL, token_type="bearer"
    )


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
        recovery_codes=recovery_codes,
        user_id=user_db.user_id,
        username=user_db.username,
    )


@router.delete("/{user_id}", response_model=UserDeleteResponse)
async def delete_user(
    calling_user_db: Annotated[UserDB, Depends(get_current_user)],
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
    if calling_user_db.user_id != user_id or "admin" not in calling_user_db.scopes:
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

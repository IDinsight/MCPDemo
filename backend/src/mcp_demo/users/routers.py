"""This module contains FastAPI routers for user and user consent endpoints.

Since user content is tied to the user, consent endpoints are also defined in this
module.
"""

# Standard Library
from typing import Annotated, Any

# Third Party Library
import sqlalchemy

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.auth.utils import require_scopes, revoke_refresh_tokens_for_grant
from mcp_demo.config import Settings
from mcp_demo.scopes.schemas import ScopeAssign, ScopeCreate, ScopeResponse
from mcp_demo.scopes.utils import add_scope_to_db, check_if_scope_exists
from mcp_demo.users.models import UserDB
from mcp_demo.users.schemas import (
    ConsentCreate,
    ConsentInfo,
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
    delete_user_consent,
    delete_user_from_db,
    get_current_user,
    get_user_by_id,
    get_user_consents,
    get_user_scopes_by_id,
    reset_user_password,
    save_user_consent,
    save_user_to_db,
    verify_recovery_code,
)
from mcp_demo.utils.database import get_async_session
from mcp_demo.utils.general import generate_recovery_codes

TAG_METADATA = {"description": "Manages users", "name": "User"}
router = APIRouter(prefix="/user", tags=[TAG_METADATA["name"]])

RATE_LIMIT_LOGIN_RATE = Settings.RATE_LIMIT_LOGIN_RATE
REDIS_URL = Settings.REDIS_URL

limiter = Limiter(key_func=get_remote_address, storage_uri=REDIS_URL)


@router.get("/admin-panel", summary="Admin panel")
async def admin_panel(
    claims: dict = require_scopes(required_scopes={"admin"}),
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

    return {"message": "Welcome to the admin panel!", "user_id": claims["sub"]}


@router.post(
    "/",
    response_model=UserCreateWithRecoveryCodes,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user",
)
async def add_new_user(
    user: UserCreateWithPassword,
    asession: AsyncSession = Depends(get_async_session),
    claims: dict = require_scopes(required_scopes={"admin"}),
) -> UserCreateWithRecoveryCodes:
    """Create a new user.

    The process is as follows:

    1. Check if the username already exists in the database.
    2. Generate recovery codes for the new user.
    3. Save the new user to the database with the generated recovery codes.
    4. Add the 'read' scope to the scope database and assign it to the new user.

    Parameters
    ----------
    user
        The user object to create.
    asession
        The SQLAlchemy async session to use for all database connections.
    claims
        The claims of the authenticated user, used to verify scopes.

    Returns
    -------
    UserCreateWithCode
        The user object with the recovery codes.

    Raises
    ------
    HTTPException
        If the username already exists.
    """

    # 1.
    if await check_if_user_exists(asession=asession, user=user):
        raise HTTPException(
            detail="Username already exists.", status_code=status.HTTP_400_BAD_REQUEST
        )

    # 2.
    recovery_codes = generate_recovery_codes()

    # 3.
    user_db = await save_user_to_db(
        asession=asession, recovery_codes=recovery_codes, user=user
    )

    # 4.
    await add_scope_to_db(asession=asession, scope=ScopeCreate(name="read"))
    added_scopes = await add_scopes_to_user(
        asession=asession, scopes=["read"], user_db=user_db
    )

    return UserCreateWithRecoveryCodes(
        created_by=claims["sub"],
        recovery_codes=recovery_codes,
        scopes=added_scopes,
        user_id=user_db.user_id,
        username=user_db.username,
    )


@router.post(
    "/consents",
    response_model=ConsentInfo,
    status_code=status.HTTP_201_CREATED,
    summary="Grant or update user consent for a client",
)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def grant_user_consent(
    request: Request,
    claims: dict[str, Any] = require_scopes(
        required_scopes=set()
    ),  # Only needs authentication
    payload: ConsentCreate = Body(...),
) -> ConsentInfo:
    """Save resource-owner --> client consent so that the `auth/authorize` endpoint can
    skip UI-based consent form.

    Call this endpoint once **before** hitting the `auth/authorize` endpoint the first
    time with a given client ID/scope set. Subsequent authorizations will re-use the
    stored consent.

    Parameters
    ----------
    request
        The FastAPI request object.
    claims
        The claims of the authenticated user, used to verify the user's identity.
    payload
        The consent information to save, including the client ID and scopes.

    Returns
    -------
    ConsentInfo
        The consent information that was saved, including the client ID and scopes.
    """

    await save_user_consent(
        client_id=payload.client_id,
        redis_client=request.app.state.redis,
        scopes=payload.scopes,
        sub=str(claims["sub"]),
    )

    return ConsentInfo(client_id=payload.client_id, scopes=payload.scopes)


@router.get(
    "/consents",
    response_model=list[ConsentInfo],
    summary="List all consents granted by the current user",
)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def list_user_consents(
    request: Request, claims: dict[str, Any] = require_scopes(required_scopes=set())
) -> list[ConsentInfo]:
    """Return a list of all consents granted by the current user.

    Parameters
    ----------
    request
        The FastAPI request object.
    claims
        The claims of the authenticated user, used to verify the user's identity.

    Returns
    -------
    list[ConsentInfo]
        A list of consent information objects, each containing the client ID and scopes
        for which the user has granted consent.
    """

    rows = await get_user_consents(
        redis_client=request.app.state.redis, sub=str(claims["sub"])
    )

    return [ConsentInfo(client_id=cid, scopes=scopes) for cid, scopes in rows]


@router.delete(
    "/consents/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke consent for a specific client",
)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def revoke_user_consent(
    client_id: str,
    request: Request,
    claims: dict = require_scopes(required_scopes=set()),
) -> None:
    """Delete the user consent record. Subsequent calls to `auth/authorize` will fail
    until the user grants consent again.

    NB: After revoking consent, pre-issued access/refresh tokens will keep working
    until their natural expiration time. This is because access/refresh tokens are
    bearer tokens---a resource server validate them offline (JWT) or cache the
    introspection result. The OAuth spec only specifies that the server **may** revoke
    tokens. Revoking consent only stops **new** tokens from being issued; old tokens
    live until expiry or manual revocation. Thus, this endpoint also revokes all
    **refresh** tokens issued by the user for the given client ID. Access tokens remain
    valid until their natural expiration time (they are short-lived).

    Parameters
    ----------
    client_id
        The client ID for which to revoke consent.
    request
        The FastAPI request object.
    claims
        The claims of the authenticated user, used to verify the user's identity.
    """

    sub = str(claims["sub"])

    await delete_user_consent(
        client_id=client_id,
        redis_client=request.app.state.redis,
        sub=sub,
    )
    await revoke_refresh_tokens_for_grant(
        client_id=client_id, redis_client=request.app.state.redis, sub=sub
    )


@router.post(
    "/{user_id}/scope", response_model=ScopeResponse, summary="Add scope to user"
)
async def add_user_scope(
    scope_assign: ScopeAssign,
    user_id: int,
    asession: AsyncSession = Depends(get_async_session),
    claims: dict = require_scopes(required_scopes={"admin"}),
) -> ScopeResponse:
    """Add scopes to a user.

    Parameters
    ----------
    scope_assign
        The scopes to assign to the user.
    user_id
        The ID of the user to whom the scopes will be assigned.
    asession
        The SQLAlchemy async session to use for all database connections.
    claims
        The claims of the authenticated user, used to verify scopes.

    Returns
    -------
    ScopeResponse
        The response containing the user ID and the list of scopes assigned to the user.

    Raises
    ------
    HTTPException
        If the calling user does not have the 'admin' scope.
    """

    user_db = await get_user_by_id(asession=asession, user_id=user_id)

    added_scopes = await add_scopes_to_user(
        asession=asession, scopes=scope_assign.scopes, user_db=user_db
    )

    return ScopeResponse(created_by=claims["sub"], scopes=added_scopes)


@router.delete(
    "/{user_id}/{scope_name}", response_model=ScopeResponse, summary="Delete user scope"
)
async def delete_user_scope(
    scope_name: str,
    user_id: int,
    asession: AsyncSession = Depends(get_async_session),
    claims: dict = require_scopes(required_scopes={"admin"}),
) -> ScopeResponse:
    """Delete a scope from a user.

    The process is as follows:

    1. Check if the scope exists in the database.
    2. Check if the user exists in the database.
    3. Delete the scope from the user.
    4. If the scope is not assigned to the user, raise an error.

    Parameters
    ----------
    scope_name
        The name of the scope to delete from the user.
    user_id
        The ID of the user from whom the scope will be deleted.
    asession
        The SQLAlchemy async session to use for all database connections.
    claims
        The claims of the authenticated user, used to verify scopes.

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
    if not await check_if_scope_exists(
        asession=asession, scope=ScopeCreate(name=scope_name)
    ):
        raise HTTPException(
            detail=f"Scope '{scope_name}' does not exist.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # 2.
    try:
        user_db = await get_user_by_id(asession=asession, user_id=user_id)
    except UserNotFoundError as exc:
        raise HTTPException(
            detail=f"User ID not found: {user_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc

    # 3.
    user_db = await delete_scope_from_user(
        asession=asession, scope_name=scope_name, user_id=user_db.user_id
    )

    # 4.
    if not user_db:
        raise HTTPException(
            detail=f"Scope '{scope_name}' not found for user ID {user_id}.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return ScopeResponse(
        created_by=claims["sub"], scopes=[s.name for s in user_db.scopes]
    )


@router.post(
    "/register-first-user",
    response_model=UserCreateWithRecoveryCodes,
    summary="Register first user",
)
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
        created_by=user_db.username,
        recovery_codes=recovery_codes,
        scopes=added_scopes,
        user_id=user_db.user_id,
        username=user_db.username,
    )


@router.get("/{user_id}", response_model=UserRetrieve, summary="Get user details")
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


@router.delete("/{user_id}", response_model=UserDeleteResponse, summary="Delete user")
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def delete_user(
    calling_user_db: Annotated[UserDB, Depends(get_current_user)],
    request: Request,  # pylint: disable=W0613
    user_id: int,
    asession: AsyncSession = Depends(get_async_session),
) -> UserDeleteResponse:
    """Delete user by ID from database and Redis caches.

    The process is as follows:

    1. Check if the authenticated user has permission to delete the user.
    2. Delete the user from the database.

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

    return UserDeleteResponse(user_id=user_id, username=user_db.username)


@router.put(
    "/reset-password", response_model=UserRetrieve, summary="Reset user password"
)
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
    own password. No login/authorization is required for this endpoint, the caller only
    needs to provide a correct recovery code for their user account.

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

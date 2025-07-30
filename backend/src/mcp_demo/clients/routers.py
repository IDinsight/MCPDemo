"""This module contains FastAPI routers for client endpoints, manging
machine-to-machine service clients.
"""

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
from mcp_demo.clients.models import Oauth2ClientDB
from mcp_demo.clients.schemas import (
    OAuth2ClientCreate,
    OAuth2ClientDeleteResponse,
    OAuth2ClientResetSecret,
    OAuth2ClientResponse,
)
from mcp_demo.clients.utils import (
    Oauth2ClientNotFoundError,
    add_scopes_to_client,
    check_if_client_exists,
    check_if_clients_exist,
    delete_client_from_db,
    delete_scope_from_client,
    get_client_by_id,
    get_current_client,
    reset_client_secret,
    save_client_to_db,
)
from mcp_demo.config import Settings
from mcp_demo.scopes.schemas import ScopeAssign, ScopeCreate, ScopeResponse
from mcp_demo.scopes.utils import add_scope_to_db, check_if_scope_exists
from mcp_demo.utils.database import get_async_session

TAG_METADATA = {"description": "Manages clients", "name": "Client"}
router = APIRouter(prefix="/client", tags=[TAG_METADATA["name"]])

RATE_LIMIT_LOGIN_RATE = Settings.RATE_LIMIT_LOGIN_RATE
REDIS_URL = Settings.REDIS_URL

limiter = Limiter(key_func=get_remote_address, storage_uri=REDIS_URL)


@router.get("/admin-panel", summary="Admin panel")
async def admin_panel(
    claims: dict = require_scopes(required_scopes={"admin"}),
) -> dict[str, str]:
    """Admin panel view for clients with admin scope.

    This endpoint is protected and can only be accessed by clients with the 'admin'
    scope. It returns a simple message indicating that the client has access to the
    admin panel.

    Parameters
    ----------
    claims
        The claims of the authenticated client, used to verify scopes.

    Returns
    -------
    dict[str, str]
        A message indicating access to the admin panel.
    """

    return {"message": "Welcome to the admin panel!", "client_id": claims["sub"]}


@router.post(
    "/",
    response_model=OAuth2ClientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new client",
)
async def add_new_client(
    oauth2_client: OAuth2ClientCreate,
    asession: AsyncSession = Depends(get_async_session),
    claims: dict = require_scopes(required_scopes={"admin"}),
) -> OAuth2ClientResponse:
    """Create a new client with a hashed secret for secure machine-to-machine auth.

    This endpoint is intended for administrative use to provision new service clients.
    Each client is stored with a hashed `secret` and associated scopes.

    NB: This endpoint is currently unguarded---anyone can create a new client.

    The process is as follows:

    1. Validate that the `client_id` does not already exist.
    2. Add each scope in the `scopes` list to the scope database.
    3. Save the new client to the database.

    Parameters
    ----------
    oauth2_client
        The OAuth2 client object to create, containing `client_id`, `secret`, `scopes`,
        and `is_active`.
    asession
        The SQLAlchemy async session to use for all database connections.
    claims
        The claims of the authenticated client, used to verify scopes.

    Returns
    -------
    OAuth2ClientCreate
        The persisted client with `client_id`, `scopes`, and `is_active` fields.

    Raises
    ------
    HTTPException
        If client ID already exists in the database.
    """

    # 1.
    if await check_if_client_exists(asession=asession, client=oauth2_client):
        raise HTTPException(
            detail="Client ID already exists", status_code=status.HTTP_400_BAD_REQUEST
        )

    # 2.
    for scope in oauth2_client.scopes:
        await add_scope_to_db(asession=asession, scope=ScopeCreate(name=scope))

    # 3.
    client_db = await save_client_to_db(asession=asession, client=oauth2_client)

    return OAuth2ClientResponse(
        allowed_code_challenge_methods=client_db.allowed_code_challenge_methods,
        client_id=client_db.client_id,
        created_by=claims["sub"],
        created_datetime_utc=client_db.created_datetime_utc,
        is_active=client_db.is_active,
        pkce_enforced=client_db.pkce_enforced,
        redirect_uris=client_db.redirect_uris,
        scopes=client_db.scopes,
        updated_datetime_utc=client_db.updated_datetime_utc,
    )


@router.post(
    "/{client_id}/scope", response_model=ScopeResponse, summary="Add scope to client"
)
async def add_client_scope(
    client_id: str,
    scope_assign: ScopeAssign,
    asession: AsyncSession = Depends(get_async_session),
    claims: dict = require_scopes(required_scopes={"admin"}),
) -> ScopeResponse:
    """Add scopes to a client.

    Parameters
    ----------
    client_id
        The client ID to which the scopes will be assigned.
    scope_assign
        The scopes to assign to the client.
    asession
        The SQLAlchemy async session to use for all database connections.
    claims
        The claims of the authenticated client, used to verify scopes.

    Returns
    -------
    ScopeResponse
        The response containing the client ID and the list of scopes assigned to the
        client.

    Raises
    ------
    HTTPException
        If the calling client does not have the 'admin' scope.
    """

    client_db = await get_client_by_id(asession=asession, client_id=client_id)

    added_scopes = await add_scopes_to_client(
        asession=asession, client_db=client_db, scopes=scope_assign.scopes
    )

    return ScopeResponse(created_by=claims["sub"], scopes=added_scopes)


@router.delete(
    "/{client_id}/{scope_name}",
    response_model=ScopeResponse,
    summary="Delete client scope",
)
async def delete_client_scope(
    client_id: str,
    scope_name: str,
    asession: AsyncSession = Depends(get_async_session),
    claims: dict = require_scopes(required_scopes={"admin"}),
) -> ScopeResponse:
    """Delete a scope from a client.

    The process is as follows:

    1. Check if the scope exists in the database.
    2. Check if the client exists in the database.
    3. Delete the scope from the client.
    4. If the scope is not assigned to the client, raise an error.

    Parameters
    ----------
    client_id
        The client ID from which the scope will be deleted.
    scope_name
        The name of the scope to delete from the client.
    asession
        The SQLAlchemy async session to use for all database connections.
    claims
        The claims of the authenticated client, used to verify scopes.

    Returns
    -------
    ScopeResponse
        The response containing the client ID and the list of scopes remaining for the
        client.

    Raises
    ------
    HTTPException
        If the calling client does not have the 'admin' scope.
        If the scope does not exist.
        If the scope is not assigned to the client.
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
        client_db = await get_client_by_id(asession=asession, client_id=client_id)
    except Oauth2ClientNotFoundError as exc:
        raise HTTPException(
            detail=f"Client ID not found: {client_id}",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc

    # 3.
    client_db = await delete_scope_from_client(
        asession=asession, client_id=client_db.client_id, scope_name=scope_name
    )

    # 4.
    if not client_db:
        raise HTTPException(
            detail=f"Scope '{scope_name}' not found for client ID {client_id}.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return ScopeResponse(created_by=claims["sub"], scopes=client_db.scopes)


@router.post(
    "/register-first-client",
    response_model=OAuth2ClientResponse,
    summary="Register first client",
)
async def register_first_client(
    oauth2_client: OAuth2ClientCreate,
    asession: AsyncSession = Depends(get_async_session),
) -> OAuth2ClientResponse:
    """Register the first client.

    The process is as follows:

    1. Check if any clients already exist in the database. If so, raise an error.
    2. Ensure the `admin` scope is included in the scopes for the very first client.
    3. Add scopes to the scope database.
    4. Save the first client to the database.

    Parameters
    ----------
    oauth2_client
        The OAuth2 client object to create, containing `client_id`, `secret`, `scopes`,
        and `is_active`.
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    OAuth2ClientResponse
        The persisted client with `client_id`, `scopes`, and `is_active` fields.

    Raises
    ------
    HTTPException
        If clients already exist in the database, indicating that this endpoint should
        not be called again.
    """

    # 1.
    if await check_if_clients_exist(asession=asession):
        raise HTTPException(
            detail="Clients already exist. Cannot register the first client again.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # 2.
    if "admin" not in oauth2_client.scopes:
        oauth2_client.scopes.append("admin")

    # 3.
    for scope in oauth2_client.scopes:
        await add_scope_to_db(asession=asession, scope=ScopeCreate(name=scope))

    # 4.
    client_db = await save_client_to_db(asession=asession, client=oauth2_client)

    return OAuth2ClientResponse(
        allowed_code_challenge_methods=client_db.allowed_code_challenge_methods,
        client_id=client_db.client_id,
        created_by=client_db.client_id,
        created_datetime_utc=client_db.created_datetime_utc,
        is_active=client_db.is_active,
        pkce_enforced=client_db.pkce_enforced,
        redirect_uris=client_db.redirect_uris,
        scopes=client_db.scopes,
        updated_datetime_utc=client_db.updated_datetime_utc,
    )


@router.get(
    "/{client_id}", response_model=OAuth2ClientResponse, summary="Get client details"
)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def get_client(
    calling_client_db: Annotated[Oauth2ClientDB, Depends(get_current_client)],
    request: Request,  # pylint: disable=W0613
    client_id: str,
    asession: AsyncSession = Depends(get_async_session),
) -> OAuth2ClientResponse:
    """Return a client profile iff the caller is the same `sub` *or* carries the `admin`
    scope.

    Parameters
    ----------
    calling_client_db
        The client database object of the authenticated client, used to verify
        permissions.
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    client_id
        The client ID to retrieve.
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    OAuth2ClientResponse
        The persisted client with `client_id`, `scopes`, and `is_active` fields.

    Raises
    ------
    HTTPException
        If the authenticated client does not have permission to view the client profile.
        If the client ID does not exist in the database.
    """

    if calling_client_db.client_id != client_id:
        caller_scopes = calling_client_db.scopes
        if "admin" not in caller_scopes:
            raise HTTPException(
                detail=f"Client ID not found: {client_id}.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

    try:
        target_client_db = await get_client_by_id(
            asession=asession, client_id=client_id
        )
    except Oauth2ClientNotFoundError as exc:
        raise HTTPException(
            detail=f"Client ID not found: {client_id}.",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc

    return OAuth2ClientResponse(
        allowed_code_challenge_methods=target_client_db.allowed_code_challenge_methods,
        client_id=target_client_db.client_id,
        created_datetime_utc=target_client_db.created_datetime_utc,
        is_active=target_client_db.is_active,
        pkce_enforced=target_client_db.pkce_enforced,
        redirect_uris=target_client_db.redirect_uris,
        scopes=target_client_db.scopes,
        updated_datetime_utc=target_client_db.updated_datetime_utc,
    )


@router.delete(
    "/{client_id}", response_model=OAuth2ClientDeleteResponse, summary="Delete client"
)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def delete_client(
    client_id: str,
    request: Request,  # pylint: disable=W0613
    asession: AsyncSession = Depends(get_async_session),
    claims: dict = require_scopes(required_scopes={"admin"}),
) -> OAuth2ClientDeleteResponse:
    """Delete client by ID from database.

    The process is as follows:

    1. Check if the authenticated client has permission to delete the client.
    2. Delete the client from the database.

    NB: To prevent inference attacks, we also raise a 404 error if the calling client
    ID is not the same as the client ID to delete.

    Parameters
    ----------
    client_id
        The client ID to delete.
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    asession
        The SQLAlchemy async session to use for all database connections.
    claims
        The claims of the authenticated client, used to verify scopes.

    Returns
    -------
    OAuth2ClientDeleteResponse
        The client deletion response.

    Raises
    ------
    HTTPException
        If the authenticated client does not have permission to delete the client.
        If the client ID does not exist in the database.
        If there is an error deleting the client from the database.
    """

    # 1.
    try:
        client_db = await get_client_by_id(asession=asession, client_id=client_id)
    except Oauth2ClientNotFoundError as exc:
        raise HTTPException(
            detail=f"Client ID not found: {client_id}.",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from exc

    try:
        await delete_client_from_db(asession=asession, client_id=client_db.client_id)
    except sqlalchemy.exc.IntegrityError as e:
        raise HTTPException(
            detail="Error deleting client.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from e

    return OAuth2ClientDeleteResponse(client_id=client_id, deleted_by=claims["sub"])


@router.put(
    "/reset-secret", response_model=OAuth2ClientResponse, summary="Reset client secret"
)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def reset_secret(
    calling_client_db: Annotated[Oauth2ClientDB, Depends(get_current_client)],
    request: Request,  # pylint: disable=W0613
    client: OAuth2ClientResetSecret,
    asession: AsyncSession = Depends(get_async_session),
) -> OAuth2ClientResponse:
    """Reset client secre.

    NB: When this endpoint is called, the assumption is that the calling client is the
    client that is requesting to reset their own secret. This is because a client's
    secret is universal and belongs to the client. Thus, only a client can reset their
    own secret.

    Parameters
    ----------
    calling_client_db
        The client database object of the authenticated client, used to verify
        permissions.
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    client
        The OAuth2 client object containing the `client_id` and the new `secret`.
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    OAuth2ClientResponse
        The updated client with the new secret, `client_id`, `scopes`, and `is_active`
        fields.

    Raises
    ------
    HTTPException
        If the authenticated client does not have permission to reset the secret.
        If the client does not exist in the database.
    """

    if calling_client_db.client_id != client.client_id:
        caller_scopes = calling_client_db.scopes
        if "admin" not in caller_scopes:
            raise HTTPException(
                detail=f"Client ID not found: {client.client_id}.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

    client_to_update = await check_if_client_exists(asession=asession, client=client)

    if client_to_update is None:
        raise HTTPException(
            detail="Client not found.", status_code=status.HTTP_404_NOT_FOUND
        )

    updated_client_db = await reset_client_secret(
        asession=asession, client=client, client_db=client_to_update
    )
    return OAuth2ClientResponse(
        allowed_code_challenge_methods=updated_client_db.allowed_code_challenge_methods,
        client_id=updated_client_db.client_id,
        created_datetime_utc=updated_client_db.created_datetime_utc,
        is_active=updated_client_db.is_active,
        pkce_enforced=updated_client_db.pkce_enforced,
        redirect_uris=updated_client_db.redirect_uris,
        scopes=updated_client_db.scopes,
        updated_datetime_utc=updated_client_db.updated_datetime_utc,
    )

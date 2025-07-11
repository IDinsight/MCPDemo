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
    OAuth2ClientResponse,
)
from mcp_demo.clients.utils import (
    Oauth2ClientNotFoundError,
    check_if_client_exists,
    delete_client_from_db,
    get_client_by_id,
    get_current_client,
    save_client_to_db,
)
from mcp_demo.config import Settings
from mcp_demo.utils.database import get_async_session

TAG_METADATA = {"description": "Manages clients", "name": "Client"}
router = APIRouter(prefix="/client", tags=[TAG_METADATA["name"]])

limiter = Limiter(key_func=get_remote_address, storage_uri=Settings.REDIS_URL)


@router.get("/admin-panel")
async def admin_view(
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

    return {"message": "Welcome to the admin panel!"}


@router.post(
    "/register",
    response_model=OAuth2ClientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a machine-to-machine service client",
)
@limiter.limit(Settings.RATE_LIMIT_LOGIN_RATE)
async def register(
    oauth2_client: OAuth2ClientCreate,
    request: Request,  # pylint: disable=W0613
    asession: AsyncSession = Depends(get_async_session),
) -> OAuth2ClientResponse:
    """Create a new service client with a hashed secret for secure service-to-service
    auth.

    This endpoint is intended for administrative use to provision new service clients.
    Each client is stored with a hashed `secret` and associated scopes.

    The process is as follows:

    1. Validate that the `client_id` does not already exist.
    2. Save the new client to the database.

    Parameters
    ----------
    oauth2_client
        The OAuth2 client object to create, containing `client_id`, `secret`, `scopes`,
        and `is_active`.
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    OAuth2ClientCreate
        The persisted client with `client_id`, `scopes`, and `is_active` fields.

    Raises
    ------
    HTTPException
        If `client_id` already exists in the database.
    """

    # 1.
    if await check_if_client_exists(asession=asession, client=oauth2_client):
        raise HTTPException(
            detail="Client ID already exists", status_code=status.HTTP_400_BAD_REQUEST
        )

    # 2.
    client_db = await save_client_to_db(asession=asession, client=oauth2_client)

    return OAuth2ClientResponse(
        client_id=client_db.client_id,
        is_active=client_db.is_active,
        scopes=client_db.scopes,
    )


@router.delete("/{client_id}", response_model=OAuth2ClientDeleteResponse)
@limiter.limit(Settings.RATE_LIMIT_LOGIN_RATE)
async def delete_client(
    calling_client_db: Annotated[Oauth2ClientDB, Depends(get_current_client)],
    client_id: str,
    request: Request,  # pylint: disable=W0613
    asession: AsyncSession = Depends(get_async_session),
) -> OAuth2ClientDeleteResponse:
    """Delete client by ID from database.

    The process is as follows:

    1. Check if the authenticated client has permission to delete the client.
    2. Delete the client from the database.

    NB: To prevent inference attacks, we also raise a 404 error if the calling client
    ID is not the same as the client ID to delete.

    Parameters
    ----------
    calling_client_db
        The client database object of the authenticated client, used to verify
        permissions.
    client_id
        The client ID to delete.
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    asession
        The SQLAlchemy async session to use for all database connections.

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
    if (
        calling_client_db.client_id != client_id
        and "admin" not in calling_client_db.scopes
    ):
        raise HTTPException(
            detail=f"Client ID not found: {client_id}.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # 2.
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

    return OAuth2ClientDeleteResponse(client_id=client_id)

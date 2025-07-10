"""This module contains FastAPI routers for client endpoints."""

# Third Party Library
from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis import asyncio as aioredis
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.auth.utils import get_jwt_token
from mcp_demo.clients.schemas import ServiceClientCreate, ServiceClientResponse
from mcp_demo.clients.utils import (
    OAuth2ClientCredentialsRequestForm,
    check_if_client_exists,
    get_service_scopes,
    save_client_to_db,
    verify_client_secret,
)
from mcp_demo.config import Settings
from mcp_demo.schemas import TokenResponse
from mcp_demo.utils.database import get_async_session
from mcp_demo.utils.general import get_redis_client

TAG_METADATA = {"description": "Handles client authentication", "name": "Client"}
router = APIRouter(prefix="/client", tags=[TAG_METADATA["name"]])

limiter = Limiter(key_func=get_remote_address, storage_uri=Settings.REDIS_URL)


@router.post(
    "/get-client-token", summary="Machine-to-machine token (Client-Credentials)"
)
async def get_client_token(
    asession: AsyncSession = Depends(get_async_session),
    form: OAuth2ClientCredentialsRequestForm = Depends(),
    redis_client: aioredis.Redis = Depends(get_redis_client),
) -> TokenResponse:
    """Exchange `client_id` and  `client_secret` for an RS256 JWT scoped to the service.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    form
        The OAuth2 client credentials request form containing the `client_id` and
        `client_secret`.
    redis_client
        The Redis client to use for caching the access token.

    Returns
    -------
    TokenResponse
        The token response containing the access token, token type, and expiration time.
    """

    client_db = await verify_client_secret(
        asession=asession, client_id=form.client_id, client_secret=form.client_secret
    )
    scopes = get_service_scopes(client_db=client_db)
    access_token = await get_jwt_token(
        redis_client=redis_client,
        scopes=scopes,
        sub=client_db.client_id,
    )

    return TokenResponse(
        access_token=access_token,
        token_type="Bearer",
        expires_in=Settings.AUTH_TOKEN_TTL,
    )


@router.post(
    "/create-service-client",
    response_model=ServiceClientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a machine-to-machine service client",
)
@limiter.limit(Settings.RATE_LIMIT_LOGIN_RATE)
async def create_service_client(
    request: Request,  # pylint: disable=W0613
    service_client: ServiceClientCreate,
    asession: AsyncSession = Depends(get_async_session),
) -> ServiceClientResponse:
    """Create a new service client with a hashed secret.

    The process is as follows:

    1. Check if the `client_id` already exists in the database.
    2. Hash the `secret`.
    3. Save the new service client to the database.

    Parameters
    ----------
    request
        The FastAPI request object, used to access the requested scopes. This is needed
        for SlowAPI rate limiting.
    service_client
        The service client object to create, containing `client_id`, `secret`, `scopes`,
        and `is_active`.
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    ServiceClientResponse
        The service client object with the hashed secret and other details.

    Raises
    ------
    HTTPException
        If `client_id` already exists in the database.
    """

    # 1.
    service_client_db = await check_if_client_exists(
        asession=asession, client=service_client
    )

    if service_client_db is not None:
        raise HTTPException(
            detail="Client ID already exists", status_code=status.HTTP_409_CONFLICT
        )

    # 2.
    client_db = await save_client_to_db(asession=asession, client=service_client)

    return ServiceClientResponse(
        client_id=client_db.client_id,
        is_active=client_db.is_active,
        scopes=client_db.scopes,
    )

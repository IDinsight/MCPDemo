"""This module contains FastAPI routers for scope endpoints."""

# Third Party Library
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.ext.asyncio import AsyncSession

# Package Library
from mcp_demo.auth.utils import require_scopes
from mcp_demo.config import Settings
from mcp_demo.scopes.schemas import ScopeCreate, ScopeDeleteResponse, ScopeResponse
from mcp_demo.scopes.utils import add_scope_to_db, delete_scope_from_db
from mcp_demo.utils.database import get_async_session

TAG_METADATA = {"description": "Manages scopes", "name": "Scope"}
router = APIRouter(prefix="/scope", tags=[TAG_METADATA["name"]])

RATE_LIMIT_LOGIN_RATE = Settings.RATE_LIMIT_LOGIN_RATE
REDIS_URL = Settings.REDIS_URL

limiter = Limiter(key_func=get_remote_address, storage_uri=REDIS_URL)


@router.post("/", response_model=ScopeResponse, summary="Create a new global scope")
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def create_global_scope(
    request: Request,  # pylint: disable=W0613
    scope_create: ScopeCreate,
    asession: AsyncSession = Depends(get_async_session),
    claims: dict = require_scopes(required_scopes={"admin"}),
) -> ScopeResponse:
    """Add a new global OAuth2 scope.

    Parameters
    ----------
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    scope_create
        The scope to create, which must be a valid scope name.
    asession
        The SQLAlchemy async session to use for all database connections.
    claims
        The claims of the authenticated user, used to verify scopes.

    Returns
    -------
    ScopeResponse
        The response containing the created scope name.
    """

    scope_db = await add_scope_to_db(asession=asession, scope=scope_create)
    return ScopeResponse(
        created_by=int(claims["sub"]), scopes=[scope_db.name], user_id=claims["sub"]
    )


@router.delete(
    "/{scope_name}",
    description="Admin-only. Use force=true to delete even if users still hold the scope.",
    response_model=ScopeDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a global scope",
)
@limiter.limit(RATE_LIMIT_LOGIN_RATE)
async def delete_global_scope(
    request: Request,  # pylint: disable=W0613
    scope_name: str,
    asession: AsyncSession = Depends(get_async_session),
    claims: dict = require_scopes(required_scopes={"admin"}),
    force: bool = Query(False, description="Force deletion even if linked to users."),
) -> ScopeDeleteResponse:
    """Remove a global OAuth2 scope (admin-only).

    Parameters
    ----------
    request
        The FastAPI request object. This is needed for SlowAPI rate limiting.
    scope_name
        The name of the scope to delete.
    asession
        The SQLAlchemy async session to use for all database connections.
    claims
        The claims of the authenticated user, used to verify scopes.
    force
        If True, delete the scope even if it is still assigned to users (association
        rows).

    Returns
    -------
    ScopeDeleteResponse
        The response containing the scope name and whether it was removed.
    """

    deleted, existed = await delete_scope_from_db(
        asession=asession, force=force, scope_name=scope_name
    )

    if not existed:
        raise HTTPException(status_code=status.HTTP_204_NO_CONTENT)

    return ScopeDeleteResponse(name=scope_name, removed=deleted, user_id=claims["sub"])

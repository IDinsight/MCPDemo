"""This module contains utilities for scopes."""

# Standard Library
from typing import Sequence

# Third Party Library
from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Package Library
from mcp_demo.config import Settings
from mcp_demo.scopes.models import ScopeDB, user_scope_table
from mcp_demo.scopes.schemas import Scope, ScopeCreate

AUTH_ALLOWED_SCOPES = Settings.AUTH_ALLOWED_SCOPES


async def add_scope_to_db(*, asession: AsyncSession, scope: ScopeCreate) -> ScopeDB:
    """Add a new (global) scope to the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    scope
        The scope to add to the database.

    Returns
    -------
    ScopeDB
        The newly created `ScopeDB` object.
    """

    name = scope.name.strip()

    if name not in AUTH_ALLOWED_SCOPES:
        raise HTTPException(
            detail=f"Invalid scope name: '{name}'.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    existing_scope_db = await check_if_scope_exists(asession=asession, scope=scope)
    if existing_scope_db is not None:
        return existing_scope_db

    scope_db = ScopeDB(name=name)
    asession.add(scope_db)
    await asession.commit()
    await asession.refresh(scope_db)

    return scope_db


async def check_if_scope_exists(
    *, asession: AsyncSession, scope: Scope | ScopeCreate
) -> ScopeDB | None:
    """Check if a scope exists in the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    scope
        The scope to check for existence in the database.

    Returns
    -------
    ScopeDB | None
        The `ScopeDB` object if it exists, otherwise `None`.
    """

    stmt = select(ScopeDB).where(ScopeDB.name == scope.name)
    result = await asession.execute(stmt)
    scope_db = result.scalar_one_or_none()
    return scope_db


async def delete_scope_from_db(
    *, asession: AsyncSession, force: bool = False, scope_name: str
) -> tuple[bool, bool]:
    """Remove a global scope from the database.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    force
        If True, delete even if the scope is still assigned to users (association rows
        are deleted first). If False and the scope is referenced and we raise 409
        Conflict.
    scope_name
        The name of the scope to delete.

    Returns
    -------
    tuple[bool, bool]
        A tuple where the first element indicates if the scope was deleted, and the
        second element indicates if the scope existed before deletion.
            - First element: True if deleted, False if not found.
            - Second element: True if scope existed, False if it did not.
    """

    scope_db = await asession.get(ScopeDB, scope_name)
    if scope_db is None:
        return False, False  # Nothing to delete

    # Check if any user has this scope.
    linked = await asession.scalar(
        select(user_scope_table.c.user_id).where(
            user_scope_table.c.scope_name == scope_name
        )
    )
    if linked is not None and not force:
        raise HTTPException(
            detail=f"Scope '{scope_name}' is still assigned to users. Set force=true to override.",
            status_code=status.HTTP_409_CONFLICT,
        )

    # Cascade delete: remove association rows first (if any), then the scope row.
    await asession.execute(
        delete(user_scope_table).where(user_scope_table.c.scope_name == scope_name)
    )
    await asession.delete(scope_db)
    await asession.flush()

    return True, True


async def get_all_scopes_with_users(*, asession: AsyncSession) -> Sequence[ScopeDB]:
    """Retrieve all scopes and their associated user IDs.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.

    Returns
    -------
    Sequence[ScopeDB]
        A sequence of `ScopeDB` objects, each containing the scope name and a list of
        associated user IDs.
    """

    result = await asession.execute(
        select(ScopeDB).options(selectinload(ScopeDB.users))
    )
    scope_dbs = result.scalars().all()

    return scope_dbs

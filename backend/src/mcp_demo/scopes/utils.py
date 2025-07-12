"""This module contains utilities for scopes."""

# Third Party Library
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Package Library
from mcp_demo.users.models import UserDB


async def get_user_scopes_by_id(*, asession: AsyncSession, user_id: int) -> set[str]:
    """Get the scopes of a user.

    Parameters
    ----------
    asession
        The SQLAlchemy async session to use for all database connections.
    user_id
        The ID of the user whose scopes are to be retrieved.

    Returns
    -------
    set[str]
        A set of scope names associated with the user.
    """

    stmt = (
        select(UserDB)
        .options(selectinload(UserDB.scopes))
        .where(UserDB.user_id == user_id)
    )
    result = await asession.execute(stmt)
    user = result.scalar_one()
    return {s.name for s in user.scopes}

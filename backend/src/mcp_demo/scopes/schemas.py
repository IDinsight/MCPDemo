"""This module contains Pydantic models for scopes."""

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field


# Scopes.
class Scope(BaseModel):
    """Pydantic model for a scope."""

    name: str = Field(..., examples=["admin", "read", "write"])

    model_config = ConfigDict(from_attributes=True)


class ScopeAssign(BaseModel):
    """Pydantic model for assigning scopes to a user."""

    scopes: list[str] = Field(..., examples=[["admin", "read", "write"]])

    model_config = ConfigDict(from_attributes=True)


class ScopeCreate(Scope):
    """Pydantic model for creating a new scope."""


class ScopeDeleteResponse(Scope):
    """Pydantic model for scope deletion response."""

    removed: bool


class ScopeResponse(ScopeAssign):
    """Pydantic model for the response of scope assignment."""

    user_id: int

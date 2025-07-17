"""This module contains FastAPI routers for admin endpoints."""

# Third Party Library
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

TAG_METADATA = {
    "description": "Application healthcheck.",
    "name": "Admin",
}
router = APIRouter(prefix="/admin", tags=[TAG_METADATA["name"]])


@router.get("/health")
async def healthcheck() -> JSONResponse:
    """Healthcheck endpoint that checks connection to the database.

    Returns
    -------
    JSONResponse
        A JSON response with the status of the database connection.
    """

    return JSONResponse(content={"status": "ok"}, status_code=status.HTTP_200_OK)

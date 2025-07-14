"""This module contains FastAPI utilities."""

# Standard Library
import os

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable

# Third Party Library
import sentry_sdk

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from loguru import logger
from prometheus_client import CollectorRegistry, make_asgi_app, multiprocess
from redis import asyncio as aioredis
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

# Package Library
from mcp_demo import auth, clients, scopes, users
from mcp_demo.config import Settings
from mcp_demo.middlewares.fastapi_ import AuditMiddleware, SecurityHeadersMiddleware
from mcp_demo.middlewares.prometheus_ import PrometheusMiddleware
from mcp_demo.utils.general import make_dir

DOMAIN_NAME = os.getenv("DOMAIN_NAME", "")
REDIS_URL = Settings.REDIS_URL
SENTRY_DSN = Settings.SENTRY_DSN
SENTRY_TRACES_SAMPLE_RATE = Settings.SENTRY_TRACES_SAMPLE_RATE

limiter = Limiter(key_func=get_remote_address, storage_uri=Settings.REDIS_URL)


def create_fastapi_app() -> FastAPI:
    """Create the FastAPI application for the backend.

    1. Create a FastAPI application instance and attach the MCP server instance to its
        state.
    2. Include routers for all the endpoints.
    3. Add exception handlers.
    4. Add middlewares.
    5. Mount the metrics app on /metrics as an independent application.
    6. Initialize Sentry for error tracking if the SENTRY_DSN is provided.

    Returns
    -------
    FastAPI
        The FastAPI application instance.
    """

    # 1.
    app = FastAPI(
        debug=True,
        lifespan=lifespan_fastapi,
        openapi_tags=[
            auth.TAG_METADATA,
            clients.TAG_METADATA,
            scopes.TAG_METADATA,
            users.TAG_METADATA,
        ],
        title="MCP Demo APIs",
    )

    # 2.
    app.include_router(auth.routers.router)
    app.include_router(clients.routers.router)
    app.include_router(scopes.routers.router)
    app.include_router(users.routers.router)

    # 3.
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

    # 4.
    origins = [
        f"http://{DOMAIN_NAME}",
        f"http://{DOMAIN_NAME}:3000",
        f"https://{DOMAIN_NAME}",
    ]
    app.add_middleware(AuditMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_headers=["*"],
        allow_methods=["*"],
        allow_origins=origins,
    )
    app.add_middleware(PrometheusMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(SlowAPIMiddleware)

    # 5.
    metrics_app = create_metrics_app()
    app.mount("/metrics", metrics_app)

    # 6.
    if not SENTRY_DSN or SENTRY_DSN == "" or SENTRY_DSN == "https://...":
        logger.log("ATTN", "No SENTRY_DSN provided. Sentry is disabled.")
    else:
        sentry_sdk.init(
            _experiments={"continuous_profiling_auto_start": True},
            dsn=SENTRY_DSN,
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
        )

    return app


def create_metrics_app() -> Callable:
    """Create prometheus metrics app

    Returns
    -------
    Callable
        The metrics app.
    """

    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return make_asgi_app(registry=registry)


@asynccontextmanager
async def lifespan_fastapi(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan events for the FastAPI application.

    The process is as follows:

    1. Initialize Redis client for the FastAPI application.
    2. Set up the rate limiter using Redis as the storage.
    3. Yield control to the FastAPI application.
    4. Close the Redis connection when the FastAPI application finishes.

    Parameters
    ----------
    app
        The FastAPI application instance.

    Yields
    ------
    AsyncIterator[None]
        A context manager that provides control to the FastAPI application.
    """

    logger.info("Starting FastAPI application...")

    make_dir(Path(os.getenv("PATHS_PROJECT_DIR", "/tmp")) / "logs" / "chat_sessions")

    try:
        # 1.
        logger.info("Initializing Redis client...")
        app.state.redis = await aioredis.from_url(f"{REDIS_URL}", decode_responses=True)
        logger.success("Redis connection established!")

        # 2.
        app.state.limiter = limiter

        # 3.
        logger.log("CELEBRATE", "Ready to roll! 🚀")

        yield
    finally:
        # 4.
        logger.info("Closing Redis connection...")
        await app.state.redis.aclose()
        logger.success("Redis connection closed!")

        logger.success("FastAPI application finished!")


async def rate_limit_handler(request: Request, exc: Exception) -> Response:
    """Handle rate limit exceptions. This function is used to satisfy mypy.

    See: https://github.com/laurentS/slowapi/issues/188

    Parameters
    ----------
    request
        The FastAPI request object.
    exc
        The exception that was raised, expected to be a RateLimitExceeded exception.

    Returns
    -------
    Response
        The response to be returned, typically a JSON response with a 429 status code.

    Raises
    ------
    Exception
        If the exception is not a `RateLimitExceeded`, it will be raised to let FastAPI
        handle it with its default handlers.
    """

    if isinstance(exc, RateLimitExceeded):
        # Delegate to SlowAPI.
        return _rate_limit_exceeded_handler(request, exc)

    # Let FastAPI fall back to its default handlers for anything else.
    raise exc

"""This module contains the FastAPI application for the backend."""

# Standard Library
import os

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Callable

# Third Party Library
import sentry_sdk

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CollectorRegistry, make_asgi_app, multiprocess
from redis import asyncio as aioredis

# Package Library
from mcp_demo import users
from mcp_demo.config import Settings
from mcp_demo.prometheus_middleware import PrometheusMiddleware
from mcp_demo.utils.general import make_dir
from mcp_demo.utils.logging_ import initialize_logger

DOMAIN_NAME = os.getenv("DOMAIN_NAME", "")
LOGGING_LEVEL = Settings.LOGGING_LOG_LEVEL
REDIS_URL = Settings.REDIS_URL
SENTRY_DSN = Settings.SENTRY_DSN
SENTRY_TRACES_SAMPLE_RATE = Settings.SENTRY_TRACES_SAMPLE_RATE

# Only need to initialize loguru once for the entire backend!
logger = initialize_logger(logging_level=LOGGING_LEVEL)


def create_fastapi_app() -> FastAPI:
    """Create the FastAPI application for the backend. The process is as follows:

    1. Include routers for all the endpoints.
    2. Add CORS middleware for cross-origin requests.
    3. Add Prometheus middleware for metrics.
    4. Mount the metrics app on /metrics as an independent application.

    Returns
    -------
    FastAPI
        The application instance.
    """

    app = FastAPI(
        debug=True,
        lifespan=lifespan,
        openapi_tags=[users.TAG_METADATA],
        title="MCP Demo APIs",
    )

    # 1.
    app.include_router(users.routers.router)

    origins = [
        f"http://{DOMAIN_NAME}",
        f"http://{DOMAIN_NAME}:3000",
        f"https://{DOMAIN_NAME}",
    ]

    # 2.
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_headers=["*"],
        allow_methods=["*"],
        allow_origins=origins,
    )

    # 3.
    app.add_middleware(PrometheusMiddleware)
    metrics_app = create_metrics_app()

    # 4.
    app.mount("/metrics", metrics_app)

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
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Lifespan events for the FastAPI application.

    The process is as follows:

    1. Connect to Redis.
    2. Yield control to the application.
    3. Close the Redis connection when the application finishes.

    Parameters
    ----------
    app
        The application instance.
    """

    logger.info("Application started!")

    make_dir(Path(os.getenv("PATHS_PROJECT_DIR", "/tmp")) / "logs" / "chat_sessions")

    # 1.
    logger.info("Initializing Redis client...")
    app.state.redis = await aioredis.from_url(f"{REDIS_URL}", decode_responses=True)
    logger.success("Redis connection established!")

    # 2.
    logger.log("CELEBRATE", "Ready to roll! 🚀")

    yield

    # 3.
    logger.info("Closing Redis connection...")
    await app.state.redis.close()
    logger.success("Redis connection closed!")

    logger.success("Application finished!")

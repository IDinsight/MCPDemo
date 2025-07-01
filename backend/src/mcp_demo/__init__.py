"""This module contains the FastAPI application for the backend."""

# Standard Library
import os

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable

# Third Party Library
import sentry_sdk

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastmcp import FastMCP
from prometheus_client import CollectorRegistry, make_asgi_app, multiprocess
from redis import asyncio as aioredis
from starlette.applications import Starlette

# Package Library
from mcp_demo import users
from mcp_demo.config import Settings
from mcp_demo.prometheus_middleware import PrometheusMiddleware
from mcp_demo.tools import basic_tools
from mcp_demo.utils.general import make_dir
from mcp_demo.utils.logging_ import initialize_logger

DOMAIN_NAME = os.getenv("DOMAIN_NAME", "")
LOGGING_LEVEL = Settings.LOGGING_LOG_LEVEL
REDIS_URL = Settings.REDIS_URL
SENTRY_DSN = Settings.SENTRY_DSN
SENTRY_TRACES_SAMPLE_RATE = Settings.SENTRY_TRACES_SAMPLE_RATE

# Only need to initialize loguru once for the entire backend!
logger = initialize_logger(logging_level=LOGGING_LEVEL)


@dataclass
class MCPServerContext:
    """Context for the MCP server application."""

    runtime_context: str

    some_text: str = "This is a demo MCP server context."


def create_fastapi_app() -> FastAPI:
    """Create the FastAPI application for the backend.

    1. Create a FastAPI application instance and attach the MCP server instance to its
        state.
    2. Include routers for all the endpoints.
    3. Add CORS middleware for cross-origin requests.
    4. Add Prometheus middleware for metrics.
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
        openapi_tags=[users.TAG_METADATA],
        title="MCP Demo APIs",
    )

    # 2.
    app.include_router(users.routers.router)

    # 3.
    origins = [
        f"http://{DOMAIN_NAME}",
        f"http://{DOMAIN_NAME}:3000",
        f"https://{DOMAIN_NAME}",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_headers=["*"],
        allow_methods=["*"],
        allow_origins=origins,
    )

    # 4.
    app.add_middleware(PrometheusMiddleware)

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


def create_mcp_server_app() -> Starlette:
    """Create the MCP server application for the backend.

    The process is as follows:

    1. Create an MCP server application instance.
    2. Register tools with the MCP server.

    Returns
    -------
    Starlette
        The MCP server application instance.
    """

    # 1.
    mcp = FastMCP(
        instructions="This is a demo MCP server. Use the tools to interact with it.",
        lifespan=lifespan_mcp,
        name="MCP Demo",
        on_duplicate_prompts="replace",
        on_duplicate_resources="warn",
        on_duplicate_tools="error",
    )
    app = mcp.http_app(path="/mcp")

    # 2.
    logger.info("Registering tools with the MCP server...")
    basic_tools.register_tools(mcp=mcp)
    mcp.tool()(basic_tools.calculate_bmi)
    mcp.tool()(basic_tools.get_weather)
    logger.success("Finished registering tools with the MCP server!")

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

    1. Connect to Redis.
    2. Yield control to the FastAPI application.
    3. Close the Redis connection when the application finishes.

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
        logger.log("CELEBRATE", "Ready to roll! 🚀")

        yield
    finally:
        # 3.
        logger.info("Closing Redis connection...")
        await app.state.redis.aclose()
        logger.success("Redis connection closed!")

        logger.success("FastAPI application finished!")


@asynccontextmanager
async def lifespan_mcp(server: FastMCP) -> AsyncIterator[MCPServerContext]:
    """Lifespan events for the MCP server application.

    The process is as follows:

    1. Initialize the MCP server tools.
    2. Yield control to the MCP server application.
    3. Close the MCP server when the application finishes.

    Parameters
    ----------
    server
        The MCP server instance.

    Yields
    ------
    AsyncIterator[MCPServerContext]
        A context manager that provides control to the MCP server application.
    """

    logger.info("Starting MCP server application...")

    try:
        # 1.
        server_tools = await server.get_tools()
        logger.debug(f"{list(server_tools.keys()) = }")

        # 2.
        logger.log("CELEBRATE", "Ready to roll! 🚀")

        yield MCPServerContext(runtime_context="new context")
    finally:
        # 3.
        logger.success("MCP server application finished!")

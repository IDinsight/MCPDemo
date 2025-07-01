"""This module contains the FastAPI application for the backend."""

# pylint: disable=W0603
# Standard Library
import os

from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import import_module
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
from mcp_demo.utils.general import make_dir, yaml_serializer
from mcp_demo.utils.logging_ import initialize_logger

DOMAIN_NAME = os.getenv("DOMAIN_NAME", "")
FASTMCP_MOUNT_PATH = Settings.FASTMCP_MOUNT_PATH
LOGGING_LEVEL = Settings.LOGGING_LOG_LEVEL
REDIS_URL = Settings.REDIS_URL
SENTRY_DSN = Settings.SENTRY_DSN
SENTRY_TRACES_SAMPLE_RATE = Settings.SENTRY_TRACES_SAMPLE_RATE

MCP_APP: Starlette | None = None
MCP_SERVER: FastMCP | None = None

# Only need to initialize loguru once for the entire backend!
logger = initialize_logger(logging_level=LOGGING_LEVEL)


@dataclass
class MCPServerContext:
    """Context for the MCP server application."""

    redis_client: aioredis.Redis
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
    2. Register server components such as tools, resources, prompts, etc. with the MCP
        server.

    Returns
    -------
    Starlette
        The MCP server application instance.
    """

    global MCP_APP, MCP_SERVER

    if not (MCP_APP and MCP_SERVER):
        # 1.
        MCP_SERVER = FastMCP(
            exclude_tags={"deprecated", "internal"},  # Hide these tagged components
            instructions="This is a demo MCP server. Use the tools to interact with it.",
            lifespan=lifespan_mcp,
            mask_error_details=True,  # Mask error details in responses and defer to ToolError for security reasons
            name="MCP Demo",
            on_duplicate_prompts="error",
            on_duplicate_resources="error",
            on_duplicate_tools="error",
            tool_serializer=yaml_serializer,
        )
        MCP_APP = MCP_SERVER.http_app(path=f"/{FASTMCP_MOUNT_PATH}")

    # 2.
    register_server_components()

    return MCP_APP


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
    2. Yield control to the FastAPI application.
    3. Close the Redis connection when the FastAPI application finishes.

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

    1. List the MCP server tools (for demonstration purposes).
    2. Initialize Redis client for the MCP server.
    3. Yield control to the MCP server application.
    4. Close the Redis connection when the MCP server application finishes.
    5. Close the MCP server when the application finishes.

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

    redis_client: aioredis.Redis | None = None

    try:
        # 1.
        server_tools = await server.get_tools()
        server_tool_names = list(server_tools.keys())
        logger.info(f"Available tools server-side: {server_tool_names}")

        server_resources = await server.get_resources()
        server_resource_names = list(server_resources.keys())
        logger.info(f"Available resources server-side: {server_resource_names}")

        server_resource_tempaltes = await server.get_resource_templates()
        server_resource_template_names = list(server_resource_tempaltes.keys())
        logger.info(
            f"Available resource templates server-side: {server_resource_template_names}"
        )

        server_prompts = await server.get_prompts()
        server_prompt_names = list(server_prompts.keys())
        logger.info(f"Available prompts server-side: {server_prompt_names}")

        # 2.
        logger.info("Initializing Redis client...")
        redis_client = await aioredis.from_url(f"{REDIS_URL}", decode_responses=True)
        assert isinstance(redis_client, aioredis.Redis)
        logger.success("Redis connection established!")

        # 3.
        logger.log("CELEBRATE", "Ready to roll! 🚀")

        yield MCPServerContext(redis_client=redis_client, runtime_context="new context")
    finally:
        if isinstance(redis_client, aioredis.Redis):
            # 4.
            logger.info("Closing Redis connection...")
            await redis_client.aclose()
            logger.success("Redis connection closed!")

        # 5.
        logger.success("MCP server application finished!")


def register_server_components() -> None:
    """Register service components such as tools, resources, prompts, etc. with the MCP
    server. This is accomplished by importing the relevant modules, thereby loading any
    `@mcp` decorators within those modules.

    NB: The use of `import_module` allows for dynamic loading of modules, which avoids
    circular import issues that can arise with direct imports.

    NB: This function demonstrates how to manually register prompts with the MCP server
    using the `prompt()` method. In this scenario, there is a circular import issue if
    the `MCP_SERVER` object is imported directly in the `prompts` module. To get around
    this issue, we specify in the `__init__.py` of the `prompts` module the set of
    prompts to be registered, and then we import them here for registration. Also note
    that due to the way Python and Uvicorn initialize, the initialization is called
    twice. For module-level imports, this is not an issue---in fact, it serves as a
    good sanity check against your code! However, for the `prompt()` method, `FastMCP`
    will raise a `ValueError` if the prompt is already registered. Therefore, we catch
    the `ValueError` and log a message indicating that the prompt is already registered.

    NB: This repo is **purposely** architected to set up a circular import issue---this
    occurs because the MCP server instance is initialized in the same module as the
    FastAPI instance (i.e., this module). Although it's relatively straightforward to
    avoid the circular import issue by moving the MCP server initialization to a
    separate module, we want to demonstrate how to handle such issues in a real-world
    scenario, especially if one is integrating with an existing codebase.
    """

    logger.info("Registering components with the MCP server...")

    for attr_path in [
        "mcp_demo.resources.basic_resources",
        "mcp_demo.tools.basic_tools",
    ]:
        logger.log("ATTN", f"Importing path for registration: {attr_path}")
        import_module(attr_path)

    assert isinstance(MCP_SERVER, FastMCP)
    prompts_module = import_module("mcp_demo.prompts")
    for prompt_fn in prompts_module.__all__:
        logger.log("ATTN", f"Importing path for registration: {prompt_fn}")
        try:
            MCP_SERVER.prompt(getattr(prompts_module, prompt_fn))
        except ValueError:
            logger.info(
                f"Prompt {prompt_fn} already registered with the MCP server. Skipping."
            )

    logger.success("Successfully all registered components with the MCP server!")

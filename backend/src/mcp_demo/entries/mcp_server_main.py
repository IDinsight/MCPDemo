"""This module contains the main entry point for the main MCP server application.

From the backend directory of this project, this entry point can be invoked from the
command line via:

python -m src.mcp_demo.entries.mcp_server_main

or

python src/mcp_demo/entries/mcp_server_main.py
"""

# Standard Library
import os
import sys

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

# Third Party Library
import typer
import uvicorn

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import (
    ErrorHandlingMiddleware,
    RetryMiddleware,
)
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware
from fastmcp.server.middleware.timing import DetailedTimingMiddleware
from loguru import logger
from redis import asyncio as aioredis

# Append the framework path. NB: This is required if this entry point is invoked from
# the command line. However, it is not necessary if it is imported from a pip install.
if __name__ == "__main__":
    PACKAGE_PATH = Path(__file__).resolve().parents[2]
    if PACKAGE_PATH not in sys.path:
        print(f"Appending '{PACKAGE_PATH}' to system path...")
        sys.path.append(str(PACKAGE_PATH))

# Package Library
from mcp_demo.config import Settings
from mcp_demo.middlewares.mcp_server import TagBasedMiddleware
from mcp_demo.utils.mcp_server import create_mcp_server_app, get_bearer_auth_provider

assert (
    sys.version_info.major >= 3 and sys.version_info.minor >= 11
), "MCP Demo requires at least Python 3.11!"


FASTMCP_MOUNT_PATH = Settings.FASTMCP_MOUNT_PATH
REDIS_URL = Settings.REDIS_URL

# Instantiate typer apps for the command line interface.
cli = typer.Typer()


@dataclass
class ChatMCPServerContext:
    """Context for the chat MCP server application."""

    redis_client: aioredis.Redis


@dataclass
class MainMCPServerContext:
    """Context for the main MCP server application."""

    runtime_context: str

    some_text: str = "This is the context for the main MCP server."


@asynccontextmanager
async def lifespan_chat_server(server: FastMCP) -> AsyncIterator[ChatMCPServerContext]:
    """Lifespan events for the chat MCP server application.

    The process is as follows:

    1. List the chat MCP server tools (for demonstration purposes).
    2. Initialize Redis client for the chat MCP server.
    3. Yield control to the chat MCP server application.
    4. Close the Redis connection when the chat MCP server application finishes.
    5. Perform any necessary cleanup when the chat MCP server application finishes.

    Parameters
    ----------
    server
        The chat MCP server instance.

    Yields
    ------
    AsyncIterator[ChatMCPServerContext]
        A context manager that provides control to the chat MCP server application.
    """

    logger.info("Starting chat MCP server application...")

    redis_client: aioredis.Redis | None = None

    try:
        # 1.
        server_tools = await server.get_tools()
        server_tool_names = list(server_tools.keys())
        logger.info(f"Available tools chat server-side: {server_tool_names}")

        server_resources = await server.get_resources()
        server_resource_names = list(server_resources.keys())
        logger.info(f"Available resources chat server-side: {server_resource_names}")

        server_resource_templates = await server.get_resource_templates()
        server_resource_template_names = list(server_resource_templates.keys())
        logger.info(
            f"Available resource templates chat server-side: "
            f"{server_resource_template_names}"
        )

        server_prompts = await server.get_prompts()
        server_prompt_names = list(server_prompts.keys())
        logger.info(f"Available prompts chat server-side: {server_prompt_names}")

        # 2.
        logger.info("Initializing Redis client...")
        redis_client = await aioredis.from_url(f"{REDIS_URL}", decode_responses=True)
        assert isinstance(redis_client, aioredis.Redis)
        logger.success("Redis connection established!")

        # 3.
        logger.log("CELEBRATE", "Ready to roll! 🚀")

        yield ChatMCPServerContext(redis_client=redis_client)
    finally:
        if isinstance(redis_client, aioredis.Redis):
            # 4.
            logger.info("Closing Redis connection...")
            await redis_client.aclose()
            logger.success("Redis connection closed!")

        # 5.
        logger.success("Chat MCP server application finished!")


@asynccontextmanager
async def lifespan_main_server(server: FastMCP) -> AsyncIterator[MainMCPServerContext]:
    """Lifespan events for the main MCP server application.

    The process is as follows:

    1. List the main MCP server components (for demonstration purposes).
    2. Yield control to the main MCP server application.
    3. Perform any necessary cleanup when the main MCP server application finishes.

    Parameters
    ----------
    server
        The main MCP server instance.

    Yields
    ------
    AsyncIterator[MainMCPServerContext]
        A context manager that provides control to the main MCP server application.
    """

    logger.info("Starting main MCP server application...")

    try:
        # 1.
        server_tools = await server.get_tools()
        server_tool_names = list(server_tools.keys())
        logger.info(f"Available tools main server-side: {server_tool_names}")

        server_resources = await server.get_resources()
        server_resource_names = list(server_resources.keys())
        logger.info(f"Available resources main server-side: {server_resource_names}")

        server_resource_templates = await server.get_resource_templates()
        server_resource_template_names = list(server_resource_templates.keys())
        logger.info(
            f"Available resource templates main server-side: "
            f"{server_resource_template_names}"
        )

        server_prompts = await server.get_prompts()
        server_prompt_names = list(server_prompts.keys())
        logger.info(f"Available prompts main server-side: {server_prompt_names}")

        # 2.
        logger.log("CELEBRATE", "Ready to roll! 🚀")

        yield MainMCPServerContext(runtime_context="new context")
    finally:
        # 3.
        logger.success("Main MCP server application finished!")


# Create the main MCP server application instance.
app_main, mcp_main = create_mcp_server_app(
    auth=get_bearer_auth_provider(),  # Use BearerAuthProvider for authentication
    exclude_tags={"deprecated", "internal"},  # Hide these tagged components
    instructions="This is the main MCP server.",
    lifespan=lifespan_main_server,
    mask_error_details=False,
    mcp_app_mount_path=FASTMCP_MOUNT_PATH,
    middleware=[
        ErrorHandlingMiddleware(include_traceback=True, transform_errors=True),
        RetryMiddleware(
            max_retries=3, retry_exceptions=(ConnectionError, TimeoutError)
        ),
        SlidingWindowRateLimitingMiddleware(max_requests=100, window_minutes=1),
        DetailedTimingMiddleware(),
        LoggingMiddleware(include_payloads=True, max_payload_length=1000),
        TagBasedMiddleware(),
    ],
    on_duplicate_prompts="error",
    on_duplicate_resources="error",
    on_duplicate_tools="error",
    register_modules={
        "mcp_demo.prompts.base",
        "mcp_demo.resources.basic_resources",
        "mcp_demo.tools.basic_tools",
    },
    server_name="Main Server",
)

# Create the chat MCP server application instance.
app_chat, mcp_chat = create_mcp_server_app(
    auth=get_bearer_auth_provider(),  # Use BearerAuthProvider for authentication
    exclude_tags={"deprecated", "internal"},  # Hide these tagged components
    instructions="This MCP server handles LLM chat functionalities.",
    lifespan=lifespan_chat_server,
    mask_error_details=True,  # Mask error details in responses and defer to ToolError for security reasons
    mcp_app_mount_path=FASTMCP_MOUNT_PATH,
    on_duplicate_prompts="error",
    on_duplicate_resources="error",
    on_duplicate_tools="error",
    register_modules={"mcp_demo.prompts.chat"},
    server_name="Chat Server",
)

# Mount the chat MCP server application to the main MCP server application.
# NB: FastMCP automatically uses proxy mounting when the mounted server has a custom
# lifespan but you can override this behavior by setting `as_proxy=False`.
# ref: https://gofastmcp.com/servers/composition#direct-vs-proxy-mounting
mcp_main.mount(mcp_chat, prefix="/chat")


@cli.command()
def main(
    *,
    host: str = typer.Option(
        Settings.FASTMCP_HOST,
        "--host",
        help="The host address to bind the server to.",
        show_default=True,
    ),
    port: int = typer.Option(
        Settings.FASTMCP_PORT,
        "--port",
        help="The port number to bind the server to.",
        show_default=True,
    ),
    no_reload: bool = typer.Option(
        False,
        "--no-reload",
        help="Specifies whether the server should automatically reload when changes are detected.",
        show_default=True,
    ),
) -> None:
    """Start the main MCP server application using Uvicorn.

    The process is as follows:

    1. Run the MCP server application using Uvicorn.

    Parameters
    ----------
    host
        The host address to bind the server to.
    port
        The port number to bind the server to.
    no_reload
        Specifies whether the server should automatically reload when changes are
        detected.
    """

    logger.info("Starting main MCP server with Uvicorn 🦄...")

    # 1.
    project_dir = Path(os.getenv("PATHS_PROJECT_DIR", ""))
    assert project_dir.is_dir(), f"'{project_dir}' is not a directory."
    uvicorn.run(
        "mcp_demo.entries.mcp_server_main:app_main",
        host=host,
        port=port,
        log_config=None,  # Disable Uvicorn's default logging config
        log_level=Settings.LOGGING_LOG_LEVEL.lower(),
        reload=not no_reload,
        reload_dirs=[str(project_dir / "backend" / "src")],
        root_path=os.getenv("API_BACKEND_ROOT", ""),
    )


if __name__ == "__main__":
    cli()

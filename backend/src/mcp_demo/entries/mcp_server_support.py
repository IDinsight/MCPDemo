"""This module contains the main entry point for the support MCP server application.

From the backend directory of this project, this entry point can be invoked from the
command line via:

python -m src.mcp_demo.entries.mcp_server_support

or

python src/mcp_demo/entries/mcp_server_support.py
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
from mcp_demo.utils.general import yaml_serializer
from mcp_demo.utils.mcp_server import create_mcp_server_app

assert (
    sys.version_info.major >= 3 and sys.version_info.minor >= 11
), "MCP Demo requires at least Python 3.11!"


FASTMCP_MOUNT_PATH = Settings.FASTMCP_MOUNT_PATH
REDIS_URL = Settings.REDIS_URL

# Instantiate typer apps for the command line interface.
cli = typer.Typer()


@dataclass
class MCPServerContext:
    """Context for the MCP server application."""

    redis_client: aioredis.Redis
    runtime_context: str

    some_text: str = "This is the context for the support MCP server."


@asynccontextmanager
async def lifespan_mcp(server: FastMCP) -> AsyncIterator[MCPServerContext]:
    """Lifespan events for the support MCP server application.

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
        A context manager that provides control to the support MCP server application.
    """

    logger.info("Starting support MCP server application...")

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
        logger.success("Support MCP server application finished!")


# Create the MCP server application instance.
app, _ = create_mcp_server_app(
    exclude_tags={"deprecated", "internal"},  # Hide these tagged components
    instructions="This is the support MCP server.",
    lifespan=lifespan_mcp,
    mask_error_details=True,  # Mask error details in responses and defer to ToolError for security reasons
    mcp_app_mount_path=FASTMCP_MOUNT_PATH,
    on_duplicate_prompts="error",
    on_duplicate_resources="error",
    on_duplicate_tools="error",
    server_name="Support Server",
    tool_serializer=yaml_serializer,
)


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
    """Start the support MCP server application using Uvicorn.

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

    logger.info("Starting support MCP server with Uvicorn 🦄...")

    # 1.
    project_dir = Path(os.getenv("PATHS_PROJECT_DIR", ""))
    assert project_dir.is_dir(), f"'{project_dir}' is not a directory."
    uvicorn.run(
        "mcp_demo.entries.mcp_server_b:app",
        host=host,
        port=port + 1,
        log_config=None,  # Disable Uvicorn's default logging config
        log_level=Settings.LOGGING_LOG_LEVEL.lower(),
        reload=not no_reload,
        reload_dirs=[str(project_dir / "backend" / "src")],
        root_path=os.getenv("API_BACKEND_ROOT", ""),
    )


if __name__ == "__main__":
    cli()

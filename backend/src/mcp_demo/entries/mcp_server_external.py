"""This module contains the main entry point for an external MCP server application.

From the backend directory of this project, this entry point can be invoked from the
command line via:

python src/mcp_demo/entries/mcp_server_external.py
"""

# Standard Library
import os
import sys

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

# Third Party Library
import typer
import uvicorn

from fastmcp import FastMCP
from loguru import logger

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
from mcp_demo.utils.mcp_server import __MCP, register_server_components

assert (
    sys.version_info.major >= 3 and sys.version_info.minor >= 11
), "MCP Demo requires at least Python 3.11!"

# Instantiate typer apps for the command line interface.
cli = typer.Typer()


@asynccontextmanager
async def lifespan_external_server(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Lifespan events for the external MCP server application.

    The process is as follows:

    1. List the external MCP server tools (for demonstration purposes).
    2. Yield control to the external MCP server application.
    3. Perform any necessary cleanup when the external MCP server application finishes.

    Parameters
    ----------
    server
        The external MCP server instance.

    Yields
    ------
    AsyncIterator[dict[str, Any]]
        A context manager that provides control to the external MCP server application.
    """

    logger.info("Starting external MCP server application...")

    try:
        # 1.
        server_tools = await server.get_tools()
        server_tool_names = list(server_tools.keys())
        logger.info(f"Available tools external server-side: {server_tool_names}")

        # 2.
        logger.log("CELEBRATE", "Ready to roll! 🚀")

        yield {"a": "Context A", "b": "Context B"}
    finally:
        # 3.
        logger.success("Support MCP server application finished!")


# Create the MCP server application instance.
server_name = "External Server"
if server_name in __MCP:
    app = __MCP[server_name][0]
else:
    mcp = FastMCP(
        auth=None,
        exclude_tags={"deprecated", "internal"},  # Hide these tagged components
        instructions="This is the external MCP server.",
        lifespan=lifespan_external_server,
        mask_error_details=True,  # Mask error details in responses and defer to ToolError for security reasons
        name=server_name,
        on_duplicate_prompts="error",
        on_duplicate_resources="error",
        on_duplicate_tools="error",
        tool_serializer=yaml_serializer,
    )
    app = mcp.http_app(path=f"/{Settings.EXTERNAL_FASTMCP_MOUNT_PATH}/")
    __MCP[server_name] = (app, mcp)
    register_server_components(
        register_modules={"mcp_demo.tools.external_tools"}, server_name=server_name
    )


@cli.command()
def main(
    *,
    host: str = typer.Option(
        Settings.EXTERNAL_FASTMCP_HOST,
        "--host",
        help="The host address to bind the server to.",
        show_default=True,
    ),
    port: int = typer.Option(
        Settings.EXTERNAL_FASTMCP_PORT,
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
    """Start the external MCP server application using Uvicorn.

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

    logger.info("Starting external MCP server with Uvicorn 🦄...")

    # 1.
    project_dir = Path(os.getenv("PATHS_PROJECT_DIR", ""))
    assert project_dir.is_dir(), f"'{project_dir}' is not a directory."
    uvicorn.run(
        "mcp_demo.entries.mcp_server_external:app",
        host=host,
        port=port,
        log_config=None,  # Disable Uvicorn's default logging config
        log_level=Settings.LOGGING_LOG_LEVEL.lower(),
        reload=not no_reload,
        reload_dirs=[str(project_dir / "backend" / "src")],
    )


if __name__ == "__main__":
    cli()

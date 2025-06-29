"""This module contains the main entry point for the MCP server.

From the backend directory of this project, this entry point can be invoked frmo the
command line via:

python -m src.mcp_demo.entries.mcp_server.py

or

python -m src/mcp_demo/entries/mcp_server.py

or

uv run src/mcp_demo/entries/mcp_server.py
"""

# Standard Library
import os
import sys

# Third Party Library
import typer

from mcp.server.fastmcp import FastMCP

# Append the framework path. NB: This is required if this entry point is invoked from
# the command line. However, it is not necessary if it is imported from a pip install.
if __name__ == "__main__":
    PATHS_PROJECT_DIR = os.getenv("PATHS_PROJECT_DIR", None)
    assert PATHS_PROJECT_DIR
    if PATHS_PROJECT_DIR not in sys.path:
        print(f"Appending '{PATHS_PROJECT_DIR}' to system path...")
        sys.path.append(str(PATHS_PROJECT_DIR))

# Package Library
from mcp_demo.utils.logging_ import initialize_logger

# Instantiate typer apps for the command line interface.
cli = typer.Typer()

logger = initialize_logger()

# This object must exist in the global scope of this module. It is the MCP server
# instance that will be used to run the MCP server. Valid names are either "mcp",
# "server", or "app".
# Create an MCP server
mcp = FastMCP(
    name="Calculator",
    host="0.0.0.0",  # only used for SSE transport (localhost)
    port=8050,  # only used for SSE transport (set this to any port)
)


# Add a simple calculator tool
@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers together"""
    return a + b


@cli.command()
def main(*, transport_type: str = "stdio") -> None:
    """Start the main MCP server.

    The process is as follows:

    1. XXX

    Parameters
    ----------
    transport_type
        The type of transport to use for the MCP server. Valid options are "sse" or
        "stdio".
    """

    if transport_type not in ["sse", "stdio"]:
        raise ValueError(
            f"Invalid transport type: {transport_type}. "
            f"Valid options are 'sse' or 'stdio'."
        )

    logger.info(f"Starting MCP server with transport type: {transport_type}")

    mcp.run(transport=transport_type)


# Run the server
if __name__ == "__main__":
    cli()

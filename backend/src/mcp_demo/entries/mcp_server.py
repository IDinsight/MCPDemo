"""This module contains the main entry point for the MCP server.

From the backend directory of this project, this entry point can be invoked from the
command line via:

python -m src.mcp_demo.entries.mcp_server.py

or

python -m src/mcp_demo/entries/mcp_server.py
"""

# Standard Library
import sys

from pathlib import Path

# Third Party Library
import typer

# Append the framework path. NB: This is required if this entry point is invoked from
# the command line. However, it is not necessary if it is imported from a pip install.
if __name__ == "__main__":
    PACKAGE_PATH = Path(__file__).resolve().parents[2]
    if PACKAGE_PATH not in sys.path:
        print(f"Appending '{PACKAGE_PATH}' to system path...")
        sys.path.append(str(PACKAGE_PATH))

# Package Library
from mcp_demo import create_mcp_server
from mcp_demo.config import Settings
from mcp_demo.utils.logging_ import initialize_logger

assert (
    sys.version_info.major >= 3 and sys.version_info.minor >= 11
), "MCP Demo requires at least Python 3.11!"

# Instantiate typer apps for the command line interface.
cli = typer.Typer()

mcp = create_mcp_server()
logger = initialize_logger()

FASTMCP_TRANSPORT_TYPE = Settings.FASTMCP_TRANSPORT_TYPE


@cli.command()
def main() -> None:
    """Start the main MCP server.

    The process is as follows:

    1. Run the MCP server with the specified transport type.
    """

    logger.info(
        f"Starting MCP server with transport type {FASTMCP_TRANSPORT_TYPE} 🤖..."
    )

    # 1.
    mcp.run(transport=FASTMCP_TRANSPORT_TYPE)


if __name__ == "__main__":
    cli()

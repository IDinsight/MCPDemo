"""This module contains the main entry point for the MCP server application.

From the backend directory of this project, this entry point can be invoked from the
command line via:

python -m src.mcp_demo.entries.mcp_server_app

or

python src/mcp_demo/entries/mcp_server_app.py
"""

# Standard Library
import os
import sys

from pathlib import Path

# Third Party Library
import typer
import uvicorn

from loguru import logger

# Append the framework path. NB: This is required if this entry point is invoked from
# the command line. However, it is not necessary if it is imported from a pip install.
if __name__ == "__main__":
    PACKAGE_PATH = Path(__file__).resolve().parents[2]
    if PACKAGE_PATH not in sys.path:
        print(f"Appending '{PACKAGE_PATH}' to system path...")
        sys.path.append(str(PACKAGE_PATH))

# Package Library
from mcp_demo import create_mcp_server_app
from mcp_demo.config import Settings

assert (
    sys.version_info.major >= 3 and sys.version_info.minor >= 11
), "MCP Demo requires at least Python 3.11!"

# Instantiate typer apps for the command line interface.
cli = typer.Typer()

# app = create_mcp_server_app()
app = create_mcp_server_app()

FASTMCP_HOST = Settings.FASTMCP_HOST
FASTMCP_PORT = Settings.FASTMCP_PORT


@cli.command()
def main(
    host: str = FASTMCP_HOST, port: int = FASTMCP_PORT, reload: bool = True
) -> None:
    """Start the MCP server application using Uvicorn.

    The process is as follows:

    1. Run the MCP server application using Uvicorn.

    Parameters
    ----------
    host
        The host address to bind the server to.
    port
        The port number to bind the server to.
    reload
        Specifies whether the server should automatically reload when changes are
        detected.
    """

    logger.info("Starting MCP server with Uvicorn 🦄...")

    # 1.
    project_dir = Path(os.getenv("PATHS_PROJECT_DIR", ""))
    assert project_dir.is_dir(), f"'{project_dir}' is not a directory."
    uvicorn.run(
        "mcp_demo.entries.mcp_server_app:app",
        host=host,
        port=port,
        log_config=None,  # Disable Uvicorn's default logging config
        log_level=Settings.LOGGING_LOG_LEVEL.lower(),
        reload=reload,
        reload_dirs=[str(project_dir / "backend" / "src")],
        root_path=os.getenv("API_BACKEND_ROOT", ""),
    )


if __name__ == "__main__":
    cli()

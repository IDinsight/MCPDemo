"""This module contains the main entry point for the FastAPI application.

From the backend directory of this project, this entry point can be invoked from the
command line via:

python src/mcp_demo/entries/fastapi_app.py
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
from mcp_demo.config import Settings
from mcp_demo.utils.fastapi_ import create_fastapi_app

assert (
    sys.version_info.major >= 3 and sys.version_info.minor >= 11
), "MCP Demo requires at least Python 3.11!"

# Instantiate typer apps for the command line interface.
cli = typer.Typer()

app = create_fastapi_app()


@cli.command()
def main(
    host: str = typer.Option(
        Settings.FASTAPI_HOST,
        "--host",
        help="The host address to bind the server to.",
        show_default=True,
    ),
    port: int = typer.Option(
        Settings.FASTAPI_PORT,
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
    """Start the FastAPI application using Uvicorn.

    The process is as follows:

    1. Run the FastAPI application using Uvicorn.

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

    logger.info(f"Starting FastAPI with Uvicorn 🦄 on {host}:{port}...")

    # 1.
    project_dir = Path(os.getenv("PATHS_PROJECT_DIR", ""))
    assert project_dir.is_dir(), f"'{project_dir}' is not a directory."
    uvicorn.run(
        "mcp_demo.entries.fastapi_app:app",
        host=host,
        port=port,
        log_config=None,  # Disable Uvicorn's default logging config
        log_level=Settings.LOGGING_LOG_LEVEL.lower(),
        reload=not no_reload,
        reload_dirs=[str(project_dir / "backend" / "src")],
        root_path=os.getenv("$CADDY_BACKEND_ROOT_API", ""),
    )


if __name__ == "__main__":
    cli()

"""This module contains the main entry point for the client.

From the backend directory of this project, this entry point can be invoked from the
command line via:

python -m src.mcp_demo.entries.client_call

or

python src/mcp_demo/entries/client_call.py
"""

# Standard Library
import asyncio
import sys

from pathlib import Path

# Third Party Library
import typer

from fastmcp import Client
from fastmcp.utilities.mcp_config import MCPConfig, RemoteMCPServer
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

assert (
    sys.version_info.major >= 3 and sys.version_info.minor >= 11
), "MCP Demo requires at least Python 3.11!"

# Instantiate typer apps for the command line interface.
cli = typer.Typer()

FASTMCP_DEBUG = Settings.FASTMCP_DEBUG
FASTMCP_HOST = Settings.FASTMCP_HOST
FASTMCP_MOUNT_PATH = Settings.FASTMCP_MOUNT_PATH
FASTMCP_PORT = Settings.FASTMCP_PORT
FASTMCP_TRANSPORT_TYPE = Settings.FASTMCP_TRANSPORT_TYPE


async def _run_client(
    *, host: str, port: int, server_mount_path: str, transport: str
) -> None:
    """Main function to demonstrate the MCP client connecting to the server.

    Parameters
    ----------
    host
        The host address for the MCP client.
    port
        The port number for the MCP client.
    server_mount_path
        The mount path for the MCP server.
    transport
        The transport type for the MCP client.
    """

    client: Client = Client(
        MCPConfig(
            mcpServers={
                "remote_server": RemoteMCPServer(
                    transport=transport, url=f"http://{host}:{port}/{server_mount_path}"
                )
            }
        )
    )
    async with client:
        tools = await client.list_tools()
        tool_names = [tool.name for tool in tools]
        logger.info(f"Available tools client-side: {tool_names}")

        # Call tools.
        bmi = await client.call_tool("calculate_bmi", {"height": 1.78, "weight": 72})
        logger.info(f"BMI: {bmi}")


@cli.command()
def main(
    *,
    host: str = typer.Option(
        FASTMCP_HOST,
        "--host",
        help="The host address for the MCP client.",
        show_default=True,
    ),
    port: int = typer.Option(
        FASTMCP_PORT,
        "--port",
        help="The port number for the MCP client.",
        show_default=True,
    ),
    server_mount_path: str = typer.Option(
        FASTMCP_MOUNT_PATH,
        "--server-mount-path",
        help="The mount path for the MCP server.",
        show_default=True,
    ),
    transport: str = typer.Option(
        FASTMCP_TRANSPORT_TYPE,
        "--transport",
        case_sensitive=True,
        help="The transport type for the MCP client.",
        show_choices=True,
    ),
) -> None:
    """Wrapper function for running the MCP client.

    Parameters
    ----------
    host
        The host address for the MCP client.
    port
        The port number for the MCP client.
    server_mount_path
        The mount path for the MCP server.
    transport
        The transport type for the MCP client.
    """

    asyncio.run(
        _run_client(
            host=host,
            port=port,
            server_mount_path=server_mount_path,
            transport=transport,
        )
    )


if __name__ == "__main__":
    cli()

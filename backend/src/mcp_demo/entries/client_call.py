"""This module contains the main entry point for the client.

From the backend directory of this project, this entry point can be invoked from the
command line via:

python -m src.mcp_demo.entries.client_call

or

python src/mcp_demo/entries/client_call.py
"""

# pylint: disable=R0915
# Standard Library
import asyncio
import json
import sys

from pathlib import Path

# Third Party Library
import typer

from fastmcp import Client
from fastmcp.client.client import CallToolResult
from fastmcp.exceptions import ToolError
from loguru import logger
from mcp.types import PromptMessage, TextContent, TextResourceContents

# Append the framework path. NB: This is required if this entry point is invoked from
# the command line. However, it is not necessary if it is imported from a pip install.
if __name__ == "__main__":
    PACKAGE_PATH = Path(__file__).resolve().parents[2]
    if PACKAGE_PATH not in sys.path:
        print(f"Appending '{PACKAGE_PATH}' to system path...")
        sys.path.append(str(PACKAGE_PATH))

# Package Library
from mcp_demo.config import Settings
from mcp_demo.utils.mcp_client import (
    get_mcp_config,
    list_prompts,
    list_resource_templates,
    list_resources,
    list_tools,
    log_handler,
)

assert (
    sys.version_info.major >= 3 and sys.version_info.minor >= 11
), "MCP Demo requires at least Python 3.11!"

# Instantiate typer apps for the command line interface.
cli = typer.Typer()

FASTMCP_HOST = Settings.FASTMCP_HOST
FASTMCP_MOUNT_PATH = Settings.FASTMCP_MOUNT_PATH
FASTMCP_PORT = Settings.FASTMCP_PORT
FASTMCP_TRANSPORT = Settings.FASTMCP_TRANSPORT


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
        log_handler=log_handler,
        transport=get_mcp_config(  # type: ignore
            host=host,
            port=port,
            server_mount_path=server_mount_path,
            transport=transport,
        ),
    )

    async with client:
        logger.success(f"MCP client connection status: {client.is_connected()}")

        # Basic server interaction.
        await client.ping()
        logger.log("CELEBRATE", "MCP server is reachable.")

        # List available components.
        await list_tools(client=client)
        await list_resources(client=client)
        await list_resource_templates(client=client)
        await list_prompts(client=client)

        # Call main server tools.
        greet_result = await client.call_tool("main_server_greet", {"name": "Foobar"})
        logger.info(f"{greet_result = }")

        bmi_result = await client.call_tool(
            "main_server_calculate_bmi", {"height": 1.78, "weight": 72}, timeout=60
        )
        assert isinstance(bmi_result, CallToolResult), f"{type(bmi_result) = }"
        assert bmi_result.is_error is False
        logger.info(f"{bmi_result.data = }")
        logger.info(f"{bmi_result.structured_content = }\n")

        try:
            await client.call_tool("main_server_deprecated_tool", {"a": 5, "b": 10})
        except ToolError as e:
            logger.warning(f"Calling deprecated tool result: {e}\n")

        my_tool_add_result = await client.call_tool(
            "main_server_my_tool_add", {"a": 5, "b": 10}
        )
        logger.info(f"{my_tool_add_result = }\n")

        user_agent_info_result = await client.call_tool(
            "main_server_user_agent_info", {}
        )
        logger.info(f"{user_agent_info_result.data = }\n")

        try:
            await client.call_tool("main_server_foobar_tool", {"a": 5, "b": 10})
        except ToolError as e:
            logger.info(f"Calling foobar tool: {e}\n")

        # Read main server resources.
        data_resource = await client.read_resource("data://main_server/3")
        logger.debug(f"{data_resource = }")
        assert isinstance(data_resource[0], TextResourceContents)
        data_resource_text = json.loads(data_resource[0].text)
        logger.info(f"{data_resource_text = }\n")

        search_resource = await client.read_resource("search://main_server/foobar")
        assert isinstance(search_resource[0], TextResourceContents)
        search_resource_text = json.loads(search_resource[0].text)
        logger.info(f"{search_resource_text = }\n")

        # Read main server resource templates.
        lookup_user_email = await client.read_resource(
            "users://main_server/email/example@gmail.com"
        )
        assert isinstance(lookup_user_email[0], TextResourceContents)
        lookup_user_email_text = lookup_user_email[0].text
        logger.info(f"{lookup_user_email_text = }\n")

        lookup_user_name = await client.read_resource("users://main_server/name/foo")
        assert isinstance(lookup_user_name[0], TextResourceContents)
        lookup_user_name_text = lookup_user_name[0].text
        logger.info(f"{lookup_user_name_text = }\n")

        # Get main server prompts.
        roleplay_scenario_result = await client.get_prompt(
            "main_server_roleplay_scenario",
            {"character": "Alice", "situation": "a complex problem"},
        )
        for message in roleplay_scenario_result.messages:
            logger.info(f"Role: {message.role}")
            logger.info(f"Content: {message.content}\n")

        error_correction_result = await client.get_prompt(
            "main_server_/chat_error_correction",
            {"error_info_str": "some complex error trace"},
        )
        message = error_correction_result.messages[0]
        assert isinstance(message, PromptMessage)
        assert isinstance(message.content, TextContent)
        error_correction_prompt = message.content.text
        logger.info(f"{error_correction_prompt = }\n")

        # Call external server tools.
        get_weather_result = await client.call_tool(
            "external_server_get_weather", {"city": "Northville"}
        )
        logger.info(f"{get_weather_result.data = }\n")

    logger.info(f"Client connection closed. Connection status: {client.is_connected()}")


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
        FASTMCP_TRANSPORT,
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

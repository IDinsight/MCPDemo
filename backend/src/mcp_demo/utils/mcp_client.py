"""This module contains MCP client utilities."""

# Standard Library
from pathlib import Path

# Third Party Library
from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from fastmcp.utilities.mcp_config import MCPConfig, RemoteMCPServer
from loguru import logger

# Package Library
from mcp_demo.config import Settings


def get_mcp_config(
    *, host: str, port: int, server_mount_path: str, transport: str
) -> MCPConfig:
    """Get the MCP client configuration.

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

    Returns
    -------
    MCPConfig
        A configuration object for the MCP client, containing the server information
        and authentication details.
    """

    # Read the access token from its temporary file. DO NOT DO THIS IN PRODUCTION!
    # This is just for demonstration purposes.
    token_fp = Path("/tmp") / "mcp_demo_token.txt"
    with token_fp.open("r") as f:
        access_token = f.read().strip()

    mcp_config = MCPConfig(
        mcpServers={
            "main_server": RemoteMCPServer(
                auth=BearerAuth(token=access_token),
                transport=transport,
                url=f"http://{host}:{port}/{server_mount_path}",
            ),
            "external_server": RemoteMCPServer(
                auth=None,
                transport=Settings.EXTERNAL_FASTMCP_TRANSPORT,
                url=f"http://{Settings.EXTERNAL_FASTMCP_HOST}:{Settings.EXTERNAL_FASTMCP_PORT}/{Settings.EXTERNAL_FASTMCP_MOUNT_PATH}",
            ),
        }
    )
    logger.debug(f"{mcp_config = }")
    return mcp_config


async def list_prompts(*, client: Client, verbose: bool = False) -> None:
    """List all prompts available on the MCP server.

    Parameters
    ----------
    client
        The MCP client instance connected to the server.
    verbose
        If True, print detailed information about each prompt.
    """

    prompts = await client.list_prompts()
    for prompt in prompts:
        prompt_str = ""
        prompt_str += f"\nPrompt Name:\n{prompt.name}\n\n"
        if prompt.arguments:
            prompt_str += (
                f"Prompt Arguments:\n{[arg.name for arg in prompt.arguments]}\n\n"
            )
        if verbose:
            prompt_str += f"Prompt Description:\n{prompt.description}\n\n"
        logger.info(f"{prompt_str}")


async def list_resource_templates(*, client: Client, verbose: bool = False) -> None:
    """List all resource templates available on the MCP server.

    Parameters
    ----------
    client
        The MCP client instance connected to the server.
    verbose
        If True, print detailed information about each resource template.
    """

    resource_templates = await client.list_resource_templates()
    for template in resource_templates:
        template_str = ""
        template_str += f"\nResource Template URI:\n{template.uriTemplate}\n\n"
        template_str += f"Resource Template Name:\n{template.name}\n\n"
        template_str += f"Resource Template MIME Type:\n{template.mimeType}\n\n"
        if verbose:
            template_str += (
                f"Resource Template Description:\n{template.description}\n\n"
            )
            if template.annotations:
                template_str += (
                    f"Resource Template Annotations:\n{template.annotations}\n\n"
                )
        logger.info(f"{template_str}")


async def list_resources(*, client: Client, verbose: bool = False) -> None:
    """List all resources available on the MCP server.

    Parameters
    ----------
    client
        The MCP client instance connected to the server.
    verbose
        If True, print detailed information about each resource.
    """

    resources = await client.list_resources()
    for resource in resources:
        resource_str = ""
        resource_str += f"\nResource URI:\n{resource.uri}\n\n"
        resource_str += f"Resource Name:\n{resource.name}\n\n"
        resource_str += f"Resource MIME Type:\n{resource.mimeType}\n\n"
        if verbose:
            resource_str += f"Resource Description:\n{resource.description}\n\n"
            if resource.annotations:
                resource_str += f"Resource Annotations:\n{resource.annotations}\n\n"
            if resource.size:
                resource_str += f"Resource Size:\n{resource.size}\n\n"
        logger.info(f"{resource_str}")


async def list_tools(*, client: Client, verbose: bool = False) -> None:
    """List all tools available on the MCP server.

    Parameters
    ----------
    client
        The MCP client instance connected to the server.
    verbose
        If True, print detailed information about each tool.
    """

    tools = await client.list_tools()
    for tool in tools:
        tool_str = ""
        tool_str += f"\nTool Name:\n{tool.name}\n\n"
        if verbose:
            tool_str += f"Tool Description:\n{tool.description}\n\n"
            if tool.annotations:
                tool_str += f"Tool Annotations:\n{tool.annotations}\n\n"
        if tool.inputSchema:
            tool_str += f"Tool Parameters:\n{tool.inputSchema}\n\n"
        logger.info(f"{tool_str}")

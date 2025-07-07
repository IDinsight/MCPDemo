"""This module contains MCP client utilities."""

# Third Party Library
import requests

from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from fastmcp.client.logging import LogMessage
from fastmcp.utilities.mcp_config import MCPConfig, RemoteMCPServer
from loguru import logger

# Package Library
from mcp_demo.config import Settings


def get_bearer_auth_token() -> str:
    """Get the bearer authentication token for the client.

    Returns
    -------
    str
        The bearer authentication token.

    Raises
    ------
    RuntimeError
        If the access token cannot be retrieved from the server.
    ValueError
        If the client_type is not 'docker' or 'local'.
    """

    url = "http://0.0.0.0:8000/auth/token"
    payload = {
        "password": Settings.AUTH_USER_PASSPHRASE.get_secret_value(),
        "scope": "read",
        "username": Settings.AUTH_USER_NAME,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(url, data=payload, headers=headers, timeout=60)
    if response.ok:
        token_data = response.json()
        assert "access_token" in token_data, "Access token not found in response."
    else:
        logger.error(f"Failed to retrieve access token: {response.text}")
        raise RuntimeError(
            f"Failed to retrieve access token from {url}. "
            "Please check the server configuration."
        )

    return token_data["access_token"]


def get_mcp_config_docker(
    *,
    host: str,
    port: int,
    server_mount_path: str,
    transport: str,
) -> MCPConfig:
    """Get the docker MCP client configuration.

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

    Raises
    ------
    RuntimeError
        If the access token cannot be retrieved from the server.
    """

    access_token = get_bearer_auth_token()
    server_config = {
        "main_server": RemoteMCPServer(
            auth=BearerAuth(token=access_token),
            transport=transport,
            url=f"http://{host}:{port}/{server_mount_path}",
        )
    }
    mcp_config = MCPConfig(mcpServers=server_config)
    logger.debug(f"{mcp_config = }")
    return mcp_config


def get_mcp_config_local(
    *,
    host: str,
    include_external_servers: bool = False,
    port: int,
    server_mount_path: str,
    transport: str,
) -> MCPConfig:
    """Get the local MCP client configuration.

    Parameters
    ----------
    host
        The host address for the MCP client.
    include_external_servers
        If True, include external MCP servers in the configuration.
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

    access_token = get_bearer_auth_token()
    server_config = {
        "main_server": RemoteMCPServer(
            auth=BearerAuth(token=access_token),
            transport=transport,
            url=f"http://{host}:{port}/{server_mount_path}",
        )
    }
    if include_external_servers:
        server_config["external_server"] = RemoteMCPServer(
            auth=None,
            transport=Settings.EXTERNAL_FASTMCP_TRANSPORT,
            url=f"http://{Settings.EXTERNAL_FASTMCP_HOST}:{Settings.EXTERNAL_FASTMCP_PORT}/{Settings.EXTERNAL_FASTMCP_MOUNT_PATH}",
        )
    mcp_config = MCPConfig(mcpServers=server_config)
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


async def log_handler(message: LogMessage) -> None:
    """Handle log messages from the MCP server.

    Parameters
    ----------
    message
        The log message received from the MCP server.
    """

    level = message.level.upper()
    data = message.data
    logger.log(level, f"[{level}] {message.logger or 'Server'}: {data}")

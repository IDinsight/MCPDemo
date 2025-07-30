"""This module contains MCP client utilities."""

# Standard Library
import base64
import hashlib
import secrets

# Third Party Library
import jwt as pyjwt
import requests

from fastapi import status
from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from fastmcp.client.logging import LogMessage
from fastmcp.mcp_config import MCPConfig, RemoteMCPServer
from loguru import logger
from requests.auth import HTTPBasicAuth
from yarl import URL

# Package Library
from mcp_demo.config import Settings


def get_access_token(
    *, grant_type: str = "password", password: str, username: str
) -> str:
    """Get the access token for the client.

    Parameters
    ----------
    grant_type
        The grant type to use. Options are "password", "client_credentials", or "pkce".
    password
        The password for the MCP client authentication.
    username
        The username for the MCP client authentication.

    Returns
    -------
    str
        The oauth token.

    Raises
    ------
    RuntimeError
        If the access token cannot be retrieved from the server.
    ValueError
        If an unsupported authentication type is provided.
    """

    # Local/dev server URL for token retrieval.
    url = "http://0.0.0.0:8000/auth/token"

    # Prod (through Caddy).
    # url = "https://api.example.com/api/auth/token"

    match grant_type:
        case "pkce":
            return get_access_token_for_pkce(password=password, username=username)
        case "client_credentials":
            payload = {"grant_type": "client_credentials"}
        case "password":
            payload = {
                "grant_type": "password",
                "password": password,
                "username": username,
            }
        case _:
            raise ValueError(
                f"Unsupported grant type: {grant_type}. "
                f"Valid options are 'password', 'client_credentials', or 'pkce'."
            )
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(
        url,
        auth=(
            HTTPBasicAuth(username, password)
            if grant_type == "client_credentials"
            else None
        ),
        data=payload,
        headers=headers,
        timeout=60,
    )
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


def get_access_token_for_pkce(*, password: str, username: str) -> str:
    """Perform a full OAuth 2.1 Authorization Code + PKCE flow to obtain a Bearer
    access token for FastMCP from a FastAPI-based authorization server.

    This function assumes:
        1. The user already exists in the user database.
        2. The client (`client1`) is registered with `client_secret`, redirect URI, and
            allowed scopes.
        3. The FastAPI server is running locally and exposes /auth and /user endpoints.
        4. CORS, CSRF, and PKCE security features are enforced, and handled
            appropriately here.

    The process is as follows:

    1. Use the same session to maintain cookies.
    2. Authenticate the resource owner (user) via /auth/login to set a session cookie.
    3. Generate a PKCE code verifier/challenge pair.
    4. Initiate the OAuth authorization request to /auth/authorize. This step must come
        **before** user consents because the authorization server can't ask the user to
        approve something until it knows what the **client** is asking for (and the
        client can only ask for scopes it has been registered with). In this step, the
        client tells the server what scopes it wants access to. Only **after** the
        server has that information can the server then:
            - Authenticate the user so that it knows whose data the clients wants to
                touch.
            - Present a consent screen (step 5) that says something like "Client1 wants
                admin access - Allow/Deny"
            - The user can choose to deny the request at that point. If the user does,
                then the server must stop the flow and send the browser back with an
                error message and the rest of the steps never happen. Here, we do not
                have a UI and thus, we mimic the user always consenting to the request.
        **However**, we do not have a UI to present the consent screen, so we have to
        mimic the user consenting to the request by calling the /user/consents endpoint
        **first**. In reality, we would have the auth/authorize endpoint redirect the
        user to a consent screen, and then the user would either approve or deny the
        request. If the user approves, the server would then redirect back to the
        redirect URI with a one-time `code` that can be redeemed for an access token.
    5. Grant client-specific consent for scopes (via /user/consents).
    6. Extract the one-time `code` from the redirect URI.
    6. Redeem the code at /auth/token with PKCE and client credentials.
    8. Return the resulting access token.

    Parameters
    ----------
    password : str
        The password for the user logging in.
    username : str
        The username for the user logging in.

    Returns
    -------
    str
        A valid JWT access token to use as a Bearer token with FastMCP.

    Raises
    ------
    RuntimeError
        If any step in the OAuth flow fails.
    """

    # 1.
    session = requests.Session()

    # 2.
    auth_login_url = "http://0.0.0.0:8000/auth/login"
    user_login_payload = {"password": password, "username": username}
    headers = {"accept": "*/*", "Content-Type": "application/json"}
    auth_login_response = session.post(
        auth_login_url,
        headers=headers,
        json=user_login_payload,
        timeout=60,
    )
    if not auth_login_response.status_code == status.HTTP_204_NO_CONTENT:
        logger.error("Failed user login.")
        raise RuntimeError(
            f"Failed user login from: {auth_login_url}. "
            f"Please check the server configuration."
        )
    cookie_token = auth_login_response.cookies.get("access_token", None)
    assert cookie_token, "Cookie token not found in cookies."

    # 5.
    user_consents_url = "http://0.0.0.0:8000/user/consents"
    headers = {
        "accept": "application/json",
        "Authorization": f"Bearer {cookie_token}",
        "Content-Type": "application/json",
    }
    user_consents_payload = {"client_id": "client1", "scopes": ["admin"]}
    user_consents_response = requests.post(
        user_consents_url, headers=headers, json=user_consents_payload, timeout=60
    )
    if not user_consents_response.status_code == status.HTTP_201_CREATED:
        logger.error("Failed user consents.")
        raise RuntimeError(
            f"Failed user consents from: {user_consents_url}. "
            "Please check the server configuration."
        )
    try:
        _ = user_consents_response.json()
    except requests.exceptions.JSONDecodeError as exc:
        raise RuntimeError(
            f"Failed to decode JSON response from {user_consents_url}. "
            "Please check the server configuration."
        ) from exc

    # 3.
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )

    # 4. Hardcoded redirect URI for the OAuth flow. This is included in the response
    # from registering client1 in the FastAPI server.
    redirect_uri = "http://localhost:8000/docs/oauth2-redirect"
    auth_authorize_response = session.get(
        "http://0.0.0.0:8000/auth/authorize",
        allow_redirects=False,  # Only need the Location header
        headers={"Authorization": f"Bearer {cookie_token}"},  # For dev/prod environment
        params={
            "client_id": "client1",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": "admin",
            "state": secrets.token_urlsafe(16),
        },
        timeout=60,
    )
    if auth_authorize_response.status_code != status.HTTP_302_FOUND:
        raise RuntimeError(
            f"/auth/authorize failed ({auth_authorize_response.status_code}): "
            f"{auth_authorize_response.text or auth_authorize_response.reason}"
        )

    # 6.
    redirect_location = auth_authorize_response.headers["Location"]
    code = URL(redirect_location).query.get("code")
    if not code:
        raise RuntimeError("Authorisation code missing in redirect")

    # 7.
    auth_token_response = session.post(
        "http://0.0.0.0:8000/auth/token",
        data={
            "client_id": "client1",
            "client_secret": "client1",  #  pragma: allowlist secret
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=60,
    )
    if auth_token_response.status_code != status.HTTP_200_OK:
        raise RuntimeError(
            f"/auth/token failed ({auth_token_response.status_code}): "
            f"{auth_token_response.text or auth_token_response.reason}"
        )

    # 8.
    try:
        json_response = auth_token_response.json()
        access_token = json_response["access_token"]
        payload = pyjwt.decode(access_token, options={"verify_signature": False})
        logger.debug(f"{payload = }")
        return access_token
    except (ValueError, KeyError) as exc:
        raise RuntimeError("Token response did not contain 'access_token'") from exc


def get_mcp_config(
    *,
    grant_type: str,
    host: str,
    include_external_servers: bool = False,
    password: str,
    port: int,
    server_mount_path: str,
    transport: str,
    username: str,
) -> MCPConfig:
    """Get the local MCP client configuration with Bearer Authentication.

    Parameters
    ----------
    grant_type
        The grant type to use for authentication. Options are "password",
        "client_credentials", or "pkce".
    host
        The host address for the MCP client.
    include_external_servers
        If True, include external MCP servers in the configuration.
    password
        The password for the MCP client authentication.
    port
        The port number for the MCP client.
    server_mount_path
        The mount path for the MCP server.
    transport
        The transport type for the MCP client.
    username
        The username for the MCP client authentication.

    Returns
    -------
    MCPConfig
        A configuration object for the MCP client, containing the server information
        and authentication details.

    Raises
    ------
    ValueError
        If an unsupported authentication type is provided.
    """

    if grant_type not in ["password", "client_credentials", "pkce"]:
        raise ValueError(
            f"Unsupported grant type: {grant_type}. "
            f"Valid options are 'password', 'client_credentials', or 'pkce'."
        )

    access_token = get_access_token(
        grant_type=grant_type, password=password, username=username
    )
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

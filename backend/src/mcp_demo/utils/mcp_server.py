"""This module contains MCP server utilities."""

# Standard Library
from importlib import import_module
from typing import Any, Callable

# Third Party Library
from fastmcp import FastMCP
from fastmcp.server.auth import BearerAuthProvider
from loguru import logger
from starlette.applications import Starlette

# Package Library
from mcp_demo.config import Settings

__MCP: dict[str, tuple[Starlette, FastMCP]] = {}


def create_mcp_server_app(
    *,
    lifespan: Callable | None = None,
    mcp_app_mount_path: str = Settings.FASTMCP_MOUNT_PATH,
    register_modules: set[str] | None = None,
    server_name: str,
    **kwargs: Any,
) -> tuple[Starlette, FastMCP]:
    """Create the MCP server application for the backend.

    The process is as follows:

    1. If the MCP server application instance already exists, return it.
    2. Create an MCP server instance.
    3. Create a Starlette application that mounts the MCP server instance at the
        specified path.
    4. Store the MCP server application instance in a global variable for later use.
    5. Register server components such as tools, resources, prompts, etc. with the MCP
        server.

    Parameters
    ----------
    lifespan
        An optional lifespan context manager for the MCP server application. If not
        provided, the MCP server will use its default lifespan management.
    mcp_app_mount_path
        The path at which the MCP server application will be mounted.
    register_modules
        A set of module paths to register with the MCP server. This allows for dynamic
        registration of tools, resources, and prompts defined in those modules.
    server_name
        The name of the MCP server instance. This is also used to identify the server
        instance in the global variable `__MCP`.
    kwargs
        Additional keyword arguments passed explicitly to the `FastMCP` constructor.

    Returns
    -------
    tuple[Starlette, FastMCP]
        A tuple containing the Starlette application and the FastMCP server instance.
        The Starlette application can be mounted in a FastAPI app, and the FastMCP
        server instance can be used to interact with the MCP server.
    """

    # 1.
    if server_name in __MCP:
        return __MCP[server_name]

    # 2.
    mcp = FastMCP(
        auth=get_bearer_auth_provider(),  # Use BearerAuthProvider for authentication
        lifespan=lifespan,
        name=server_name,
        **kwargs,
    )

    # 3.
    app = mcp.http_app(path=f"/{mcp_app_mount_path}")

    # 4.
    __MCP[server_name] = (app, mcp)

    # 5.
    register_server_components(
        register_modules=register_modules, server_name=server_name
    )

    return app, mcp


def get_bearer_auth_provider() -> BearerAuthProvider:
    """Initialize and return a configured `BearerAuthProvider` for FastMCP.

    Ref: https://gofastmcp.com/servers/auth/bearer

    This provider enables JWT-based Bearer authentication for a FastMCP server by
    validating incoming JWTs using a remote JWKS endpoint.

    Configuration summary:
        - Uses `Settings.AUTH_AUDIENCE` as the expected `aud` claim.
        - Uses `Settings.AUTH_TOKEN_ISSUER` as the expected `iss` claim.
        - Retrieves public keys from the JWKS URL in `Settings.AUTH_JWKS_URI`.
        - Enforces the presence of the "read" scope on all tokens.

    FastMCP's `BearerAuthProvider` automates:
        - Downloading and caching the JWKS (obeying `Cache-Control` headers).
        - Checking signature validity (`RS256` by default).
        - Validating token expiry, issuer, audience, and scopes.
        - Rejecting unauthorized or malformed tokens.

    Returns
    -------
    BearerAuthProvider
        A reusable provider instance ready to attach to a FastMCP server for secure,
        authenticated MCP endpoints.
    """

    auth = BearerAuthProvider(
        audience=Settings.AUTH_AUDIENCE,
        issuer=Settings.AUTH_TOKEN_ISSUER,
        jwks_uri=Settings.AUTH_JWKS_URI,
        required_scopes=["read"],
    )

    return auth


def get_mcp_server(*, server_name: str) -> FastMCP | None:
    """Get the MCP server instance from the global variable.

    Parameters
    ----------
    server_name
        The name of the MCP server instance to retrieve. This should match the name
        used when creating the MCP server application.

    Returns
    -------
    FastMCP | None
        The MCP server instance if it exists, otherwise `None`.

    Raises
    ------
    KeyError
        If the specified server name does not exist in the global MCP server instances.
    """

    if not __MCP:
        logger.warning(f"MCP server is not initialized: {server_name}")
        return None

    if server_name not in __MCP:
        raise KeyError(f"MCP server instance was not found: {server_name}")

    return __MCP[server_name][1]  # Return the FastMCP instance


def register_server_components(
    *, register_modules: set[str] | None, server_name: str
) -> None:
    """Register service components such as tools, resources, prompts, etc. with the MCP
    server. This is accomplished by importing the relevant modules, thereby loading any
    `@mcp` decorators within those modules.

    Parameters
    ----------
    register_modules
        A set of module paths to register with the MCP server. This allows for dynamic
        registration of tools, resources, and prompts defined in those modules.
    server_name
        The name of the MCP server instance to register components with. This should
        match the name used when creating the MCP server application.
    """

    if not register_modules:
        logger.warning(
            "No modules to register with the MCP server. Skipping registration."
        )
        return

    logger.info("Registering components with the MCP server...")

    for attr_path in register_modules:
        logger.log(
            "ATTN", f"Registering module for MCP server '{server_name}': {attr_path}"
        )
        import_module(attr_path)

    logger.success("Successfully all registered components with the MCP server!")

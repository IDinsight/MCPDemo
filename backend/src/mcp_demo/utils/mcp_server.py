"""This module contains MCP server utilities."""

# Future Library
from __future__ import annotations

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
from mcp_demo.utils.general import convert_to_list

__MCP: dict[str, tuple[Starlette, FastMCP]] = {}


class MockMCP:
    """Generic stand-in for the real FastMCP instance while it is not yet registered
    in `__MCP`.

    Any attribute access returns another `MockMCP`, so you can chain indefinitely:
    `mcp.tools.foo.bar...`

    Calling the object:
        - If the call looks like a decorator, then return the function unchanged
            (identity decorator).
        - Otherwise, return itself, so further chaining is still possible.
    """

    __slots__ = ("_path",)

    def __init__(self, *, path: str = "mcp") -> None:
        """

        Parameters
        ----------
        path
            The path at which the mock MCP server is mounted. This is used to
            differentiate between different mock instances.
        """

        self._path = path

    def __getattr__(self, item: str) -> MockMCP:
        """Return a new `MockMCP` instance with the path updated to include the item
        accessed. This allows for chaining of attributes, simulating the behavior of a
        real FastMCP instance.

        Parameters
        ----------
        item
            The name of the attribute being accessed.

        Returns
        -------
        MockMCP
            A new `MockMCP` instance with the updated path.
        """

        return MockMCP(path=f"{self._path}.{item}")

    def __bool__(self) -> bool:
        """Evaluate to False in truthy tests. This is useful to prevent the mock
        instance from being treated as a valid FastMCP instance in conditional checks.

        Returns
        -------
        bool
            Always returns `False`, indicating that this is a mock instance and not a
            real FastMCP instance.
        """

        return False

    def __call__(self, *args: Any, **kwargs: Any) -> Callable | MockMCP:
        """Handle calls to the mock MCP instance. If called with a single callable
        argument, it acts as an identity decorator, returning the function unchanged.
        If called with no arguments or multiple arguments, it returns a new `MockMCP`
        instance with the current path. This allows the mock to be used in a way that
        mimics the behavior of a real FastMCP instance, while still allowing for
        chaining of attributes.

        Parameters
        ----------
        args
            Positional arguments passed to the call.
        kwargs
            Keyword arguments passed to the call.

        Returns
        -------
        Callable | MockMCP
            If called with a single callable argument, returns that function unchanged
            (identity decorator). Otherwise, returns a new `MockMCP` instance with the
            current path.
        """

        # Used directly as a decorator (e.g., @mcp.prompt).
        if args and len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]

        # Used as a decorator factory (e..g., @mcp.prompt("name", opts=...)) or as a
        # normal call (e.g., mcp.tools.analyse("file.csv")).
        return MockMCP(path=f"{self._path}()")

    def __repr__(self) -> str:
        """Return a string representation of the mock MCP instance. This is useful for
        debugging and logging, providing a clear indication that this is a mock
        instance and showing the path at which it is mounted.

        Returns
        -------
        str
            A string representation of the mock MCP instance, indicating its path.
        """

        return f"<Mock MCP placeholder: {self._path}>"

    def __str__(self) -> str:
        """Return a string representation of the mock MCP instance. This is useful for
        debugging and logging, providing a clear indication that this is a mock
        instance and showing the path at which it is mounted.

        Returns
        -------
        str
            A string representation of the mock MCP instance, indicating its path.
        """

        return repr(self)


def create_mcp_server_app(
    *,
    auth: BearerAuthProvider | None = None,
    lifespan: Callable | None = None,
    mcp_app_mount_path: str = Settings.FASTMCP_MOUNT_PATH,
    middleware: Callable | list[Callable] | None = None,
    register_modules: set[str] | None = None,
    server_name: str,
    **kwargs: Any,
) -> tuple[Starlette, FastMCP]:
    """Create the MCP server application for the backend.

    The process is as follows:

    1. If the MCP server application instance already exists, return it.
    2. Create an MCP server instance.
    3. If middleware is provided, add it to the MCP server instance.
    4. Create a Starlette application that mounts the MCP server instance at the
        specified path.
    5. Store the MCP server application instance in a global variable for later use.
    6. Register server components such as tools, resources, prompts, etc. with the MCP
        server.

    Parameters
    ----------
    auth
        An optional authentication provider for the MCP server. If not provided, the
        server will not have authentication enabled.
    lifespan
        An optional lifespan context manager for the MCP server application. If not
        provided, the MCP server will use its default lifespan management.
    mcp_app_mount_path
        The path at which the MCP server application will be mounted.
    middleware
        An optional middleware or list of middlewares to apply to the MCP server
        application. This can be used to add custom processing for requests and
        responses. The order provided in the list will be preserved. Middleware should
        be passed like `my_middleware(...)`, NOT `my_middleware` (without parentheses).
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
        auth=auth, json_response=True, lifespan=lifespan, name=server_name, **kwargs
    )

    # 3.
    for mw in convert_to_list(middleware or []):
        mcp.add_middleware(mw)

    # 4.
    app = mcp.http_app(path=f"/{mcp_app_mount_path}")

    # 5.
    __MCP[server_name] = (app, mcp)

    # 6.
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


def get_mcp_server(*, server_name: str) -> FastMCP | MockMCP:
    """Get the MCP server instance from the global variable. If the global variable
    `__MCP` is empty, return a `MockMCP` instance.

    Parameters
    ----------
    server_name
        The name of the MCP server instance to retrieve. This should match the name
        used when creating the MCP server application.

    Returns
    -------
    FastMCP | MockMCP
        The MCP server instance if it exists, otherwise a `MockMCP` instance.

    Raises
    ------
    KeyError
        If the specified server name does not exist in the global MCP server instances.
    """

    if not __MCP:
        logger.warning(f"MCP server is not initialized: {server_name}")
        return MockMCP(path=server_name)

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

"""This module contains MCP server utilities."""

# Future Library
from __future__ import annotations

# Standard Library
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import import_module
from typing import Any, AsyncIterator, Callable

# Third Party Library
from fastmcp import FastMCP
from fastmcp.server.auth import BearerAuthProvider
from fastmcp.server.middleware.error_handling import (
    ErrorHandlingMiddleware,
    RetryMiddleware,
)
from fastmcp.server.middleware.logging import LoggingMiddleware
from fastmcp.server.middleware.rate_limiting import SlidingWindowRateLimitingMiddleware
from fastmcp.server.middleware.timing import DetailedTimingMiddleware
from loguru import logger
from redis import asyncio as aioredis
from starlette.applications import Starlette

# Package Library
from mcp_demo.config import Settings
from mcp_demo.middlewares.mcp_ import TagBasedMiddleware

__MCP: dict[str, tuple[Starlette, FastMCP]] = {}


@dataclass
class ChatMCPServerContext:
    """Context for the chat MCP server application."""

    redis_client: aioredis.Redis


@dataclass
class MainMCPServerContext:
    """Context for the main MCP server application."""

    runtime_context: str

    some_text: str = "This is the context for the main MCP server."


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


def create_mcp_server_app() -> Starlette:
    """Create the MCP server application for the backend.

    The process is as follows:

    1. If the MCP server application instances already exist, then return the main MCP
        server application only (the chat MCP server is mounted to the main MCP server
        application).
    2. Create the MCP server instance.
    3. Create a Starlette application that mounts the MCP server instance at the
        specified path.
    4. Store the MCP server application instance in a global variable for later use.
    5. Register server components such as tools, resources, prompts, etc. with the MCP
        server.

    Returns
    -------
    Starlette
        The Starlette application instance that serves as the MCP server.
    """

    # 1.
    if "Main Server" in __MCP and "Chat Server" in __MCP:
        return __MCP["Main Server"][0]

    # Main Server
    # 2.
    server_name = "Main Server"
    mcp = FastMCP(
        auth=get_bearer_auth_provider(),  # Use BearerAuthProvider for authentication
        exclude_tags={"deprecated", "internal"},  # Hide these tagged components
        instructions="This is the main MCP server.",
        lifespan=lifespan_main_server,
        mask_error_details=False,
        middleware=[
            ErrorHandlingMiddleware(include_traceback=True, transform_errors=True),
            RetryMiddleware(
                max_retries=3, retry_exceptions=(ConnectionError, TimeoutError)
            ),
            SlidingWindowRateLimitingMiddleware(max_requests=1000, window_minutes=1),
            DetailedTimingMiddleware(),
            LoggingMiddleware(include_payloads=True, max_payload_length=1000),
            TagBasedMiddleware(),
        ],
        name=server_name,
        on_duplicate_prompts="error",
        on_duplicate_resources="error",
        on_duplicate_tools="error",
    )

    # 3.
    app = mcp.http_app(path=f"/{Settings.FASTMCP_MOUNT_PATH}/")

    # 4.
    __MCP[server_name] = (app, mcp)

    # 5.
    register_server_components(
        register_modules={
            "mcp_demo.prompts.base",
            "mcp_demo.resources.basic_resources",
            "mcp_demo.tools.basic_tools",
        },
        server_name=server_name,
    )

    # Chat Server
    # 2.
    server_name = "Chat Server"
    mcp_chat = FastMCP(
        auth=get_bearer_auth_provider(),  # Use BearerAuthProvider for authentication
        exclude_tags={"deprecated", "internal"},  # Hide these tagged components
        instructions="This MCP server handles LLM chat functionalities.",
        lifespan=lifespan_chat_server,
        mask_error_details=True,  # Mask error details in responses and defer to ToolError for security reasons
        middleware=[
            ErrorHandlingMiddleware(include_traceback=True, transform_errors=True),
            RetryMiddleware(
                max_retries=3, retry_exceptions=(ConnectionError, TimeoutError)
            ),
            SlidingWindowRateLimitingMiddleware(max_requests=1000, window_minutes=1),
            DetailedTimingMiddleware(),
            LoggingMiddleware(include_payloads=True, max_payload_length=1000),
            TagBasedMiddleware(),
        ],
        name=server_name,
        on_duplicate_prompts="error",
        on_duplicate_resources="error",
        on_duplicate_tools="error",
    )

    # 3.
    app_chat = mcp.http_app(path=f"/{Settings.FASTMCP_MOUNT_PATH}/")

    # 4.
    __MCP[server_name] = (app_chat, mcp_chat)

    # 5.
    register_server_components(
        register_modules={"mcp_demo.prompts.chat"}, server_name=server_name
    )

    # Mount the chat MCP server application to the main MCP server application.
    # NB: FastMCP automatically uses proxy mounting when the mounted server has a
    # custom lifespan but you can override this behavior by setting `as_proxy=False`.
    # ref: https://gofastmcp.com/servers/composition#direct-vs-proxy-mounting
    mcp.mount(mcp_chat, prefix="chat")

    return app


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
        required_scopes=[  # This dictates what scopes are required for the client
            "admin",
        ],
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


@asynccontextmanager
async def lifespan_chat_server(server: FastMCP) -> AsyncIterator[ChatMCPServerContext]:
    """Lifespan events for the chat MCP server application.

    The process is as follows:

    1. List the chat MCP server prompts (for demonstration purposes).
    2. Initialize Redis client for the chat MCP server.
    3. Yield control to the chat MCP server application.
    4. Close the Redis connection when the chat MCP server application finishes.
    5. Perform any necessary cleanup when the chat MCP server application finishes.

    Parameters
    ----------
    server
        The chat MCP server instance.

    Yields
    ------
    AsyncIterator[ChatMCPServerContext]
        A context manager that provides control to the chat MCP server application.
    """

    logger.info("Starting chat MCP server application...")

    redis_client: aioredis.Redis | None = None

    try:
        # 1.
        server_prompts = await server.get_prompts()
        server_prompt_names = list(server_prompts.keys())
        logger.info(f"Available prompts chat server-side: {server_prompt_names}")

        # 2.
        logger.info("Initializing Redis client...")
        redis_client = await aioredis.from_url(
            f"{Settings.REDIS_URL}", decode_responses=True
        )
        assert isinstance(redis_client, aioredis.Redis)
        logger.success("Redis connection established!")

        # 3.
        logger.log("CELEBRATE", "Ready to roll! 🚀")

        yield ChatMCPServerContext(redis_client=redis_client)
    finally:
        if isinstance(redis_client, aioredis.Redis):
            # 4.
            logger.info("Closing Redis connection...")
            await redis_client.aclose()
            logger.success("Redis connection closed!")

        # 5.
        logger.success("Chat MCP server application finished!")


@asynccontextmanager
async def lifespan_main_server(server: FastMCP) -> AsyncIterator[MainMCPServerContext]:
    """Lifespan events for the main MCP server application.

    The process is as follows:

    1. List the main MCP server components (for demonstration purposes).
    2. Yield control to the main MCP server application.
    3. Perform any necessary cleanup when the main MCP server application finishes.

    Parameters
    ----------
    server
        The main MCP server instance.

    Yields
    ------
    AsyncIterator[MainMCPServerContext]
        A context manager that provides control to the main MCP server application.
    """

    logger.info("Starting main MCP server application...")

    try:
        # 1.
        server_tools = await server.get_tools()
        server_tool_names = list(server_tools.keys())
        logger.info(f"Available tools main server-side: {server_tool_names}")

        server_resources = await server.get_resources()
        server_resource_names = list(server_resources.keys())
        logger.info(f"Available resources main server-side: {server_resource_names}")

        server_resource_templates = await server.get_resource_templates()
        server_resource_template_names = list(server_resource_templates.keys())
        logger.info(
            f"Available resource templates main server-side: "
            f"{server_resource_template_names}"
        )

        server_prompts = await server.get_prompts()
        server_prompt_names = list(server_prompts.keys())
        logger.info(f"Available prompts main server-side: {server_prompt_names}")

        # 2.
        logger.log("CELEBRATE", "Ready to roll! 🚀")

        yield MainMCPServerContext(runtime_context="new context")
    finally:
        # 3.
        logger.success("Main MCP server application finished!")


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

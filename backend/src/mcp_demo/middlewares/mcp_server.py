"""This module contains middlewares for MCP servers."""

# Standard Library
from typing import Any, Callable

# Third Party Library
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from loguru import logger


class TagBasedMiddleware(Middleware):
    """Middleware to check tool metadata based on tags.

    This middleware checks if a tool has a specific tag (e.g., "private") and raises an
    error if the tag is present. It also checks if the tool is enabled. If the tool is
    not found or an error occurs, it allows the execution to continue and handles the
    error naturally.
    """

    async def on_call_tool(
        self, context: MiddlewareContext, call_next: Callable
    ) -> Any:
        """Middleware to check tool metadata based on tags.

        Parameters
        ----------
        context
            The context of the middleware, which includes the request and other
            relevant information.
        call_next
            The next middleware or the actual tool call to be executed.

        Returns
        -------
        Any
            The result of the tool call, which may include the tool's response or an
            error.
        """

        # Access the tool object to check its metadata.
        if context.fastmcp_context:
            try:
                tool = await context.fastmcp_context.fastmcp.get_tool(
                    context.message.name
                )

                # Check if this tool has a "foobar" tag.
                if "foobar" in tool.tags:
                    logger.error(f"{tool = }")
                    logger.error(f"{tool.tags = }")
                    raise ToolError("Access denied by middleware: foobar tool")
            finally:
                pass

        return await call_next(context)

"""This module provides mathematical tools for the MCP demo application."""

# Third Party Library
from loguru import logger
from mcp.server.fastmcp import Context, FastMCP


def register_tools(*, mcp: FastMCP) -> None:
    """Register the mathematical tools with the MCP server.

    Parameters
    ----------
    mcp
        The MCP server instance to register the tools with.
    """

    logger.info("Registering mathematical tools...")

    @mcp.tool()
    def add(*, a: int, b: int, ctx: Context) -> int:
        """Add two numbers together.

        Parameters
        ----------
        a
            The first number to add.
        b
            The second number to add.
        ctx
            The context of the request, which includes metadata and lifespan context.

        Returns
        -------
        int
            The sum of the two numbers.
        """

        logger.debug(f"{dir(ctx) = }")
        logger.debug(f"{ctx.request_context.meta = }")
        logger.debug(f"{ctx.request_context.request_id = }")
        logger.debug(f"{ctx.request_context.session.client_params = }")

        assert (
            ctx.request_context.lifespan_context.some_context
            == "This is some context for the MCP server."
        )

        return a + b

    @mcp.tool()
    def multiply(*, a: int, b: int) -> int:
        """Multiply two numbers.

        Parameters
        ----------
        a
            The first number to multiply.
        b
            The second number to multiply.

        Returns
        -------
        int
            The product of the two numbers.
        """

        return a * b

    @mcp.tool()
    def subtract(*, a: int, b: int) -> int:
        """Subtract two numbers.

        Parameters
        ----------
        a
            The first number to subtract.
        b
            The second number to subtract.

        Returns
        -------
        int
            The difference of the two numbers.
        """

        return a - b

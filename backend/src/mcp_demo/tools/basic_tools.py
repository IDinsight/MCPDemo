"""This module contains examples of basic tools for the MCP demo application."""

# Standard Library
import os

from typing import Any

# Third Party Library
from fastapi import Request
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import (
    AccessToken,
    get_access_token,
    get_context,
    get_http_request,
)
from fastmcp.tools import Tool
from fastmcp.tools.tool import ToolResult
from fastmcp.tools.tool_transform import ArgTransform, forward
from loguru import logger

# Package Library
from mcp_demo.utils.mcp_server import get_mcp_server

mcp_main = get_mcp_server(server_name="Main Server")


class ATool:
    """A simple tool class to demonstrate tool registration for individual methods."""

    def __init__(self, *, name: str) -> None:
        """Initialize the tool.

        Parameters
        ----------
        name
            The name of the tool.
        """

        self.name = name

    async def my_tool_add(self, *, a: float, b: float) -> float:
        """Add two numbers together.

        Parameters
        ----------
        a
            The first number to add.
        b
            The second number to add.

        Returns
        -------
        float
            The sum of the two numbers.
        """

        logger.debug(f"{self.name} = ")
        return a + b


a_tool = ATool(name="A Tool")
mcp_main.tool()(a_tool.my_tool_add)


@mcp_main.tool
def add(*, a: float, b: float) -> float:
    """Add two numbers together.

    Parameters
    ----------
    a
        The first number to add.
    b
        The second number to add.

    Returns
    -------
    float
        The sum of the two numbers.
    """

    return a + b


async def ensure_positive(*, a: float, b: float) -> ToolResult:
    """Ensure that both x and y are positive integers.

    Parameters
    ----------
    a
        The first number to check.
    b
        The second number to check.

    Returns
    -------
    float
        The result of the addition if both a and b are positive.

    Raises
    ------
    ValueError
        If either a or b is less than or equal to zero.

    Returns
    -------
    ToolResult
        The sum of a and b if both are positive.
    """

    if a <= 0 or b <= 0:
        raise ValueError("a and b must be positive")

    return await forward(a=a, b=b)


add_ensure_positive = Tool.from_tool(
    add, name="add_positives_only", transform_fn=ensure_positive
)
mcp_main.add_tool(add_ensure_positive)


@mcp_main.tool
async def calculate_bmi(*, height: float, weight: float) -> float:
    """Async tool demonstration.

    Parameters
    ----------
    height
        The height in meters.
    weight
        The weight in kilograms.

    Returns
    -------
    dict[str, float]
        A dictionary containing the calculated BMI.

    Raises
    ------
    ToolError
        If the user does not have the required permissions.
    ValueError
        If height is less than or equal to zero.
    """

    access_token: AccessToken | None = get_access_token()
    assert access_token is not None
    user_scopes = access_token.scopes

    required_scope = "admin"
    if user_scopes and required_scope not in user_scopes:
        raise ToolError(
            f"Insufficient permissions: '{required_scope}' scope required. "
            f"Got: {user_scopes}"
        )

    ctx = get_context()
    await ctx.error(
        f"INFO FROM SERVER: Calculating BMI for height: {height} m, weight: {weight} kg"
    )
    if height <= 0:
        raise ValueError("Height must be greater than zero.")

    return weight / (height**2)


@mcp_main.tool(tags={"deprecated"})
def deprecated_tool(*, a: int, b: int) -> int:
    """A deprecated tool that should not be used.

    Parameters
    ----------
    a
        The first number to add.
    b
        The second number to add.

    Returns
    -------
    int
        The sum of the two numbers.
    """

    logger.warning("This tool is deprecated and should not be used.")
    return a + b


@mcp_main.tool(enabled=False)
async def disabled_tool(*, a: int, b: int) -> int:
    """An internal tool that should not be listed.

    Parameters
    ----------
    a
        The first number to add.
    b
        The second number to add.

    Returns
    -------
    int
        The sum of the two numbers.
    """

    logger.debug("This is an internal tool and should not be listed.")
    return a + b


@mcp_main.tool()
def divide_with_error_handling(*, a: int, b: int) -> float:
    """Divide two numbers with error handling.

    Parameters
    ----------
    a
        The numerator.
    b
        The denominator.

    Returns
    -------
    float
        The result of the division.

    Raises
    ------
    ToolError
        If the denominator is zero.
    TypeError
        If either argument is not a number.
    """

    if b == 0:
        # Error messages from ToolError are always sent to clients, regardless of
        # `mask_error_details` setting when instantiating the MCP server.
        raise ToolError("Division by zero is not allowed.")

    # If mask_error_details=True, this message would be masked
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        raise TypeError("Both arguments must be numbers.")

    return a / b


@mcp_main.tool(tags={"foobar"})
async def foobar_tool(*, a: int, b: int) -> int:
    """An internal tool that should not be listed due to its tag via the use of
    middleware.

    Parameters
    ----------
    a
        The first number to add.
    b
        The second number to add.

    Returns
    -------
    int
        The sum of the two numbers.
    """

    logger.debug("This is the foobar tool and should not be listed due to middleware.")
    return a + b


@mcp_main.tool
async def greet(*, name: str) -> str:
    """Greet a user with their name.

    Parameters
    ----------
    name
        The name of the user to greet.

    Returns
    -------
    str
        A greeting message for the user.
    """

    ctx = get_context()
    await ctx.error("IN GREET.")
    await ctx.report_progress(progress=50, total=100)
    return f"Hello, {name}!"


@mcp_main.tool(tags={"internal"})
async def internal_tool(*, a: int, b: int) -> int:
    """An internal tool that should not be listed.

    Parameters
    ----------
    a
        The first number to add.
    b
        The second number to add.

    Returns
    -------
    int
        The sum of the two numbers.
    """

    logger.debug("This is an internal tool and should not be listed.")
    return a + b


@mcp_main.tool(enabled=False)
def send_email(*, api_key: str, body: str, subject: str, to: str) -> dict[str, str]:
    """Send an email.

    Parameters
    ----------
    api_key
        The API key for the email service.
    body
        The body of the email.
    subject
        The subject of the email.
    to
        The recipient's email address.

    Returns
    -------
    dict[str, str]
        A dictionary containing the email details.
    """

    logger.debug(f"{api_key = }")

    return {"to": to, "subject": subject, "body": body}


send_email_api_key_hidden = Tool.from_tool(
    send_email,
    name="send_notification",
    transform_args={
        "api_key": ArgTransform(default=os.environ.get("EMAIL_API_KEY", ""), hide=True)
    },
)

mcp_main.add_tool(send_email_api_key_hidden)


@mcp_main.tool
async def user_agent_info() -> dict[str, Any]:
    """Return information about the user agent.

    Returns
    -------
    dict[str, Any]
        A dictionary containing the user agent, client IP, and request path.
    """

    # Get the HTTP request.
    request: Request = get_http_request()

    # Access request data.
    user_agent = request.headers.get("user-agent", "Unknown")
    client_ip = request.client.host if request.client else "Unknown"

    return {"client_ip": client_ip, "path": request.url.path, "user_agent": user_agent}

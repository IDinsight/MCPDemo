"""This module contains examples of basic tools for the MCP demo application."""

# Standard Library
from typing import Any

# Third Party Library
from fastapi import Request
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_context, get_http_request
from loguru import logger

# Package Library
from mcp_demo.tools.schemas import WeatherData
from mcp_demo.utils.mcp_server import get_mcp_server

mcp = get_mcp_server(server_name="Main Server")


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
mcp.tool()(a_tool.my_tool_add)


@mcp.tool()
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
    ValueError
        If height is less than or equal to zero.
    """

    ctx = get_context()
    await ctx.info(f"Calculating BMI for height: {height} m, weight: {weight} kg")

    if height <= 0:
        raise ValueError("Height must be greater than zero.")

    return weight / (height**2)


@mcp.tool(tags={"deprecated"})
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


@mcp.tool(enabled=False)
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


@mcp.tool()
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


@mcp.tool()
def get_weather(*, city: str) -> WeatherData:
    """Tool with structured output.

    Parameters
    ----------
    city
        The name of the city to get the weather for.

    Returns
    -------
    WeatherData
        A Pydantic model containing the weather data for the specified city.
    """

    return WeatherData(
        city=city,
        condition="partly cloudy",
        humidity=65.0,
        temperature=22.5,
        wind_speed=12.3,
    )


@mcp.tool(tags={"internal"})
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


@mcp.tool
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

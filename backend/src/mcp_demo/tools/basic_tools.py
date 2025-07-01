"""This module contains examples of basic tools for the MCP demo application."""

# Third Party Library
from loguru import logger
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field


def calculate_bmi(*, height: float, weight: float) -> float:
    """Calculate Body Mass Index (BMI).

    Parameters
    ----------
    height
        The height in meters.
    weight
        The weight in kilograms.

    Returns
    -------
    float
        The calculated BMI.

    Raises
    ------
    ValueError
        If height is less than or equal to zero.
    """

    if height <= 0:
        raise ValueError("Height must be greater than zero.")

    return weight / (height**2)


def register_tools(*, mcp: FastMCP) -> None:
    """Register the mathematical tools with the MCP server.

    Parameters
    ----------
    mcp
        The MCP server instance to register the tools with.
    """

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
            == "This is a demo MCP server context."
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

        ctx = mcp.get_context()

        assert (
            ctx.request_context.lifespan_context.some_context  # type: ignore
            == "This is a demo MCP server context."
        )

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


class WeatherData(BaseModel):
    """Pydantic model for structured weather data."""

    city: str
    condition: str
    humidity: float = Field(..., description="Humidity percentage.")
    temperature: float = Field(..., description="Temperature in Celsius.")
    wind_speed: float


def get_weather(*, city: str) -> WeatherData:
    """Get structured weather data.

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

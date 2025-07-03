"""This module contains examples of external tools for the MCP demo application."""

# Package Library
from mcp_demo.tools.schemas import WeatherData
from mcp_demo.utils.mcp_server import get_mcp_server

mcp_external = get_mcp_server(server_name="External Server")


@mcp_external.tool()
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

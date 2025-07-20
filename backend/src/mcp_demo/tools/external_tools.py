"""This module contains examples of external tools for the MCP demo application."""

# Third Party Library
from fastmcp import Context

# Package Library
from mcp_demo.tools.schemas import WeatherData
from mcp_demo.utils.mcp_server import get_mcp_server

mcp_external = get_mcp_server(server_name="External Server")


@mcp_external.tool()
async def get_weather(*, city: str, ctx: Context) -> WeatherData:
    """Tool with structured output.

    Parameters
    ----------
    city
        The name of the city to get the weather for.
    ctx
        The context for the tool call, which can be used to access additional
        information such as the request or other metadata.

    Returns
    -------
    WeatherData
        A Pydantic model containing the weather data for the specified city.
    """

    await ctx.error("In get_weather tool, this is a fake error message.")
    return WeatherData(
        city=city,
        condition="partly cloudy",
        humidity=65.0,
        temperature=22.5,
        wind_speed=12.3,
    )

"""
Make sure:
1. The server is running before running this script.
2. The server is configured to use SSE transport.
3. The server is listening on port 8050.

To run the server:
uv run server.py
"""

# Standard Library
import asyncio

# Third Party Library
from loguru import logger
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.types import TextContent


async def main() -> None:
    """Main function to demonstrate the MCP client connecting to the server."""

    # Connect to the server using SSE
    async with sse_client("http://localhost:8050/sse") as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # Initialize the connection
            await session.initialize()

            # List available tools
            tools_result = await session.list_tools()
            logger.info("Available tools:")
            for tool in tools_result.tools:
                logger.info(f"  - {tool.name}: {tool.description}")

            # Call our calculator tool
            result = await session.call_tool("add", arguments={"a": 2, "b": 3})
            assert isinstance(result.content[0], TextContent)
            logger.info(f"2 + 3 = {result.content[0].text}")


if __name__ == "__main__":
    asyncio.run(main())

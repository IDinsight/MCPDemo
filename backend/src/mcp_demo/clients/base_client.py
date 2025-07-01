"""This module contains the base MCP client for the backend."""

# Standard Library
import asyncio

# Third Party Library
from fastmcp import Client
from fastmcp.utilities.mcp_config import MCPConfig, RemoteMCPServer
from loguru import logger


async def main() -> None:
    """Main function to demonstrate the MCP client connecting to the server."""

    client: Client = Client(
        MCPConfig(
            mcpServers={
                "remote_server": RemoteMCPServer(
                    transport="http", url="http://127.0.0.1:8100/mcp"
                )
            }
        )
    )
    async with client:
        tools = await client.list_tools()
        logger.info(tools)

        # Call tools.
        # res = await client.call_tool("add", {"a": 2, "b": 3})
        # logger.info(f"Result of add: {res}")

        bmi = await client.call_tool("calculate_bmi", {"height": 1.78, "weight": 72})
        logger.info(f"Calculated BMI: {bmi}")


if __name__ == "__main__":
    asyncio.run(main())

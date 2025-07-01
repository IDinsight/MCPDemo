"""Package initialization for the MCP server.

This module imports and exposes key components-intro required for MCP servers, such as
tool registration.

Exports:
    - `register_tools`: The function to register tools with the MCP server.

These components-intro can be imported directly from the package for use in the
application.
"""

# Package Library
from mcp_demo.tools.basic_tools import register_tools

__all__ = ["register_tools"]

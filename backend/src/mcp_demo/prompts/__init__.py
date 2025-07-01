"""Package initialization for prompts.

This module imports and exposes key components-intro for MCP server prompts.
"""

# Package Library
from mcp_demo.prompts.base import (
    data_analysis_prompt,
    data_based_prompt,
    error_correction,
    generate_report_request,
    roleplay_scenario,
)
from mcp_demo.prompts.chat import summarize_chat_history

__all__ = [
    "data_analysis_prompt",
    "data_based_prompt",
    "error_correction",
    "generate_report_request",
    "roleplay_scenario",
    "summarize_chat_history",
]

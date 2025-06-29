"""This module contains the prompts used for chat."""

# Third Party Library
from dotmap import DotMap

# Package Library
from mcp_demo.prompts.base import BasePrompts


class ChatPrompts(BasePrompts):
    """Chat prompts."""

    system_messages = DotMap(
        {
            **BasePrompts.system_messages,
        }
    )
    prompts = DotMap(
        {
            **BasePrompts.prompts,
            "summarize_chat_history": "Summarize the following conversation to be used as a prompt for continuing the conversation later:\n\n{conversation}",
        }
    )

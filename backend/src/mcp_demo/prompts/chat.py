"""This module contains the prompts used for chat."""

# Standard Library
from textwrap import dedent

# Package Library
from mcp_demo.utils.mcp_server import get_mcp_server

mcp_chat = get_mcp_server(server_name="Chat Server")


@mcp_chat.prompt
def error_correction(*, error_info_str: str) -> str:
    """Error correction prompt for LLMs.

    Parameters
    ----------
    error_info_str
        The string containing information about the error that occurred.

    Returns
    -------
    str
        A formatted prompt string for error correction.
    """

    return dedent(
        f"""Your last message resulted in the following errors:

⚠️ **Error during response validation**

{error_info_str}

Please correct your response and try again.
        """
    )


@mcp_chat.prompt
def summarize_chat_history(*, conversation: str) -> str:
    """Summarizes the chat history for later use.

    Parameters
    ----------
    conversation
        The string containing the chat history to be summarized.

    Returns
    -------
    str
        A formatted prompt string for summarizing the chat history.
    """

    return dedent(
        f"""Summarize the following conversation to be used as a prompt for continuing the conversation later:\n\n{conversation}"""
    )

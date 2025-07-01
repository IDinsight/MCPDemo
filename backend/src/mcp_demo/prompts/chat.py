"""This module contains the prompts used for chat."""

# Standard Library
from textwrap import dedent


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

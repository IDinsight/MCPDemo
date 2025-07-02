"""This module contains the base class for all prompts."""

# Standard Library
from textwrap import dedent

# Third Party Library
import aiohttp

from fastmcp import Context
from fastmcp.prompts.prompt import Message
from mcp.types import PromptMessage

# Package Library
from mcp_demo.utils.mcp_server import get_mcp_server

mcp = get_mcp_server(server_name="Main Server")


@mcp.prompt
def data_analysis_prompt(
    *,
    analysis_type: str = "summary",
    data_uri: str,  # Required - no default value
    include_charts: bool = False,
) -> str:
    """Creates a request to analyze data with specific parameters.

    Parameters
    ----------
    analysis_type
        The type of analysis to perform (default is "summary").
    data_uri
        The URI of the data to be analyzed.
    include_charts
        Whether to include charts and visualizations in the analysis (default is False).

    Returns
    -------
    str
        A formatted prompt string for data analysis.
    """

    prompt = (
        f"Please perform a '{analysis_type}' analysis on the data found at {data_uri}."
    )
    if include_charts:
        prompt += " Include relevant charts and visualizations."
    return prompt


@mcp.prompt
async def data_based_prompt(*, data_id: str) -> str:
    """Asynchronous prompt that generates a prompt based on data that needs to be
    fetched.

    NB: In a real-world scenario, you can fetch data from a database or API.

    Parameters
    ----------
    data_id
        The identifier for the data to be fetched.

    Returns
    -------
    str
        A prompt string that includes the fetched data.
    """

    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.example.com/data/{data_id}") as response:
            data = await response.json()
            return f"Analyze this data: {data['content']}"


@mcp.prompt
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


@mcp.prompt
async def generate_report_request(*, ctx: Context, report_type: str) -> str:
    """Generates a request for a report.

    Parameters
    ----------
    ctx
        The context of the request, which includes metadata like request ID.
    report_type
     The type of report to generate.

    Returns
    -------
    str
        A formatted request string for generating the report.
    """

    return f"Please create a {report_type} report. Request ID: {ctx.request_id}"


@mcp.prompt
def roleplay_scenario(*, character: str, situation: str) -> list[PromptMessage]:
    """Set up a role-playing scenario with initial messages.

    Parameters
    ----------
    character
        The character to roleplay as.
    situation
        The situation or context for the roleplay.

    Returns
    -------
    list[PromptMessage]
        A list of messages to initiate the roleplay scenario.
    """

    return [
        Message(f"Let's roleplay. You are {character}. The situation is: {situation}"),
        Message("Okay, I understand. I am ready. What happens next?", role="assistant"),
    ]


@mcp.prompt
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

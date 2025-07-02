"""This module contains examples of basic resources for the MCP demo application."""

# Standard Library
from typing import Any

# Third Party Library
from fastmcp import Context
from fastmcp.exceptions import ResourceError

# Package Library
from mcp_demo.utils.mcp_server import get_mcp_server

mcp = get_mcp_server(server_name="Server A")


@mcp.resource("data://config")
def get_config() -> dict[str, Any]:
    """Resource that returns a JSON data (dict is auto-serialized).

    Returns
    -------
    dict[str, Any]
        A configuration dictionary with theme, version, and features.
    """

    return {
        "theme": "dark",
        "version": "1.2.0",
        "features": ["tools", "resources"],
    }


@mcp.resource("data://{id_}")
def get_data_by_id(*, id_: str) -> dict[str, str]:
    """Error handling example.

    Parameters
    ----------
    id_
        The ID of the data to retrieve.

    Returns
    -------
    dict[str, str]
        A dictionary containing the ID and a value.

    Raises
    ValueError
        If the ID is "secure", indicating that secure data cannot be accessed.
    ResourceError
        If the ID is "missing", indicating that the data is not found in the database.
    """

    if id_ == "secure":
        raise ValueError("Cannot access secure data")
    if id_ == "missing":
        raise ResourceError("Data ID 'missing' not found in database")
    return {"id": id_, "value": "data"}


@mcp.resource("resource://{name}/details")
async def get_details(*, ctx: Context, name: str) -> dict:
    """Get details for a specific name.

    Parameters
    ----------
    ctx
        The context of the request, which includes metadata like request ID.
    name
        The name for which details are requested.
    """

    return {"name": name, "accessed_at": ctx.request_id}


@mcp.resource("resource://greeting")
def get_greeting() -> str:
    """Basic dynamic resource that returns a string.

    Returns
    -------
    str
        A greeting message.
    """

    return "Hello from FastMCP Resources!"


@mcp.resource("repos://{owner}/{repo}/info")
def get_repo_info(*, owner: str, repo: str) -> dict[str, Any]:
    """Resource template with multiple parameters.

    Parameters
    ----------
    owner
        The owner of the repository.
    repo
        The name of the repository.

    Returns
    -------
    dict[str, Any]
        A dictionary containing repository information such as owner, name, full name,
        stars, and forks.
    """

    return {
        "owner": owner,
        "name": repo,
        "full_name": f"{owner}/{repo}",
        "stars": 120,
        "forks": 48,
    }


@mcp.resource("resource://system-status")
async def get_system_status(ctx: Context) -> dict:
    """Provides system status information.

    Parameters
    ----------
    ctx
        The context of the request, which includes metadata like request ID.

    Returns
    -------
    dict
        A dictionary containing the system status and request ID.
    """

    return {"status": "operational", "request_id": ctx.request_id}


@mcp.resource("repo://{owner}/{path*}/template.py")
def get_template_file(*, owner: str, path: str) -> dict[str, str]:
    """Retrieves a file from a specific repository and path, but
    only if the resource ends with `template.py`.

    Parameters
    ----------
    owner
        The owner of the repository.
    path
        The path within the repository where the file is located.

    Returns
    -------
    dict[str, str]
        A dictionary containing the owner, path, and content of the file.
    """

    # Can match repo://jlowin/fastmcp/src/resources/template.py
    return {
        "content": f"File at {path}/template.py in {owner}'s repository",
        "owner": owner,
        "path": path + "/template.py",
    }


def lookup_user(
    *,
    email: str | None = None,
    name: str | None = None,
) -> dict[str, str] | str:
    """Look up a user by either name or email.

    Parameters
    ----------
    email
        The email address of the user.
    name
        The name of the user.

    Returns
    -------
    dict[str, str] | str
        A dictionary containing user information if found, or an error message.
    """

    if email:
        return email
    if name:
        return name
    return {"error": "No lookup parameters provided"}


mcp.resource("users://email/{email}")(lookup_user)
mcp.resource("users://name/{name}")(lookup_user)


@mcp.resource("search://{query}")
def search_resources(
    *, include_archived: bool = False, max_results: int = 10, query: str
) -> dict[str, Any]:
    """Search for resources matching the query string.

    NB: Only 'query' is required in the URI, the other parameters use their defaults.

    Parameters
    ----------
    include_archived
        Whether to include archived resources in the search results.
    max_results
        The maximum number of results to return.
    query
        The search query string.

    Returns
    -------
    dict[str, Any
        A dictionary containing the search query, maximum results, whether archived
        resources are included, and the search results.
    """

    return {
        "query": query,
        "max_results": max_results,
        "include_archived": include_archived,
        "results": "some results",
    }

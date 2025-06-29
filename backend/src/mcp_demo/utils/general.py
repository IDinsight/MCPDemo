"""This module contains general utilities.

NB: As a general rule of thumb, this module should not import utilities from other
utils modules (in order to avoid circular imports). If a utility function is needed in
multiple modules, then it is a general utility and should be defined in this module
instead.
"""

# Standard Library
import re

from pathlib import Path
from typing import Any

# Third Party Library
from loguru import logger


def convert_to_list(x: Any) -> list[Any]:
    """Wrap `x` in a list.

    Parameters
    ----------
    x
        Any object to wrap in a list.

    Returns
    -------
    list[Any]
        The passed in `x` wrapped in a list if it's not a list.
    """

    return [x] if not isinstance(x, list) else x


def escape_angle_brackets(x: Any) -> str:
    """Escape angle brackets for colorized logging. If this is not done, then
    `loguru` will throw a `ValueError` when attempting to log objects with angle
    brackets. See: https://github.com/Delgan/loguru/issues/140 for more details.

    Parameters
    ----------
    x
        Any object.

    Returns
    -------
    str
        The string version of `x` with escaped angle brackets.
    """

    return recurse_replace(r"\>", ">", recurse_replace(r"\<", "<", str(x)))


def make_dir(dir_: str | Path, verbose: bool = True) -> None:
    """Create a directory.

    Parameters
    ----------
    dir_
        Directory to create.
    verbose
        Specifies whether to log directory creation.
    """

    dir_ = Path(dir_)
    if not Path.is_dir(dir_):
        if verbose:
            logger.info(f"Creating directory: {dir_}")
        Path.mkdir(dir_, exist_ok=True, parents=True)
        if verbose:
            logger.success(f"Created directory: {dir_}")


def recurse_replace(new_str: str, orig_str: str, x: Any) -> Any:
    """Recursively replace all instances of `orig_str` in `x` with the value specified
    by `new_str`.

    Parameters
    ----------
    new_str
        The replacement string.
    orig_str
        The original string.
    x
        Either a string, list, or dictionary. This object will be recursively scanned
        in order to replace all instances of `orig_str` with `new_str`.

    Returns
    -------
    Any
        The final return of this function is the original passed in `x` with all
        instances of `orig_str` replaced with `new_str`.

    """

    if isinstance(x, str) and orig_str in x:
        return x.replace(orig_str, new_str)
    if isinstance(x, list):
        for i, item in enumerate(x):
            x[i] = recurse_replace(new_str, orig_str, x=item)
    elif isinstance(x, dict):
        for k, v in list(x.items()):
            k_ = recurse_replace(new_str, orig_str, x=k)
            x.pop(k)
            x[k_] = recurse_replace(new_str, orig_str, x=v)
    return x


def remove_json_markdown(*, text: str) -> str:
    """Remove JSON markdown from text.

    Parameters
    ----------
    text
        The text containing the JSON markdown.

    Returns
    -------
    str
        The text with the json markdown removed.
    """

    text = text.strip()
    text = re.sub(r"```(json)?\n", "", text).rstrip("```")
    text = text.replace(r"\{", "{").replace(r"\}", "}")
    return text.strip()

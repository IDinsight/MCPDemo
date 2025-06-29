"""This module contains the base class for all prompts."""

# Standard Library
from textwrap import dedent

# Third Party Library
from dotmap import DotMap


class BasePrompts:
    """Base prompts. This class mainly serves to contain generally useful prompts and
    type hints for all prompt classes. If a prompt is NOT listed here, then `mypy` will
    catch it.

    PLEASE LIST THINGS IN ALPHABETICAL ORDER HERE!
    """

    system_messages = DotMap(
        {
            "default": "You are a helpful assistant.",
        }
    )
    prompts = DotMap(
        {
            "error_correction": dedent(
                """Your last message resulted in the following errors:

⚠️ **Error during response validation**

{error_info_str}

Please correct your response and try again.
                """
            ),
        }
    )

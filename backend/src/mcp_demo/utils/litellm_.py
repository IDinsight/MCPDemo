"""This module contains utilities for LiteLLM."""

# Standard Library
import asyncio
import json
import traceback

from copy import deepcopy
from typing import Any, Callable, Coroutine, Iterable, Optional, TypeVar

# Third Party Library
import griffe
import litellm

from _griffe.docstrings.models import DocstringSectionText
from aiolimiter import AsyncLimiter
from loguru import logger
from tenacity import (
    AsyncRetrying,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

# Package Library
from mcp_demo.config import Settings
from mcp_demo.prompts.base import error_correction
from mcp_demo.schemas import Limits, ValidatorCall
from mcp_demo.utils.general import convert_to_list, remove_json_markdown

limits = Limits(max_retry_attempts=5)

MODELS_LLM = Settings.MODELS_LLM
LITELLM_API_KEY = Settings.LITELLM_API_KEY
LITELLM_ENDPOINT = Settings.LITELLM_ENDPOINT
LITELLM_MODEL_DEFAULT = Settings.LITELLM_MODEL_DEFAULT
LITELLM_MODEL_EMBEDDING = Settings.LITELLM_MODEL_EMBEDDING
TEXT_GENERATION_DEFAULT = Settings.TEXT_GENERATION_DEFAULT

CALL_TIMEOUT_SEC = 15  # Give up on any single call after N seconds
MAX_IN_FLIGHT = 15  # Max *concurrent* requests
REQS_PER_MINUTE = 100  # Hard rate‑limit
RETRY_ATTEMPTS = 4  # Total tries per call (1 initial + 3 retries)
RETRYABLE_EXCEPTIONS = (TimeoutError, RuntimeError)  # Extend as needed

ASYNC_LIMITER = AsyncLimiter(REQS_PER_MINUTE, time_period=60)

T = TypeVar("T")


class LLMCallException(Exception):
    """Custom exception raised when an LLM call returns an error."""

    def __init__(self, *, error_msg: str) -> None:
        """

        Parameters
        ----------
        error_msg
            The error message returned from the LLM call.
        """

        super().__init__(f"LLM call returned an error: {error_msg}")

        self.error_msg = error_msg


class ValidatorCallException(Exception):
    """Custom exception raised when the maximum number of retries for validating an LLM
    response is met and there are still validation errors.
    """

    def __init__(self, *, error_msg: str) -> None:
        """

        Parameters
        ----------
        error_msg
            The error message returned from the LLM call.
        """

        super().__init__(
            f"Maximum number of validation retries met with error: {error_msg}"
        )

        self.error_msg = error_msg


async def _call_api_single(
    *,
    model: str = LITELLM_MODEL_DEFAULT,
    remove_json_strs: bool = False,
    system_msg: str = "You are a helpful assistant.",
    text_generation_params: Optional[dict[str, Any]] = None,
    user_msg: str,
    validator_call: Optional[ValidatorCall],
) -> str:
    """Single API call, wrapped by limiter *and* timeout but **without** retries
    (handled one layer above).

    Parameters
    ----------
    model
        The API model to use.
    remove_json_strs
        Specifies whether to remove JSON markdown strings from the assistant response.
    system_msg
        The system message to use for the LLM.
    text_generation_params
        Dictionary containing text generation parameters.
    user_msg
        The user message to use for the LLM.
    validator_call
        Dictionary containing the validator call parameters. If provided, then this
        validator will be called on the model API response to validate the response
        content. Upon failure, the error message from the validator will be used to
        as additional context for the model to use in correcting its responses before
        returning the final response to the caller. NB: Validators should not change
        the contents of the generated response, but rather validate it and provide
        additional context for the model to use in correcting its responses.

    Returns
    -------
    str
        The raw content string from the LLM API call.
    """

    async with ASYNC_LIMITER:  # Blocks if we exceed `REQS_PER_MINUTE`
        content = await asyncio.wait_for(
            get_acompletion(
                model=model,
                remove_json_strs=remove_json_strs,
                system_msg=system_msg,
                text_generation_params=text_generation_params
                or TEXT_GENERATION_DEFAULT,
                user_msg=user_msg,
                validator_call=validator_call,
            ),
            timeout=CALL_TIMEOUT_SEC,  # Enforce per‑call timeout
        )

    return content


async def _call_api_with_retry(
    *,
    model: str = LITELLM_MODEL_DEFAULT,
    remove_json_strs: bool = False,
    retry_attempts: int = RETRY_ATTEMPTS,
    sem: asyncio.Semaphore,
    system_msg: str = "You are a helpful assistant.",
    text_generation_params: Optional[dict[str, Any]] = None,
    user_msg: str,
    validator_call: Optional[ValidatorCall] = None,
) -> str:
    """Wrap the raw API calls with:

    1. Concurrency semaphore to limit in-flight works.
    2. Retry logic (retried up to `RETRY_ATTEMPTS`) with random exponential backoff and
        hard capped at `CALL_TIMEOUT_SEC`.

    Parameters
    ----------
    model
        The API model to use.
    remove_json_strs
        Specifies whether to remove JSON markdown strings from the assistant response.
    retry_attempts
        Number of retry attempts to make in case of failure.
    sem
        An asyncio semaphore to limit in-flight works.
    system_msg
        The system message to use for the LLM.
    text_generation_params
        Dictionary containing text generation parameters.
    user_msg
        The user message to use for the LLM.
    validator_call
        Dictionary containing the validator call parameters. If provided, then this
        validator will be called on the model API response to validate the response
        content. Upon failure, the error message from the validator will be used to
        as additional context for the model to use in correcting its responses before
        returning the final response to the caller. NB: Validators should not change
        the contents of the generated response, but rather validate it and provide
        additional context for the model to use in correcting its responses.

    Returns
    -------
    str
        The raw content string from the LLM API call.
    """

    async with sem:  # Overall concurrency guard shared by the batch runner
        async for attempt in AsyncRetrying(  # Retries on errors
            reraise=True,
            retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
            stop=stop_after_attempt(retry_attempts),
            wait=wait_random_exponential(min=1, max=60),
        ):
            with attempt:
                content = await _call_api_single(
                    model=model,
                    remove_json_strs=remove_json_strs,
                    system_msg=system_msg,
                    text_generation_params=text_generation_params
                    or TEXT_GENERATION_DEFAULT,
                    user_msg=user_msg,
                    validator_call=validator_call,
                )

    return content


def _update_messages_with_errors(
    *,
    error: Exception,
    original_assistant_content: str,
    original_messages: list[dict[str, Any]],
    validator_call: ValidatorCall,
) -> list[dict[str, Any]]:
    """Update the original message history with error information.

    NB: We make a deep copy of the original messages since the original message list
    passed in does not need the error information appended to it

    Parameters
    ----------
    error
        The exception that occurred during the validation of the API response.
    original_assistant_content
        The content of the assistant's response before validation.
    original_messages
        List of original messages that were sent to the model.
    validator_call
        Dictionary containing the validator call parameters.

    Returns
    -------
    list[dict[str, Any]]
        The updated list of messages with error information appended.
    """

    new_messages = deepcopy(original_messages)
    validator_module = validator_call.validator_module
    validator_module_str = (
        f"{validator_module.__module__}.{validator_module.__qualname__}"
    )
    validator_fn = griffe.load(validator_module_str)
    section_text = next(
        (
            section
            for section in validator_fn.docstring.parsed  # type: ignore
            if isinstance(section, DocstringSectionText)
        ),
        None,
    )
    validator_docstrings = (
        section_text.value if section_text else "No descriptions available."
    )
    cleaned_trace = "\n".join(
        [line.rstrip() for line in traceback.format_exc().splitlines() if line.strip()]
    )
    error_info = {
        "error": str(error),
        "validator_function": validator_module_str,
        "validator_docstrings": validator_docstrings,
        "traceback": cleaned_trace,
    }
    logger.error(
        f"API call failed validation due to:\n\n"
        f"{error_info}\n\n"
        f"Retry attempts left: {validator_call.num_retries}"
    )
    new_messages.append({"content": original_assistant_content, "role": "assistant"})
    new_messages.append(
        {
            "content": error_correction(
                error_info_str=json.dumps(error_info, indent=2)
            ),
            "role": "user",
        }
    )

    return new_messages


async def _validate_api_call(
    *,
    extra_kwargs: dict[str, Any],
    litellm_endpoint: str = LITELLM_ENDPOINT,
    messages: list[dict[str, Any]],
    model: str,
    remove_json_strs: bool = False,
    text_generation_params: dict[str, Any],
    validator_call: Optional[ValidatorCall] = None,
) -> str:
    """Validate API response.

    Parameters
    ----------
    litellm_endpoint
        The LiteLLM endpoint to use.
    messages
        A list of messages comprising the conversation so far for the model. Each
        dictionary must contain the keys `content` and `role` at a minimum.
    model
        The name of the LLM model to use.
    remove_json_strs
        Specifies whether to remove JSON markdown strings from the assistant response.
    text_generation_params
        Dictionary containing text generation parameters.
    validator_call
        Dictionary containing the validator call parameters.

    Returns
    -------
    str
        The validated content of the LLM response.

    Raises
    ------
    LLMCallException
        If the LLM call returns an error or if the response structure is not as
        expected.
    ValidatorCallException
        If the maximum number of retries for validating an LLM response is met and
        there are still validation errors.
    """

    try:
        response = await litellm.acompletion(
            api_base=litellm_endpoint,
            api_key=LITELLM_API_KEY,
            messages=messages,
            model=model,
            **extra_kwargs,
            **text_generation_params,
        )
    except Exception as e:
        logger.error("Error calling LLM", exc_info=True)
        raise LLMCallException(error_msg=f"Error during LLM call: {e}") from e

    # Check if the returned response contains an error field.
    if hasattr(response, "error") and response.error:
        error_msg = getattr(response, "error", "Unknown error")
        logger.error(f"LLM call returned an error: {error_msg}")
        raise LLMCallException(error_msg=f"LLM call returned an error: {error_msg}")

    # Ensure that the response has valid content.
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError) as e:
        logger.error("LLM response structure is not as expected", exc_info=True)
        raise LLMCallException(
            error_msg="LLM response structure is not as expected"
        ) from e

    content = remove_json_markdown(text=content) if remove_json_strs else content

    # Call validator if provided.
    if validator_call and validator_call.num_retries > 0:
        validator_call.num_retries -= 1
        validator_module = validator_call.validator_module
        validator_kwargs = validator_call.validator_kwargs
        try:
            validator_module(content, **validator_kwargs)
        except Exception as e:  # pylint: disable=W0718
            if validator_call.num_retries == 0:
                raise ValidatorCallException(
                    error_msg="Maximum number of validation retries met"
                ) from e
            new_messages = _update_messages_with_errors(
                error=e,
                original_messages=messages,
                original_assistant_content=content,
                validator_call=validator_call,
            )
            return await _validate_api_call(
                extra_kwargs=extra_kwargs,
                litellm_endpoint=litellm_endpoint,
                messages=new_messages,
                model=model,
                remove_json_strs=remove_json_strs,
                text_generation_params=text_generation_params,
                validator_call=validator_call,
            )

    return content


async def get_acompletion(
    *,
    json_response: bool = False,
    litellm_endpoint: str = LITELLM_ENDPOINT,
    messages: Optional[list[dict[str, Any]]] = None,
    model: Optional[str] = None,
    remove_json_strs: bool = False,
    system_msg: Optional[str] = None,
    text_generation_params: dict[str, Any],
    user_msg: Optional[str] = None,
    validator_call: Optional[ValidatorCall] = None,
) -> str:
    """Get the completion response from the model API asynchronously.

     NB: Validators should not change or mutate the contents of the generated response
     or anything else! It's sole purpose is to validate the generated response and
     provide additional context for the model to use in correcting its responses.

    Parameters
    ----------
    json_response
        Specifies whether to return the response as a JSON object.
    litellm_endpoint
        The LiteLLM endpoint to use.
    messages
        A list of messages comprising the conversation so far for the model. Each
        dictionary must contain the keys `content` and `role` at a minimum. If `None`,
        then `user_msg` and `system_msg` must be provided.
    model
        The name of the LLM model to use. If not specified, then this is pulled from
        `text_generation_params` with a fallback to `LITELLM_MODEL_DEFAULT`.
    remove_json_strs
        Specifies whether to remove JSON markdown strings from the assistant response.
    system_msg
        The system message. If `None`, then `messages` must be provided.
    text_generation_params
        Dictionary containing text generation parameters.
    user_msg
        The user message. If `None`, then `messages` must be provided.
    validator_call
        Dictionary containing the validator call parameters. If provided, then this
        validator will be called on the model API response to validate the response
        content. Upon failure, the error message from the validator will be used to
        as additional context for the model to use in correcting its responses before
        returning the final response to the caller.

    Returns
    -------
    str
        The completion response from the model provider.
    """

    if not messages:
        assert isinstance(user_msg, str) and isinstance(system_msg, str)
        messages = [
            {"content": system_msg, "role": "system"},
            {"content": user_msg, "role": "user"},
        ]

    extra_kwargs = {}
    if json_response:
        extra_kwargs["response_format"] = {"type": "json_object"}

    text_generation_params_copy = deepcopy(text_generation_params)
    if model:
        text_generation_params_copy.pop("model", None)
    else:
        model = text_generation_params_copy.pop("model", LITELLM_MODEL_DEFAULT)

    assert isinstance(model, str) and model
    content = await _validate_api_call(
        extra_kwargs=extra_kwargs,
        litellm_endpoint=litellm_endpoint,
        messages=messages,
        model=model,
        remove_json_strs=remove_json_strs,
        text_generation_params=text_generation_params_copy,
        validator_call=validator_call,
    )

    return content


async def get_acompletion_batch(
    *,
    make_coros: Callable[[asyncio.Semaphore], Iterable[Coroutine[Any, Any, T]]],
    max_errors_to_show: int = 3,
    max_in_flight: int = MAX_IN_FLIGHT,
) -> list[T | BaseException]:
    """Make API calls in batch mode.

    The process is as follows:

    1. Create one task per classification.
    2. Guarantee that *all* tasks finish and keep the exceptions for inspection.
    3. Check for any failures.

    Parameters
    ----------
    make_coros
        A callable that receives the semaphore and returns an iterable of
        *already-constructed* coroutine objects.
    max_in_flight
        Upper-bound on concurrently-running tasks.
    max_errors_to_show
        How many error objects to attach to the raised `RuntimeError` (helps avoid huge
        tracebacks).

    Returns
    -------
    list[T | BaseException]
        List of successful results, **in the same order as supplied coroutines.**

    Raises
    ------
    RuntimeError
        If any coroutine raised an exception.
    """

    logger.info("Making API calls in parallel...")

    # 1.
    sem = asyncio.Semaphore(max_in_flight)
    coros = list(make_coros(sem))
    tasks = [asyncio.create_task(coro) for coro in coros]

    # 2.
    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]

    # 3.
    if errors:
        # Cancel any tasks that are still running (just in case).
        for t in tasks:
            if not t.done():
                t.cancel()
        raise RuntimeError(
            f"Batch aborted: {len(errors)} / {len(results)} calls failed.",
            errors[:max_errors_to_show],
        )

    logger.success("Finished making API calls in parallel!")

    return results


@retry(
    stop=stop_after_attempt(limits.max_retry_attempts),
    wait=wait_random_exponential(min=1, max=60),
)
def get_completion(
    *,
    json_response: bool = False,
    messages: Optional[list[dict[str, Any]]] = None,
    model: Optional[str] = None,
    remove_json_strs: bool = False,
    system_msg: Optional[str] = None,
    text_generation_params: dict[str, Any],
    user_msg: Optional[str] = None,
) -> str:
    """Get the completion response from the model API.

    NB: This function is a synchronous wrapper around the `litellm.completion` method
    and is meant to be called when using the LitelLM Python SDK directly. When using
    the LitelLM proxy server, the `get_acompletion` function should be used instead.

    Parameters
    ----------
    json_response
        Specifies whether to return the response as a JSON object.
    messages
        A list of messages comprising the conversation so far for the model. Each
        dictionary must contain the keys `content` and `role` at a minimum. If `None`,
        then `user_msg` and `system_msg` must be provided.
    model
        The name of the LLM model to use. If not specified, then this is pulled from
        `text_generation_params` with a fallback to `LITELLM_MODEL_DEFAULT`.
    remove_json_strs
        Specifies whether to remove JSON markdown strings from the assistant response.
    system_msg
        The system message. If `None`, then `messages` must be provided.
    text_generation_params
        Dictionary containing text generation parameters.
    user_msg
        The user message. If `None`, then `messages` must be provided.

    Returns
    -------
    str
        The completion response from the model provider.

    Raises
    ------
    LLMCallException
        If the LLM call returns an error or if the response structure is not as
        expected.
    """

    if not messages:
        assert isinstance(user_msg, str) and isinstance(system_msg, str)
        messages = [
            {"content": system_msg, "role": "system"},
            {"content": user_msg, "role": "user"},
        ]
    extra_kwargs = {}
    if json_response:
        extra_kwargs["response_format"] = {"type": "json_object"}

    text_generation_params_copy = text_generation_params.copy()
    if model:
        text_generation_params_copy.pop("model", None)
    else:
        model = text_generation_params_copy.pop("model", MODELS_LLM)

    try:
        response = litellm.completion(
            messages=messages,
            model=model,
            **extra_kwargs,
            **text_generation_params_copy,
        )
    except Exception as e:
        logger.error("Error calling LLM", exc_info=True)
        raise LLMCallException(error_msg=f"Error during LLM call: {e}") from e

    # Check if the returned response contains an error field.
    if hasattr(response, "error") and response.error:
        error_msg = getattr(response, "error", "Unknown error")
        logger.error(f"LLM call returned an error: {error_msg}")
        raise LLMCallException(error_msg=f"LLM call returned an error: {error_msg}")

    # Ensure that the response has valid content.
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError) as e:
        logger.error("LLM response structure is not as expected", exc_info=True)
        raise LLMCallException(
            error_msg="LLM response structure is not as expected"
        ) from e

    return remove_json_markdown(text=content) if remove_json_strs else content


@retry(
    stop=stop_after_attempt(limits.max_retry_attempts),
    wait=wait_random_exponential(min=1, max=60),
)
def get_embedding(
    *, batch_size: Optional[int] = None, model_name: str, text: str | list[str]
) -> dict[str, Any]:
    """Get the embedding response from the model API.

    Parameters
    ----------
    batch_size
        The batch size for the embedding model.
    model_name
        The model name.
    text
        The string (or a list of strings) to embed.

    Returns
    -------
    dict[str, Any]
        The full response from the model provider.
    """

    all_responses: dict[str, Any] = {}
    text = convert_to_list(text)
    batch_size = batch_size or len(text)
    for i in range(0, len(text), batch_size):
        batch_text = text[i : i + batch_size]
        response = litellm.embedding(model_name, input=batch_text).model_dump()
        if not all_responses:
            all_responses = response
            continue

        for next_index, data_dict in enumerate(
            response["data"], all_responses["data"][-1]["index"] + 1
        ):
            data_dict["index"] = next_index
            all_responses["data"].append(data_dict)
        all_responses["usage"]["prompt_tokens"] += response["usage"].get(
            "prompt_tokens", 0
        )
        all_responses["usage"]["total_tokens"] += response["usage"].get(
            "total_tokens", 0
        )
    logger.info("Finished obtaining all embeddings!")
    return all_responses

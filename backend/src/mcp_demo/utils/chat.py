"""This module contains utilities for handling chat messages and histories."""

# Standard Library
import hashlib
import json
import os
import struct
import time

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Third Party Library
import aiofiles
import redis.asyncio as aioredis
import requests

from colored import Back, Fore, Style
from fastapi import Request
from litellm import token_counter
from loguru import logger

# Package Library
from mcp_demo.config import Settings
from mcp_demo.prompts.chat import summarize_chat_history
from mcp_demo.utils.general import convert_to_list
from mcp_demo.utils.litellm_ import get_acompletion

CHAT_ENV = Settings.CHAT_ENV
LITELLM_ENDPOINT = Settings.LITELLM_ENDPOINT
LITELLM_MODEL_CHAT = Settings.LITELLM_MODEL_CHAT
REDIS_CACHE_PREFIX_CHAT = Settings.REDIS_CACHE_PREFIX_CHAT
TEXT_GENERATION_DEFAULT = Settings.TEXT_GENERATION_DEFAULT


class InvalidChatStateFormat(Exception):
    """Raised when chat state stored in Redis is not valid JSON."""


class AsyncChatSessionManager:
    """Manage chat sessions using Redis."""

    _base_key_prefix: str = REDIS_CACHE_PREFIX_CHAT
    _default_max_input_tokens = 1048576
    _default_max_output_tokens = 8192
    _default_max_tokens = 8192

    def __init__(
        self, *, env: Optional[str] = CHAT_ENV, redis_client: aioredis.Redis
    ) -> None:
        """

        Parameters
        ----------
        env
            The environment for the chat session ID generator (e.g., "prod", "dev").
        redis_client
            The Redis client to use for storing session information.
        """

        self.environment = env
        self.redis_client = redis_client

    def _build_context(self, *, namespace: str, signed: bool, user_id: str) -> str:
        """Build the context string for generating the chat session ID.

        Parameters
        ----------
        namespace
            The namespace to use for generating the chat session ID. This is used to
            uniquely identify the chat session within a specific namespace (e.g.,
            "chat").
        signed
            Specifies whether to return a signed integer for the session ID.
        user_id
            The ID of the user initiating the session. This is used to uniquely identify
            the user for whom the session ID is being generated.

        Returns
        -------
        str
            The context string used for generating the chat session ID.
        """

        return f"{self.environment}:{namespace}:{user_id}:signed={signed}"

    @staticmethod
    def _hash_context_to_int32(*, context: str, signed: bool) -> int:
        """Hash the context string to a 32-bit integer.

        Parameters
        ----------
        context
            The context string to hash. This should be a string that uniquely
            identifies the chat session.
        signed
            Specifies whether to return a signed integer for the session ID.

        Returns
        -------
        int
            A 32-bit integer derived from the SHA256 hash of the context string.
        """

        hash_bytes = hashlib.sha256(context.encode()).digest()
        hash_int = int.from_bytes(hash_bytes[:4], byteorder="little", signed=False)
        return struct.unpack("i", struct.pack("I", hash_int))[0] if signed else hash_int

    @staticmethod
    def _init_chat_history(
        *, chat_params: dict[str, Any], session_id: int | str, system_message: str
    ) -> list[dict[str, str | None]]:
        """

        Parameters
        ----------
        chat_params
            Dictionary containing the chat parameters for the session.
        session_id
            The session ID for which to initialize the chat history.
        system_message
            A system message to initialize the chat history.

        Returns
        -------
        list[dict[str, str | None]]
            The initialized chat history.
        """

        logger.info(f"Initializing chat history for session: {session_id}")
        chat_history: list[dict[str, str | None]] = []
        append_message_content_to_chat_history(
            chat_history=chat_history,
            message_content=system_message,
            model=chat_params["model"],
            model_context_length=chat_params["max_input_tokens"],
            name=str(session_id),
            role="system",
            total_tokens_for_next_generation=chat_params["max_output_tokens"],
        )
        logger.success(f"Finished initializing chat history for session: {session_id}")
        return chat_history

    def _init_chat_params(
        self,
        *,
        model_name: str,
        session_id: int | str,
        text_generation_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Initialize chat parameters for the session.

        Parameters
        ----------
        model_name
            The LiteLLM model name.
        session_id
            The session ID for which to initialize the chat parameters.
        text_generation_params
            Dictionary containing the text generation parameters to use for the chat
            session.

        Returns
        -------
        dict[str, Any]
            The initialized chat parameters for the given session ID.
        """

        logger.info(f"Initializing chat parameters for session: {session_id}")
        chat_params = {}
        model_info_endpoint = f"{LITELLM_ENDPOINT.rstrip('/')}/model/info"
        model_info = requests.get(
            model_info_endpoint, headers={"accept": "application/json"}, timeout=600
        ).json()
        for dict_ in model_info["data"]:
            if dict_["model_name"] == model_name:
                chat_params = dict_["model_info"]
                assert "model" not in chat_params
                assert "text_generation_params" not in chat_params
                chat_params["model"] = dict_["litellm_params"]["model"]
                chat_params["text_generation_params"] = text_generation_params
                break
        assert (
            chat_params
        ), f"Model '{model_name}' not found in model info from {model_info_endpoint}."
        if chat_params["max_input_tokens"] is None:
            logger.warning(
                f"Got `None` for 'max_input_tokens' for model name: {model_name}. "
                f"Setting 'max_input_tokens' to {self._default_max_input_tokens}."
            )
            chat_params["max_input_tokens"] = self._default_max_input_tokens
        if chat_params["max_output_tokens"] is None:
            logger.warning(
                f"Got `None` for 'max_output_tokens' for model name: {model_name}. "
                f"Setting 'max_output_tokens' to {self._default_max_output_tokens}."
            )
            chat_params["max_output_tokens"] = self._default_max_output_tokens
        if chat_params["max_tokens"] is None:
            logger.warning(
                f"Got `None` for 'max_tokens' for model name: {model_name}. "
                f"Setting 'max_tokens' to {self._default_max_tokens}."
            )
            chat_params["max_tokens"] = self._default_max_tokens
        logger.success(
            f"Finished initializing chat parameters for session: {session_id}"
        )
        return chat_params

    async def check_if_chat_session_exists(
        self, *, namespace: str, signed: bool = True, user_id: str
    ) -> tuple[bool, int | str]:
        """Check if the chat session exists in Redis.

        Parameters
        ----------
        namespace
            The namespace to use for generating the chat session ID. This is used to
            uniquely identify the chat session within a specific namespace (e.g.,
            "chat").
        signed
            Specifies whether to return a signed integer for the session ID.
        user_id
            The ID of the user initiating the session. This is used to uniquely identify
            the user for whom the session ID is being generated.

        Returns
        -------
        tuple[bool, int | str]
            A tuple containing a boolean indicating whether the chat session exists and
            the session ID.
        """

        context = self._build_context(
            namespace=namespace, signed=signed, user_id=user_id
        )
        session_id = self._hash_context_to_int32(context=context, signed=signed)
        redis_key = self.get_redis_key(session_id=session_id)
        chat_session_exists = bool(await self.redis_client.exists(redis_key))
        return chat_session_exists, session_id

    async def delete_chat_history(self, *, session_id: int | str) -> None:
        """Delete the chat history in Redis for a given session ID.

        Parameters
        ----------
        session_id
            The session ID whose chat history should be updated.

        Raises
        ------
        ValueError
            If the session ID does not exist in Redis.
        """

        redis_key = self.get_redis_key(session_id=session_id)

        if not await self.redis_client.exists(redis_key):
            raise ValueError(f"Session ID '{session_id}' does not exist.")

        await self.redis_client.delete(redis_key)

    async def dump_chat_session_to_file(self, *, session_id: int | str) -> None:
        """Dump a chat session to a timestamped JSON file asynchronously.

        Parameters
        ----------
        session_id
            The session ID to dump.

        Raises
        ------
        ValueError
            If the session does not exist or contains no metadata.
        """

        logger.info(f"Dumping chat session for session ID: {session_id}")
        redis_key = self.get_redis_key(session_id=session_id)
        if not await self.redis_client.exists(redis_key):
            raise ValueError(f"Session ID '{session_id}' does not exist.")
        raw_metadata = await self.redis_client.hgetall(redis_key)  # type: ignore
        if not raw_metadata:
            raise ValueError(f"No metadata found for session ID '{session_id}'.")
        metadata = dict(raw_metadata.items())
        assert "logged_at" not in metadata, f"{metadata = }"
        metadata["logged_at"] = datetime.now(timezone.utc).isoformat()

        # Decode specific JSON fields back into objects.
        for field in ["chat_history", "chat_params"]:
            metadata[field] = json.loads(metadata[field])

        # Ensure proper types for numeric fields.
        metadata["created_at"] = int(metadata["created_at"])
        metadata["signed"] = metadata["signed"].lower() == "true"

        logs_dir = Path(os.getenv("PATHS_LOGS_DIR", ""))
        assert logs_dir.is_dir(), f"Logs directory does not exist: {logs_dir}"
        fp = logs_dir / "chat_sessions" / f"{session_id}.jsonl"

        async with aiofiles.open(fp, mode="a", encoding="utf-8") as f:
            await f.write(json.dumps(metadata, ensure_ascii=False))
            await f.write("\n")
        logger.success(f"Finished dumping chat session for session ID: {session_id}")

    async def get_chat_state(self, *, session_id: int | str) -> dict[str, Any]:
        """Retrieve the chat state for a given session ID.

        Parameters
        ----------
        session_id
            The session ID for which to retrieve chat state.

        Returns
        -------
        dict[str, Any]
            Dictionary containing various chat state parameters for the given session
            ID.

        Raises
        ------
        InvalidChatStateFormat
            If the chat state stored in Redis is not valid JSON.
        """

        redis_key = self.get_redis_key(session_id=session_id)
        chat_history_raw, chat_params_raw = await self.redis_client.hmget(
            redis_key, ["chat_history", "chat_params"]
        )  # type: ignore
        try:
            chat_history = json.loads(chat_history_raw)
            chat_params = json.loads(chat_params_raw)
            return {"chat_history": chat_history, "chat_params": chat_params}
        except json.JSONDecodeError as e:
            raise InvalidChatStateFormat(
                f"Invalid JSON for chat states in session {session_id}: {e}"
            ) from e

    def get_redis_key(self, *, session_id: int | str) -> str:
        """Return the Redis key for a given session ID.

        Parameters
        ----------
        session_id
            The session ID for which to generate the Redis key.

        Returns
        -------
        str
            The Redis key for the given session ID.
        """

        return f"{self._base_key_prefix}:{session_id}"

    async def init_chat_session(
        self,
        *,
        as_string: bool = True,
        model_name: str = "chat",
        namespace: str = "chat",
        reset_chat_session: bool = False,
        signed: bool = True,
        system_message: str = "You are a helpful assistant.",
        text_generation_params: Optional[dict[str, Any]] = None,
        topic: Optional[str | list[str]] = None,
        user_id: str,
    ) -> tuple[list[dict[str, str | None]], dict[str, Any], int | str]:
        """Generate a unique 32-bit chat session ID based on user ID amd optional topic.

        Parameters
        ----------
        as_string
            Specifies whether to return the session ID as a string.
        model_name
            The LiteLLM model name.
        namespace
            The namespace to use for generating the chat session ID. This is used to
            uniquely identify the chat session within a specific namespace (e.g.,
            "chat").
        reset_chat_session
            Specifies whether to reset the chat session for the user.
        signed
            Specifies whether to return a signed 32-bit integer.
        system_message
            A system message to initialize the chat history.
        text_generation_params
            Dictionary containing the text generation parameters. If `None`, then the
            default `TEXT_GENERATION_DEFAULT` will be used.
        topic
            A list of topic keywords associated with the chat session. This can also be
            a single string or `None`. If `None`, the session ID will be based solely
            on the `user_id` and timestamp.
        user_id
            The ID of the user initiating the session.

        Returns
        -------
        tuple[list[dict[str, str | None]], dict[str, Any], int | str]
            The chat history, chat parameters, and session ID for the initialized chat
            session.
        """

        text_generation_params = text_generation_params or TEXT_GENERATION_DEFAULT
        topic_list = convert_to_list(topic or [])
        context = self._build_context(
            namespace=namespace, signed=signed, user_id=user_id
        )
        session_id = self._hash_context_to_int32(context=context, signed=signed)
        redis_key = self.get_redis_key(session_id=session_id)
        if reset_chat_session or not await self.redis_client.exists(redis_key):
            logger.info(f"Initialize new chat session for session_id: {session_id}")
            chat_params = self._init_chat_params(
                model_name=model_name,
                session_id=session_id,
                text_generation_params=text_generation_params,
            )
            chat_history = self._init_chat_history(
                chat_params=chat_params,
                session_id=session_id,
                system_message=system_message,
            )
            metadata = {
                "chat_history": json.dumps(chat_history),
                "chat_params": json.dumps(chat_params),
                "created_at": str(int(time.time())),
                "environment": self.environment,
                "namespace": namespace,
                "session_id": str(session_id),
                "signed": str(signed),
                "topic": ",".join(sorted(topic_list)) if topic_list else "",
                "user_id": user_id,
            }  # Do NOT use "logged_at" key here!
            await self.redis_client.hset(redis_key, mapping=metadata)  # type: ignore
            logger.success(
                f"Finished initializing new chat session for session_id: {session_id}"
            )
        else:
            logger.info(
                f"Retrieving existing chat session for session_id: {session_id}."
            )

        chat_state = await self.get_chat_state(session_id=session_id)
        chat_history = chat_state["chat_history"]
        chat_params = chat_state["chat_params"]
        return chat_history, chat_params, str(session_id) if as_string else session_id

    async def reset_chat_history(self, *, session_id: int | str) -> None:
        """Reset the chat history.

        Parameters
        ----------
        session_id
            The session ID to reset the chat history for.

        Raises
        ------
        ValueError
            If the session ID does not exist in Redis.
        """

        logger.log("ATTN", f"Resetting chat history for session: {session_id}")
        redis_key = self.get_redis_key(session_id=session_id)
        if not await self.redis_client.exists(redis_key):
            raise ValueError(f"Session ID '{session_id}' does not exist.")
        await self.redis_client.hset(redis_key, "chat_history", json.dumps([]))  # type: ignore
        logger.success(f"Finished resetting chat history for session: {session_id}")

    async def update_chat_history(
        self, *, chat_history: list[dict[str, str | None]], session_id: int | str
    ) -> None:
        """Update the chat history in Redis for a given session ID.

        Parameters
        ----------
        chat_history
            A list of messages (dicts) to store as the new chat history.
        session_id
            The session ID whose chat history should be updated.

        Raises
        ------
        ValueError
            If the session ID does not exist in Redis.
        TypeError
            If `chat_history` is not a list of dicts.
        """

        redis_key = self.get_redis_key(session_id=session_id)

        if not await self.redis_client.exists(redis_key):
            raise ValueError(f"Session ID '{session_id}' does not exist.")
        if not isinstance(chat_history, list) or not all(
            isinstance(m, dict) for m in chat_history
        ):
            raise TypeError("`chat_history` must be a list of dicts.")

        await self.redis_client.hset(
            redis_key, "chat_history", json.dumps(chat_history)
        )  # type: ignore

    async def summarize_chat_history(
        self, *, session_id: int | str
    ) -> tuple[list[dict[str, str | None]], dict[str, Any]]:
        """Summarize the chat history for a given session ID. This function can be used
        to summarize the chat history for a session to reduce the size of the chat
        history while retaining the context of the conversation.

        Parameters
        ----------
        session_id
            The session ID for the chat whose history is to be summarized.

        Returns
        -------
        tuple[list[dict[str, str | None]], dict[str, Any]]
            A tuple containing the summarized chat history and the chat parameters.

        Raises
        ------
        ValueError
            If the session ID does not exist in Redis.
        """

        logger.log("ATTN", f"Summarizing chat history for session: {session_id}")
        redis_key = self.get_redis_key(session_id=session_id)
        if not await self.redis_client.exists(redis_key):
            raise ValueError(f"Session ID '{session_id}' does not exist.")

        old_chat_state = await self.get_chat_state(session_id=session_id)
        old_chat_history = old_chat_state["chat_history"]
        chat_params = old_chat_state["chat_params"]
        assert all(
            x in chat_params
            for x in [
                "max_input_tokens",
                "max_output_tokens",
                "model",
                "text_generation_params",
            ]
        ), f"{chat_params = }"

        if not old_chat_history:
            logger.error(f"No chat history found for session: {session_id}")
            return old_chat_history, chat_params

        summary_index = 1 if old_chat_history[0].get("role", None) == "system" else 0
        if len(old_chat_history) <= summary_index:
            logger.log(
                "ATTN",
                "The existing chat history does not contain any messages to summarize!",
            )
            return old_chat_history, chat_params

        text_generation_params = deepcopy(chat_params["text_generation_params"])
        text_generation_params["n"] = 1
        conversation = ""
        for message in old_chat_history[summary_index:]:
            role = message.get("role", "N/A")
            content = message.get("content", "")
            conversation += f"Role: {role}\tContent: {content}\n\n"
        assert conversation, "Got empty conversation for summarization!"
        messages = [
            {
                "content": summarize_chat_history(convservation=conversation),
                "role": "user",
            }
        ]
        summary_content = await get_acompletion(
            messages=messages, text_generation_params=text_generation_params
        )
        logger.debug(f"Summary of chat history: {summary_content}")

        max_input_tokens = chat_params["max_input_tokens"]
        max_output_tokens = chat_params["max_output_tokens"]
        model = chat_params["model"]
        system_message = old_chat_history.pop(0) if summary_index == 1 else {}
        new_chat_history: list[dict[str, str | None]] = []
        if system_message:
            append_message_content_to_chat_history(
                chat_history=new_chat_history,
                message_content=system_message["content"],
                model=model,
                model_context_length=max_input_tokens,
                name=session_id,
                role="system",
                total_tokens_for_next_generation=max_output_tokens,
            )
        append_message_content_to_chat_history(
            chat_history=new_chat_history,
            message_content=summary_content,
            model=model,
            model_context_length=max_input_tokens,
            name=session_id,
            role="user",
            total_tokens_for_next_generation=max_output_tokens,
        )
        await self.redis_client.hset(
            redis_key, "chat_history", json.dumps(new_chat_history)
        )  # type: ignore
        logger.success(f"Finished summarizing chat history for session: {session_id}")
        return new_chat_history, chat_params


def _truncate_chat_history(
    *,
    chat_history: list[dict[str, str | None]],
    model: str,
    model_context_length: int,
    total_tokens_for_next_generation: int,
) -> None:
    """Truncate the chat history if necessary. This process removes older messages past
    the total token limit of the model (but maintains the initial system message if
    any) and effectively mimics an infinite chat buffer.

    NB: This process does not reset or summarize the chat history. Reset and
    summarization are done explicitly. Instead, this function should be invoked each
    time a message is appended to the chat history.

    Parameters
    ----------
    chat_history
        The chat history buffer.
    model
        The name of the LLM model.
    model_context_length
        The maximum number of tokens allowed for the model. This is the context window
        length for the model (i.e, maximum number of input + output tokens).
    total_tokens_for_next_generation
        The total number of tokens used during ext generation.
    """

    chat_history_tokens = token_counter(messages=chat_history, model=model)
    remaining_tokens = model_context_length - (
        chat_history_tokens + total_tokens_for_next_generation
    )
    if remaining_tokens > 0:
        return
    logger.log(
        "ATTN",
        f"Truncating earlier chat messages for next generation.\n"
        f"Model context length: {model_context_length}\n"
        f"Total tokens so far: {chat_history_tokens}\n"
        f"Total tokens requested for next generation: "
        f"{total_tokens_for_next_generation}",
    )
    index = 1 if chat_history[0]["role"] == "system" else 0
    while remaining_tokens <= 0 and chat_history:
        index = min(len(chat_history) - 1, index)
        last_message = chat_history.pop(index)
        chat_history_tokens -= token_counter(messages=[last_message], model=model)
        remaining_tokens = model_context_length - (
            chat_history_tokens + total_tokens_for_next_generation
        )
        if remaining_tokens <= 0 and not chat_history:
            chat_history.append(last_message)
            break
    if not chat_history:
        logger.log("ATTN", "Empty chat history after truncating chat messages!")


def append_message_content_to_chat_history(
    *,
    chat_history: list[dict[str, str | None]],
    message_content: Optional[str] = None,
    model: str,
    model_context_length: int,
    name: int | str,
    role: str,
    total_tokens_for_next_generation: int,
    truncate_history: bool = True,
) -> None:
    """Append a single message content to the chat history.

    Parameters
    ----------
    chat_history
        The chat history buffer.
    message_content
        The contents of the message. `message_content` is required for all messages,
        and may be null for assistant messages with function calls.
    model
        The name of the LLM model.
    model_context_length
        The maximum number of tokens allowed for the model. This is the context window
        length for the model (i.e, maximum number of input + output tokens).
    name
        The name of the author of this message. `name` is required if role is
        `function`, and it should be the name of the function whose response is in
        the content. May contain a-z, A-Z, 0-9, and underscores, with a maximum length
        of 64 characters.
    role
        The role of the messages author.
    total_tokens_for_next_generation
        The total number of tokens during text generation.
    truncate_history
        Specifies whether to truncate the chat history. Truncation is done after all
        messages are appended to the chat history.
    """

    name = str(name)
    roles = ["assistant", "function", "system", "tool", "user"]
    assert len(name) <= 64, f"`name` must be <= 64 characters: {name}"
    assert role in roles, f"Invalid role: {role}. Valid roles are: {roles}"
    if role not in ["assistant", "function"]:
        assert (
            message_content is not None
        ), "`message_content` can only be `None` for `assistant` and `function` roles."
    message = {"content": message_content, "name": name, "role": role}
    chat_history.append(message)
    if truncate_history:
        _truncate_chat_history(
            chat_history=chat_history,
            model=model,
            model_context_length=model_context_length,
            total_tokens_for_next_generation=total_tokens_for_next_generation,
        )


def append_messages_to_chat_history(
    *,
    chat_history: list[dict[str, str | None]],
    messages: dict[str, str | None] | list[dict[str, str | None]],
    model: str,
    model_context_length: int,
    total_tokens_for_next_generation: int,
) -> None:
    """Append a list of messages to the chat history.

    NB: Truncation should be done after ALL messages are appended to the chat history.

    Parameters
    ----------
    chat_history
        The chat history buffer.
    messages
        A list of messages to be appended to the chat history. The order of the
        messages in the list is the order in which they are appended to the chat
        history.
    model
        The name of the LLM model.
    model_context_length
        The maximum number of tokens allowed for the model. This is the context window
        length for the model (i.e, maximum number of input + output tokens).
    total_tokens_for_next_generation
        The total number of tokens during text generation.
    """

    for message in convert_to_list(messages):
        name = message.get("name", None)
        role = message.get("role", None)
        assert name and role
        append_message_content_to_chat_history(
            chat_history=chat_history,
            message_content=message.get("content", None),
            model=model,
            model_context_length=model_context_length,
            name=name,
            role=role,
            total_tokens_for_next_generation=total_tokens_for_next_generation,
            truncate_history=False,
        )
    _truncate_chat_history(
        chat_history=chat_history,
        model=model,
        model_context_length=model_context_length,
        total_tokens_for_next_generation=total_tokens_for_next_generation,
    )


def format_prompt(
    *,
    prompt: str,
    prompt_kws: Optional[dict[str, Any]] = None,
    remove_leading_blank_spaces: bool = True,
) -> str:
    """Format prompt.

    Parameters
    ----------
    prompt
        String denoting the prompt.
    prompt_kws
        If not `None`, then a dictionary containing <key, value> pairs of parameters to
        use for formatting `prompt`.
    remove_leading_blank_spaces
        Specifies whether to remove leading blank spaces from the prompt.

    Returns
    -------
    str
        The formatted prompt.
    """

    if remove_leading_blank_spaces:
        prompt = "\n".join([m.lstrip() for m in prompt.split("\n")])
    return prompt.format(**prompt_kws) if prompt_kws else prompt


async def get_chat_response(
    *,
    chat_history: Optional[list[dict[str, str | None]]] = None,
    chat_params: dict[str, Any],
    litellm_model: str = LITELLM_MODEL_CHAT,
    message: str,
    name: Optional[str] = None,
    remove_json_strs: bool = False,
    role: str = "user",
    session_id: int | str,
    **kwargs: Any,
) -> str:
    """Get the chat response. Chat responses appends two messages to the chat history,
    one from the user and one from the assistant. The user message is appended first,
    and the assistant message is appended after the response is received from the LLM
    provider.

    Parameters
    ----------
    chat_history
        The chat history buffer.
    chat_params
        Dictionary containing the chat parameters.
    litellm_model
        The LiteLLM model **endpoint** name.
    message
        A string containing the message itself.
    name
        The name of the author of this message. `name` is required if role is
        `function`, and it should be the name of the function whose response is in
        the content. May contain a-z, A-Z, 0-9, and underscores, with a maximum length
        of 64 characters.
    remove_json_strs
        Specifies whether to remove JSON markdown strings from the assistant response.
    role
        The role of the messages author.
    session_id
        The session ID for the chat.
    kwargs
        Additional keyword arguments for calling the LLM provider.

    Returns
    -------
    str
        The response from the LLM model.
    """

    chat_history = chat_history or []
    model = chat_params["model"]
    model_context_length = chat_params["max_input_tokens"]
    text_generation_params = chat_params.get(
        "text_generation_params", TEXT_GENERATION_DEFAULT
    )
    total_tokens_for_next_generation = chat_params["max_output_tokens"]

    append_message_content_to_chat_history(
        chat_history=chat_history,
        message_content=message,
        model=model,
        model_context_length=model_context_length,
        name=name or session_id,
        role=role,
        total_tokens_for_next_generation=total_tokens_for_next_generation,
    )
    message_content = await get_acompletion(
        messages=chat_history,
        model=litellm_model,
        remove_json_strs=remove_json_strs,
        text_generation_params=text_generation_params,
        **kwargs,
    )
    append_message_content_to_chat_history(
        chat_history=chat_history,
        message_content=message_content,
        model=model,
        model_context_length=model_context_length,
        name=name or session_id,
        role="assistant",
        total_tokens_for_next_generation=total_tokens_for_next_generation,
    )

    return message_content


async def get_chat_session_manager(*, request: Request) -> AsyncChatSessionManager:
    """Get an async chat session manager.

    NB: This function is intended to be used within an async context (e.g., FastAPI
    route handlers).

    Parameters
    ----------
    request
        The FastAPI request object.

    Returns
    -------
    AsyncChatSessionManager
        An instance of `AsyncChatSessionManager` that can be used to generate
        unique chat session IDs and manage chat sessions.
    """

    return AsyncChatSessionManager(redis_client=request.app.state.redis)


def log_chat_history(
    *,
    chat_history: list[dict[str, Any]],
    context: Optional[str] = None,
    session_id: int | str,
) -> None:
    """Log the chat history.

    Parameters
    ----------
    chat_history
        A list of dictionaries representing chat messages. Each dictionary should have
        at least the keys `role` and `content`.
    context
        Optional string that denotes the context in which the chat history is being
        logged. Useful to keep track of the call chain execution.
    session_id
        The ID of the chat session whose history is being logged.
    """

    if CHAT_ENV not in ["testing"]:
        return

    context = context or "No Context Provided"
    header_str = (
        f"###Chat History For Session: {session_id} | Context: {context.upper()}###"
    )
    chat_history_str = ""

    for message in chat_history:
        role = message["role"]
        content = message["content"]
        name = message.get("name", None)
        function_call = message.get("function_call", None)

        match role:
            case "system":
                chat_history_str += f"{Style.BOLD}{Fore.cyan}{Back.dark_gray}{role}{Style.reset}: {Fore.cyan}{content}{Style.reset}\n\n"
            case "assistant":
                chat_history_str += f"{Style.BOLD}{Fore.magenta}{Back.dark_gray}{role}{Style.reset}: {Fore.magenta}{content}{Style.reset}\n\n"
            case "user":
                chat_history_str += f"{Style.BOLD}{Fore.green}{Back.dark_gray}{role}{Style.reset}: {Fore.green}{content}{Style.reset}\n\n"
            case "tool":
                chat_history_str += f"{Style.BOLD}{Fore.blue}{Back.dark_gray}{role}{Style.reset}: {Fore.blue}({name}): {content}{Style.reset}\n\n"
            case "function":
                chat_history_str += f"{Style.BOLD}{Fore.yellow}{Back.dark_gray}{role}{Style.reset}: {Fore.yellow}({function_call or name}): {content}{Style.reset}\n\n"
            case _:
                chat_history_str += f"{Style.BOLD}{Fore.red}{Back.dark_gray}UNKNOWN ROLE `{role}`{Style.reset}: {Fore.red}{content}{Style.reset}\n\n"

    logger.log("CHAT", f"{Style.BOLD}{header_str}\n\n{chat_history_str}{Style.reset}")


def prettify_chat_history(
    *, chat_history: list[dict[str, Any]], role_labels: Optional[dict[str, str]] = None
) -> str:
    """Take a list of chat messages and returns a formatted string for pretty display.

    Parameters
    ----------
    chat_history
        A list of dictionaries representing chat messages. Each dictionary should have
        at least the keys `role` and `content`.
    role_labels
        A mapping between standard chat roles and custom labels. This allows you to
        customize the display labels for different roles in the chat. For example, you
        could use `{"user": "Customer", "assistant": "Support"}` to change the labels
        for the user and assistant roles. If not provided, the default labels will be
        used: "User" for user messages, "Assistant" for assistant messages, and
        "System" for system messages.

    Returns
    -------
    str
        A formatted string representing the chat history, with each message on a new
        line and labeled by its role.
    """

    role_labels = role_labels or {
        "assistant": "Assistant",
        "function": "Function",
        "system": "System",
        "tool": "Tool",
        "user": "User",
    }
    pretty_lines = []
    for message in chat_history:
        content = message["content"]
        role = role_labels.get(message["role"], message["role"].capitalize())
        pretty_lines.append(f"{role}: {content}")
    return "\n\n".join(pretty_lines)

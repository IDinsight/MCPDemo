"""This module contains the main configurations for the backend.

Any configurations added to backend/.env should be added to `BackendSettings` as well.
"""

# Standard Library
import os

from typing import Any, Literal, Optional

# Third Party Library
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendSettings(BaseSettings):
    """Pydantic settings for backend."""

    # Authentication
    AUTH_ALLOWED_SCOPES: set[str] = {"admin", "read", "write"}
    AUTH_AUDIENCE: str = "MCP_Demo_Server"
    AUTH_FILELOCK_TIMEOUT: int = 2
    AUTH_JWK_ALGORITHM: str = "RS256"
    AUTH_JWKS_FN: str = "jwks.json"
    AUTH_JWKS_URI: str = "http://0.0.0.0:8000/auth/jwks.json"
    AUTH_ROTATION_KEEP_LAST_N: int = 2
    AUTH_RSA_KEY_SIZE: int = Field(3072, ge=1024)
    AUTH_RSA_PASSPHRASE: SecretStr = Field(
        ..., description="Passphrase protecting the JWT signing key."
    )
    AUTH_RSA_PUBLIC_EXPONENT: int = Field(65537, ge=3, le=65537)
    AUTH_TOKEN_ISSUER: str = "https://tokens.local"
    AUTH_TOKEN_TTL: int = 900  # 15 minutes

    # Chat
    CHAT_ENV: str = "dev"

    # LiteLLM
    LITELLM_API_KEY: str = os.getenv("LITELLM_API_KEY", "dummy-key")
    LITELLM_ENDPOINT: str = os.getenv("LITELLM_ENDPOINT", "http://localhost:4000")
    LITELLM_MODEL_CHAT: str = os.getenv("LITELLM_MODEL_CHAT", "openai/chat")
    LITELLM_MODEL_DEFAULT: str = os.getenv("LITELLM_MODEL_DEFAULT", "openai/default")
    LITELLM_MODEL_EMBEDDING: str = os.getenv(
        "LITELLM_MODEL_EMBEDDING", "openai/embedding"
    )

    # External MCP Server
    EXTERNAL_FASTMCP_HOST: str = "0.0.0.0"
    EXTERNAL_FASTMCP_MOUNT_PATH: str = "external"
    EXTERNAL_FASTMCP_PORT: int = 8200
    EXTERNAL_FASTMCP_TRANSPORT: Literal["http", "sse", "stdio", "streamable-http"] = (
        "http"
    )

    # Logging
    LOGGING_LOG_LEVEL: str = "INFO"

    # FastAPI
    FASTAPI_HOST: str = "0.0.0.0"
    FASTAPI_PORT: int = 8000

    # FastMCP
    FASTMCP_HOST: str = "0.0.0.0"
    FASTMCP_MOUNT_PATH: str = "mcp"
    FASTMCP_PORT: int = 8100
    FASTMCP_TRANSPORT: Literal["http", "sse", "stdio", "streamable-http"] = "http"

    # Models
    MODELS_LLM: str = "openai/gpt-4o"

    # OpenAI #
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Postgres
    POSTGRES_ASYNC_API: str = Field("asyncpg", validation_alias="POSTGRES_ASYNC_API")
    POSTGRES_DB: str = Field("mcp_demo", validation_alias="POSTGRES_DB")
    POSTGRES_DB_POOL_SIZE: int = Field(10, validation_alias="POSTGRES_DB_POOL_SIZE")
    POSTGRES_HOST: str = Field("localhost", validation_alias="POSTGRES_HOST")
    POSTGRES_PASSWORD: str = Field("postgres", validation_alias="POSTGRES_PASSWORD")
    POSTGRES_PORT: str = Field("5432", validation_alias="POSTGRES_PORT")
    POSTGRES_SYNC_API: str = Field("psycopg2", validation_alias="POSTGRES_SYNC_API")
    POSTGRES_USER: str = Field("postgres", validation_alias="POSTGRES_USER")

    # Prometheus
    PROMETHEUS_MULTIPROC_DIR: str = "/tmp"

    # Rate Limits
    RATE_LIMIT_LOGIN_LOCK_SECONDS: int = 600
    RATE_LIMIT_LOGIN_LOCK_THRESHOLD: int = 5
    RATE_LIMIT_LOGIN_RATE: str = "5/minute"

    # Redis
    REDIS_CACHE_PREFIX_CHAT: str = os.getenv("REDIS_CACHE_PREFIX_CHAT", "chat_sessions")
    REDIS_CACHE_PREFIX_LOCK_USER: str = "lock:{user}:{ip}"
    REDIS_CACHE_PREFIX_LOGIN_FAIL: str = "login_fail:{user}:{ip}"
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")

    # Sentry
    SENTRY_DSN: Optional[str] = None
    SENTRY_TRACES_SAMPLE_RATE: float = 1.0

    # Text Generation Parameters
    TEXT_GENERATION_DEFAULT: dict[str, Any] = {
        "frequency_penalty": 0.0,
        "n": 1,
        "presence_penalty": 0.0,
        "temperature": 0.7,
        "top_p": 0.9,
    }
    TEXT_GENERATION_OPENAI: dict[str, Any] = {
        "frequency_penalty": 0.0,
        "n": 1,
        "presence_penalty": 0.0,
        "temperature": 0.7,
        "top_p": 0.9,
    }

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @classmethod
    def create_sync_postgres_db_url(cls) -> str:
        """Create the synchronous PostgreSQL database URL.

        Returns
        -------
        str
            The PostgreSQL database URL.
        """

        return f"postgresql+{cls().POSTGRES_SYNC_API}://{cls().POSTGRES_USER}:{cls().POSTGRES_PASSWORD}@{cls().POSTGRES_HOST}:{cls().POSTGRES_PORT}/{cls().POSTGRES_DB}"

    @field_validator("MODELS_LLM", mode="before")
    @classmethod
    def validate_model_names(cls, value: str) -> str:
        """Ensure that the model names starts with either 'openai/' or
        'sentence-transformers/'.

        Parameters
        ----------
        value
            The model name to validate.

        Returns
        -------
        str
            The validated model name.

        Raises
        ------
        ValueError
            If the model name does not start with the allowed prefixes.
        """

        allowed_prefixes = ("openai/", "sentence-transformers/")
        if not value.startswith(allowed_prefixes):
            raise ValueError(
                f"Invalid model name: '{value}'. "
                f"Must start with one of {allowed_prefixes}."
            )
        return value

    @field_validator("*", mode="before")
    @classmethod
    def validate_litellm_models(cls, value: Any, info: Any) -> Any:
        """Validate all fields that start with "LITELLM_MODEL_".

        Parameters
        ----------
        value
            The value to validate.
        info
            Metadata about the field being validated, including its name.

        Returns
        -------
        Any
            The validated value if it passes the checks.

        Raises
        ------
        ValueError
            If the value does is an empty string or does not start with "openai/".
        """

        if info.field_name.startswith("LITELLM_MODEL_") and not value.startswith(
            "openai/"
        ):
            raise ValueError(
                f"{info.field_name} must be a non-empty string that starts with "
                f"'openai/'."
            )
        return value


Settings: BackendSettings = BackendSettings()

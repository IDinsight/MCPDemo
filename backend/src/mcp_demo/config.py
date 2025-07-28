"""This module contains the main configurations for the backend.

Any configurations added to backend/.env should be added to `BackendSettings` as well.
"""

# Standard Library
import os

from typing import Literal, Optional

# Third Party Library
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackendSettings(BaseSettings):
    """Pydantic settings for backend."""

    # Authentication
    AUTH_ALLOWED_SCOPES: set[str] = {"admin", "read", "write"}
    AUTH_AUDIENCE: str = "MCP_Demo_Server"
    AUTH_CODE_TTL: int = 300  # Five‑minute max, well below the 10‑min spec. limit
    AUTH_CODE_VERIFIER_MAX_LEN: int = 128
    AUTH_CODE_VERIFIER_MIN_LEN: int = 43
    AUTH_FILELOCK_TIMEOUT: int = 2
    AUTH_JWK_ALGORITHM: str = "RS256"
    AUTH_JWKS_FN: str = "jwks.json"
    AUTH_JWKS_URI: str = "http://0.0.0.0:8000/auth/jwks.json"
    AUTH_PKCE_ALLOWED_METHODS: set[str] = {
        "S256",  # RFC 7636 §4.2 – recommended
        "plain",  # Optional fall-back for legacy clients
    }
    AUTH_ROTATION_KEEP_LAST_N: int = 2
    AUTH_RSA_KEY_SIZE: int = Field(3072, ge=1024)
    AUTH_RSA_PASSPHRASE: SecretStr = Field(
        ..., description="Passphrase protecting the JWT signing key."
    )
    AUTH_RSA_PUBLIC_EXPONENT: int = Field(65537, ge=3, le=65537)
    AUTH_TOKEN_ISSUER: str = "https://tokens.local"
    AUTH_TOKEN_REFRESH_TTL: int = 60 * 60 * 24 * 30  # 30 days
    AUTH_TOKEN_TTL: int = 900  # 15 minutes

    # Cross-Site Request Forgery (CSRF)
    CSRF_SECRET_KEY: SecretStr = Field(
        ..., description="Secret key for CSRF protection."
    )  # 32 bytes random

    # External MCP Server
    EXTERNAL_FASTMCP_HOST: str = "0.0.0.0"
    EXTERNAL_FASTMCP_MOUNT_PATH: str = "external"
    EXTERNAL_FASTMCP_PORT: int = 8200
    EXTERNAL_FASTMCP_TRANSPORT: Literal["http", "sse", "stdio", "streamable-http"] = (
        "http"
    )

    # FastAPI
    FASTAPI_ENV: str = "local"
    FASTAPI_HOST: str = "0.0.0.0"
    FASTAPI_PORT: int = 8000

    # FastMCP
    FASTMCP_HOST: str = "0.0.0.0"
    FASTMCP_MOUNT_PATH: str = "mcp"
    FASTMCP_PORT: int = 8100
    FASTMCP_TRANSPORT: Literal["http", "sse", "stdio", "streamable-http"] = "http"

    # Logging
    LOGGING_LOG_LEVEL: str = "INFO"

    # Postgres
    POSTGRES_ASYNC_API: str = Field("asyncpg", validation_alias="POSTGRES_ASYNC_API")
    POSTGRES_DB: str = Field("mcp_demo", validation_alias="POSTGRES_DB")
    POSTGRES_DB_POOL_SIZE: int = Field(10, validation_alias="POSTGRES_DB_POOL_SIZE")
    POSTGRES_HOST: str = Field("localhost", validation_alias="POSTGRES_HOST")
    POSTGRES_PASSWORD: str = Field("postgres", validation_alias="POSTGRES_PASSWORD")
    POSTGRES_PORT: str = Field("5432", validation_alias="POSTGRES_PORT")
    POSTGRES_SYNC_API: str = Field("psycopg2", validation_alias="POSTGRES_SYNC_API")
    POSTGRES_USER: str = Field("postgres", validation_alias="POSTGRES_USER")

    # Rate Limits
    RATE_LIMIT_LOGIN_LOCK_SECONDS: int = 600
    RATE_LIMIT_LOGIN_LOCK_THRESHOLD: int = 5
    RATE_LIMIT_LOGIN_RATE: str = "5/minute"

    # Redis
    REDIS_CACHE_PREFIX_AUTH_CODE: str = "auth:code:{code_hash}"
    REDIS_CACHE_PREFIX_JTI: str = "jti:{jti}"
    REDIS_CACHE_PREFIX_JWKS_CURRENT: str = "jwks:current"
    REDIS_CACHE_PREFIX_LOCK_CLIENT: str = "lock:{client_id}:{ip}"
    REDIS_CACHE_PREFIX_LOCK_USER: str = "lock:{username}:{ip}"
    REDIS_CACHE_PREFIX_LOGIN_FAIL_CLIENT: str = "login_fail:{client_id}:{ip}"
    REDIS_CACHE_PREFIX_LOGIN_FAIL_USER: str = "login_fail:{username}:{ip}"
    REDIS_CACHE_PREFIX_REFRESH_TOKEN: str = "auth:refresh:{token_hash}"
    REDIS_CACHE_PREFIX_SUB_JTIS: str = "auth:sub_jtis:{grant_type}:{sub}"
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")

    # Sentry
    SENTRY_DSN: Optional[str] = None
    SENTRY_TRACES_SAMPLE_RATE: float = 1.0

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="allow"
    )

    @classmethod
    def create_sync_postgres_db_url(cls) -> str:
        """Create the synchronous PostgreSQL database URL.

        Returns
        -------
        str
            The PostgreSQL database URL.
        """

        return f"postgresql+{cls().POSTGRES_SYNC_API}://{cls().POSTGRES_USER}:{cls().POSTGRES_PASSWORD}@{cls().POSTGRES_HOST}:{cls().POSTGRES_PORT}/{cls().POSTGRES_DB}"


Settings: BackendSettings = BackendSettings()

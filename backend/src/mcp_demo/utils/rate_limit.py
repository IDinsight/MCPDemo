"""This module contains utilities for rate limiting."""

# Standard Library
from typing import Optional

# Third Party Library
from redis import asyncio as aioredis

# Package Library
from mcp_demo.config import Settings

RATE_LIMIT_LOGIN_LOCK_SECONDS = Settings.RATE_LIMIT_LOGIN_LOCK_SECONDS
RATE_LIMIT_LOGIN_LOCK_THRESHOLD = Settings.RATE_LIMIT_LOGIN_LOCK_THRESHOLD
REDIS_CACHE_PREFIX_LOCK_CLIENT = Settings.REDIS_CACHE_PREFIX_LOCK_CLIENT
REDIS_CACHE_PREFIX_LOCK_USER = Settings.REDIS_CACHE_PREFIX_LOCK_USER
REDIS_CACHE_PREFIX_LOGIN_FAIL_CLIENT = Settings.REDIS_CACHE_PREFIX_LOGIN_FAIL_CLIENT
REDIS_CACHE_PREFIX_LOGIN_FAIL_USER = Settings.REDIS_CACHE_PREFIX_LOGIN_FAIL_USER


async def is_locked_out(
    *,
    client_id: Optional[str] = None,
    ip: str,
    redis_client: aioredis.Redis,
    username: Optional[str] = None,
) -> bool:
    """Check if a user is locked out from logging in based on failed attempts from a
    specific IP address.

    Parameters
    ----------
    client_id
        The client ID attempting to log in, if applicable.
    ip
        The IP address of the user/client attempting to log in.
    redis_client
        The Redis client to use for checking the lock status.
    username
        The username attempting to log in, if applicable.

    Returns
    -------
    bool
        True if the user/client is locked out, False otherwise.
    """

    assert client_id or username and not (client_id and username)

    if client_id:
        return (
            await redis_client.exists(
                REDIS_CACHE_PREFIX_LOCK_CLIENT.format(client_id=client_id, ip=ip)
            )
            == 1
        )
    return (
        await redis_client.exists(
            REDIS_CACHE_PREFIX_LOCK_USER.format(ip=ip, username=username)
        )
        == 1
    )


async def record_failed_login(
    *,
    client_id: Optional[str] = None,
    ip: str,
    redis_client: aioredis.Redis,
    username: Optional[str] = None,
) -> None:
    """Record a failed login attempt for a user from a specific IP address.

    This function increments the count of failed login attempts for a user and sets an
    expiration time for the key in Redis. If the count exceeds the threshold, it sets a
    hard lock key to prevent further login attempts.

    Parameters
    ----------
    client_id
        The client ID that has failed login, if applicable.
    ip
        The IP address of the user/client that has failed login.
    redis_client
        The Redis client to use for storing the failed login attempts.
    username
        The username that has failed login, if applicable.
    """

    assert client_id or username and not (client_id and username)

    if client_id:
        k = REDIS_CACHE_PREFIX_LOGIN_FAIL_CLIENT.format(client_id=client_id, ip=ip)
    else:
        k = REDIS_CACHE_PREFIX_LOGIN_FAIL_USER.format(ip=ip, username=username)

    async with redis_client.pipeline() as pipe:
        pipe.incr(k)
        pipe.expire(k, RATE_LIMIT_LOGIN_LOCK_SECONDS)
        incr_count, _ = await pipe.execute()

    if incr_count >= RATE_LIMIT_LOGIN_LOCK_THRESHOLD:
        if client_id:
            await redis_client.set(
                REDIS_CACHE_PREFIX_LOCK_CLIENT.format(client_id=client_id, ip=ip),
                1,
                ex=RATE_LIMIT_LOGIN_LOCK_SECONDS,
            )
        else:
            await redis_client.set(
                REDIS_CACHE_PREFIX_LOCK_USER.format(ip=ip, username=username),
                1,
                ex=RATE_LIMIT_LOGIN_LOCK_SECONDS,
            )


async def reset_failed_login(
    *,
    client_id: Optional[str] = None,
    ip: str,
    redis_client: aioredis.Redis,
    username: Optional[str] = None,
) -> None:
    """Reset the failed login attempts for a user/client from a specific IP address.

    This function deletes the Redis keys associated with the user's/client's failed
    login attempts and the hard lock key, allowing the user/client to attempt logging
    in again.

    Parameters
    ----------
    client_id
        The client ID whose failed login attempts are being reset, if applicable.
    ip
        The IP address of the user/client whose failed login attempts are being reset.
    redis_client
        The Redis client to use for deleting the failed login attempts.
    username
        The username whose failed login attempts are being reset, if applicable.
    """

    assert client_id or username and not (client_id and username)

    if client_id:
        await redis_client.delete(
            REDIS_CACHE_PREFIX_LOGIN_FAIL_CLIENT.format(client_id=client_id, ip=ip)
        )
        await redis_client.delete(
            REDIS_CACHE_PREFIX_LOCK_CLIENT.format(client_id=client_id, ip=ip)
        )
    else:
        await redis_client.delete(
            REDIS_CACHE_PREFIX_LOGIN_FAIL_USER.format(ip=ip, username=username)
        )
        await redis_client.delete(
            REDIS_CACHE_PREFIX_LOCK_USER.format(ip=ip, username=username)
        )

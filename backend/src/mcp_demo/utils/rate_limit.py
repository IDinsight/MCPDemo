"""This module contains utilities for rate limiting."""

# Third Party Library
from redis import asyncio as aioredis

# Package Library
from mcp_demo.config import Settings

RATE_LIMIT_LOGIN_LOCK_SECONDS = Settings.RATE_LIMIT_LOGIN_LOCK_SECONDS
RATE_LIMIT_LOGIN_LOCK_THRESHOLD = Settings.RATE_LIMIT_LOGIN_LOCK_THRESHOLD
REDIS_CACHE_PREFIX_LOCK_USER = Settings.REDIS_CACHE_PREFIX_LOCK_USER
REDIS_CACHE_PREFIX_LOGIN_FAIL = Settings.REDIS_CACHE_PREFIX_LOGIN_FAIL


async def is_locked_out(*, ip: str, redis_client: aioredis.Redis, user: str) -> bool:
    """Check if a user is locked out from logging in based on failed attempts from a
    specific IP address.

    Parameters
    ----------
    ip
        The IP address of the user attempting to log in.
    redis_client
        The Redis client to use for checking the lock status.
    user
        The username of the user attempting to log in.

    Returns
    -------
    bool
        True if the user is locked out, False otherwise.
    """

    return (
        await redis_client.exists(REDIS_CACHE_PREFIX_LOCK_USER.format(ip=ip, user=user))
        == 1
    )


async def record_failed_login(
    *, ip: str, redis_client: aioredis.Redis, user: str
) -> None:
    """Record a failed login attempt for a user from a specific IP address.

    This function increments the count of failed login attempts for a user and sets an
    expiration time for the key in Redis. If the count exceeds the threshold, it sets a
    hard lock key to prevent further login attempts.

    Parameters
    ----------
    ip
        The IP address of the user attempting to log in.
    redis_client
        The Redis client to use for storing the failed login attempts.
    user
        The username of the user attempting to log in.

    Raises
    -------
    aioredis.RedisError
        If there is an error communicating with the Redis server.
    """

    k = REDIS_CACHE_PREFIX_LOGIN_FAIL.format(ip=ip, user=user)
    async with redis_client.pipeline() as pipe:
        pipe.incr(k)
        pipe.expire(k, RATE_LIMIT_LOGIN_LOCK_SECONDS)
        incr_count, _ = await pipe.execute()

    if incr_count >= RATE_LIMIT_LOGIN_LOCK_THRESHOLD:
        await redis_client.set(
            REDIS_CACHE_PREFIX_LOCK_USER.format(ip=ip, user=user),
            1,
            ex=RATE_LIMIT_LOGIN_LOCK_SECONDS,
        )


async def reset_failed_login(
    *, ip: str, redis_client: aioredis.Redis, user: str
) -> None:
    """Reset the failed login attempts for a user from a specific IP address.

    This function deletes the Redis keys associated with the user's failed login
    attempts and the hard lock key, allowing the user to attempt logging in again.

    Parameters
    ----------
    ip
        The IP address of the user whose failed login attempts are being reset.
    redis_client
        The Redis client to use for deleting the failed login attempts.
    user
        The username of the user whose failed login attempts are being reset.
    """

    await redis_client.delete(REDIS_CACHE_PREFIX_LOGIN_FAIL.format(ip=ip, user=user))
    await redis_client.delete(REDIS_CACHE_PREFIX_LOCK_USER.format(ip=ip, user=user))

"""This module contains authentication utilities for key management and JWT token
operations.

This module provides core logic for managing signing keys and issuing RS256 JWT tokens
consumed by FastMCP and other services that trust this auth service.

This module houses *all* key-management and token-generation logic used by the
authentication service and, indirectly, every other server that trusts its tokens
(e.g., the FastMCP server).

Overview
--------

1. First startup
    - When `get_jwt_token()` (via `get_latest_private_key_and_kid()`) detects an empty
        JWKS, it calls `rotate_keys()`, which:
            - Generates an RSA‑3072 key pair.
            - Saves the encrypted private and public keys to disk.
            - Writes public keys into `jwks.json`.
            - Updates the in-memory JWKS cache.
2. Normal token issuance
    - `routers.issue_token` calls `get_jwt_token()`, which:
        - Loads the latest private key.
        - Creates a payload with `sub`, `scopes`, `jti`, `iat`, `exp`, etc.
        - Signs the JWT with RS256 and the appropriate `kid`.
        - Stores the `jti` in Redis with TTL = token lifetime.
3. Key rotation (optional scheduled task)
    - `rotate_keys()` generates a new key pair, prepends to `jwks.json`, prunes old
        keys (keeping last N), deletes stale PEMs, and updates the in-memory cache.
4. JWKS serving
    - `/auth/jwks.json` endpoints call `load_jwks()` or `load_jwks_from_redis()` to
        return the current JWKS in a cache-aware manner.


Concurrency & Safety
--------------------
1. `process_lock()` uses a filesystem lock to prevent concurrent rotations.
2. `atomic_write()` ensures atomic file updates.
3. In-memory cache (`_JWKS_CACHE` + `_JWKS_MTIME`) prevents redundant I/O while
    reflecting cross-process updates.

Environment contracts
---------------------
1. `$PATHS_SECRETS_DIR` must exist and be writable.
2. Relevant `Settings.*` values (e.g. issuer, audience) must be configured.

All public functions are `async` and should be called with `await`.
"""

# pylint: disable=W0603
# Future Library
from __future__ import annotations

# Standard Library
import asyncio
import base64
import json
import os
import time

from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from secrets import token_hex
from threading import Lock
from typing import Annotated, Any, AsyncIterator, Optional, cast

# Third Party Library
import jwt

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)
from fastapi import Depends, Form, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from filelock import FileLock
from jose import JWTError, jwk
from loguru import logger
from redis import asyncio as aioredis

# Package Library
from mcp_demo.config import Settings
from mcp_demo.utils.general import atomic_write, get_redis_client, make_dir

_JWKS_CACHE: dict[str, Any] | None = None  # In-memory copy
_JWKS_MTIME: float | None = None  # Last os.stat mtime
_JWKS_THREAD_LOCK = Lock()  # Per-process guard

PATHS_PROJECT_DIR = os.getenv("PATHS_PROJECT_DIR", None)
assert PATHS_PROJECT_DIR is not None
_SECRETS_DIR = Path(PATHS_PROJECT_DIR) / "secrets"
make_dir(_SECRETS_DIR, mode=0o700)
_LOCK = FileLock(str(_SECRETS_DIR / ".rotate.lock"), timeout=2)

AUTH_AUDIENCE = Settings.AUTH_AUDIENCE
AUTH_FILELOCK_TIMEOUT = Settings.AUTH_FILELOCK_TIMEOUT
AUTH_JWK_ALGORITHM = Settings.AUTH_JWK_ALGORITHM
AUTH_JWKS_FN = Settings.AUTH_JWKS_FN
AUTH_ROTATION_KEEP_LAST_N = Settings.AUTH_ROTATION_KEEP_LAST_N
AUTH_RSA_KEY_SIZE = Settings.AUTH_RSA_KEY_SIZE
AUTH_RSA_PUBLIC_EXPONENT = Settings.AUTH_RSA_PUBLIC_EXPONENT
AUTH_TOKEN_ISSUER = Settings.AUTH_TOKEN_ISSUER
AUTH_TOKEN_TTL = Settings.AUTH_TOKEN_TTL
REDIS_CACHE_PREFIX_JTI = Settings.REDIS_CACHE_PREFIX_JTI
REDIS_CACHE_PREFIX_JWKS_CURRENT = Settings.REDIS_CACHE_PREFIX_JWKS_CURRENT

oauth2_scheme = OAuth2PasswordBearer(
    scopes={
        "admin": "Admin user with full permissions",
        "read": "User with read permissions only.",
        "write": "User with read and write permissions.",
    },
    tokenUrl="/auth/token",
)


class ClientCredentialsRequestForm:
    """Form model supporting both 'client_credentials' and 'password' grant types.

    NB: FastAPI’s built-in OAuth2 forms don’t support multi-grant flows.
    """

    def __init__(
        self,
        *,
        client_id: str | None = Form(..., min_length=1),
        client_secret: str | None = Form(..., min_length=1),
        grant_type: str = Form(
            default="client_credentials",
            regex="^(client_credentials|password)$",
        ),
        password: str | None = Form(..., min_length=1),
        scope: str = Form(default=""),
        username: str | None = Form(None, min_length=1),
    ) -> None:
        """

        Parameters
        ----------
        client_id
            The client identifier issued to the client during registration.
        client_secret
            The client secret issued to the client during registration. This is used to
            authenticate the client and should be kept confidential.
        grant_type
            The OAuth2 grant type, must be 'client_credentials'.
        password
            The password of the user for password grant type. Optional, only used for
            password grant.
        scope
            Space-separated list of scopes requested by the client.
        username
            The username of the user for password grant type. Optional, only used for
            password grant.

        Raises
        ------
        ValueError
            If the required fields for the specified grant type are not provided.
        """

        match grant_type:
            case "client_credentials":
                assert (
                    client_id and client_secret
                ), "client_id and client_secret must be provided for client_credentials"
            case "password":
                assert (
                    username and password
                ), "username and password must be provided for password grant"
            case _:
                raise ValueError(
                    f"Unsupported grant_type: {grant_type}. "
                    f"Valid options are: 'client_credentials' or 'password'."
                )

        self.client_id = client_id
        self.client_secret = client_secret
        self.grant_type = grant_type
        self.password = password
        self.scopes: list[str] = scope.split()
        self.username = username


def _set_cache(*, mtime: float | None, new_jwks: dict[str, Any]) -> None:
    """Atomically replace the in-memory JWKS + mtime for *all* threads.

    If we mutate the old dict (e.g., insert(0, key)), any other thread that already
    holds a reference can observe a half-updated structure. Creating a brand-new
    dict/list and swapping the reference guarantees readers see either the old or the
    new version—never an in-between state.

    Parameters
    ----------
    mtime
        The last modification time of the JWKS file, used to track changes.
    new_jwks
        The new JWKS (JSON Web Key Set) to be set in the cache. This should be a fresh
        dictionary containing the keys, typically loaded from `jwks.json`.
    """

    global _JWKS_CACHE, _JWKS_MTIME
    with _JWKS_THREAD_LOCK:
        # Never mutate the existing dict; just point to a fresh copy.
        _JWKS_CACHE = new_jwks
        _JWKS_MTIME = mtime


async def _verify_caller(
    *,
    options: Optional[dict[str, Any]] = None,
    redis_client: Annotated[aioredis.Redis, Depends(get_redis_client)],
    required_scopes: set[str],
    token: Annotated[str, Depends(oauth2_scheme)],
) -> dict[str, Any]:
    """Shared JWT verifier for **both** users and service-clients.

    Parameters
    ----------
    options
        Optional additional options for the JWT decoding process. This can include
        settings like `verify_signature`, `verify_aud`, etc. If not provided, defaults
        to verifying all standard claims.
    redis_client
        The Redis client used to check the JTI (JWT ID) for replay attacks.
    required_scopes
        The scopes required for the operation being performed. This is a set of
        strings representing the scopes that the client must have in order to access
        the requested resource or perform the action.
    token
        The JWT token to decode and verify.

    Returns
    -------
    dict[str, Any]
        The decoded JWT payload if the token is valid and contains the required scopes.

    Raises
    ------
    HTTPException
        If the token is invalid, expired, or does not contain the required scopes.
        This exception will have a status code of 401 (Unauthorized) if the token
        cannot be validated, or 403 (Forbidden) if the token does not have the
        required scopes.
    """

    credentials_exception = HTTPException(
        detail="Could not validate client credentials",
        headers={"WWW-Authenticate": "Bearer"},
        status_code=status.HTTP_401_UNAUTHORIZED,
    )
    options = options or {
        "verify_aud": True,
        "verify_exp": True,
        "verify_nbf": True,
        "verify_iat": True,
        "verify_iss": True,
    }

    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise credentials_exception
        last_jwks = await load_jwks()
        key = next(k for k in last_jwks["keys"] if k["kid"] == kid)
        if not key:
            raise credentials_exception
        public_key = jwk.construct(key).to_pem().decode()
        payload = jwt.decode(
            token,
            public_key,
            algorithms=[AUTH_JWK_ALGORITHM],
            audience=AUTH_AUDIENCE,
            issuer=AUTH_TOKEN_ISSUER,
            options=options,
        )

        # Every token we issue *should* contain a jti; treat its absence as invalid.
        # In addition, if the token has already been used (replayed) or we never issued
        # it, then we also raise an exception.
        jti = payload.get("jti")
        if not jti or not await redis_client.exists(
            REDIS_CACHE_PREFIX_JTI.format(jti=jti)
        ):
            raise credentials_exception
    except (StopIteration, JWTError) as exc:
        raise credentials_exception from exc

    if not payload.get("sub"):
        raise credentials_exception

    # Enforce scopes.
    token_scopes = {s for s in payload.get("scope", "").split() if s}
    if not required_scopes.issubset(token_scopes):
        raise HTTPException(
            detail="Not enough permissions", status_code=status.HTTP_403_FORBIDDEN
        )

    return payload


def b64url(*, data: bytes) -> str:
    """Return RFC 7515 base64url without padding.

    Ref: https://datatracker.ietf.org/doc/html/rfc7515

    Parameters
    ----------
    data
        The data to be encoded in base64url format.

    Returns
    -------
    str
        The base64url encoded string without padding.
    """

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_rsa_keypair(
    *,
    key_size: int = AUTH_RSA_KEY_SIZE,
    public_exponent: int = AUTH_RSA_PUBLIC_EXPONENT,
) -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """Generate an RSA keypair.

    Parameters
    ----------
    key_size
        The size of the RSA key in bits. Use 2048 for most applications, or 4096 for
        longer-term certificates.
    public_exponent
        The public exponent for the RSA key.

    Returns
    -------
    tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]
        A tuple containing the generated private key and its corresponding public key.
    """

    private_key = rsa.generate_private_key(
        public_exponent=public_exponent, key_size=key_size
    )

    return private_key, private_key.public_key()


async def get_cached_jwks() -> dict[str, Any]:
    """Thread-safe, read-only view of the in-memory JWKS.

    Returns
    -------
    dict[str, Any]
        The cached JWKS (JSON Web Key Set) containing the keys. If the cache is empty,
        it will load the JWKS from disk.
    """

    if _JWKS_CACHE is None:
        # First call – prime the cache.
        await load_jwks()

    assert isinstance(_JWKS_CACHE, dict) and _JWKS_CACHE

    return _JWKS_CACHE


async def get_jwt_token(
    *,
    jwks_fn: str = AUTH_JWKS_FN,
    passphrase: str | None = None,
    redis_client: aioredis.Redis,
    scopes: list[str],
    sub: str,
) -> str:
    """Generate a JWT token for the given subject and scopes.

    NB: We use RS256 for signing the JWT, which requires a private key. `kid` is used
    to identify the key in the JWKS (JSON Web Key Set) endpoint. The `iss` (issuer) and
    `aud` (audience) fields are set to the issuer and audience of the token,
    respectively, and should match the values expected by the server.

    The process is as follows:

    1. Validate the requested scopes against the allowed scopes.
    2. Grab the most-recent private key and its kid (auto-bootstraps on first run).
    3. Create a JWT payload with the subject, scopes, issuer, audience, issued at
         time, and expiration time.
    4. Sign with RS256 and embed the kid so verifiers find the right JWK.
    5. Store the JWT ID (jti) in Redis with a TTL equal to the token's expiration time,
        ensuring that the same jti cannot be reused within the token's lifetime. If
        the jti already exists, retry up to `max_attempts` times to generate a unique
        jti. If it still fails, raise an error.

    Parameters
    ----------
    jwks_fn
        The filename for the JWKS (JSON Web Key Set) file. This is only used for
        generating a new key if the JWKS file is empty.
    passphrase
        Passphrase to decrypt the private key.
    redis_client
        An instance of `aioredis.Redis` used to store the JWT ID (jti) with a TTL.
    scopes
        A list of scopes to include in the JWT token.
    sub
        The subject for which the JWT token is being generated, typically a user ID.

    Returns
    -------
    str
        The generated JWT token as a string.

    Raises
    ------
    RuntimeError
        If the function is unable to mint a unique JWT ID (jti) after several attempts.
    ValueError
        If any of the requested scopes are not allowed by the authentication service.
        This ensures that only valid scopes are included in the JWT token.
    """

    # 1.
    invalid_scopes = set(scopes) - Settings.AUTH_ALLOWED_SCOPES
    if invalid_scopes:
        raise ValueError(f"Unrecognised scopes requested: {', '.join(invalid_scopes)}")

    # 2.
    passphrase = passphrase or Settings.AUTH_RSA_PASSPHRASE.get_secret_value()
    private_key, kid = await get_latest_private_key_and_kid(
        jwks_fn=jwks_fn, passphrase=passphrase
    )

    # 3.
    max_attempts = 3
    attempt_num = 1
    while attempt_num <= max_attempts:
        # 3.
        jti = token_hex(12)
        now = int(time.time())
        payload = {
            "aud": AUTH_AUDIENCE,
            "exp": now + AUTH_TOKEN_TTL,
            "iat": now,
            "iss": AUTH_TOKEN_ISSUER,
            "jti": jti,
            "nbf": now - 30,
            "scope": " ".join(scopes),
            "sub": sub,
            "typ": "JWT",
        }

        # 4.
        token = cast(
            str,
            jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid}),
        )

        # 5.
        is_set = await redis_client.set(
            ex=AUTH_TOKEN_TTL,
            name=REDIS_CACHE_PREFIX_JTI.format(jti=jti),
            nx=True,  # Only if it does not yet exist
            value=sub,
        )
        if is_set:
            return token
        attempt_num += 1

    raise RuntimeError(f"Unable to mint unique JTI after {max_attempts} attempts")


async def get_latest_private_key_and_kid(
    *, jwks_fn: str = AUTH_JWKS_FN, passphrase: str
) -> tuple[rsa.RSAPrivateKey, str]:
    """Get the latest private key and its key ID (kid) for signing JWT tokens.

    Parameters
    ----------
    jwks_fn
        The filename for the JWKS (JSON Web Key Set) file. This is only used for
        generating a new key if the JWKS file is empty.
    passphrase
        Passphrase to decrypt the private key.

    Returns
    -------
    tuple[rsa.RSAPrivateKey, str]
        A tuple containing the loaded RSA private key and its key ID (kid).
    """

    jwks = await load_jwks(jwks_fn=jwks_fn)
    if not jwks["keys"]:
        # First run – create a pair automatically.
        kid = await rotate_keys(jwks_fn=jwks_fn, passphrase=passphrase)
    else:
        # Read the first (only) kid from jwks.json.
        kid = jwks["keys"][0]["kid"]

    private_key_fp = _SECRETS_DIR / f"private_{kid}.pem"

    return (
        load_private_key(passphrase=passphrase, private_key_fp=private_key_fp),
        kid,
    )


def jwk_from_public_key(
    *,
    alg: str = AUTH_JWK_ALGORITHM,
    kid: str,
    public_key: rsa.RSAPublicKey,
    use: str = "sig",
) -> dict[str, Any]:
    """Convert an RSA public key to a JWK payload suitable for a JWKS endpoint.

    Parameters
    ----------
    alg
        The algorithm used for the key, typically "RS256" for RSA keys.
    kid
        The key ID for the JWK, used to identify the key in a JWKS.
    public_key
        The RSA public key to be converted to JWK format.
    use
        The intended use of the key, typically "sig" for signature.

    Returns
    -------
    dict[str, Any]
        A dictionary representing the JWK (JSON Web Key) format of the public key.
    """

    numbers = public_key.public_numbers()
    return {
        "alg": alg,
        "e": b64url(data=numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
        "kid": kid,
        "kty": "RSA",
        "n": b64url(data=numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "use": use,
    }


async def load_jwks(*, jwks_fn: str = AUTH_JWKS_FN) -> dict[str, Any]:
    """Load the JSON Web Key Set (JWKS) from disk.

    NB: This function reads and parses the `jwks.json` file specified by
    `Settings.AUTH_JWKS_FN`. It caches the parsed keys in memory and tracks the file's
    last modification time. Subsequent calls only re-read and re-parse the file if its
    `mtime` has changed, making repeated invocations lightweight and efficient.

    Parameters
    ----------
    jwks_fn
        The filename for the JWKS (JSON Web Key Set) file.

    Returns
    -------
    dict[str, Any]
        The JWKS containing the keys, or an empty set if the file does not exist.
    """

    jwks_fp = _SECRETS_DIR / jwks_fn

    async with process_lock():
        if not jwks_fp.is_file():
            _set_cache(mtime=None, new_jwks={"keys": []})
            assert isinstance(_JWKS_CACHE, dict)
            return _JWKS_CACHE

        mtime = jwks_fp.stat().st_mtime
        if _JWKS_CACHE is None or _JWKS_MTIME != mtime:  # File changed or first read
            try:
                new_jwks = json.loads(jwks_fp.read_text("utf-8"))
            except json.JSONDecodeError:
                logger.warning("Corrupted JWKS, regenerating")
                new_jwks = {"keys": []}
            _set_cache(mtime=mtime, new_jwks=new_jwks)  # Single, atomic swap
        assert isinstance(_JWKS_CACHE, dict)
        return _JWKS_CACHE


async def load_jwks_from_redis(
    *, force_refresh: bool = False, redis_client: aioredis.Redis
) -> dict[str, Any]:
    """Return the JSON Web Key Set (JWKS), cached in-process and backed by Redis.

    Parameters
    ----------
    force_refresh
        Skip the in-memory cache and fetch from Redis again (rarely needed).
    redis_client
        An instance of `aioredis.Redis` used to fetch the JWKS.

    Returns
    -------
    dict[str, Any]
        The JWKS containing the public keys used to verify incoming JWTs.

    Raises
    ------
    RuntimeError
        If the JWKS is not found in Redis and the initialisation fails, indicating a
        configuration error.
    """

    if _JWKS_CACHE is not None and not force_refresh:
        return _JWKS_CACHE  # Serve the in-process copy

    raw = await redis_client.get(REDIS_CACHE_PREFIX_JWKS_CURRENT)

    if not raw:
        async with process_lock():
            raw = await redis_client.get(REDIS_CACHE_PREFIX_JWKS_CURRENT)
        if not raw:
            await rotate_keys_with_redis(
                keep_last_n=AUTH_ROTATION_KEEP_LAST_N,
                key_size=AUTH_RSA_KEY_SIZE,
                passphrase=Settings.AUTH_RSA_PASSPHRASE.get_secret_value(),
                redis_client=redis_client,
            )
            raw = await redis_client.get(REDIS_CACHE_PREFIX_JWKS_CURRENT)
            if not raw:  # Still missing --> configuration error
                raise RuntimeError(
                    f"JWKS initialisation failed. "
                    f"'{REDIS_CACHE_PREFIX_JWKS_CURRENT}' not set in Redis."
                )

    jwks = json.loads(raw)

    # Atomic swap into the per-process cache.
    _set_cache(mtime=None, new_jwks=jwks)

    assert isinstance(_JWKS_CACHE, dict) and _JWKS_CACHE

    return _JWKS_CACHE


def load_private_key(
    *, passphrase: str, private_key_fp: str | Path
) -> rsa.RSAPrivateKey:
    """Load a PEM-encoded private key from disk.

    Parameters
    ----------
    passphrase
        Passphrase to decrypt the private key.
    private_key_fp
        The path to the PEM file containing the private key.

    Returns
    -------
    rsa.RSAPrivateKey
        The loaded RSA private key.

    Raises
    ------
    TypeError
        If the loaded key is not an RSA private key.
    ValueError
        If the private key file is invalid or cannot be loaded.
    """

    private_key_fp = Path(private_key_fp)

    try:
        private_key = load_pem_private_key(
            private_key_fp.read_bytes(), password=passphrase.encode()
        )
    except ValueError as e:
        logger.error(f"Failed to load private key from {private_key_fp}: {e}")
        raise ValueError(f"Invalid private key file: {private_key_fp}") from e

    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise TypeError(f"Expected RSA private key, got {type(private_key).__name__}")

    return private_key


def load_public_key(*, public_key_fp: str | Path) -> rsa.RSAPublicKey:
    """Load a PEM-encoded public key from disk.

    Parameters
    ----------
    public_key_fp
        The path to the PEM file containing the public key.

    Returns
    -------
    rsa.RSAPublicKey
        The loaded RSA public key.

    Raises
    ------
    TypeError
        If the loaded key is not an RSA public key.
    """

    public_key_fp = Path(public_key_fp)
    public_key = load_pem_public_key(public_key_fp.read_bytes())

    if not isinstance(public_key, rsa.RSAPublicKey):
        raise TypeError(f"Expected RSA public key, got {type(public_key).__name__}")

    return public_key


@asynccontextmanager
async def process_lock() -> AsyncIterator[None]:
    """Context manager to acquire a process-wide lock.

    This is useful for ensuring that only one process can perform certain operations
    at a time, such as writing to a file or rotating keys. The lock is implemented
    using a file lock, which is suitable for cross-process synchronization.

    Notes:

    1. Non-blocking acquire (blocking=False) means the worker thread returns
        immediately.
    2. We poll inside the event loop with a tiny asyncio.sleep, so the loop stays
        responsive.
    3. We raise quickly if exceeding the filelock timeout instead of tying up the
        worker.

    Yields
    ------
    AsyncIterator[None]
        A context manager that acquires the lock before yielding and releases it
        after the block is executed.
    """

    deadline = time.monotonic() + AUTH_FILELOCK_TIMEOUT
    backoff = 0.05  # 50 ms – tweak if needed

    while True:
        acquired = await asyncio.to_thread(_LOCK.acquire, blocking=False)
        if acquired:
            break
        if time.monotonic() >= deadline:  # Hard ceiling
            raise TimeoutError(
                f"Could not obtain rotate.lock after {AUTH_FILELOCK_TIMEOUT}s"
            )
        await asyncio.sleep(backoff)
        backoff = min(backoff * 1.5, 0.5)  # Exponential back-off, cap at 500 ms

    try:
        yield
    finally:
        _LOCK.release()


async def rotate_keys(
    *,
    jwks_fn: str = AUTH_JWKS_FN,
    keep_last_n: int = AUTH_ROTATION_KEEP_LAST_N,
    key_size: int = AUTH_RSA_KEY_SIZE,
    passphrase: str,
) -> str:
    """Generate a new RSA key-pair, add it to `jwks.json`, delete keys older than
    `AUTH_ROTATION_KEEP_LAST_N`.

    The process is as follows:

    1. Generate a new kid (key ID) for the new key pair.
    2. Generate a new RSA key pair (private and public keys).
    3. Save the private and public keys to a PEM file, optionally encrypted with a
        passphrase.
    4. Load the existing JWKS (JSON Web Key Set) from `jwks.json`.
    5. Add the new key to the JWKS, ensuring it is at the top of the list. Prune the
        oldest keys, keeping only the most recent `AUTH_ROTATION_KEEP_LAST_N` keys.
    6. Save the updated JWKS back to `jwks.json`.
    7. Update the in-memory cache of the JWKS and its last modified time so that the
        current process can use the new key immediately.

    Parameters
    ----------
    jwks_fn
        The filename for the JWKS (JSON Web Key Set) file.
    keep_last_n
        The number of most recent keys to keep in the JWKS. Older keys will be deleted.
    key_size
        The size of the RSA key in bits. Use 2048 for most applications, or 4096 for
        longer-term certificates.
    passphrase
        Passphrase to encrypt the private key.

    Returns
    -------
    str
        The key ID (kid) of the newly generated key pair, which can be used to sign
        JWTs.

    Raises
    ------
    ValueError
        If a duplicate key ID (kid) is detected after rotation, indicating a logic
        error in the key management process.
    """

    async with process_lock():
        # 1.
        kid = token_hex(8)  # 16-char random key ID

        # 2.
        private_key, public_key = await asyncio.to_thread(
            generate_rsa_keypair, key_size=key_size
        )

        # 3.
        await asyncio.to_thread(
            save_keypair,
            passphrase=passphrase,
            private_key=private_key,
            private_key_fp=_SECRETS_DIR / f"private_{kid}.pem",
            public_key=public_key,
            public_key_fp=_SECRETS_DIR / f"public_{kid}.pem",
        )

        # 4.
        jwks = deepcopy(await load_jwks(jwks_fn=jwks_fn))
        new_public_jwk = jwk_from_public_key(kid=kid, public_key=public_key)
        jwks["keys"].insert(0, new_public_jwk)  # Safe – private copy

        # 5.
        while len(jwks["keys"]) > keep_last_n:
            # Remove **oldest** JWK.
            old = jwks["keys"].pop()
            old_kid = old["kid"]

            # Remove corresponding PEMs (ignore if already gone).
            await asyncio.to_thread(
                (_SECRETS_DIR / f"private_{old_kid}.pem").unlink, missing_ok=True
            )
            await asyncio.to_thread(
                (_SECRETS_DIR / f"public_{old_kid}.pem").unlink, missing_ok=True
            )

        # 6.
        await asyncio.to_thread(save_jwks, jwks=jwks, jwks_fn=jwks_fn)

        # 7.
        mtime = (_SECRETS_DIR / jwks_fn).stat().st_mtime
        _set_cache(new_jwks=jwks, mtime=mtime)

        if kid in {key["kid"] for key in jwks["keys"][1:]}:
            raise ValueError(f"Duplicate kid detected after rotation: {kid}")

        return kid


async def rotate_keys_with_redis(
    *,
    keep_last_n: int = AUTH_ROTATION_KEEP_LAST_N,
    key_size: int = AUTH_RSA_KEY_SIZE,
    passphrase: str,
    redis_client: aioredis.Redis,
) -> str:
    """Generate a new RSA key-pair and push the updated JWKS to Redis instead of
    writing jwks.json to disk.

    The process is as follows:

    1. Generate a new kid (key ID) for the new key pair.
    2. Generate a new RSA key pair (private and public keys).
    3. Save the private and public keys to a PEM file, optionally encrypted with a
        passphrase.
    4. Load the existing JWKS (JSON Web Key Set) from Redis (or start empty).
    5. Insert the fresh public key at the head of the list.
    6. Prune the JWKS to keep only the most recent `keep_last_n` public keys.
    7. Save the whole JWKS back to Redis in one atomic SET operation.
    8. Refresh the in-memory cache for this process.

    Parameters
    ----------
    keep_last_n
        The number of most recent keys to keep in the JWKS. Older keys will be deleted.
    key_size
        The size of the RSA key in bits. Use 2048 for most applications, or 4096 for
        longer-term certificates.
    passphrase
        Passphrase to encrypt the private key.
    redis_client
        An instance of `aioredis.Redis` used to store the JWKS.

    Returns
    -------
    str
        The key ID (kid) of the newly generated key pair, which can be used to sign
        JWTs.
    """

    async with process_lock():
        # 1.
        kid = token_hex(8)  # 16-char random key ID

        # 2.
        private_key, public_key = await asyncio.to_thread(
            generate_rsa_keypair, key_size=key_size
        )

        # 3.
        await asyncio.to_thread(
            save_keypair,
            passphrase=passphrase,
            private_key=private_key,
            private_key_fp=_SECRETS_DIR / f"private_{kid}.pem",
            public_key=public_key,
            public_key_fp=_SECRETS_DIR / f"public_{kid}.pem",
        )

        # 4.
        raw = await redis_client.get(REDIS_CACHE_PREFIX_JWKS_CURRENT)
        if raw:
            try:
                jwks = json.loads(raw)
            except json.JSONDecodeError:
                jwks = {"keys": []}
        else:
            jwks = {"keys": []}

        # 5.
        new_jwk = jwk_from_public_key(kid=kid, public_key=public_key)

        # 6.
        jwks["keys"] = [new_jwk, *[k for k in jwks["keys"] if k["kid"] != kid]][
            :keep_last_n
        ]

        # 7.
        await redis_client.set(
            REDIS_CACHE_PREFIX_JWKS_CURRENT, json.dumps(jwks, separators=(",", ":"))
        )

        # 8.
        _set_cache(mtime=None, new_jwks=jwks)

        return kid


def sanitize_scopes(*, requested_scopes: list[str] | None) -> list[str]:
    """Sanitize the requested scopes against the allowed scopes.

    This function ensures that the requested scopes are valid and allowed by the
    authentication service. If no scopes are requested, it defaults to the "read"
    scope to ensure that at least one scope is always present.

    Parameters
    ----------
    requested_scopes
        A list of requested scopes. If None, it defaults to an empty list.

    Returns
    -------
    list[str]
        A list of sanitized scopes that are allowed by the authentication service.
        If no valid scopes are requested, it defaults to ["read"].
    """

    allowed = Settings.AUTH_ALLOWED_SCOPES
    requested = set(requested_scopes or [])
    cleaned = list(allowed & requested) or ["read"]  # Never issue an empty scope set
    return cleaned


def save_jwks(*, jwks: dict[str, Any], jwks_fn: str = AUTH_JWKS_FN) -> None:
    """Save the JWKS (JSON Web Key Set) to a file.

    Parameters
    ----------
    jwks
        The JWKS to be saved, typically containing one or more JWKs (JSON Web Keys).
    jwks_fn
        The filename for the JWKS (JSON Web Key Set) file.
    """

    atomic_write(
        data=json.dumps(jwks, ensure_ascii=False, separators=(",", ":")).encode(),
        mode="wb",
        perm=0o640,
        target_fp=_SECRETS_DIR / jwks_fn,
    )


def save_keypair(
    *,
    passphrase: str,
    private_key: rsa.RSAPrivateKey,
    private_key_fp: Path,
    public_key: rsa.RSAPublicKey,
    public_key_fp: Path,
) -> tuple[Path, Path]:
    """Write PEM files to disk (PKCS-8 for the private key) under kid-specific
    filenames.

    Parameters
    ----------
    passphrase
        Passphrase to encrypt the private key.
    private_key
        The RSA private key to be saved.
    private_key_fp
        The file path where the private key PEM file will be saved.
    public_key
        The RSA public key to be saved.
    public_key_fp
        The file path where the public key PEM file will be saved.

    Returns
    -------
    tuple[Path, Path]
        A tuple containing the file paths of the saved private and public keys.
    """

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase.encode()),
        format=serialization.PrivateFormat.PKCS8,
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    atomic_write(data=private_pem, target_fp=private_key_fp, mode="wb", perm=0o600)
    atomic_write(data=public_pem, target_fp=public_key_fp, mode="wb", perm=0o640)

    return private_key_fp, public_key_fp

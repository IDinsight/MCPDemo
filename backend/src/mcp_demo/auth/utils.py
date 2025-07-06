"""This module contains authentication utilities.

This module houses *all* key-management and token-generation logic used by the
authentication service and, indirectly, every other server that trusts its tokens
(e.g., the FastMCP server).

High-level flow
---------------
1. First start-up
   - `rotate_keys()` is invoked automatically by `get_jwt_token()` (via
    `get_latest_private_key_and_kid()`) when `jwks.json` is empty. It generates a fresh
    RSA-3072 key-pair, stores the private key (PKCS-8 + passphrase) in
    `$PATHS_SECRETS_DIR`, publishes the public key in `jwks.json`, and primes the
    in-memory JWKS cache.
2. Normal token issuance
   - `routers.issue_token` calls `get_jwt_token()`:
    2a. `get_latest_private_key_and_kid()` fetches the newest private key.
    2b. A short-lived RS256 JWT is signed (`kid` header set).
    2c. The JWT is returned to the caller.
3. Key rotation (scheduled task or manual call)
   - `rotate_keys()` generates a *new* key-pair, inserts its JWK at the head of
   `jwks.json`, prunes keys beyond `AUTH_ROTATION_KEEP_LAST_N`, deletes stale PEM
   files, and updates the global JWKS cache.
4. JWKS serving
   - API route `/auth/jwks.json` simply calls `load_jwks()`. Other servers (e.g.,
   FastMCP servers) periodically fetch this endpoint to verify future tokens.

Concurrency & Safety
--------------------
1. `process_lock()` wraps all file-system writes so multiple Uvicorn workers (or cron
    jobs) cannot rotate keys concurrently.
2. Atomic writes via `atomic_write()` guarantee that readers never observe partially
    written files.
3. In-memory cache (`_JWKS_CACHE` + `_JWKS_MTIME`) avoids repeated disk I/O and
    re-parses while still noticing on-disk changes made by other processes.

Environment contracts
---------------------
1. `$PATHS_SECRETS_DIR` (directory) must exist and be writable by the process.
2. `Settings.*` values (issuer, audience, time-outs, etc.) drive defaults.

All public helpers are asyncio-friendly; call them with `await` unless noted otherwise.
"""

# pylint: disable=W0603
# Standard Library
import asyncio
import base64
import json
import os
import time

from contextlib import asynccontextmanager
from pathlib import Path
from secrets import token_hex
from typing import Any, AsyncIterator, cast

# Third Party Library
import jwt

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from filelock import FileLock
from loguru import logger

# Package Library
from mcp_demo.config import Settings
from mcp_demo.utils.general import atomic_write, make_dir

_JWKS_CACHE: dict[str, Any] | None = None  # In-memory copy
_JWKS_MTIME: float | None = None  # Last os.stat mtime

PATHS_PROJECT_DIR = os.getenv("PATHS_PROJECT_DIR", None)
assert PATHS_PROJECT_DIR is not None
_SECRETS_DIR = Path(PATHS_PROJECT_DIR) / "secrets"
make_dir(_SECRETS_DIR, mode=0o700)
_LOCK = FileLock(str(_SECRETS_DIR / ".rotate.lock"), timeout=0)  # Non-blocking lock

AUTH_AUDIENCE = Settings.AUTH_AUDIENCE
AUTH_FILELOCK_TIMEOUT = Settings.AUTH_FILELOCK_TIMEOUT
AUTH_JWK_ALGORITHM = Settings.AUTH_JWK_ALGORITHM
AUTH_JWKS_FN = Settings.AUTH_JWKS_FN
AUTH_ROTATION_KEEP_LAST_N = Settings.AUTH_ROTATION_KEEP_LAST_N
AUTH_RSA_KEY_SIZE = Settings.AUTH_RSA_KEY_SIZE
AUTH_RSA_PUBLIC_EXPONENT = Settings.AUTH_RSA_PUBLIC_EXPONENT
AUTH_TOKEN_ISSUER = Settings.AUTH_TOKEN_ISSUER
AUTH_TOKEN_TTL = Settings.AUTH_TOKEN_TTL


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


async def get_jwt_token(
    *,
    jwks_fn: str = AUTH_JWKS_FN,
    passphrase: str,
    scopes: list[str],
    sub: str,
) -> str:
    """Generate a JWT token for the given subject and scopes.

    NB: We use RS256 for signing the JWT, which requires a private key. `kid` is used
    to identify the key in the JWKS (JSON Web Key Set) endpoint. The `iss` (issuer) and
    `aud` (audience) fields are set to the issuer and audience of the token,
    respectively, and should match the values expected by the server.

    The process is as follows:

    1. Grab the most-recent private key and its kid (auto-bootstraps on first run).
    2. Create a JWT payload with the subject, scopes, issuer, audience, issued at
         time, and expiration time.
    3. Sign with RS256 and embed the kid so verifiers find the right JWK.

    Parameters
    ----------
    jwks_fn
        The filename for the JWKS (JSON Web Key Set) file. This is only used for
        generating a new key if the JWKS file is empty.
    passphrase
        Passphrase to decrypt the private key.
    scopes
        A list of scopes to include in the JWT token.
    sub
        The subject for which the JWT token is being generated, typically a user ID.

    Returns
    -------
    str
        The generated JWT token as a string.
    """

    # 1.
    private_key, kid = await get_latest_private_key_and_kid(
        jwks_fn=jwks_fn, passphrase=passphrase
    )

    # 2.
    now = int(time.time())
    payload = {
        "aud": AUTH_AUDIENCE,
        "exp": now + AUTH_TOKEN_TTL,
        "iat": now,
        "iss": AUTH_TOKEN_ISSUER,
        "jti": token_hex(12),
        "nbf": now - 30,
        "scope": " ".join(scopes),
        "sub": sub,
        "typ": "JWT",
    }

    # 3.
    return cast(
        str,
        jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid}),
    )


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

    global _JWKS_CACHE, _JWKS_MTIME

    jwks_fp = _SECRETS_DIR / jwks_fn

    async with process_lock():
        if not jwks_fp.is_file():
            _JWKS_CACHE = {"keys": []}
            _JWKS_MTIME = None
            assert isinstance(_JWKS_CACHE, dict)
            return _JWKS_CACHE

        mtime = jwks_fp.stat().st_mtime
        if _JWKS_CACHE is None or _JWKS_MTIME != mtime:  # File changed or first read
            try:
                _JWKS_CACHE = json.loads(jwks_fp.read_text("utf-8"))
                _JWKS_MTIME = mtime
            except json.JSONDecodeError:
                logger.warning("Corrupted JWKS, regenerating")
                _JWKS_CACHE = {"keys": []}
        assert isinstance(_JWKS_CACHE, dict)
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
        jwks = await load_jwks(jwks_fn=jwks_fn)
        jwks["keys"].insert(0, jwk_from_public_key(kid=kid, public_key=public_key))

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
        global _JWKS_CACHE, _JWKS_MTIME

        _JWKS_CACHE = jwks
        _JWKS_MTIME = (_SECRETS_DIR / jwks_fn).stat().st_mtime

        if kid in {key["kid"] for key in jwks["keys"][1:]}:
            logger.warning(f"Duplicate kid detected after rotation: {kid}")

        return kid


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


async def verify_user(*, form: OAuth2PasswordRequestForm = Depends()) -> dict[str, Any]:
    """Verify user credentials.

    TODO: Swap in a real DB lookup + salted hash check. At the moment, this only keeps
    track of a single user with a hardcoded username and passphrase from the .env file.

    Parameters
    ----------
    form
        The OAuth2 password request form containing the username, password, and scopes.

    Returns
    -------
    dict[str, Any]
        A dictionary containing the subject (username) and scopes of the authenticated
        user.

    Raises
    ------
    HTTPException
        If the username or password is incorrect, an HTTP 401 Unauthorized error is
        raised.
    """

    if not (
        form.username == Settings.AUTH_USER_NAME
        and form.password == Settings.AUTH_USER_PASSPHRASE.get_secret_value()
    ):
        raise HTTPException(
            detail="Bad credentials.",
            headers={"WWW-Authenticate": "Bearer"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    scopes = form.scopes or ["read"]  # Default for FastMCP

    return {"sub": form.username, "scopes": scopes}

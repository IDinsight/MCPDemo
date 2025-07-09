"""This module contains general utilities.

NB: As a general rule of thumb, this module should not import utilities from other
utils modules (in order to avoid circular imports). If a utility function is needed in
multiple modules, then it is a general utility and should be defined in this module
instead.
"""

# Standard Library
import os
import random
import re
import secrets
import string

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

# Third Party Library
import yaml

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exc
from argon2.low_level import Type
from loguru import logger

# Tuned for 64 MiB & 2 rounds ≈ 120 ms on AWS t4g.medium.
_PH = PasswordHasher(
    hash_len=32,
    memory_cost=64 * 1024,
    parallelism=2,
    salt_len=16,
    time_cost=2,
    type=Type.ID,
)


def atomic_write(
    *,
    data: bytes | str,
    mode: str = "wb",
    perm: int = 0o600,
    target_fp: str | os.PathLike,
) -> None:
    """Atomically write *data* to **target_fp**, replacing any existing file.

    The process is as follows:

    1. A temporary file is created **in the same directory** as *target_fp* so the
       final `os.replace()` call stays on the same filesystem.
    2. The full payload is written, flushed, and `fsync()`’d to disk.
    3. Restrictive permissions (default `rw-------`) are applied to the closed temp
       file.
    4. `os.replace()` renames the temp file over *target_fp*—atomic on local
       POSIX filesystems and Windows NTFS.
    5. The parent directory is `fsync()`’d, guaranteeing the rename is committed.
    6. If anything after step 2 fails, the original file is left untouched and the
       temp file is deleted.

    **Caveats**

    1. Atomicity is **not guaranteed on network filesystems** such as NFS or SMB.
    2. The function *overwrites* any existing `target_fp`; pass a non-existent path
        if you need a “create-only” semantic.
    3. Symlink attacks are mitigated because all work is done in a fresh temp file
      before the final, atomic rename.

    Parameters
    ----------
    data
        Bytes or text to persist. *mode* must contain `'b'` when passing `bytes`
        and must omit `'b'` when passing `str`; a `TypeError` is raised otherwise.
    mode
        File-open mode. Defaults to `"wb"` (binary write). Use `"w"` for text or
        `"a"` / `"ab"` to append.
    perm
        Octal permissions applied **after** the temp file is closed; default `0o600`
        (owner read-write).
    target_fp
        Destination path (str or `Path`). Parent directory must be writable.

    Raises
    ------
    TypeError
        If *data* type doesn’t match the indicated *mode*.
    OSError
        For any underlying I/O failure during write, flush, or rename.
    """

    if (
        "b" in mode
        and isinstance(data, str)
        or "b" not in mode
        and isinstance(data, bytes)
    ):
        raise TypeError(
            f"Expected data to be {'bytes' if 'b' in mode else 'str'}, "
            f"got {type(data).__name__}"
        )

    target_fp = Path(target_fp)
    dir_ = target_fp.parent

    # 1.
    with NamedTemporaryFile(
        dir=dir_, delete=False, encoding="utf-8" if "b" not in mode else None, mode=mode
    ) as tmp:
        # 2.
        tmp_path = Path(tmp.name)
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())

    # 3.
    tmp_path.chmod(perm)

    try:
        # 4.
        os.replace(tmp_path, target_fp)  # Atomic on POSIX & Windows

        # 5.
        dir_fd = os.open(str(dir_), os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)  # Flush directory metadata
        finally:
            os.close(dir_fd)  # Prevent FD leak
    except OSError as e:
        logger.error(f"Failed to replace {target_fp} with {tmp_path}: {e}")
        raise OSError(f"Failed to replace {target_fp} with {tmp_path}") from e
    finally:
        # 6.
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


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


def hash_password(*, password: str) -> str:
    """Hash a password using Argon2.

    Parameters
    ----------
    password
        The password to hash.

    Returns
    -------
    str
        The hashed password.
    """

    return _PH.hash(password)


def generate_random_string(*, size: int) -> str:
    """Generate a random string of fixed length.

    Parameters
    ----------
    size
        The size of the random string to generate.

    Returns
    -------
    str
        The generated random string.
    """

    return "".join(random.choices(string.ascii_letters + string.digits, k=size))


def generate_recovery_codes(*, code_length: int = 20, num_codes: int = 5) -> list[str]:
    """Generate recovery codes for a user.

    Parameters
    ----------
    code_length
        The length of each recovery code.
    num_codes
        The number of recovery codes to generate.

    Returns
    -------
    list[str]
        A list of recovery codes.
    """

    chars = string.ascii_letters + string.digits
    return [
        "".join(secrets.choice(chars) for _ in range(code_length))
        for _ in range(num_codes)
    ]


def make_dir(dir_: str | Path, mode: int = 0o777, verbose: bool = True) -> None:
    """Create a directory.

    Parameters
    ----------
    dir_
        Directory to create.
    mode
        The mode to set on the directory. Defaults to `0o777` (read, write, and
        execute for everyone).
    verbose
        Specifies whether to log directory creation.
    """

    dir_ = Path(dir_)
    if not Path.is_dir(dir_):
        if verbose:
            logger.info(f"Creating directory: {dir_}")
        Path.mkdir(dir_, exist_ok=True, mode=mode, parents=True)
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


def verify_password(
    *, plain_password: str, hashed_password: str
) -> tuple[bool, str | None]:
    """Verify a plain text password against a hashed password.

    If the stored hash was generated with weaker parameters, it is transparently
    re-hashed with the current policy and the new digest is returned; callers can
    persist it.

    Parameters
    ----------
    plain_password
        The plain text password to verify.
    hashed_password
        The hashed password to verify against.

    Returns
    -------
    tuple[bool, str | None]
        A tuple containing a boolean indicating whether the password is valid and the
        hashed password (which may be re-hashed if needed).
    """

    try:
        _PH.verify(hashed_password, plain_password)
    except argon2_exc.VerifyMismatchError:
        return False, None

    if _PH.check_needs_rehash(hashed_password):
        hashed_password = hash_password(password=plain_password)
    return True, hashed_password


def yaml_serializer(data: dict[str, Any]) -> str:
    """Serialize a dictionary to a YAML string.

    Parameters
    ----------
    data
        The dictionary to serialize.

    Returns
    -------
    str
        The serialized YAML string.
    """

    return yaml.dump(data, sort_keys=True)

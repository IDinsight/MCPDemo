"""This module contains general utilities.

NB: As a general rule of thumb, this module should not import utilities from other
utils modules (in order to avoid circular imports). If a utility function is needed in
multiple modules, then it is a general utility and should be defined in this module
instead.
"""

# Standard Library
import os
import re

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

# Third Party Library
import yaml

from loguru import logger


def atomic_write(
    *,
    data: bytes | str,
    mode: str = "wb",
    perm: int = 0o600,
    target_fp: str | os.PathLike,
) -> None:
    """Atomically write `data` to `target`.

    NB: On network filesystems (NFS) atomicity is **NOT** guaranteed. It is only
    guaranteed on POSIX filesystems and Windows.

    The process is as follows:

    1. Create a temp file in the **same directory** as `target` so the final rename
        stays on the filesystem.
    2. Write, flush, and fsync the temp file.
    3. Set restrictive permissions (default is rw-------). This is done after the
        file is closed to avoid issues with open file descriptors.
    4. Rename (os.replace) it over `target`.
    5. fsync the directory containing `target` to ensure the rename is committed.
    6. If replace failed, tidy up temp file.

    Any exception before Step 3 leaves the original `target` untouched.

    Parameters
    ----------
    data
        The data to write to the target file. If `mode` is binary (`b`), then this
        should be bytes-like data. If `mode` is text, then this should be a string.
    mode
        The mode in which to open the file. Defaults to `wb` (write binary). If you
        want to write text, use `w` (write text). If you want to append, use `ab` or
        `a` (append binary or text, respectively).
    perm
        The permissions to set on the target file after writing. Defaults to `0o600`
        (read and write for the owner only).
    target_fp
        The target file path where the data should be written. This can be a string or
        a `Path` object. The target file should not exist before calling this function.

    Raises
    ------
    TypeError
        If `mode` is binary (`b`) and `data` is not bytes-like, or if `mode` is text
        and `data` is bytes-like.
    OSError
        If there is an error during file operations, such as writing, flushing, or
        renaming the file.
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

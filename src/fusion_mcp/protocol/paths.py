"""File path checks for export and import (c12 D2), shared by the MCP server and the add-in.
Stdlib only.

The server calls them before a request is sent (an early refusal: the server and Fusion run
on one machine); the add-in calls them again, authoritatively, because a file may appear
between the two calls. Every refusal is ``INVALID_PARAMS`` with
``details = {"param": "path", "reason": <code>, "path": <path>}``. An extension is never
appended silently: a mismatch is an error.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from .errors import BridgeError, ErrorCode

OUTPUT_REASONS = ("not_absolute", "bad_extension", "parent_missing", "is_directory", "exists")
INPUT_REASONS = ("not_absolute", "not_found", "is_directory", "bad_extension")


def _refuse(reason: str, message: str, path: Any, **extra: Any) -> BridgeError:
    details: dict[str, Any] = {"param": "path", "reason": reason, "path": path}
    details.update(extra)
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _absolute(path: Any) -> str:
    if not isinstance(path, str) or not path.strip():
        raise _refuse("not_absolute", "parameter 'path' must be a non-empty string", path)
    if not os.path.isabs(path):
        raise _refuse("not_absolute", f"path must be absolute, got {path!r}", path)
    return os.path.normpath(path)


def _check_extension(path: str, extensions: Iterable[str]) -> None:
    allowed = [e.lower() for e in extensions]
    if os.path.splitext(path)[1].lower() not in allowed:
        raise _refuse(
            "bad_extension",
            f"path must end with {' or '.join(allowed)}: {path!r}",
            path,
            expected=allowed,
        )


def check_output_path(path: Any, extensions: Iterable[str], overwrite: bool = False) -> str:
    """Normalized absolute ``path`` a file may be written to: the extension (any case) is one
    of ``extensions``, the parent directory exists, the path is not a directory and, unless
    ``overwrite``, no file is there yet."""
    normalized = _absolute(path)
    _check_extension(normalized, extensions)
    parent = os.path.dirname(normalized)
    if not os.path.isdir(parent):
        raise _refuse("parent_missing", f"directory {parent!r} does not exist", normalized)
    if os.path.isdir(normalized):
        raise _refuse("is_directory", f"path {normalized!r} is a directory", normalized)
    if os.path.exists(normalized) and not overwrite:
        raise _refuse(
            "exists",
            f"file {normalized!r} already exists: pass overwrite=true to replace it",
            normalized,
        )
    return normalized


def check_input_path(path: Any, extensions: Iterable[str]) -> str:
    """Normalized absolute ``path`` of an existing file whose extension (any case) is one of
    ``extensions``."""
    normalized = _absolute(path)
    if not os.path.exists(normalized):
        raise _refuse("not_found", f"file {normalized!r} does not exist", normalized)
    if os.path.isdir(normalized):
        raise _refuse("is_directory", f"path {normalized!r} is a directory", normalized)
    _check_extension(normalized, extensions)
    return normalized

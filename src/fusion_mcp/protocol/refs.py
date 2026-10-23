"""Reference grammar shared by the MCP server and the add-in (c03 D15). Stdlib only.

- ``origin:<NAME>``: the origin planes, axes and point of a component, ``NAME`` one of
  ``ORIGIN_NAMES`` (case-insensitive in, upper-case out).
- Assembly context: ``<inner>@<occurrence path>`` (``entityToken`` is base64 without ``@``).
  Name refs (``NAME_PREFIXES``) are never split on ``@``: names may contain it and profile
  refs use it as the point separator.
"""

from __future__ import annotations

from .errors import BridgeError, ErrorCode

ORIGIN_PREFIX = "origin:"
ORIGIN_NAMES = ("XY", "XZ", "YZ", "X", "Y", "Z", "O")
NAME_PREFIXES = ("component:", "occurrence:", "param:", "timeline:", "document:", "profile:")
CONTEXT_SEPARATOR = "@"


def is_origin_ref(ref: object) -> bool:
    return isinstance(ref, str) and ref[: len(ORIGIN_PREFIX)].lower() == ORIGIN_PREFIX


def parse_origin_ref(ref: str) -> str:
    """``"origin:xy"`` -> ``"XY"``; an unknown name or a non-origin ref is ``INVALID_PARAMS``."""
    if not is_origin_ref(ref):
        raise BridgeError(ErrorCode.INVALID_PARAMS, f"{ref!r} is not an origin ref", {"ref": ref})
    name = ref[len(ORIGIN_PREFIX) :].upper()
    if name not in ORIGIN_NAMES:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"unknown origin element {ref[len(ORIGIN_PREFIX) :]!r}; expected one of "
            + ", ".join(ORIGIN_NAMES),
            {"ref": ref, "expected": list(ORIGIN_NAMES)},
        )
    return name


def format_origin_ref(name: str) -> str:
    upper = name.upper()
    if upper not in ORIGIN_NAMES:
        raise ValueError(f"unknown origin element {name!r}")
    return ORIGIN_PREFIX + upper


def is_name_ref(ref: str) -> bool:
    return ref.startswith(NAME_PREFIXES)


def split_context(ref: str) -> tuple[str, str | None]:
    """``"<inner>@<path>"`` -> ``(inner, path)``; no context -> ``(ref, None)``.

    Name refs are returned whole. An empty inner part or path is a ``ValueError``.
    """
    if is_name_ref(ref) or CONTEXT_SEPARATOR not in ref:
        return ref, None
    inner, path = ref.split(CONTEXT_SEPARATOR, 1)
    if not inner or not path:
        raise ValueError(f"ref {ref!r} has an empty part around '{CONTEXT_SEPARATOR}'")
    return inner, path


def with_context(inner: str, path: str | None) -> str:
    """Inverse of ``split_context``; ``inner`` must not carry a context or be a name ref."""
    if not inner:
        raise ValueError("inner ref must not be empty")
    if CONTEXT_SEPARATOR in inner or is_name_ref(inner):
        raise ValueError(f"ref {inner!r} cannot take an assembly context")
    if path is None:
        return inner
    if not path:
        raise ValueError("assembly context path must not be empty")
    return f"{inner}{CONTEXT_SEPARATOR}{path}"

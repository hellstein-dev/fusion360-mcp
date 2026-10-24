"""Vocabularies of the ``export.*`` / ``import.*`` methods and the colour format of
``appearance.set_color`` (c12 D3), shared by the MCP server and the add-in. Stdlib only.

The server turns the tuples into ``Literal[...]`` types; the add-in re-checks raw params
before it maps a value to a Fusion enum.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from typing import Any

from .errors import BridgeError, ErrorCode

CAD_EXPORT_FORMATS = ("step", "iges", "sat", "smt", "f3d", "usdz")
MESH_EXPORT_FORMATS = ("stl", "3mf", "obj")
IMPORT_FORMATS = ("step", "iges", "sat", "smt", "f3d", "stl", "obj", "3mf")
CAD_IMPORT_FORMATS = ("step", "iges", "sat", "smt", "f3d")
MESH_IMPORT_FORMATS = ("stl", "obj", "3mf")
EXTENSIONS: dict[str, tuple[str, ...]] = {
    "step": (".step", ".stp"),
    "iges": (".iges", ".igs"),
    "sat": (".sat",),
    "smt": (".smt",),
    "f3d": (".f3d",),
    "usdz": (".usdz",),
    "stl": (".stl",),
    "3mf": (".3mf",),
    "obj": (".obj",),
    "dxf": (".dxf",),
}
REFINEMENTS = ("low", "medium", "high", "custom")
FILE_UNITS = ("mm", "cm", "m", "in", "ft")


def extensions_of(formats: Sequence[str]) -> tuple[str, ...]:
    """Every extension of ``formats``, in order."""
    return tuple(ext for name in formats for ext in EXTENSIONS[name])


def detect_format(path: str, allowed: Sequence[str]) -> str:
    """Format of ``path`` by its extension (any case), one of ``allowed``; otherwise
    ``INVALID_PARAMS`` with ``details = {"param": "format", "path", "expected"}``."""
    extension = os.path.splitext(path)[1].lower()
    for name in allowed:
        if extension in EXTENSIONS[name]:
            return name
    raise BridgeError(
        ErrorCode.INVALID_PARAMS,
        f"cannot tell the format from {extension or 'no extension'!r}: pass 'format' "
        f"({', '.join(allowed)}) or use a matching extension",
        {"param": "format", "path": path, "expected": list(allowed)},
    )


_HEX = re.compile(r"[0-9A-Fa-f]+")


def _bad_color(value: Any, why: str) -> BridgeError:
    return BridgeError(
        ErrorCode.INVALID_PARAMS,
        f"parameter 'color' {why}: use '#RRGGBB', '#RRGGBBAA' or [r, g, b] / [r, g, b, a] "
        "with integers 0..255",
        {"param": "color", "value": value if isinstance(value, str) else repr(value)},
    )


def parse_color(value: Any) -> tuple[int, int, int, int]:
    """``"#RRGGBB"``, ``"#RRGGBBAA"`` or a list of 3/4 integers 0..255 -> ``(r, g, b, a)``
    (alpha 255 when omitted); anything else is ``INVALID_PARAMS``."""
    if isinstance(value, str):
        text = value.strip()
        digits = text[1:]
        if not text.startswith("#") or len(digits) not in (6, 8) or not _HEX.fullmatch(digits):
            raise _bad_color(value, "is not a hex colour")
        channels = [int(digits[i : i + 2], 16) for i in range(0, len(digits), 2)]
    elif isinstance(value, list | tuple):
        if len(value) not in (3, 4):
            raise _bad_color(value, f"must have 3 or 4 channels, got {len(value)}")
        for channel in value:
            if isinstance(channel, bool) or not isinstance(channel, int):
                raise _bad_color(value, "channels must be integers")
            if not 0 <= channel <= 255:
                raise _bad_color(value, f"channel {channel} is outside 0..255")
        channels = list(value)
    else:
        raise _bad_color(value, f"must be a string or a list, got {type(value).__name__}")
    if len(channels) == 3:
        channels.append(255)
    r, g, b, a = channels
    return r, g, b, a


def format_color(rgba: Sequence[int]) -> str:
    """``(r, g, b, a)`` -> ``"#RRGGBBAA"``."""
    r, g, b, a = rgba
    return f"#{r:02X}{g:02X}{b:02X}{a:02X}"

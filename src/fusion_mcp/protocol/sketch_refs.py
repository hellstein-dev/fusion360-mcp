"""Profile refs (c05 D8), shared by the MCP server and the add-in. Stdlib only.

A sketch profile has no persistent id: Fusion recomputes ``Sketch.profiles`` on every sketch
change and reorders the collection. A profile is therefore addressed by a point inside its
region: ``profile:<sketchEntityToken>@<x>,<y>`` with ``x``/``y`` in sketch-space mm, four
decimals. ``@`` and ``,`` never occur in a base64 ``entityToken``; parsing splits on the
*last* ``@`` anyway. ``profile:`` is one of ``refs.NAME_PREFIXES``, so the assembly-context
grammar (``<ref>@<path>``) never splits a profile ref.
"""

from __future__ import annotations

import math
import re

from .errors import BridgeError, ErrorCode

PROFILE_PREFIX = "profile:"
POINT_SEPARATOR = "@"
COORD_SEPARATOR = ","
PROFILE_REF_DECIMALS = 4

# A plain decimal number: no whitespace, no ``nan``/``inf``, no ``_`` digit separators.
_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")


def is_profile_ref(ref: object) -> bool:
    return isinstance(ref, str) and ref.startswith(PROFILE_PREFIX)


def _coord(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError(f"profile point coordinate must be finite, got {value!r}")
    # ``+ 0.0`` turns -0.0 into 0.0 so a point on an axis never prints as ``-0.0000``.
    return f"{round(float(value), PROFILE_REF_DECIMALS) + 0.0:.{PROFILE_REF_DECIMALS}f}"


def format_profile_ref(sketch_token: str, x_mm: float, y_mm: float) -> str:
    """``("tok", 1.5, -2)`` -> ``"profile:tok@1.5000,-2.0000"``.

    An empty token or a non-finite coordinate is a ``ValueError`` (a bug in the caller, not
    agent input).
    """
    if not sketch_token:
        raise ValueError("sketch token must not be empty")
    point = f"{_coord(x_mm)}{COORD_SEPARATOR}{_coord(y_mm)}"
    return f"{PROFILE_PREFIX}{sketch_token}{POINT_SEPARATOR}{point}"


def _invalid(ref: object, why: str) -> BridgeError:
    return BridgeError(
        ErrorCode.INVALID_PARAMS,
        f"malformed profile ref {ref!r}: {why}; expected 'profile:<sketch token>@<x>,<y>'"
        " (sketch-space mm), as returned by sketch_profiles",
        {"ref": ref, "reason": "malformed_profile_ref"},
    )


def parse_profile_ref(ref: object) -> tuple[str, float, float]:
    """``"profile:tok@1.5,-2"`` -> ``("tok", 1.5, -2.0)``; anything malformed is
    ``INVALID_PARAMS``."""
    if not isinstance(ref, str):
        raise _invalid(ref, "not a string")
    if not ref.startswith(PROFILE_PREFIX):
        raise _invalid(ref, f"missing '{PROFILE_PREFIX}' prefix")
    body = ref[len(PROFILE_PREFIX) :]
    if POINT_SEPARATOR not in body:
        raise _invalid(ref, f"missing '{POINT_SEPARATOR}' before the point")
    token, point = body.rsplit(POINT_SEPARATOR, 1)
    if not token:
        raise _invalid(ref, "empty sketch token")
    parts = point.split(COORD_SEPARATOR)
    if len(parts) != 2:
        raise _invalid(ref, "the point must be two comma-separated numbers")
    if not all(_NUMBER_RE.fullmatch(part) for part in parts):
        raise _invalid(ref, "point coordinates must be plain decimal numbers")
    x, y = float(parts[0]), float(parts[1])
    if not (math.isfinite(x) and math.isfinite(y)):
        raise _invalid(ref, "point coordinates must be finite")
    return token, x, y

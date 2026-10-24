"""String vocabularies of the ``feature.*`` methods (c07 D2), shared by the MCP server and the
add-in. Stdlib only.

The server turns each tuple into a ``Literal[...]`` (``server/tools/_feature_models.py``, a
test keeps both in sync); the add-in re-checks raw params with ``check_choice`` before it maps
a value to a Fusion enum.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .errors import BridgeError, ErrorCode

OPERATIONS = ("new_body", "join", "cut", "intersect", "new_component")
EXTENT_TYPES = ("distance", "to_entity", "through_all")
EXTRUDE_MODES = ("one_side", "two_sides", "symmetric")
DIRECTIONS = ("positive", "negative")
START_TYPES = ("profile_plane", "offset", "entity")
THIN_LOCATIONS = ("side1", "side2", "center")
# The ``health.state`` of feature results; the same words as ``health`` in c03 descriptions.
HEALTH_STATES = ("ok", "warning", "error", "unknown")


def check_choice(name: str, value: Any, choices: Sequence[str]) -> str:
    """``value`` if it is one of ``choices``; otherwise ``INVALID_PARAMS`` with
    ``details = {"param", "value", "expected"}`` (``value`` is a ``repr`` unless it is a
    string or ``None``)."""
    if isinstance(value, str) and value in choices:
        return value
    raise BridgeError(
        ErrorCode.INVALID_PARAMS,
        f"parameter '{name}' must be one of {', '.join(choices)}; got {value!r}",
        {
            "param": name,
            "value": value if isinstance(value, str | None) else repr(value),
            "expected": list(choices),
        },
    )

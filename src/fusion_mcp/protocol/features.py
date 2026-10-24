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
# Sweep (D12), loft (D13), rib/web (D14).
SWEEP_ORIENTATIONS = ("perpendicular", "parallel")
RAIL_SCALINGS = ("scale", "stretch", "none")
LOFT_END_CONDITIONS = (
    "free",
    "tangent",
    "smooth",
    "direction",
    "point_sharp",
    "point_tangent",
)
RIB_THICKNESS_LOCATIONS = ("symmetric", "side1", "side2")
RIB_DEPTH_TYPES = ("to_next", "distance")
# Hole (D15) and thread (D16).
HOLE_KINDS = ("simple", "counterbore", "countersink")
HOLE_PLACEMENTS = ("point", "sketch_points", "face_offsets", "center")
HOLE_EXTENT_TYPES = ("distance", "through_all", "to_entity")
THREAD_LOCATIONS = ("high", "low")
# Thread type Fusion offers by default for metric threads; designations stay strings.
DEFAULT_THREAD_TYPE = "ISO Metric profile"
# Fillet, rule fillet (D17) and chamfer (D18).
FILLET_MODES = ("constant", "variable", "chord")
FILLET_CORNERS = ("rolling_ball", "setback")
RULE_FILLET_RULES = ("all_edges", "between_faces")
CHAMFER_MODES = ("equal", "two_distances", "distance_angle")
# Patterns, mirror and body operations (c08 D1).
PATTERN_DISTANCE_TYPES = ("extent", "spacing")
COMPUTE_OPTIONS = ("optimized", "identical", "adjust")
COMBINE_OPERATIONS = ("join", "cut", "intersect")
MOVE_TYPES = ("translate", "translate_along", "rotate", "rotate_about", "align", "matrix")
SPLIT_FACE_TYPES = ("intersection", "along_vector", "closest_point")
REMOVE_MODES = ("remove", "delete")


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

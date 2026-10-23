"""Geometric query filters (c03 D9): parse, match and select brief entity descriptions.

Stdlib only, shared by the add-in (``inspect.query``) and later changes. Works on the dicts
``describe(..., "brief")`` produces, which are already in mm and degrees; faces carry
``radius_mm``/``axis`` and edges ``radius_mm``/``direction`` added by the handler.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import BridgeError, ErrorCode

KINDS = ("face", "edge", "vertex", "body")
SURFACE_TYPES = (
    "plane",
    "cylinder",
    "cone",
    "sphere",
    "torus",
    "elliptical_cylinder",
    "elliptical_cone",
    "nurbs",
    "other",
)
CURVE_TYPES = ("line", "arc", "circle", "ellipse", "elliptical_arc", "nurbs", "other")
SORT_KEYS = ("area", "length", "radius", "volume", "x", "y", "z")
ORDERS = ("desc", "asc")

DEFAULT_TOLERANCE_DEG = 1.0
DEFAULT_ORDER = "desc"
DEFAULT_LIMIT = 50
MAX_LIMIT = 1000

COMMON_KEYS = frozenset({"kind", "scope", "within_box", "sort_by", "order", "limit"})
# ``units`` is read by the add-in ``Context`` for every request; query values are mm anyway.
RESERVED_KEYS = frozenset({"units"})
KIND_KEYS: dict[str, frozenset[str]] = {
    "face": frozenset(
        {
            "surface_type",
            "normal",
            "normal_tolerance_deg",
            "axis",
            "area_min_mm2",
            "area_max_mm2",
            "radius_min_mm",
            "radius_max_mm",
        }
    ),
    "edge": frozenset(
        {
            "curve_type",
            "direction",
            "direction_tolerance_deg",
            "length_min_mm",
            "length_max_mm",
            "radius_min_mm",
            "radius_max_mm",
        }
    ),
    "vertex": frozenset(),
    "body": frozenset(),
}
ALL_KEYS = COMMON_KEYS | RESERVED_KEYS | frozenset().union(*KIND_KEYS.values())
# Which kinds a sort key makes sense for.
SORT_KINDS: dict[str, tuple[str, ...]] = {
    "area": ("face", "body"),
    "length": ("edge",),
    "radius": ("face", "edge"),
    "volume": ("body",),
    "x": KINDS,
    "y": KINDS,
    "z": KINDS,
}
RANGES = (
    ("area_min_mm2", "area_max_mm2", "area_mm2"),
    ("length_min_mm", "length_max_mm", "length_mm"),
    ("radius_min_mm", "radius_max_mm", "radius_mm"),
)

Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class QuerySpec:
    kind: str
    scope: list[str] | None = None
    surface_type: str | None = None
    curve_type: str | None = None
    normal: Vec3 | None = None
    normal_tolerance_deg: float = DEFAULT_TOLERANCE_DEG
    axis: Vec3 | None = None
    direction: Vec3 | None = None
    direction_tolerance_deg: float = DEFAULT_TOLERANCE_DEG
    area_min_mm2: float | None = None
    area_max_mm2: float | None = None
    length_min_mm: float | None = None
    length_max_mm: float | None = None
    radius_min_mm: float | None = None
    radius_max_mm: float | None = None
    within_box: tuple[Vec3, Vec3] | None = None
    sort_by: str | None = None
    order: str = DEFAULT_ORDER
    limit: int = DEFAULT_LIMIT


def _invalid(param: str, message: str) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, {"param": param})


def _number(param: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _invalid(param, f"parameter '{param}' must be a finite number")
    return float(value)


def _vec3(param: str, value: Any, nonzero: bool = True) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _invalid(param, f"parameter '{param}' must be a list of 3 numbers")
    x, y, z = (_number(param, v) for v in value)
    if nonzero and math.hypot(x, y, z) == 0.0:
        raise _invalid(param, f"parameter '{param}' must be a non-zero vector")
    return (x, y, z)


def _choice(param: str, value: Any, choices: Sequence[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise _invalid(param, f"parameter '{param}' must be one of {', '.join(choices)}")
    return value


def _tolerance(param: str, value: Any) -> float:
    tol = _number(param, value)
    if not 0.0 <= tol <= 180.0:
        raise _invalid(param, f"parameter '{param}' must be within 0..180 degrees")
    return tol


def _scope(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(r, str) and r for r in value):
        raise _invalid("scope", "parameter 'scope' must be a list of refs")
    return list(value)


def _box(value: Any) -> tuple[Vec3, Vec3]:
    if not isinstance(value, dict) or set(value) != {"min_mm", "max_mm"}:
        raise _invalid(
            "within_box", "parameter 'within_box' must be {'min_mm': [..], 'max_mm': [..]}"
        )
    lo = _vec3("within_box", value["min_mm"], nonzero=False)
    hi = _vec3("within_box", value["max_mm"], nonzero=False)
    if any(a > b for a, b in zip(lo, hi, strict=True)):
        raise _invalid("within_box", "within_box.min_mm must not exceed max_mm")
    return (lo, hi)


def _limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
        raise _invalid("limit", f"parameter 'limit' must be an integer 1..{MAX_LIMIT}")
    return value


def parse_query(params: dict[str, Any]) -> QuerySpec:
    """Validate ``params`` into a ``QuerySpec``; any problem is ``INVALID_PARAMS``."""
    if not isinstance(params, dict):
        raise _invalid("params", "query parameters must be an object")
    kind = _choice("kind", params.get("kind"), KINDS)
    given = {k: v for k, v in params.items() if v is not None and k not in RESERVED_KEYS}
    for key in given:
        if key not in ALL_KEYS:
            raise _invalid(key, f"unknown query filter '{key}'")
        if key not in COMMON_KEYS and key not in KIND_KEYS[kind]:
            raise _invalid(key, f"filter '{key}' does not apply to kind '{kind}'")

    values: dict[str, Any] = {"kind": kind}
    if "scope" in given:
        values["scope"] = _scope(given["scope"])
    if "surface_type" in given:
        values["surface_type"] = _choice("surface_type", given["surface_type"], SURFACE_TYPES)
    if "curve_type" in given:
        values["curve_type"] = _choice("curve_type", given["curve_type"], CURVE_TYPES)
    for key in ("normal", "axis", "direction"):
        if key in given:
            values[key] = _vec3(key, given[key])
    for key in ("normal_tolerance_deg", "direction_tolerance_deg"):
        if key in given:
            values[key] = _tolerance(key, given[key])
    for lo_key, hi_key, _ in RANGES:
        for key in (lo_key, hi_key):
            if key in given:
                values[key] = _number(key, given[key])
        lo, hi = values.get(lo_key), values.get(hi_key)
        if lo is not None and hi is not None and lo > hi:
            raise _invalid(lo_key, f"'{lo_key}' ({lo}) is greater than '{hi_key}' ({hi})")
    if "within_box" in given:
        values["within_box"] = _box(given["within_box"])
    if "sort_by" in given:
        sort_by = _choice("sort_by", given["sort_by"], SORT_KEYS)
        if kind not in SORT_KINDS[sort_by]:
            raise _invalid("sort_by", f"sort_by '{sort_by}' does not apply to kind '{kind}'")
        values["sort_by"] = sort_by
    if "order" in given:
        values["order"] = _choice("order", given["order"], ORDERS)
    if "limit" in given:
        values["limit"] = _limit(given["limit"])
    return QuerySpec(**values)


# --- matching ------------------------------------------------------------------------------


def angle_deg(a: Sequence[float], b: Sequence[float]) -> float:
    """Angle between two non-zero vectors, 0..180 degrees."""
    na, nb = math.hypot(*a), math.hypot(*b)
    if na == 0.0 or nb == 0.0:
        return 180.0
    cos = sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _same_direction(want: Vec3, got: Any, tol: float) -> bool:
    return got is not None and angle_deg(want, got) <= tol


def _parallel(want: Vec3, got: Any, tol: float) -> bool:
    if got is None:
        return False
    angle = angle_deg(want, got)
    return min(angle, 180.0 - angle) <= tol


def position(item: dict[str, Any]) -> list[float] | None:
    """Where an item is: face centroid, edge midpoint, vertex point or bbox centre."""
    for key in ("centroid_mm", "mid_mm", "point_mm"):
        if item.get(key) is not None:
            return list(item[key])
    box = item.get("bbox")
    if isinstance(box, dict) and box.get("min_mm") is not None:
        return [(a + b) / 2.0 for a, b in zip(box["min_mm"], box["max_mm"], strict=True)]
    return None


def _in_range(value: Any, lo: float | None, hi: float | None) -> bool:
    if lo is None and hi is None:
        return True
    if value is None:
        return False
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def matches(spec: QuerySpec, item: dict[str, Any]) -> bool:
    """True when ``item`` (a brief description of an entity of ``spec.kind``) passes every
    given filter."""
    if spec.surface_type is not None and item.get("surface_type") != spec.surface_type:
        return False
    if spec.curve_type is not None and item.get("curve_type") != spec.curve_type:
        return False
    if spec.normal is not None and not _same_direction(
        spec.normal, item.get("normal"), spec.normal_tolerance_deg
    ):
        return False
    if spec.axis is not None and not _parallel(
        spec.axis, item.get("axis"), spec.normal_tolerance_deg
    ):
        return False
    if spec.direction is not None and not _parallel(
        spec.direction, item.get("direction"), spec.direction_tolerance_deg
    ):
        return False
    for lo_key, hi_key, field in RANGES:
        if not _in_range(item.get(field), getattr(spec, lo_key), getattr(spec, hi_key)):
            return False
    if spec.within_box is not None:
        where = position(item)
        lo, hi = spec.within_box
        if where is None or not all(a <= v <= b for a, v, b in zip(lo, where, hi, strict=True)):
            return False
    return True


def sort_value(sort_by: str, item: dict[str, Any]) -> float | None:
    if sort_by in ("x", "y", "z"):
        where = position(item)
        return None if where is None else where["xyz".index(sort_by)]
    field = {"area": "area_mm2", "length": "length_mm", "radius": "radius_mm"}.get(
        sort_by, "volume_mm3"
    )
    value = item.get(field)
    return float(value) if isinstance(value, (int, float)) else None


def select(spec: QuerySpec, items: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Filter, sort (items without the sort value go last) and cut to ``limit``.

    Returns ``(selected, total)`` where ``total`` counts every match before the limit.
    """
    found = [item for item in items if matches(spec, item)]
    if spec.sort_by is not None:
        sort_by = spec.sort_by
        keyed = [(sort_value(sort_by, item), item) for item in found]
        with_value = [(v, i) for v, i in keyed if v is not None]
        without = [i for v, i in keyed if v is None]
        with_value.sort(key=lambda pair: pair[0], reverse=spec.order == "desc")
        found = [i for _, i in with_value] + without
    return found[: spec.limit], len(found)

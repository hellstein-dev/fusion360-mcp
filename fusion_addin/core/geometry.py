"""Serialisation helpers for Fusion geometry (c03 D3).

Duck-typed on ``.x/.y/.z`` and ``.minPoint/.maxPoint`` so they run on fakes. Lengths go
through ``ctx.units`` (``fusion_mcp.protocol.units``, INV-6) and come out in mm; direction
vectors are unitless and normalised here.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from fusion_mcp.protocol.units import USER_ROUND_DIGITS, Units

DEFAULT_UNITS = Units()
VECTOR_ROUND_DIGITS = 9


def _round(value: float, digits: int = USER_ROUND_DIGITS) -> float:
    # ``+ 0.0`` turns -0.0 into 0.0.
    return round(float(value), digits) + 0.0


def length(cm: float, units: Any = None) -> float:
    return _round((units or DEFAULT_UNITS).length_out(cm))


def point(p3d: Any, units: Any = None) -> list[float]:
    """``Point3D`` (cm) -> ``[x, y, z]`` mm."""
    return [length(p3d.x, units), length(p3d.y, units), length(p3d.z, units)]


def _unit(x: float, y: float, z: float) -> list[float]:
    norm = math.hypot(x, y, z)
    if norm == 0.0:
        return [0.0, 0.0, 0.0]
    return [_round(c / norm, VECTOR_ROUND_DIGITS) for c in (x, y, z)]


def vector(v3d: Any) -> list[float]:
    """``Vector3D`` -> normalised ``[x, y, z]`` (the input is not modified)."""
    copy = v3d.copy() if hasattr(v3d, "copy") else v3d
    if hasattr(copy, "normalize"):
        copy.normalize()
    return _unit(copy.x, copy.y, copy.z)


def direction(start: Any, end: Any) -> list[float]:
    """Normalised direction from point ``start`` to point ``end``."""
    return _unit(end.x - start.x, end.y - start.y, end.z - start.z)


def bbox(bb3d: Any, units: Any = None) -> dict[str, list[float]]:
    """``BoundingBox3D`` -> ``{"min_mm", "max_mm", "size_mm"}``."""
    lo = point(bb3d.minPoint, units)
    hi = point(bb3d.maxPoint, units)
    return box_dict(lo, hi)


def box_dict(lo: list[float], hi: list[float]) -> dict[str, list[float]]:
    return {
        "min_mm": lo,
        "max_mm": hi,
        "size_mm": [_round(b - a) for a, b in zip(lo, hi, strict=True)],
    }


def union_bbox(boxes: Iterable[dict[str, Any] | None]) -> dict[str, list[float]] | None:
    """Smallest box holding every box dict (``None`` entries skipped); ``None`` if empty."""
    present = [b for b in boxes if b]
    if not present:
        return None
    lo = [min(b["min_mm"][i] for b in present) for i in range(3)]
    hi = [max(b["max_mm"][i] for b in present) for i in range(3)]
    return box_dict(lo, hi)

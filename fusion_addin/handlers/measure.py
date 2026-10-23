"""``measure.*``: minimum distance and angle between entities or points (c03 D10)."""

from __future__ import annotations

import math
from typing import Any

import adsk.core

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

from ..core import geometry
from ..core.registry import handler


def _target(ctx: Any, value: Any, name: str = "target") -> Any:
    """A ref (string) -> the entity; ``[x, y, z]`` in the request units (mm by default) ->
    ``Point3D`` in cm. Anything else is ``INVALID_PARAMS``."""
    if isinstance(value, str) and value:
        return ctx.refs.resolve(value)
    if (
        isinstance(value, list)
        and len(value) == 3
        and all(
            isinstance(c, (int, float)) and not isinstance(c, bool) and math.isfinite(c)
            for c in value
        )
    ):
        x, y, z = (ctx.units.length(c) for c in value)
        return adsk.core.Point3D.create(x, y, z)
    raise BridgeError(
        ErrorCode.INVALID_PARAMS,
        f"parameter '{name}' must be a ref or a point [x, y, z] in mm",
        {"param": name},
    )


def _point_or_none(p3d: Any, units: Any) -> list[float] | None:
    return None if p3d is None else geometry.point(p3d, units)


@handler("measure.distance")
def measure_distance(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    a = _target(ctx, params.get("a"), "a")
    b = _target(ctx, params.get("b"), "b")
    result = ctx.app.measureManager.measureMinimumDistance(a, b)
    return {
        "distance_mm": geometry.length(result.value, ctx.units),
        "point_a_mm": _point_or_none(result.positionOne, ctx.units),
        "point_b_mm": _point_or_none(result.positionTwo, ctx.units),
    }


@handler("measure.angle")
def measure_angle(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Angle between two entities, or at vertex ``b`` of three points/entities."""
    a = _target(ctx, params.get("a"), "a")
    b = _target(ctx, params.get("b"), "b")
    manager = ctx.app.measureManager
    if params.get("c") is None:
        result = manager.measureAngle(a, b)
    else:
        result = manager.measureAngle(a, b, _target(ctx, params["c"], "c"))
    return {"angle_deg": round(ctx.units.angle_out(result.value), 9)}

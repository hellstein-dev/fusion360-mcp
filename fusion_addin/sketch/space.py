"""Sketch-space points and the sketch transform (c05 D2).

Every 2D coordinate of the sketch tools is sketch space in the tool's units (mm by default);
Fusion works in cm. Conversions go through ``fusion_mcp.protocol.units`` (INV-6):

- ``to_point3d`` turns ``[x, y]`` (sketch space) or ``{"model": [x, y, z]}`` (model space,
  mapped with ``Sketch.modelToSketchSpace``) into a sketch-space ``Point3D`` in cm, z = 0. A
  model point off the sketch plane (|z| > 1e-6 cm) is projected onto it and the fact is
  appended to ``warnings``.
- ``to_xy_mm`` is the way out: ``Point3D`` (sketch space, cm) -> ``[x, y]`` mm.
- ``describe_transform`` reports ``Sketch.transform`` (sketch -> model): origin in model mm
  and the unit ``x_axis``, ``y_axis``, ``normal`` in model space. A sketch on ``origin:XZ``
  has its Y axis along model -Z, so the agent must look at it.
"""

from __future__ import annotations

from typing import Any

import adsk.core

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.units import DEFAULT_LENGTH_UNIT, Units

from ..core import geometry
from .specs import ModelXyz, PointSpec, Xy, parse_point_spec

# Distance (cm) of a model point from the sketch plane above which it counts as projected.
PLANE_TOL_CM = 1e-6
PROJECTED_WARNING = "point_projected_to_sketch_plane"


def as_units(units: Units | str | None) -> Units:
    """``Units`` as is; a unit name (``"mm"``...) or ``None`` (mm) -> ``Units``."""
    if isinstance(units, Units):
        return units
    name = DEFAULT_LENGTH_UNIT if units is None else units
    try:
        return Units(name)
    except ValueError as exc:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS, str(exc), {"param": "units", "value": units}
        ) from None


def _cm(value: float, units: Units) -> float:
    return float(units.length(value))


def to_point3d(
    spec: PointSpec | object,
    units: Units | str | None,
    sketch: Any,
    warnings: list[dict[str, Any]] | None = None,
    field: str = "point",
) -> Any:
    """Coordinate spec -> sketch-space ``Point3D`` (cm, z = 0).

    ``spec`` is a parsed ``Xy``/``ModelXyz`` or a raw value (``[x, y]`` or
    ``{"model": [x, y, z]}``). Entity specs (strings) are resolved by ``BatchScope.point``,
    here they are ``INVALID_PARAMS``.
    """
    parsed = spec if isinstance(spec, Xy | ModelXyz) else parse_point_spec(spec, field)
    u = as_units(units)
    if isinstance(parsed, Xy):
        return adsk.core.Point3D.create(_cm(parsed.x, u), _cm(parsed.y, u), 0.0)
    if isinstance(parsed, ModelXyz):
        model = adsk.core.Point3D.create(_cm(parsed.x, u), _cm(parsed.y, u), _cm(parsed.z, u))
        local = sketch.modelToSketchSpace(model)
        if abs(local.z) > PLANE_TOL_CM and warnings is not None:
            warnings.append(
                {
                    "code": PROJECTED_WARNING,
                    "field": field,
                    "message": (
                        f"{field}: the model point is {geometry.length(abs(local.z))} mm off "
                        "the sketch plane and was projected onto it"
                    ),
                    "distance_mm": geometry.length(abs(local.z)),
                }
            )
        return adsk.core.Point3D.create(local.x, local.y, 0.0)
    raise BridgeError(
        ErrorCode.INVALID_PARAMS,
        f'{field} must be coordinates here ([x, y] or {{"model": [x, y, z]}}), not an entity',
        {"field": field, "value": spec if isinstance(spec, str) else repr(spec)},
    )


def to_xy_mm(point: Any) -> list[float]:
    """Sketch-space ``Point3D`` (cm) -> ``[x, y]`` mm (z is dropped)."""
    return [geometry.length(point.x), geometry.length(point.y)]


def describe_transform(sketch: Any) -> dict[str, list[float]]:
    """``Sketch.transform`` -> ``{"origin", "x_axis", "y_axis", "normal"}`` (model mm and
    unit vectors in model space)."""
    origin, x_axis, y_axis, normal = sketch.transform.getAsCoordinateSystem()
    return {
        "origin": geometry.point(origin),
        "x_axis": geometry.vector(x_axis),
        "y_axis": geometry.vector(y_axis),
        "normal": geometry.vector(normal),
    }

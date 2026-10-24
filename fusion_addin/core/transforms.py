"""Transforms of ``body.move`` (c08 D8).

- ``move_plan(ctx, spec, units)`` validates a transform spec, resolves its refs and builds
  every Fusion value (``ValueInput``, ``Matrix3D``) without touching the design: the
  ``MoveFeatureInput`` method (``defineAsTranslateXYZ`` / ``defineAsTranslateAlongEntity`` /
  ``defineAsRotate`` / ``defineAsFreeMove`` / ``defineAsPointToPoint``) and its arguments.
- ``apply_move(ctx, move_input, spec, units)`` calls that method on a ``createInput2`` input;
  ``False`` when this Fusion version's input lacks it (the caller falls back).
- ``matrix_for(ctx, spec, units)`` - the fallback for an API without ``createInput2`` /
  ``defineAs*``: one ``Matrix3D`` (cm) for ``MoveFeatures.createInput(entities, matrix)``.
  Only transforms that can be computed here (``translate`` with numbers in model space,
  ``rotate_about``, ``align`` of two points, ``matrix``); anything else, Fusion expressions
  included, is ``FUSION_API_ERROR`` "not available".

Lengths are numbers in ``units`` (default ``ctx.units``) or expressions, angles degrees;
conversion goes through ``fusion_mcp.protocol.units`` / ``core.features`` only (INV-6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import adsk.core

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import MOVE_TYPES, check_choice
from fusion_mcp.protocol.units import Units, angle_to_internal, length_to_internal

from .entity_kinds import describe_kind, is_linear, is_point
from .features import angle_value, length_value, point3d
from .patterns import resolve_axis
from .refs import type_name

ORTHONORMAL_TOLERANCE = 1e-6

_FIELDS: dict[str, frozenset[str]] = {
    "translate": frozenset({"x", "y", "z", "design_space"}),
    "translate_along": frozenset({"entity", "distance"}),
    "rotate": frozenset({"axis", "angle"}),
    "rotate_about": frozenset({"point", "direction", "angle"}),
    "align": frozenset({"from", "to"}),
    "matrix": frozenset({"matrix"}),
}


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _not_available(what: str) -> BridgeError:
    return BridgeError(
        ErrorCode.FUSION_API_ERROR,
        f"{what} not available in this Fusion API version",
        {"feature_type": "MoveFeature"},
    )


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _unit(ctx: Any, units: Any) -> str:
    if units is None:
        units = getattr(ctx, "units", None)
    if isinstance(units, Units):
        return units.units
    return units if isinstance(units, str) else Units().units


@dataclass
class MovePlan:
    """``method`` of ``MoveFeatureInput`` and its ``args``; ``spec`` is the checked spec."""

    type: str
    method: str
    args: tuple[Any, ...]
    spec: dict[str, Any]


def _spec(spec: Any, param: str) -> tuple[dict[str, Any], str]:
    if not isinstance(spec, dict):
        raise _invalid(
            f"parameter '{param}' must be an object with 'type' ({', '.join(MOVE_TYPES)})",
            param=param,
        )
    kind = check_choice(f"{param}.type", spec.get("type"), MOVE_TYPES)
    unknown = sorted(set(spec) - _FIELDS[kind] - {"type"})
    if unknown:
        raise _invalid(
            f"parameter '{param}' of type '{kind}' has unknown fields {unknown}",
            param=param,
            type=kind,
        )
    return spec, kind


def _field(spec: dict[str, Any], name: str, param: str) -> Any:
    if spec.get(name) is None:
        raise _invalid(
            f"parameter '{param}' of type '{spec['type']}' needs '{name}'",
            param=f"{param}.{name}",
        )
    return spec[name]


def _flag(spec: dict[str, Any], name: str, default: bool, param: str) -> bool:
    value = spec.get(name, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise _invalid(f"parameter '{param}.{name}' must be a boolean", param=f"{param}.{name}")
    return value


def _triple(spec: dict[str, Any], name: str, param: str) -> list[float]:
    value = _field(spec, name, param)
    if not isinstance(value, list | tuple) or len(value) != 3 or not all(map(_number, value)):
        raise _invalid(
            f"parameter '{param}.{name}' must be [x, y, z] numbers",
            param=f"{param}.{name}",
            value=repr(value),
        )
    return [float(v) for v in value]


def _resolve(ctx: Any, ref: Any, param: str) -> Any:
    try:
        return ctx.refs.resolve(ref)
    except BridgeError as exc:
        exc.details.setdefault("param", param)
        raise


def _point_entity(ctx: Any, ref: Any, param: str) -> Any:
    entity = _resolve(ctx, ref, param)
    if not is_point(entity):
        got = describe_kind(entity)
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"parameter '{param}' must be a vertex, a sketch point, a construction point or "
            f"origin:O, got {got}",
            {"param": param, "expected": "point", "got": got},
        )
    return entity


def _rotation(ctx: Any, spec: dict[str, Any], units: Any, param: str) -> Any:
    point = _triple(spec, "point", param)
    direction = _triple(spec, "direction", param)
    angle = _field(spec, "angle", param)
    if not _number(angle):
        raise _invalid(
            f"parameter '{param}.angle' must be a number of degrees (expressions are not "
            "allowed for rotate_about)",
            param=f"{param}.angle",
            value=repr(angle),
        )
    if math.hypot(*direction) == 0:
        raise _invalid(
            f"parameter '{param}.direction' must not be the zero vector",
            param=f"{param}.direction",
        )
    matrix = adsk.core.Matrix3D.create()
    radians = float(angle_to_internal(float(angle)))
    origin = point3d(ctx, point, units, param=f"{param}.point")
    if not matrix.setToRotation(radians, adsk.core.Vector3D.create(*direction), origin):
        raise _invalid(f"parameter '{param}' is not a valid rotation", param=param)
    return matrix


def check_rigid(values: list[float], param: str = "matrix") -> None:
    """Row-major 4x4 must be a rigid transform: the 3x3 block orthonormal with determinant
    +1 (tolerance ``ORTHONORMAL_TOLERANCE``) and the last row ``[0, 0, 0, 1]``."""
    rows = [values[i * 4 : i * 4 + 3] for i in range(3)]
    for i in range(3):
        for j in range(3):
            dot = sum(rows[k][i] * rows[k][j] for k in range(3))
            if abs(dot - (1.0 if i == j else 0.0)) > ORTHONORMAL_TOLERANCE:
                raise _invalid(
                    f"parameter '{param}' must be a rigid transform: its 3x3 block is not "
                    "orthonormal (use body_scale to scale)",
                    param=param,
                    reason="not_orthonormal",
                )
    a, b, c = rows
    det = (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )
    if abs(det - 1.0) > ORTHONORMAL_TOLERANCE:
        raise _invalid(
            f"parameter '{param}' must be a rigid transform: it mirrors (determinant -1)",
            param=param,
            reason="mirror",
        )
    last = values[12:16]
    if any(abs(v - e) > ORTHONORMAL_TOLERANCE for v, e in zip(last, (0, 0, 0, 1), strict=True)):
        raise _invalid(
            f"parameter '{param}' must end with the row [0, 0, 0, 1]",
            param=param,
            reason="projective",
        )


def _matrix(ctx: Any, spec: dict[str, Any], units: Any, param: str) -> Any:
    raw = _field(spec, "matrix", param)
    if not isinstance(raw, list | tuple) or len(raw) != 16 or not all(map(_number, raw)):
        raise _invalid(
            f"parameter '{param}.matrix' must be 16 numbers (row-major 4x4)",
            param=f"{param}.matrix",
        )
    values = [float(v) for v in raw]
    check_rigid(values, f"{param}.matrix")
    unit = _unit(ctx, units)
    for index in (3, 7, 11):
        values[index] = float(length_to_internal(values[index], unit))
    matrix = adsk.core.Matrix3D.create()
    if not matrix.setWithArray(values):
        raise _invalid(f"parameter '{param}.matrix' was refused by Fusion", param=param)
    return matrix


def move_plan(ctx: Any, spec: Any, units: Any = None, param: str = "transform") -> MovePlan:
    """Checked transform -> ``MovePlan``; nothing in the design changes."""
    spec, kind = _spec(spec, param)
    if kind == "translate":
        values = [
            length_value(
                ctx, spec.get(axis) if spec.get(axis) is not None else 0, units, f"{param}.{axis}"
            )
            for axis in ("x", "y", "z")
        ]
        design_space = _flag(spec, "design_space", True, param)
        return MovePlan(kind, "defineAsTranslateXYZ", (*values, design_space), spec)
    if kind == "translate_along":
        entity = _resolve(ctx, _field(spec, "entity", param), f"{param}.entity")
        if not is_linear(entity):
            got = describe_kind(entity)
            raise BridgeError(
                ErrorCode.INVALID_PARAMS,
                f"parameter '{param}.entity' must be a straight edge, a sketch line or a "
                f"construction axis, got {got}",
                {"param": f"{param}.entity", "expected": "linear", "got": got},
            )
        distance = length_value(ctx, _field(spec, "distance", param), units, f"{param}.distance")
        return MovePlan(kind, "defineAsTranslateAlongEntity", (entity, distance), spec)
    if kind == "rotate":
        axis = resolve_axis(ctx, _field(spec, "axis", param), None, f"{param}.axis")
        angle = angle_value(ctx, _field(spec, "angle", param), f"{param}.angle")
        return MovePlan(kind, "defineAsRotate", (axis, angle), spec)
    if kind == "rotate_about":
        return MovePlan(kind, "defineAsFreeMove", (_rotation(ctx, spec, units, param),), spec)
    if kind == "align":
        source = _point_entity(ctx, _field(spec, "from", param), f"{param}.from")
        target = _point_entity(ctx, _field(spec, "to", param), f"{param}.to")
        return MovePlan(kind, "defineAsPointToPoint", (source, target), spec)
    return MovePlan(kind, "defineAsFreeMove", (_matrix(ctx, spec, units, param),), spec)


def apply_move(ctx: Any, move_input: Any, spec: Any, units: Any = None) -> bool:
    """``move_input.<defineAs*>(...)`` for ``spec`` (a spec dict or a ``MovePlan``).
    ``False`` (nothing called) when the input lacks the method; a ``False`` answer from
    Fusion is ``FUSION_API_ERROR``."""
    plan = spec if isinstance(spec, MovePlan) else move_plan(ctx, spec, units)
    if not hasattr(move_input, plan.method):
        return False
    if getattr(move_input, plan.method)(*plan.args) is False:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"Fusion rejected the MoveFeature input ({plan.method})",
            {"feature_type": "MoveFeature", "setter": plan.method},
        )
    return True


def _point_cm(entity: Any) -> Any:
    """Model-space ``Point3D`` (cm) of a vertex / sketch point / construction point."""
    if type_name(entity) == "SketchPoint":
        geometry = getattr(entity, "worldGeometry", None)
    else:
        geometry = getattr(entity, "geometry", None)
    if geometry is None or not all(hasattr(geometry, c) for c in "xyz"):
        raise _not_available(f"The position of a {type_name(entity)} is")
    return geometry


def matrix_for(ctx: Any, spec: Any, units: Any = None, param: str = "transform") -> Any:
    """Fallback ``Matrix3D`` (cm) of ``spec`` for ``MoveFeatures.createInput``."""
    plan = spec if isinstance(spec, MovePlan) else move_plan(ctx, spec, units, param)
    spec = plan.spec
    if plan.type in ("rotate_about", "matrix"):
        return plan.args[0]
    if plan.type == "translate":
        raw = [spec.get(axis) if spec.get(axis) is not None else 0 for axis in ("x", "y", "z")]
        if not all(map(_number, raw)):
            raise _not_available("Move by Fusion expressions (translate without createInput2) is")
        if not _flag(spec, "design_space", True, param):
            raise _not_available("Move in component space (translate without createInput2) is")
        unit = _unit(ctx, units)
        cm = [float(length_to_internal(float(v), unit)) for v in raw]
        matrix = adsk.core.Matrix3D.create()
        matrix.translation = adsk.core.Vector3D.create(*cm)
        return matrix
    if plan.type == "align":
        source, target = (_point_cm(e) for e in plan.args)
        matrix = adsk.core.Matrix3D.create()
        matrix.translation = adsk.core.Vector3D.create(
            target.x - source.x, target.y - source.y, target.z - source.z
        )
        return matrix
    raise _not_available(f"Move type '{plan.type}' without MoveFeatures.createInput2 is")

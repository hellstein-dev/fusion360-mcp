"""``construction.*``: construction planes, axes and points (c06 D3, D4).

Every handler runs ``_create(ctx, kind, params)``:

1. ``protocol.construction.validate`` (fields of the method, D3); common params ``component``
   (``component:``/``occurrence:`` ref, default ``design.activeComponent``) and ``name``;
2. every ref resolved (``origin:*`` in the target component) and checked with
   ``entity_kinds.require_kind`` before anything changes;
3. ``createInput`` (with the occurrence if ``component`` is an ``occurrence:`` ref), the
   method's ``setBy…`` (``False`` -> ``FUSION_API_ERROR``) and ``add``;
4. ``name``, then ``check_health`` (``error`` -> ``FUSION_API_ERROR``; the dispatcher rolls the
   timeline back, D5);
5. the answer: ``describe(entity, units, "full")`` plus ``health`` (and ``warning``).

Lengths and angles become ``ValueInput`` through ``ctx.units`` (numbers -> cm/radians,
expressions as is, INV-6); ``proportion`` is a plain real.
"""

from __future__ import annotations

import contextlib
from typing import Any

import adsk.core

from fusion_mcp.protocol.construction import ANGLE, LENGTH, PROPORTION, REFS, method_spec, validate
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.refs import is_origin_ref

from ..core.describe import describe
from ..core.entity_kinds import require_kind
from ..core.health import check_health
from ..core.refs import type_name
from ..core.registry import handler

COLLECTIONS = {
    "plane": "constructionPlanes",
    "axis": "constructionAxes",
    "point": "constructionPoints",
}

# Fusion setter of each method; its arguments are the method's fields in table order
# (``points`` expands to its refs).
SETTERS: dict[str, dict[str, str]] = {
    "plane": {
        "offset": "setByOffset",
        "angle": "setByAngle",
        "midplane": "setByTwoPlanes",
        "two_edges": "setByTwoEdges",
        "three_points": "setByThreePoints",
        "tangent": "setByTangent",
        "tangent_at_point": "setByTangentAtPoint",
        "along_path": "setByDistanceOnPath",
    },
    "axis": {},
    "point": {},
}


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _target(ctx: Any, ref: Any) -> tuple[Any, Any]:
    """``component`` param -> ``(component, occurrence or None)``."""
    if ref is None:
        return ctx.design.activeComponent, None
    entity = ctx.refs.resolve(ref)
    kind = type_name(entity)
    if kind == "Occurrence":
        return entity.component, entity
    if kind == "Component":
        return entity, None
    raise _invalid(
        f"parameter 'component' is a {kind}, not a component or occurrence",
        param="component",
        expected="component",
        got=kind,
    )


def _resolve(ctx: Any, ref: Any, component: Any, kind: str, param: str) -> Any:
    raw = ref.get("ref") if isinstance(ref, dict) else ref
    entity = ctx.refs.resolve(ref, component) if is_origin_ref(raw) else ctx.refs.resolve(ref)
    return require_kind(entity, kind, param)


def _value(converted: float | str) -> Any:
    if isinstance(converted, str):
        return adsk.core.ValueInput.createByString(converted)
    return adsk.core.ValueInput.createByReal(converted)


def _arguments(ctx: Any, kind: str, method: str, fields: dict[str, Any], component: Any) -> list:
    """Setter arguments in table order: refs resolved and kind-checked, values as
    ``ValueInput``. Runs before any change to the design."""
    args: list[Any] = []
    for field in method_spec(kind, method).fields:
        value = fields[field.name]
        if field.field_type == LENGTH:
            args.append(_value(ctx.units.length(value)))
        elif field.field_type == ANGLE:
            args.append(_value(ctx.units.angle(value)))
        elif field.field_type == PROPORTION:
            args.append(adsk.core.ValueInput.createByReal(float(value)))
        elif field.field_type == REFS:
            args.extend(
                _resolve(ctx, ref, component, str(field.entity_kind), f"{field.name}[{i}]")
                for i, ref in enumerate(value)
            )
        else:
            args.append(_resolve(ctx, value, component, str(field.entity_kind), field.name))
    return args


def _create(ctx: Any, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    method = params.get("method")
    fields = validate(kind, method, params)
    name = ctx.require(params, "name", str, None)
    if name is not None and not name.strip():
        raise _invalid("parameter 'name' must not be empty", param="name")
    component, occurrence = _target(ctx, ctx.require(params, "component", (str, dict), None))
    setter = SETTERS[kind].get(str(method))
    if setter is None:
        raise BridgeError(
            ErrorCode.INTERNAL, f"construction {kind} method {method!r} has no Fusion setter"
        )
    args = _arguments(ctx, kind, str(method), fields, component)

    collection = getattr(component, COLLECTIONS[kind])
    inp = collection.createInput(occurrence) if occurrence is not None else collection.createInput()
    if getattr(inp, setter)(*args) is False:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"Fusion rejected inputs for {method}",
            {"kind": kind, "method": method},
        )
    entity = collection.add(inp)
    if entity is None:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"Fusion did not create the construction {kind}",
            {"kind": kind, "method": method},
        )
    try:
        if name is not None:
            entity.name = name
        health = check_health(entity)
        result = describe(entity, ctx.units, "full")
    except Exception:
        # A direct design has no timeline rollback (c02 D5): take the new element back here.
        with contextlib.suppress(Exception):
            entity.deleteMe()
        raise
    result["health"] = health["state"]
    if health["state"] == "warning":
        result["warning"] = health["message"]
    return result


@handler("construction.plane", mutating=True)
def construction_plane(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _create(ctx, "plane", params)

"""``timeline.*``: inspect and edit the timeline of a parametric design (c09).

Items are addressed by the entity ref of their feature/sketch or by ``timeline:<name>``
(groups, D1); indices are informational only (INV-7). Property changes (suppression,
expressions, names, group collapse, the marker) add no timeline items, so each is
registered in a ``Compensation`` before it is made (D4, INV-5); new items (groups) are
removed by the same compensation. Model parameter values go out in mm/degrees through
``fusion_mcp.protocol.units`` (INV-6).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.units import classify_unit, number_expression, parameter_value_to_user

from ..core import health, refs
from ..core.compensate import Compensation
from ..core.registry import handler

MAX_LIMIT = 1000
DEFAULT_LIMIT = 200
MAX_ITEMS = 100
POSITIONS = ("start", "end", "before", "after")


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _api_error(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.FUSION_API_ERROR, message, details)


# --- shared helpers (D1, D2) ---------------------------------------------------------------


def _require_parametric(ctx: Any) -> Any:
    """The active design; a direct design has no timeline -> ``INVALID_PARAMS``."""
    design = ctx.design
    if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
        raise _invalid("timeline requires a parametric design (the active design is direct)")
    return design


def _safe(obj: Any, attr: str, default: Any = None) -> Any:
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def _is_group(to: Any) -> bool:
    return bool(_safe(to, "isGroup", False))


def _entity(to: Any) -> Any:
    if _is_group(to):
        return None
    return _safe(to, "entity")


def _ref_of(to: Any) -> str:
    entity = _entity(to)
    if entity is not None:
        try:
            return refs.to_ref(entity)
        except Exception:
            pass
    return refs.TIMELINE_PREFIX + to.name


def _timeline_object(ctx: Any, ref: Any, param: str = "item") -> Any:
    """``TimelineObject`` for an entity ref or ``timeline:<name>`` (D1)."""
    if not isinstance(ref, str) or not ref:
        raise _invalid(f"parameter '{param}' must be a non-empty ref string", param=param)
    if ref.startswith(refs.TIMELINE_PREFIX):
        return ctx.refs.resolve(ref)
    entity = ctx.refs.resolve(ref)
    to = _safe(entity, "timelineObject")
    if to is None:
        raise _invalid(
            f"entity has no timeline item: {refs.type_name(entity)} {ref!r}", param=param, ref=ref
        )
    return to


def _item(ctx: Any, to: Any) -> dict[str, Any]:
    """Outward form of a timeline item (D2)."""
    entity = _entity(to)
    is_group = _is_group(to)
    if is_group:
        kind = "TimelineGroup"
    elif entity is not None:
        kind = refs.type_name(entity)
    else:
        kind = refs.type_name(to)
    group = _safe(to, "parentGroup")
    component = _safe(entity, "parentComponent") if entity is not None else None
    message = _safe(to, "errorOrWarningMessage", "")
    return {
        "index": to.index,
        "name": to.name,
        "type": kind,
        "ref": _ref_of(to),
        "is_group": is_group,
        "group": refs.TIMELINE_PREFIX + group.name if group is not None else None,
        "is_suppressed": bool(_safe(to, "isSuppressed", False)),
        "is_rolled_back": bool(_safe(to, "isRolledBack", False)),
        "health": health.health_of(to),
        "message": message if isinstance(message, str) and message else None,
        "component": refs.COMPONENT_PREFIX + component.name if component is not None else None,
    }


def _snapshot_health(timeline: Any) -> dict[str, tuple[Any, str]]:
    """``ref -> (TimelineObject, health)`` of every item, groups' members included."""
    return {_ref_of(to): (to, health.health_of(to)) for to in refs.walk_timeline(timeline)}


def _new_errors(
    before: dict[str, tuple[Any, str]], timeline: Any, skip: str | None = None
) -> list[Any]:
    """Items in ``error`` now that were not in ``error`` in ``before``."""
    failed = []
    for ref, (to, state) in _snapshot_health(timeline).items():
        if ref == skip or state != "error":
            continue
        if before.get(ref, (None, ""))[1] != "error":
            failed.append(to)
    return failed


def _position(timeline: Any) -> dict[str, int]:
    return {"marker_position": timeline.markerPosition, "count": timeline.count}


def _fail_on_errors(ctx: Any, failed: list[Any], first: Any = None) -> None:
    """Raise ``FUSION_API_ERROR`` naming the failing feature (D5); the caller's
    ``Compensation`` then reverts the change."""
    if not failed:
        return
    culprit = next((to for to in failed if first is not None and _ref_of(to) == first), failed[0])
    message = _safe(culprit, "errorOrWarningMessage", "") or "the feature is in the error state"
    raise _api_error(
        f"{culprit.name}: {message}",
        reverted=True,
        health=[_item(ctx, to) for to in failed],
    )


# --- 3. reading ------------------------------------------------------------------------------


@handler("timeline.list")
def timeline_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    only_problems = ctx.require(params, "only_problems", bool, False)
    offset = ctx.require(params, "offset", int, 0)
    limit = ctx.require(params, "limit", int, DEFAULT_LIMIT)
    if offset < 0:
        raise _invalid("parameter 'offset' must be >= 0", param="offset")
    if not 1 <= limit <= MAX_LIMIT:
        raise _invalid(f"parameter 'limit' must be 1..{MAX_LIMIT}", param="limit")
    timeline = _require_parametric(ctx).timeline
    items = [_item(ctx, to) for to in refs.walk_timeline(timeline)]
    if only_problems:
        items = [i for i in items if i["health"] in ("warning", "error")]
    page = items[offset : offset + limit]
    return {
        **_position(timeline),
        "items": page,
        "total": len(items),
        "truncated": offset + len(page) < len(items),
    }


def _health_report(ctx: Any, timeline: Any) -> dict[str, Any]:
    items = [_item(ctx, to) for to in refs.walk_timeline(timeline)]
    errors = [i for i in items if i["health"] == "error"]
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": [i for i in items if i["health"] == "warning"],
        "suppressed_count": sum(1 for i in items if i["is_suppressed"]),
        "rolled_back_count": sum(1 for i in items if i["is_rolled_back"]),
        **_position(timeline),
    }


@handler("timeline.health")
def timeline_health(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _health_report(ctx, _require_parametric(ctx).timeline)


@handler("timeline.compute_all", mutating=True)
def timeline_compute_all(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    design = _require_parametric(ctx)
    design.computeAll()
    return _health_report(ctx, design.timeline)


# --- 4. marker, suppression, deletion, names --------------------------------------------------


def _rolled_back_count(timeline: Any) -> int:
    return sum(1 for to in refs.walk_timeline(timeline) if _safe(to, "isRolledBack", False))


@handler("timeline.roll_to", mutating=True)
def timeline_roll_to(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    position = ctx.require(params, "position", str)
    if position not in POSITIONS:
        raise _invalid(f"parameter 'position' must be one of {', '.join(POSITIONS)}")
    ref = ctx.require(params, "item", str, None)
    if position in ("before", "after") and ref is None:
        raise _invalid(f"position {position!r} needs 'item'", param="item")
    timeline = _require_parametric(ctx).timeline
    target = _timeline_object(ctx, ref) if position in ("before", "after") else None
    with Compensation() as comp:
        m0 = timeline.markerPosition
        comp.add(lambda: setattr(timeline, "markerPosition", m0), "marker")
        if position == "start":
            timeline.moveToBeginning()
        elif position == "end":
            timeline.moveToEnd()
        elif target is not None:
            group = _safe(target, "parentGroup")
            # rollTo into a collapsed group: expand it first (not restored, cosmetic).
            if group is not None and _safe(group, "isCollapsed", False):
                group.isCollapsed = False
            if target.rollTo(position == "before") is False:
                raise _api_error(f"Fusion could not roll the timeline to {target.name!r}")
    return {**_position(timeline), "rolled_back_count": _rolled_back_count(timeline)}


def _restore_suppressed(to: Any, value: bool) -> Callable[[], None]:
    def undo() -> None:
        if bool(to.isSuppressed) != value:
            to.isSuppressed = value

    return undo


@handler("timeline.set_suppressed", mutating=True)
def timeline_set_suppressed(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    refs_in = ctx.require(params, "items", list)
    if not 1 <= len(refs_in) <= MAX_ITEMS:
        raise _invalid(f"'items' must have 1..{MAX_ITEMS} refs, got {len(refs_in)}", param="items")
    suppressed = ctx.require(params, "suppressed", bool, True)
    allow_errors = ctx.require(params, "allow_errors", bool, False)
    timeline = _require_parametric(ctx).timeline
    targets = [_timeline_object(ctx, r, f"items[{i}]") for i, r in enumerate(refs_in)]
    for i, to in enumerate(targets):
        if not _safe(to, "canSuppress", True):
            raise _invalid(f"items[{i}]: {to.name!r} cannot be suppressed", param=f"items[{i}]")
    before = _snapshot_health(timeline)
    with Compensation() as comp:
        for to in targets:
            comp.add(_restore_suppressed(to, bool(to.isSuppressed)), f"suppress {to.name}")
            to.isSuppressed = suppressed
        if not allow_errors:
            target_refs = [_ref_of(to) for to in targets]
            failed = _new_errors(before, timeline)
            _fail_on_errors(ctx, failed, target_refs[0] if target_refs else None)
    return {"items": [_item(ctx, to) for to in targets], **_position(timeline)}


@handler("timeline.delete", mutating=True)
def timeline_delete(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    ref = ctx.require(params, "item", str)
    force = ctx.require(params, "force", bool, False)
    timeline = _require_parametric(ctx).timeline
    to = _timeline_object(ctx, ref)
    own_ref = _ref_of(to)
    deleted = {"name": to.name, "type": _item(ctx, to)["type"]}
    before = _snapshot_health(timeline)
    checked = False
    if not force and _safe(to, "canSuppress", True) and not _safe(to, "isSuppressed", False):
        # D8: probe by suppressing; dependents that fall into error block the deletion.
        to.isSuppressed = True
        try:
            broken = _new_errors(before, timeline, skip=own_ref)
            dependents = [_item(ctx, d) for d in broken]
        finally:
            to.isSuppressed = False
        if dependents:
            raise _invalid(
                f"deleting {to.name!r} would break dependents: "
                + ", ".join(d["name"] for d in dependents)
                + "; delete or edit them first, or pass force=true",
                dependents=dependents,
            )
        checked = True
    elif not force and _safe(to, "isSuppressed", False):
        checked = True  # already suppressed: dependents are what they are now
    if _is_group(to):
        result = to.deleteMe(True)
    else:
        entity = _entity(to)
        if entity is None:
            raise _invalid(f"timeline item {to.name!r} has no entity to delete")
        result = entity.deleteMe()
    if result is False:
        raise _api_error(f"Fusion did not delete {to.name!r}", item=own_ref)
    problems = [_item(ctx, d) for d in _new_errors(before, timeline, skip=own_ref)]
    return {
        "deleted": deleted,
        "dependents_checked": checked,
        "problems_after": problems,
        **_position(timeline),
    }


@handler("timeline.rename", mutating=True)
def timeline_rename(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    ref = ctx.require(params, "item", str)
    name = ctx.require(params, "name", str)
    if not name.strip():
        raise _invalid("parameter 'name' must not be empty", param="name")
    timeline = _require_parametric(ctx).timeline
    if not ref.startswith(refs.TIMELINE_PREFIX):
        entity = ctx.refs.resolve(ref)
        if refs.type_name(entity) == "BRepBody":
            raise _invalid(
                "bodies are not timeline items: rename a body with the body_* tools (c08)",
                ref=ref,
            )
    to = _timeline_object(ctx, ref)
    target = to if _is_group(to) else _entity(to)
    if target is None:
        raise _invalid(f"timeline item {to.name!r} has no renamable entity", ref=ref)
    with Compensation() as comp:
        old = target.name
        comp.add(lambda: setattr(target, "name", old), f"rename {old}")
        target.name = name
    return {"item": _item(ctx, to), **_position(timeline)}


# --- 5. model parameters -----------------------------------------------------------------------


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    if a is b:
        return True
    token = refs._token(a)
    return token is not None and token == refs._token(b)


def _feature_entity(ctx: Any, ref: Any, param: str = "feature") -> Any:
    """The feature entity of a ref (``timeline:<name>`` -> its entity)."""
    if not isinstance(ref, str) or not ref:
        raise _invalid(f"parameter '{param}' must be a non-empty ref string", param=param)
    if ref.startswith(refs.TIMELINE_PREFIX):
        entity = _entity(ctx.refs.resolve(ref))
        if entity is None:
            raise _invalid(f"timeline item {ref!r} is not a feature", param=param)
        return entity
    return ctx.refs.resolve(ref)


def _model_parameters(design: Any, feature: Any) -> list[Any]:
    found = []
    params = design.allParameters
    for i in range(params.count):
        p = params.item(i)
        if refs.type_name(p) == "ModelParameter" and _same(_safe(p, "createdBy"), feature):
            found.append(p)
    return found


def _value(param: Any) -> dict[str, Any]:
    try:
        return dict(parameter_value_to_user(param.value, param.unit))
    except Exception:
        return {"value": None, "value_unit": None}


def _expression(value: Any, param: Any, units: str, where: str) -> str:
    """D6: a string as is; a number in mm/``units`` (length), degrees (angle) or unitless."""
    if isinstance(value, str):
        if not value.strip():
            raise _invalid(f"{where}: expression must not be empty", param=where)
        return value
    if not _number(value):
        raise _invalid(
            f"{where}: value must be a finite number or a Fusion expression", param=where
        )
    kind = classify_unit(param.unit)
    if kind == "length":
        return number_expression(value, units)
    if kind == "angle":
        return number_expression(value, "deg")
    if kind == "unitless":
        return number_expression(value, "")
    raise _invalid(
        f"{where}: parameter {param.name!r} has unit {param.unit!r}; pass an expression string "
        "with explicit units instead of a number",
        param=where,
        unit=param.unit,
    )


def _restore_expression(param: Any, old: str) -> Callable[[], None]:
    def undo() -> None:
        if param.expression != old:
            param.expression = old

    return undo


def _apply_expressions(
    ctx: Any,
    feature: Any,
    changes: list[tuple[str, Any, Any]],
    units: str,
    allow_errors: bool,
) -> dict[str, Any]:
    """Set ``(key, ModelParameter, value)`` changes atomically with a health post-check (D5).

    Returns ``_edit_result``: ``{"feature": _item | None, "changed": [...]}``.
    """
    timeline = ctx.design.timeline
    planned = [(key, p, p.expression, _expression(v, p, units, key)) for key, p, v in changes]
    to = _safe(feature, "timelineObject") if feature is not None else None
    before = _snapshot_health(timeline)
    with Compensation() as comp:
        for key, param, old, new in planned:
            comp.add(_restore_expression(param, old), f"{key} {param.name}")
            param.expression = new
        if not allow_errors:
            _fail_on_errors(ctx, _new_errors(before, timeline), _ref_of(to) if to else None)
    changed = [
        {
            "key": key,
            "parameter": param.name,
            "old_expression": old,
            "new_expression": param.expression,
            **_value(param),
        }
        for key, param, old, _ in planned
    ]
    return {"feature": _item(ctx, to) if to is not None else None, "changed": changed}


@handler("timeline.feature_parameters")
def timeline_feature_parameters(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    design = _require_parametric(ctx)
    feature = _feature_entity(ctx, ctx.require(params, "feature", str))
    found = [
        {
            "name": p.name,
            "expression": p.expression,
            **_value(p),
            "role": _safe(p, "role"),
            "comment": _safe(p, "comment"),
        }
        for p in _model_parameters(design, feature)
    ]
    return {
        "feature": refs.to_ref(feature),
        "parameters": found,
        "editable_keys": _editable_keys(design, feature),
    }


@handler("timeline.set_model_parameter", mutating=True)
def timeline_set_model_parameter(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    name = ctx.require(params, "name", str)
    value = ctx.require(params, "value", (int, float, str))
    allow_errors = ctx.require(params, "allow_errors", bool, False)
    design = _require_parametric(ctx)
    param = design.allParameters.itemByName(name)
    if param is None:
        raise BridgeError(
            ErrorCode.ENTITY_NOT_FOUND,
            f"parameter {name!r} not found",
            {"ref": refs.PARAM_PREFIX + name},
        )
    kind = refs.type_name(param)
    if kind != "ModelParameter":
        raise _invalid(
            f"{name!r} is a {kind}, not a model parameter: change user parameters with "
            "parameter_update",
            name=name,
        )
    feature = _safe(param, "createdBy")
    result = _apply_expressions(
        ctx, feature, [("value", param, value)], ctx.units.units, allow_errors
    )
    (change,) = result["changed"]
    return {
        "parameter": {
            "name": param.name,
            "expression": change["new_expression"],
            "old_expression": change["old_expression"],
            "value": change["value"],
            "value_unit": change["value_unit"],
            "unit": param.unit,
        },
        "feature": result["feature"],
    }


# --- 6. feature-specific edits (D7) -------------------------------------------------------------


Getter = Callable[[Any, Any], list[Any]]


def _attr_param(*path: str, extent: str | None = None) -> Getter:
    """``feature.<path...>`` as a one-element list; the parent of the last attribute must be
    of type ``extent`` when given (e.g. ``DistanceExtentDefinition``)."""

    def get(design: Any, feature: Any) -> list[Any]:
        obj = feature
        for i, attr in enumerate(path):
            if extent is not None and i == len(path) - 1 and refs.type_name(obj) != extent:
                return []
            obj = _safe(obj, attr)
            if obj is None:
                return []
        return [obj]

    return get


def _first(*getters: Getter) -> Getter:
    def get(design: Any, feature: Any) -> list[Any]:
        for getter in getters:
            found = getter(design, feature)
            if found:
                return found
        return []

    return get


def _edge_sets(set_type: str, attr: str, role_word: str | None = None) -> Getter:
    """``attr`` of every ``set_type`` edge set; without ``edgeSets`` (old Fusion) the model
    parameters of the feature whose ``role`` contains ``role_word``."""

    def get(design: Any, feature: Any) -> list[Any]:
        edge_sets = _safe(feature, "edgeSets")
        if edge_sets is None:
            if role_word is None:
                return []
            return [
                p
                for p in _model_parameters(design, feature)
                if role_word in str(_safe(p, "role", "") or "").lower()
            ]
        found = []
        for i in range(edge_sets.count):
            edge_set = edge_sets.item(i)
            if refs.type_name(edge_set) == set_type:
                param = _safe(edge_set, attr)
                if param is not None:
                    found.append(param)
        return found

    return get


_DISTANCE = "DistanceExtentDefinition"

_EDITABLE: dict[str, dict[str, Getter]] = {
    "ExtrudeFeature": {
        "distance": _attr_param("extentOne", "distance", extent=_DISTANCE),
        "distance_two": _attr_param("extentTwo", "distance", extent=_DISTANCE),
        "taper_angle": _attr_param("taperAngleOne"),
    },
    "RevolveFeature": {
        "angle": _first(
            _attr_param("extentDefinition", "angle", extent="AngleExtentDefinition"),
            _attr_param("extentDefinition", "angleOne", extent="TwoSidesAngleExtentDefinition"),
        ),
        "angle_two": _first(
            _attr_param("extentDefinition", "angleTwo", extent="AngleExtentDefinition"),
            _attr_param("extentDefinition", "angleTwo", extent="TwoSidesAngleExtentDefinition"),
        ),
    },
    "FilletFeature": {
        "radius": _edge_sets("ConstantRadiusFilletEdgeSet", "radius", role_word="radius"),
    },
    "ChamferFeature": {
        "distance": _edge_sets("EqualDistanceChamferEdgeSet", "distance"),
    },
    "HoleFeature": {
        "diameter": _attr_param("holeDiameter"),
        "depth": _attr_param("extentDefinition", "distance", extent=_DISTANCE),
        "tip_angle": _attr_param("tipAngle"),
        "counterbore_diameter": _attr_param("counterboreDiameter"),
        "counterbore_depth": _attr_param("counterboreDepth"),
        "countersink_diameter": _attr_param("countersinkDiameter"),
        "countersink_angle": _attr_param("countersinkAngle"),
    },
    "ShellFeature": {
        "inside_thickness": _attr_param("insideThickness"),
        "outside_thickness": _attr_param("outsideThickness"),
    },
}


def _editable_keys(design: Any, feature: Any) -> list[str]:
    table = _EDITABLE.get(refs.type_name(feature), {})
    return [key for key, getter in table.items() if getter(design, feature)]


def _edit_feature(
    ctx: Any, params: dict[str, Any], feature_type: str, keys: tuple[str, ...]
) -> dict[str, Any]:
    """Shared body of ``timeline.edit_*``: type check, applicable keys, atomic apply."""
    design = _require_parametric(ctx)
    feature = _feature_entity(ctx, ctx.require(params, "feature", str))
    allow_errors = ctx.require(params, "allow_errors", bool, False)
    actual = refs.type_name(feature)
    if actual != feature_type:
        raise _invalid(
            f"feature must be a {feature_type}, got {actual}; use the edit tool of its type or "
            "timeline_set_model_parameter",
            expected=feature_type,
            actual=actual,
        )
    given = {key: ctx.require(params, key, (int, float, str), None) for key in keys}
    given = {key: value for key, value in given.items() if value is not None}
    if not given:
        raise _invalid(f"nothing to change: pass at least one of {', '.join(keys)}")
    table = _EDITABLE[feature_type]
    changes: list[tuple[str, Any, Any]] = []
    for key, value in given.items():
        targets = table[key](design, feature)
        if not targets:
            available = _editable_keys(design, feature)
            hint = f"available keys: {', '.join(available)}" if available else "no editable keys"
            raise _invalid(
                f"{key!r} does not apply to this {feature_type} ({hint}); use "
                "timeline_set_model_parameter for other parameters",
                key=key,
                available_keys=available,
            )
        changes.extend((key, target, value) for target in targets)
    return _apply_expressions(ctx, feature, changes, ctx.units.units, allow_errors)


@handler("timeline.edit_extrude", mutating=True)
def timeline_edit_extrude(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _edit_feature(ctx, params, "ExtrudeFeature", ("distance", "distance_two", "taper_angle"))


@handler("timeline.edit_revolve", mutating=True)
def timeline_edit_revolve(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _edit_feature(ctx, params, "RevolveFeature", ("angle", "angle_two"))


@handler("timeline.edit_fillet", mutating=True)
def timeline_edit_fillet(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _edit_feature(ctx, params, "FilletFeature", ("radius",))


@handler("timeline.edit_chamfer", mutating=True)
def timeline_edit_chamfer(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _edit_feature(ctx, params, "ChamferFeature", ("distance",))


HOLE_KEYS = (
    "diameter",
    "depth",
    "tip_angle",
    "counterbore_diameter",
    "counterbore_depth",
    "countersink_diameter",
    "countersink_angle",
)


@handler("timeline.edit_hole", mutating=True)
def timeline_edit_hole(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _edit_feature(ctx, params, "HoleFeature", HOLE_KEYS)


@handler("timeline.edit_shell", mutating=True)
def timeline_edit_shell(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return _edit_feature(ctx, params, "ShellFeature", ("inside_thickness", "outside_thickness"))


# --- 7. groups ------------------------------------------------------------------------------------


def _group(ctx: Any, ref: Any) -> Any:
    to = _timeline_object(ctx, ref, "group")
    if not _is_group(to):
        raise _invalid(f"{to.name!r} is not a timeline group", param="group", ref=ref)
    return to


def _members(group: Any) -> list[Any]:
    return [group.item(j) for j in range(group.count)]


@handler("timeline.create_group", mutating=True)
def timeline_create_group(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    first_ref = ctx.require(params, "first", str)
    last_ref = ctx.require(params, "last", str)
    name = ctx.require(params, "name", str, None)
    if name is not None and not name.strip():
        raise _invalid("parameter 'name' must not be empty", param="name")
    timeline = _require_parametric(ctx).timeline
    first = _timeline_object(ctx, first_ref, "first")
    last = _timeline_object(ctx, last_ref, "last")
    for param, to in (("first", first), ("last", last)):
        if _safe(to, "parentGroup") is not None or _is_group(to):
            raise _invalid(
                f"{param}: {to.name!r} is a group or inside a group; groups cannot be nested",
                param=param,
            )
    start, end = first.index, last.index
    if start > end:
        raise _invalid(
            f"'first' ({first.name!r}, index {start}) comes after 'last' "
            f"({last.name!r}, index {end})"
        )
    for index in range(start, end + 1):
        to = timeline.item(index)
        if _is_group(to) or _safe(to, "parentGroup") is not None:
            raise _invalid(f"the range contains the group {to.name!r}; groups cannot be nested")
    if name is not None and any(to.name == name for to in refs.walk_timeline(timeline)):
        raise _invalid(f"a timeline item named {name!r} already exists", name=name)
    with Compensation() as comp:
        group = timeline.timelineGroups.add(start, end)
        if group is None:
            raise _api_error(f"Fusion did not group items {start}..{end}")
        comp.add(lambda: group.deleteMe(False), "create group")
        if name is not None:
            old = group.name
            comp.add(lambda: setattr(group, "name", old), "group name")
            group.name = name
        result = {"group": _item(ctx, group), "members": [_item(ctx, m) for m in _members(group)]}
    return {**result, **_position(timeline)}


@handler("timeline.set_group_collapsed", mutating=True)
def timeline_set_group_collapsed(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    ref = ctx.require(params, "group", str)
    collapsed = ctx.require(params, "collapsed", bool)
    timeline = _require_parametric(ctx).timeline
    group = _group(ctx, ref)
    with Compensation() as comp:
        old = bool(group.isCollapsed)
        comp.add(lambda: setattr(group, "isCollapsed", old), "collapse")
        group.isCollapsed = collapsed
    return {
        "group": {**_item(ctx, group), "is_collapsed": bool(group.isCollapsed)},
        **_position(timeline),
    }


@handler("timeline.ungroup", mutating=True)
def timeline_ungroup(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    ref = ctx.require(params, "group", str)
    timeline = _require_parametric(ctx).timeline
    group = _group(ctx, ref)
    members = _members(group)
    if group.deleteMe(False) is False:
        raise _api_error(f"Fusion did not ungroup {group.name!r}", group=ref)
    return {"members": [_item(ctx, m) for m in members], **_position(timeline)}

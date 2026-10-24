"""``body.*`` volume operations and body management (c08 D8-D11, D13): combine, split, move /
copy, scale, thicken, boundary fill, rename, visibility and remove.

Feature handlers follow the c07 order (INV-5): every parameter is read, every ref resolved and
every Fusion value built first (``INVALID_PARAMS`` with no side effects; an API this Fusion
version lacks is ``FUSION_API_ERROR`` "... not available" before the design changes), then
``BodySnapshot.take``, ``createInput`` / setters, ``add`` and ``feature_result`` (c07 D8/D9).
A failed feature is rolled back by the dispatcher (deleted here in a direct design).

Rename and visibility are not timeline items, so the timeline rollback cannot undo them:
``body.set_visibility`` keeps a journal of the old values and restores them in reverse order
when an assignment fails (design R6).

Lengths are in the request ``units`` (default mm), angles in degrees, scale factors are
unitless; conversion happens only in ``core.features`` / ``core.transforms`` helpers (INV-6).
"""

from __future__ import annotations

import contextlib
import math
from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import COMBINE_OPERATIONS, REMOVE_MODES, check_choice
from fusion_mcp.protocol.refs import is_origin_ref
from fusion_mcp.protocol.units import Units

from ..core import geometry
from ..core.entity_kinds import describe_kind, is_point
from ..core.features import (
    BodySnapshot,
    feature_result,
    length_value,
    object_collection,
    operation_enum,
    point3d,
    resolve_many,
    resolve_one,
    scalar_value,
    target_component,
)
from ..core.refs import type_name
from ..core.registry import handler
from ..core.transforms import apply_move, matrix_for, move_plan
from .feature_modify import _bodies_of
from .feature_sketch import (
    _bool,
    _call,
    _finish,
    _given,
    _invalid,
    _not_available,
    _opt,
    _parametric,
    _snapshot,
)

BODY = ("BRepBody",)
FACE = ("BRepFace",)
SPLIT_TOOL_TYPES = ("ConstructionPlane", "BRepFace", "BRepBody")
FILL_TOOL_TYPES = ("BRepBody", "BRepFace", "ConstructionPlane")
OUTPUT_UNITS = Units()  # boundary fill cell descriptions are in mm / mm³


def _token(entity: Any) -> Any:
    try:
        token = entity.entityToken
    except Exception:
        token = None
    return token if isinstance(token, str) and token else id(entity)


def _items(collection: Any) -> list[Any]:
    if collection is None:
        return []
    if hasattr(collection, "count") and hasattr(collection, "item"):
        return [collection.item(i) for i in range(collection.count)]
    return list(collection)


def _bodies(ctx: Any, params: dict[str, Any], name: str = "bodies") -> list[Any]:
    return resolve_many(ctx, ctx.require(params, name, list), BODY, name)


def _ref_object(ctx: Any, entity: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"ref": ctx.refs.to_ref(entity), "type": type_name(entity)}
    name = getattr(entity, "name", None)
    if isinstance(name, str):
        data["name"] = name
    return data


def _positive_factor(value: Any, param: str) -> Any:
    """Unitless scale factor -> ``ValueInput``; plain numbers must be > 0."""
    if isinstance(value, int | float) and not isinstance(value, bool) and value <= 0:
        raise _invalid(f"parameter '{param}' must be > 0", param=param, value=value)
    return scalar_value(value, param)


# --- combine (D10) ---------------------------------------------------------------------------


@handler("body.combine", mutating=True)
def body_combine(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Join / cut / intersect tool bodies with a target body (D10)."""
    target = resolve_one(ctx, ctx.require(params, "target"), BODY, "target")
    tools = _bodies(ctx, params, "tools")
    if any(_token(tool) == _token(target) for tool in tools):
        raise _invalid(
            "the 'target' body is also listed in 'tools'", param="tools", reason="target_in_tools"
        )
    operation = check_choice("operation", _opt(params, "operation", "join"), COMBINE_OPERATIONS)
    op_enum = operation_enum(operation)
    keep_tools = _bool(ctx, params, "keep_tools", False)
    new_component = _bool(ctx, params, "new_component", False)
    component = target_component(target)

    before = _snapshot(component, tools)
    combines = component.features.combineFeatures
    inp = combines.createInput(target, object_collection(tools))
    inp.operation = op_enum
    inp.isKeepToolBodies = keep_tools
    inp.isNewComponent = new_component
    feature = combines.add(inp)
    if new_component:
        return _finish(ctx, feature, before, "new_component")
    return _finish(ctx, feature, before, None, [target])


# --- split body (D10) ------------------------------------------------------------------------


@handler("body.split", mutating=True)
def body_split(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Split bodies with a plane, face or body (D10); the new parts are ``created_bodies``."""
    bodies = _bodies(ctx, params)
    component = target_component(bodies[0])
    tool = _tool(ctx, ctx.require(params, "tool"), component, SPLIT_TOOL_TYPES, "tool")
    if any(_token(tool) == _token(body) for body in bodies):
        raise _invalid("the split 'tool' cannot be one of the 'bodies'", param="tool")
    extend = _bool(ctx, params, "extend_tool", True)

    before = _snapshot(component, bodies)
    splits = component.features.splitBodyFeatures
    entities = bodies[0] if len(bodies) == 1 else object_collection(bodies)
    inp = splits.createInput(entities, tool, extend)
    feature = splits.add(inp)
    return _finish(ctx, feature, before, None, bodies)


def _tool(ctx: Any, ref: Any, component: Any, allowed: tuple[str, ...], param: str) -> Any:
    """Tool entity of one of ``allowed``; ``origin:XY|XZ|YZ`` is the plane of ``component``."""
    raw = ref.get("ref") if isinstance(ref, dict) else ref
    if not is_origin_ref(raw):
        return resolve_one(ctx, ref, allowed, param)
    try:
        entity = ctx.refs.resolve(ref, component)
    except BridgeError as exc:
        exc.details.setdefault("param", param)
        raise
    if type_name(entity) not in allowed:
        raise _invalid(
            f"parameter '{param}' must be {' | '.join(allowed)}, got {describe_kind(entity)}",
            param=param,
            ref=raw,
            expected=list(allowed),
            actual=describe_kind(entity),
        )
    return entity


# --- move / copy (D8) ------------------------------------------------------------------------


def _copies(features: Any, bodies: list[Any], made: list[Any]) -> tuple[list[Any], Any]:
    """Copy ``bodies`` (Copy/Paste, a non-associative copy) -> ``(copies, copy feature)``.
    Without ``copyPasteBodies`` (direct design / older API) each body is
    ``copyToComponent(body.parentComponent)`` and the feature is ``None``. Everything created
    is appended to ``made`` at once, so a later failure can delete it (direct design)."""
    if hasattr(features, "copyPasteBodies"):
        feature = features.copyPasteBodies.add(object_collection(bodies))
        if feature is None:
            raise BridgeError(ErrorCode.FUSION_API_ERROR, "Fusion did not copy the bodies")
        made.append(feature)
        copies = _items(getattr(feature, "bodies", None))
    else:
        feature = None
        copies = []
        for body in bodies:
            copy = body.copyToComponent(body.parentComponent)
            if copy is None:
                raise BridgeError(
                    ErrorCode.FUSION_API_ERROR,
                    "Fusion did not copy the body",
                    {"body": getattr(body, "name", None)},
                )
            made.append(copy)
            copies.append(copy)
    if len(copies) != len(bodies):
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"Fusion made {len(copies)} copies of {len(bodies)} bodies",
            {"expected": len(bodies), "copied": len(copies)},
        )
    return copies, feature


@handler("body.move", mutating=True)
def body_move(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Move bodies (or, with ``keep_original``, copies of them) by a transform (D8)."""
    bodies = _bodies(ctx, params)
    plan = move_plan(ctx, ctx.require(params, "transform", dict))
    keep_original = _bool(ctx, params, "keep_original", False)
    component = target_component(bodies[0])
    features = component.features
    moves = features.moveFeatures

    # Decide between createInput2 + defineAs* and the matrix fallback before any change.
    probe = (
        moves.createInput2(object_collection(bodies)) if hasattr(moves, "createInput2") else None
    )
    matrix = None
    if probe is None or not hasattr(probe, plan.method):
        matrix = matrix_for(ctx, plan)

    before = BodySnapshot.take([component, *(b.parentComponent for b in bodies)])
    copy_feature = None
    entities = bodies
    made: list[Any] = []  # what a direct design (no timeline rollback) must delete on failure
    try:
        if keep_original:
            entities, copy_feature = _copies(features, bodies, made)
        collection = object_collection(entities)
        if matrix is not None:
            inp = moves.createInput(collection, matrix)
        else:
            inp = probe if not keep_original else moves.createInput2(collection)
            apply_move(ctx, inp, plan)
        feature = moves.add(inp)
        made.insert(0, feature)
        result = feature_result(ctx, feature, before, entities)
    except Exception:
        if not _parametric(ctx):
            for item in made:
                if item is not None:
                    with contextlib.suppress(Exception):
                        item.deleteMe()
        raise
    if keep_original:
        result["copy"] = {
            "feature": _ref_object(ctx, copy_feature) if copy_feature is not None else None,
            "method": "copyPasteBodies" if copy_feature is not None else "copyToComponent",
        }
    return result


# --- scale (D9) ------------------------------------------------------------------------------


@handler("body.scale", mutating=True)
def body_scale(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Uniform or non-uniform scale about a base point (D9); factors are unitless."""
    bodies = _bodies(ctx, params)
    component = target_component(bodies[0])
    point_ref = _opt(params, "point", "origin:O")
    raw = point_ref.get("ref") if isinstance(point_ref, dict) else point_ref
    try:
        point = (
            ctx.refs.resolve(point_ref, component)
            if is_origin_ref(raw)
            else ctx.refs.resolve(point_ref)
        )
    except BridgeError as exc:
        exc.details.setdefault("param", "point")
        raise
    if not is_point(point):
        raise _invalid(
            "parameter 'point' must be a vertex, a sketch point, a construction point or "
            f"origin:O, got {describe_kind(point)}",
            param="point",
            got=describe_kind(point),
        )
    factor_raw = _opt(params, "factor", 1)
    factor = _positive_factor(factor_raw, "factor")
    factors = params.get("factors")
    non_uniform: tuple[Any, ...] | None = None
    if factors is not None:
        if not isinstance(factors, dict) or set(factors) != {"x", "y", "z"}:
            raise _invalid("parameter 'factors' must be an object {x, y, z}", param="factors")
        if not (isinstance(factor_raw, int | float) and factor_raw == 1):
            raise _invalid(
                "pass either 'factor' (uniform) or 'factors' (non-uniform), not both",
                param="factors",
                reason="factor_and_factors",
            )
        non_uniform = tuple(_positive_factor(factors[a], f"factors.{a}") for a in "xyz")

    before = _snapshot(component, bodies)
    scales = component.features.scaleFeatures
    inp = scales.createInput(object_collection(bodies), point, factor)
    if non_uniform is not None:
        _call("ScaleFeature", inp, "setToNonUniform", *non_uniform)
    feature = scales.add(inp)
    return _finish(ctx, feature, before, None, bodies)


# --- thicken (D11) ---------------------------------------------------------------------------


@handler("body.thicken", mutating=True)
def body_thicken(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Thicken faces by a signed thickness (D11)."""
    faces = resolve_many(ctx, ctx.require(params, "faces", list), FACE, "faces")
    if not _given(params, "thickness"):
        raise _invalid("missing required parameter 'thickness'", param="thickness")
    raw = params["thickness"]
    if isinstance(raw, int | float) and not isinstance(raw, bool) and raw == 0:
        raise _invalid("parameter 'thickness' must not be 0", param="thickness", value=raw)
    thickness = length_value(ctx, raw, param="thickness")
    symmetric = _bool(ctx, params, "symmetric", False)
    operation = ctx.require(params, "operation", str, "new_body")
    op_enum = operation_enum(operation)
    chain = _bool(ctx, params, "chain", True)
    component = target_component(faces[0])

    before = _snapshot(component, _bodies_of(faces))
    thickens = component.features.thickenFeatures
    inp = thickens.createInput(object_collection(faces), thickness, symmetric, op_enum, chain)
    feature = thickens.add(inp)
    return _finish(ctx, feature, before, operation)


# --- boundary fill (D11) ---------------------------------------------------------------------


def _cell_info(cell: Any) -> dict[str, Any]:
    body = getattr(cell, "cellBody", None)
    info: dict[str, Any] = {}
    box = getattr(body, "boundingBox", None)
    if box is not None:
        lo, hi = geometry.point(box.minPoint), geometry.point(box.maxPoint)
        info["center"] = [round((a + b) / 2, 6) for a, b in zip(lo, hi, strict=True)]
    volume = getattr(body, "volume", None)
    if isinstance(volume, int | float):
        info["volume"] = round(OUTPUT_UNITS.volume_out(float(volume)), 6)
    return info


def _contains(cell: Any, point: Any) -> bool:
    body = getattr(cell, "cellBody", None)
    if body is None or not hasattr(body, "pointContainment"):
        return False
    inside = adsk.fusion.PointContainment.PointInsidePointContainment
    return bool(body.pointContainment(point) == inside)


def _cell_points(ctx: Any, params: dict[str, Any]) -> list[Any] | None:
    raw = params.get("cell_points")
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise _invalid(
            "parameter 'cell_points' must be a non-empty list of [x, y, z]", param="cell_points"
        )
    return [point3d(ctx, p, param=f"cell_points[{i}]") for i, p in enumerate(raw)]


@handler("body.boundary_fill", mutating=True)
def body_boundary_fill(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Fill the cells bounded by tool bodies / faces / planes (D11): every cell, or the cells
    containing ``cell_points`` (never by index, INV-7)."""
    tools = resolve_many(ctx, ctx.require(params, "tools", list), FILL_TOOL_TYPES, "tools")
    operation = ctx.require(params, "operation", str, "new_body")
    op_enum = operation_enum(operation)
    points = _cell_points(ctx, params)
    remove_tools = _bool(ctx, params, "remove_tools", False)
    component = target_component(tools[0])

    before = _snapshot(component, [t for t in tools if type_name(t) == "BRepBody"])
    fills = component.features.boundaryFillFeatures
    inp = fills.createInput(object_collection(tools), op_enum)
    try:
        cells = _items(inp.bRepCells)
        if not cells:
            raise _invalid(
                "the 'tools' enclose no cell to fill", param="tools", reason="no_cells", cells=[]
            )
        if points is None:
            chosen = cells
        else:
            chosen = []
            missing: list[int] = []
            for index, point in enumerate(points):
                inside = [c for c in cells if _contains(c, point)]
                if not inside:
                    missing.append(index)
                for cell in inside:
                    if all(cell is not c for c in chosen):
                        chosen.append(cell)
            if missing:
                raise _invalid(
                    "cell_points "
                    + ", ".join(str(params["cell_points"][i]) for i in missing)
                    + " lie in no cell of the tools",
                    param="cell_points",
                    missing=missing,
                    cells=[_cell_info(c) for c in cells],
                )
        for cell in chosen:
            cell.isSelected = True
        inp.isRemoveTools = remove_tools
    except Exception:
        with contextlib.suppress(Exception):
            inp.cancel()
        raise
    feature = fills.add(inp)
    return _finish(ctx, feature, before, operation)


# --- rename, visibility, remove (D13) --------------------------------------------------------


@handler("body.rename", mutating=True)
def body_rename(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Rename a body (D13): ``{"body": ref, "previous_name"}``."""
    body = resolve_one(ctx, ctx.require(params, "body"), BODY, "body")
    name = ctx.require(params, "name", str).strip()
    if not name:
        raise _invalid("parameter 'name' must not be empty", param="name")
    previous = body.name
    body.name = name
    return {"body": _ref_object(ctx, body), "previous_name": previous}


def _visibility(ctx: Any, body: Any) -> dict[str, Any]:
    data = _ref_object(ctx, body)
    data["light_bulb"] = bool(getattr(body, "isLightBulbOn", True))
    data["is_visible"] = bool(getattr(body, "isVisible", True))
    opacity = getattr(body, "opacity", 1.0)
    data["opacity"] = float(opacity) if isinstance(opacity, int | float) else None
    return data


@handler("body.set_visibility", mutating=True)
def body_set_visibility(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Light bulb and / or opacity of bodies (D13); all-or-nothing through a journal of the
    previous values (not timeline items, design R6)."""
    bodies = _bodies(ctx, params)
    visible = params.get("visible")
    opacity = params.get("opacity")
    if visible is None and opacity is None:
        raise _invalid("give 'visible' and/or 'opacity'", param="visible", reason="nothing")
    if visible is not None and not isinstance(visible, bool):
        raise _invalid("parameter 'visible' must be a boolean", param="visible")
    if opacity is not None and (
        isinstance(opacity, bool)
        or not isinstance(opacity, int | float)
        or not math.isfinite(opacity)
        or not 0 <= opacity <= 1
    ):
        raise _invalid(
            "parameter 'opacity' must be a number from 0 (transparent) to 1 (opaque)",
            param="opacity",
            value=opacity if isinstance(opacity, int | float) else repr(opacity),
        )

    journal: list[tuple[Any, str, Any]] = []
    try:
        for body in bodies:
            if visible is not None:
                journal.append((body, "isLightBulbOn", body.isLightBulbOn))
                body.isLightBulbOn = visible
            if opacity is not None:
                journal.append((body, "opacity", body.opacity))
                body.opacity = float(opacity)
    except Exception:
        for body, name, value in reversed(journal):
            with contextlib.suppress(Exception):
                setattr(body, name, value)
        raise
    return {"bodies": [_visibility(ctx, body) for body in bodies]}


@handler("body.remove", mutating=True)
def body_remove(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Remove (a Remove feature, parametric designs) or delete bodies (D13)."""
    bodies = _bodies(ctx, params)
    mode = check_choice("mode", _opt(params, "mode", "remove"), REMOVE_MODES)
    used = mode if _parametric(ctx) else "delete"
    refs = [ctx.refs.to_ref(body) for body in bodies]
    if used == "remove":
        for body in bodies:
            if not hasattr(body.parentComponent.features, "removeFeatures"):
                raise _not_available("RemoveFeature", "Remove features are")

    features: list[dict[str, Any]] = []
    for body in bodies:
        if used == "remove":
            feature = body.parentComponent.features.removeFeatures.add(body)
            if feature is None:
                raise BridgeError(
                    ErrorCode.FUSION_API_ERROR,
                    "Fusion did not remove the body",
                    {"body": getattr(body, "name", None)},
                )
            features.append(_ref_object(ctx, feature))
        elif body.deleteMe() is False:
            raise BridgeError(
                ErrorCode.FUSION_API_ERROR,
                "Fusion did not delete the body",
                {"body": getattr(body, "name", None)},
            )
    return {"removed": refs, "features": features, "mode_used": used}

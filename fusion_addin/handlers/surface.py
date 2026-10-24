"""``surface.*`` surface modelling (c11 D4-D11): patch, extend, trim (cell preview and removal
by points), untrim, stitch, unstitch, ruled surface and offset surface.

Every mutating handler follows the c07 order (INV-5): every parameter is read, every ref
resolved and every Fusion value built first (``INVALID_PARAMS`` with no side effects; an API
this Fusion version lacks is ``FUSION_API_ERROR`` "... not available" before ``add``), then
``BodySnapshot.take``, ``createInput`` / setters, ``add`` and ``surface_result`` (D3: c07
``feature_result`` + ``is_solid`` per body). A failed feature is rolled back by the dispatcher
(deleted in a direct design).

Trim inputs open a Fusion transaction (design R1): every path that does not reach a
successful ``add`` calls ``TrimFeatureInput.cancel()``. Trim cells are temporary objects
without refs; they are listed with their center / box / area (mm) and chosen by points
(INV-7), never by index.

Lengths are in the request ``units`` (default mm), angles in degrees; conversion happens only
in ``core.features`` helpers and ``fusion_mcp.protocol.units`` (INV-6).
"""

from __future__ import annotations

import contextlib
import math
from typing import Any

import adsk.core

from fusion_mcp.protocol.features import check_choice
from fusion_mcp.protocol.sketch_refs import is_profile_ref
from fusion_mcp.protocol.surfaces import EXTEND_ALIGNMENTS, OFFSET_TYPES
from fusion_mcp.protocol.units import Units

from ..core import geometry
from ..core.features import (
    EDGE,
    angle_value,
    feature_profiles,
    length_value,
    object_collection,
    point3d,
    resolve_many,
    resolve_one,
    target_component,
)
from ..core.refs import type_name
from ..core.registry import handler
from ..core.surfaces import (
    OPEN_PROFILE_TYPES,
    bbox_distance,
    first_body_is_solid,
    not_available,
    open_profile,
    surface_enum,
    surface_operation,
    surface_result,
)
from ..sketch.resolve import CURVE_TYPES
from .body import _tool
from .feature_modify import _bodies_of
from .feature_sketch import _bool, _given, _invalid, _opt, _snapshot

FACE = ("BRepFace",)
BODY_OR_FACE = ("BRepBody", "BRepFace")
PATCH_BOUNDARY_TYPES = ("Profile", *CURVE_TYPES, EDGE)
TRIM_TOOL_TYPES = ("BRepBody", "BRepFace", "ConstructionPlane", *CURVE_TYPES)
RULED_DIRECTION_TYPES = ("BRepEdge", "BRepFace", "ConstructionAxis", "ConstructionPlane")
OUTPUT_UNITS = Units()  # trim cell descriptions are in mm / mm²
DEFAULT_STITCH_TOLERANCE_MM = 0.1


def _items(collection: Any) -> list[Any]:
    if collection is None:
        return []
    if hasattr(collection, "count") and hasattr(collection, "item"):
        return [collection.item(i) for i in range(collection.count)]
    return list(collection)


def _length(ctx: Any, params: dict[str, Any], name: str) -> Any:
    if not _given(params, name):
        raise _invalid(f"missing required parameter '{name}'", param=name)
    return length_value(ctx, params[name], param=name)


def _nonzero_length(ctx: Any, params: dict[str, Any], name: str) -> Any:
    raw = params.get(name)
    if isinstance(raw, int | float) and not isinstance(raw, bool) and raw == 0:
        raise _invalid(f"parameter '{name}' must not be 0", param=name, value=raw)
    return _length(ctx, params, name)


def _entities(entities: list[Any]) -> Any:
    return entities[0] if len(entities) == 1 else object_collection(entities)


# --- patch (D4) ------------------------------------------------------------------------------


def _patch_boundary(ctx: Any, raw: Any) -> list[Any]:
    """``boundary`` refs -> profiles / sketch curves / edges, in order, without repeats."""
    if not isinstance(raw, list) or not raw:
        raise _invalid(
            "parameter 'boundary' must be a non-empty list of profile, sketch curve or edge refs",
            param="boundary",
            reason="empty",
        )
    out: list[Any] = []
    for index, spec in enumerate(raw):
        if isinstance(spec, dict) or (isinstance(spec, str) and is_profile_ref(spec)):
            found = feature_profiles(ctx, [spec], param="boundary")
            entities = _items(found) if isinstance(found, adsk.core.ObjectCollection) else [found]
        else:
            entities = [resolve_one(ctx, spec, PATCH_BOUNDARY_TYPES, "boundary", index)]
        for entity in entities:
            if all(entity is not seen for seen in out):
                out.append(entity)
    return out


def _weight(params: dict[str, Any]) -> float:
    value = _opt(params, "weight", 0.5)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _invalid("parameter 'weight' must be a number in [0, 1]", param="weight")
    if not 0.0 <= float(value) <= 1.0:
        raise _invalid(
            f"parameter 'weight' must be in [0, 1], got {value}", param="weight", value=value
        )
    return float(value)


@handler("surface.patch", mutating=True)
def surface_patch(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Patch surface bounded by a profile, sketch curves or edges (D4); result per D3."""
    boundary = _patch_boundary(ctx, params.get("boundary"))
    continuity = surface_enum("continuity", "continuity", _opt(params, "continuity", "connected"))
    weight = _weight(params)
    flip = _bool(ctx, params, "flip_continuity_direction", False)
    operation = _opt(params, "operation", "new_body")
    op_enum = surface_operation(operation)
    component = target_component(boundary[0])

    before = _snapshot(component, _bodies_of([b for b in boundary if type_name(b) == EDGE]))
    patches = component.features.patchFeatures
    inp = patches.createInput(_entities(boundary), op_enum)
    inp.isGroupEdges = True
    inp.groupContinuity = continuity
    inp.groupWeight = weight
    inp.groupIsContinuityDirectionFlipped = flip
    feature = patches.add(inp)
    return surface_result(ctx, feature, before, operation=operation)


# --- extend (D5) -----------------------------------------------------------------------------


@handler("surface.extend", mutating=True)
def surface_extend(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Extend free edges of surface bodies by a distance (D5); result per D3."""
    edges = resolve_many(ctx, ctx.require(params, "edges", list), (EDGE,), "edges")
    distance = _nonzero_length(ctx, params, "distance")
    extend_type = surface_enum("extend_type", "extend_type", _opt(params, "extend_type", "natural"))
    chain = _bool(ctx, params, "chain", True)
    alignment = check_choice("alignment", _opt(params, "alignment", "free"), EXTEND_ALIGNMENTS)
    component = target_component(edges[0])
    bodies = _bodies_of(edges)

    extends = component.features.extendFeatures
    inp = extends.createInput(object_collection(edges), distance, extend_type, chain)
    if hasattr(inp, "extendAlignment"):
        inp.extendAlignment = surface_enum("alignment", "alignment", alignment)
    elif alignment != "free":
        raise not_available("ExtendFeature", "extendAlignment")
    before = _snapshot(component, bodies)
    feature = extends.add(inp)
    return surface_result(ctx, feature, before, bodies)


# --- trim (D6) -------------------------------------------------------------------------------


def _cell_info(cell: Any) -> dict[str, Any]:
    body = getattr(cell, "cellBody", None)
    info: dict[str, Any] = {}
    box = getattr(body, "boundingBox", None)
    if box is not None:
        lo = geometry.point(box.minPoint, OUTPUT_UNITS)
        hi = geometry.point(box.maxPoint, OUTPUT_UNITS)
        info["center"] = [round((a + b) / 2, 6) for a, b in zip(lo, hi, strict=True)]
        info["bbox"] = {"min": lo, "max": hi}
    area = getattr(body, "area", None)
    if isinstance(area, int | float) and not isinstance(area, bool):
        info["area_mm2"] = round(OUTPUT_UNITS.area_out(float(area)), 6)
    solid = getattr(body, "isSolid", None)
    if isinstance(solid, bool):
        info["is_solid"] = solid
    return info


def _trim_setup(ctx: Any, params: dict[str, Any]) -> tuple[Any, Any]:
    """``(component, tool)`` of ``trim_tool`` (``origin:XY|XZ|YZ`` = root origin planes)."""
    tool = _tool(ctx, ctx.require(params, "trim_tool"), ctx.root, TRIM_TOOL_TYPES, "trim_tool")
    return target_component(tool), tool


def _cancel(inp: Any) -> None:
    with contextlib.suppress(Exception):
        inp.cancel()


@handler("surface.trim_cells", mutating=False)
def surface_trim_cells(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Cells a trim tool cuts the surfaces into (D6): center / bbox / area in mm. The trim
    input is always cancelled, the design does not change."""
    component, tool = _trim_setup(ctx, params)
    inp = component.features.trimFeatures.createInput(tool)
    try:
        cells = [_cell_info(cell) for cell in _items(inp.bRepCells)]
    finally:
        _cancel(inp)
    return {"cells": cells, "count": len(cells)}


def _distance(ctx: Any, cell: Any, point: Any) -> float:
    """Minimum distance (cm) from ``point`` to the cell body; bounding box fallback (R2)."""
    body = cell.cellBody
    try:
        value = ctx.app.measureManager.measureMinimumDistance(body, point).value
        if isinstance(value, int | float) and math.isfinite(value):
            return float(value)
    except Exception:
        pass
    return bbox_distance(body.boundingBox, point)


def _remove_points(ctx: Any, params: dict[str, Any]) -> list[Any]:
    raw = params.get("remove_points")
    if not isinstance(raw, list) or not raw:
        raise _invalid(
            "parameter 'remove_points' must be a non-empty list of [x, y, z]",
            param="remove_points",
        )
    return [point3d(ctx, p, param=f"remove_points[{i}]") for i, p in enumerate(raw)]


def _max_distance(ctx: Any, params: dict[str, Any]) -> float:
    value = _opt(params, "max_distance", 1.0)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _invalid("parameter 'max_distance' must be a number", param="max_distance")
    if value < 0:
        raise _invalid("parameter 'max_distance' must be >= 0", param="max_distance")
    return float(ctx.units.length(value))


@handler("surface.trim", mutating=True)
def surface_trim(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Trim surfaces with a tool, removing the cells nearest to ``remove_points`` (D6); result
    per D3 + ``removed_cells``."""
    component, tool = _trim_setup(ctx, params)
    points = _remove_points(ctx, params)
    limit = _max_distance(ctx, params)

    before = _snapshot(component, None)
    trims = component.features.trimFeatures
    inp = trims.createInput(tool)
    try:
        cells = _items(inp.bRepCells)
        if not cells:
            raise _invalid(
                "the 'trim_tool' cuts no surface into cells",
                param="trim_tool",
                reason="no_cells",
                cells=[],
            )
        chosen: list[Any] = []
        missing: list[int] = []
        for index, point in enumerate(points):
            best, cell = min(
                ((_distance(ctx, c, point), i) for i, c in enumerate(cells)), key=lambda t: t[0]
            )
            if best > limit:
                missing.append(index)
            elif all(cells[cell] is not c for c in chosen):
                chosen.append(cells[cell])
        if missing:
            raise _invalid(
                "remove_points "
                + ", ".join(str(params["remove_points"][i]) for i in missing)
                + " are farther than max_distance from every cell",
                param="remove_points",
                missing=missing,
                cells=[_cell_info(c) for c in cells],
            )
        for cell in chosen:
            cell.isSelected = True
        feature = trims.add(inp)
    except Exception:
        _cancel(inp)
        raise
    result = surface_result(ctx, feature, before)
    result["removed_cells"] = len(chosen)
    return result


# --- untrim (D7) -----------------------------------------------------------------------------


@handler("surface.untrim", mutating=True)
def surface_untrim(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Remove all / external / internal loops of surface faces (D7); result per D3."""
    faces = resolve_many(ctx, ctx.require(params, "faces", list), FACE, "faces")
    for index, face in enumerate(faces):
        body = getattr(face, "body", None)
        if getattr(body, "isSolid", False) is True:
            raise _invalid(
                f"parameter 'faces[{index}]' is a face of a solid body; untrim works on "
                "surface bodies only",
                param="faces",
                index=index,
                reason="solid_face",
            )
    loop_name = _opt(params, "loop_type", "all")
    loop_type = surface_enum("loop_type", "loop_type", loop_name)
    extension = None
    if _given(params, "extension_distance"):
        if loop_name == "internal":
            raise _invalid(
                "parameter 'extension_distance' applies only to loop_type all | external",
                param="extension_distance",
            )
        extension = length_value(ctx, params["extension_distance"], param="extension_distance")
    component = target_component(faces[0])
    bodies = _bodies_of(faces)

    before = _snapshot(component, bodies)
    untrims = component.features.untrimFeatures
    args: tuple[Any, ...] = (list(faces), loop_type)
    if extension is not None:
        args = (*args, extension)
    inp = untrims.createInputFromFaces(*args)
    feature = untrims.add(inp)
    return surface_result(ctx, feature, before, bodies)


# --- stitch / unstitch (D8, D9) --------------------------------------------------------------


@handler("surface.stitch", mutating=True)
def surface_stitch(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Stitch surface bodies / faces within a tolerance (D8); result per D3 + ``is_solid`` of
    the resulting body."""
    surfaces = resolve_many(ctx, ctx.require(params, "surfaces", list), BODY_OR_FACE, "surfaces")
    if len(surfaces) < 2:
        raise _invalid(
            f"parameter 'surfaces' needs at least 2 distinct bodies or faces, got {len(surfaces)}",
            param="surfaces",
            count=len(surfaces),
            reason="too_few_surfaces",
        )
    if _given(params, "tolerance"):
        tolerance = length_value(ctx, params["tolerance"], param="tolerance")
    else:
        tolerance = length_value(ctx, DEFAULT_STITCH_TOLERANCE_MM, "mm", "tolerance")
    operation = _opt(params, "operation", "new_body")
    op_enum = surface_operation(operation)
    component = target_component(surfaces[0])
    bodies = _bodies_of(surfaces)

    before = _snapshot(component, bodies)
    stitches = component.features.stitchFeatures
    inp = stitches.createInput(object_collection(surfaces), tolerance, op_enum)
    feature = stitches.add(inp)
    result = surface_result(ctx, feature, before, bodies, operation)
    result["is_solid"] = first_body_is_solid(feature)
    return result


@handler("surface.unstitch", mutating=True)
def surface_unstitch(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Split faces / bodies into separate surface bodies (D9); result per D3."""
    entities = resolve_many(ctx, ctx.require(params, "entities", list), BODY_OR_FACE, "entities")
    chain = _bool(ctx, params, "chain", True)
    component = target_component(entities[0])
    bodies = _bodies_of(entities)

    before = _snapshot(component, bodies)
    unstitches = component.features.unstitchFeatures
    feature = unstitches.add(object_collection(entities), chain)
    return surface_result(ctx, feature, before, bodies)


# --- ruled (D10) -----------------------------------------------------------------------------


@handler("surface.ruled", mutating=True)
def surface_ruled(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Ruled surface from edges or sketch curves (D10); result per D3."""
    edge_refs = ctx.require(params, "edges", list)
    distance = _nonzero_length(ctx, params, "distance")
    angle = angle_value(ctx, _opt(params, "angle", 0), "angle")
    ruled_name = _opt(params, "ruled_type", "normal")
    ruled_type = surface_enum("ruled_type", "ruled_type", ruled_name)
    corner = surface_enum("corner", "corner", _opt(params, "corner", "rounded"))
    alternate = _bool(ctx, params, "alternate_face", False)
    chain = _bool(ctx, params, "chain", True)
    operation = _opt(params, "operation", "new_body")
    op_enum = surface_operation(operation)
    if not edge_refs:
        raise _invalid("parameter 'edges' must not be empty", param="edges", reason="empty")
    first = resolve_one(ctx, edge_refs[0], OPEN_PROFILE_TYPES, "edges", 0)
    component = target_component(first)
    direction = None
    if ruled_name == "direction":
        if not _given(params, "direction"):
            raise _invalid("ruled_type 'direction' needs 'direction'", param="direction")
        direction = _tool(ctx, params["direction"], component, RULED_DIRECTION_TYPES, "direction")
    elif _given(params, "direction"):
        raise _invalid(
            "parameter 'direction' applies only to ruled_type 'direction'", param="direction"
        )
    profile = open_profile(ctx, edge_refs, chain, "edges")
    edges = [first] if type_name(first) == EDGE else []

    before = _snapshot(component, _bodies_of(edges))
    ruled = component.features.ruledSurfaceFeatures
    args: tuple[Any, ...] = (profile, distance, angle, ruled_type)
    if direction is not None:
        args = (*args, direction)
    inp = ruled.createInput(*args)
    inp.cornerType = corner
    inp.alternateFace = alternate
    inp.operation = op_enum
    feature = ruled.add(inp)
    return surface_result(ctx, feature, before, operation=operation)


# --- offset surface (D11) --------------------------------------------------------------------


@handler("surface.offset", mutating=True)
def surface_offset(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Offset surface bodies from faces at a signed distance along the normal (D11); result
    per D3."""
    faces = resolve_many(ctx, ctx.require(params, "faces", list), FACE, "faces")
    distance = _nonzero_length(ctx, params, "distance")
    chain = _bool(ctx, params, "chain", True)
    offset_type = check_choice("offset_type", _opt(params, "offset_type", "sharp"), OFFSET_TYPES)
    operation = _opt(params, "operation", "new_body")
    op_enum = surface_operation(operation)
    component = target_component(faces[0])

    offsets = component.features.offsetFeatures
    inp = offsets.createInput(object_collection(faces), distance, op_enum, chain)
    if hasattr(inp, "offsetType"):
        inp.offsetType = surface_enum("offset_type", "offset_type", offset_type)
    elif offset_type != "sharp":
        raise not_available("OffsetFeature", "offsetType")
    before = _snapshot(component, _bodies_of(faces))
    feature = offsets.add(inp)
    return surface_result(ctx, feature, before, operation=operation)

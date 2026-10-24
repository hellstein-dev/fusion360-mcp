"""``feature.hole``, ``feature.thread`` and ``feature.thread_options`` (c07 D15, D16).

The mutating handlers follow the order of ``feature_sketch`` (INV-5): every parameter is read,
every ref resolved and every Fusion value built first (thread type, designation and class are
checked against ``ThreadDataQuery`` too), then ``BodySnapshot.take``, ``createInput``, setters
(a ``False`` answer -> ``FUSION_API_ERROR``), ``add`` and ``feature_result`` (D8/D9).

A tapped hole (``feature.hole`` with ``thread``) adds two features in one call: the hole, then a
thread on the hole's bore (the cylindrical side faces of the smallest radius). Whatever fails
after the hole is added - the designation recommended for the bore, ``createThreadInfo``, the
thread's ``add`` or its health - raises, and the dispatcher rolls the timeline back to before
the hole, so neither feature stays (in a direct design both are deleted here).

``feature.thread_options`` only reads ``ThreadDataQuery``. Thread types, sizes, designations
and classes are Fusion strings and are passed through unchanged (INV-6); lengths are in the
request ``units`` (default mm), angles in degrees, converted only by ``core.features``
helpers and ``fusion_mcp.protocol.units``.
"""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass
from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import (
    DEFAULT_THREAD_TYPE,
    HOLE_EXTENT_TYPES,
    HOLE_KINDS,
    HOLE_PLACEMENTS,
    THREAD_LOCATIONS,
    check_choice,
)
from fusion_mcp.protocol.units import length_to_internal

from ..core.entity_kinds import describe_kind, require_kind
from ..core.features import (
    EXTENT_ENTITY_TYPES,
    BodySnapshot,
    angle_value,
    feature_result,
    length_value,
    object_collection,
    participant_bodies,
    point3d,
    resolve_many,
    resolve_one,
    target_component,
)
from ..core.registry import handler
from .feature_sketch import _call, _finish, _invalid, _is_zero, _opt, _parametric, _snapshot

DEFAULT_TIP_ANGLE = 118.0
DEFAULT_CS_ANGLE = 90.0
MAX_AVAILABLE = 50

_PLANAR_TYPES = ("BRepFace", "ConstructionPlane")
_CYLINDER = "BRepFace (cylinder)"
_CIRCULAR_EDGES = ("BRepEdge (circle)", "BRepEdge (arc)")

_PLACEMENT_FIELDS: dict[str, tuple[str, ...]] = {
    "point": ("face", "point"),
    "sketch_points": ("points",),
    "face_offsets": ("face", "point", "edge1", "offset1", "edge2", "offset2"),
    "center": ("face", "edge"),
}
_EXTENT_FIELDS: dict[str, tuple[str, ...]] = {
    "distance": ("depth",),
    "through_all": (),
    "to_entity": ("entity",),
}
_KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "simple": (),
    "counterbore": ("cb_diameter", "cb_depth"),
    "countersink": ("cs_diameter", "cs_angle"),
}
_KIND_METHODS = {
    "simple": "createSimpleInput",
    "counterbore": "createCounterboreInput",
    "countersink": "createCountersinkInput",
}
_THREAD_FIELDS = frozenset(
    {
        "thread_type",
        "designation",
        "thread_class",
        "modeled",
        "full_length",
        "length",
        "offset",
        "location",
    }
)
_LOCATION_ATTRS = {"high": "HighEndThreadLocation", "low": "LowEndThreadLocation"}


# --- small validators ------------------------------------------------------------------------


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _typed(
    spec: Any, param: str, choices: tuple[str, ...], fields: dict[str, tuple[str, ...]]
) -> tuple[dict[str, Any], str]:
    """``(spec, type)`` of a discriminated-union object: known ``type``, no unknown fields,
    every field of that type given."""
    if not isinstance(spec, dict):
        raise _invalid(
            f"parameter '{param}' must be an object with 'type' ({', '.join(choices)})",
            param=param,
        )
    kind = check_choice(f"{param}.type", spec.get("type"), choices)
    allowed = {"type", *fields[kind]}
    unknown = sorted(k for k in spec if k not in allowed and spec[k] is not None)
    if unknown:
        raise _invalid(
            f"parameter '{param}' of type '{kind}' has unknown fields {unknown}", param=param
        )
    for name in fields[kind]:
        if spec.get(name) is None:
            raise _invalid(
                f"parameter '{param}' of type '{kind}' needs '{name}'", param=f"{param}.{name}"
            )
    return spec, kind


def _positive(value: Any, param: str) -> None:
    """A plain number must be > 0 (an expression is left to Fusion)."""
    if _number(value) and not value > 0:
        raise _invalid(f"parameter '{param}' must be greater than 0", param=param, value=value)


def _angle_range(value: Any, param: str, upper_inclusive: bool) -> None:
    """A plain angle in degrees must be in (0, 180) (or (0, 180] for a tip angle)."""
    if not _number(value):
        return
    ok = 0 < value <= 180 if upper_inclusive else 0 < value < 180
    if not ok:
        bound = "(0, 180]" if upper_inclusive else "(0, 180)"
        raise _invalid(f"parameter '{param}' must be in {bound} degrees", param=param, value=value)


def _larger(big: Any, small: Any, big_name: str, small_name: str) -> None:
    """Both plain numbers (same units) -> ``big`` must exceed ``small``."""
    if _number(big) and _number(small) and not big > small:
        raise _invalid(
            f"parameter '{big_name}' must be larger than '{small_name}'",
            param=big_name,
            value=big,
            limit=small,
        )


def _flag(mapping: dict[str, Any], name: str, default: bool, where: str) -> bool:
    value = mapping.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise _invalid(f"parameter '{where}{name}' must be a boolean", param=f"{where}{name}")
    return value


def _text(mapping: dict[str, Any], name: str, where: str) -> str | None:
    value = mapping.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid(
            f"parameter '{where}{name}' must be a non-empty string", param=f"{where}{name}"
        )
    return value


def _items(collection: Any) -> list[Any]:
    if collection is None:
        return []
    if hasattr(collection, "count") and hasattr(collection, "item"):
        return [collection.item(i) for i in range(collection.count)]
    return list(collection)


def _token(entity: Any) -> Any:
    try:
        token = entity.entityToken
    except Exception:
        token = None
    return token if isinstance(token, str) and token else id(entity)


# --- hole (D15) ------------------------------------------------------------------------------


@dataclass
class _Placement:
    setter: str
    args: tuple[Any, ...]
    anchor: Any  # entity whose component gets the hole (D7)
    count: int


def _planar(ctx: Any, ref: Any, param: str) -> Any:
    entity = resolve_one(ctx, ref, _PLANAR_TYPES, param)
    return require_kind(entity, "planar", param)


def _linear_edge(ctx: Any, ref: Any, param: str) -> Any:
    edge = resolve_one(ctx, ref, ("BRepEdge",), param)
    return require_kind(edge, "linear", param)


def _circular_edge(ctx: Any, ref: Any, param: str) -> Any:
    edge = resolve_one(ctx, ref, ("BRepEdge",), param)
    actual = describe_kind(edge)
    if actual not in _CIRCULAR_EDGES:
        raise _invalid(
            f"parameter '{param}' must be a circular edge (circle or arc), got {actual}",
            param=param,
            expected=list(_CIRCULAR_EDGES),
            actual=actual,
        )
    return edge


def _placement(ctx: Any, raw: Any) -> _Placement:
    spec, kind = _typed(raw, "placement", HOLE_PLACEMENTS, _PLACEMENT_FIELDS)
    if kind == "sketch_points":
        points = resolve_many(ctx, spec["points"], ("SketchPoint",), "placement.points")
        sketch = _token(getattr(points[0], "parentSketch", None))
        for index, point in enumerate(points[1:], start=1):
            if _token(getattr(point, "parentSketch", None)) != sketch:
                raise _invalid(
                    f"parameter 'placement.points[{index}]' is in another sketch; all hole "
                    "points must be in one sketch",
                    param="placement.points",
                    index=index,
                    reason="several_sketches",
                )
        if len(points) == 1:
            return _Placement("setPositionBySketchPoint", (points[0],), points[0], 1)
        collection = object_collection(points)
        return _Placement("setPositionBySketchPoints", (collection,), points[0], len(points))
    face = _planar(ctx, spec["face"], "placement.face")
    if kind == "center":
        edge = _circular_edge(ctx, spec["edge"], "placement.edge")
        return _Placement("setPositionAtCenter", (face, edge), face, 1)
    point = point3d(ctx, spec["point"], param="placement.point")
    if kind == "point":
        return _Placement("setPositionByPoint", (face, point), face, 1)
    edge1 = _linear_edge(ctx, spec["edge1"], "placement.edge1")
    edge2 = _linear_edge(ctx, spec["edge2"], "placement.edge2")
    if _token(edge1) == _token(edge2):
        raise _invalid(
            "parameters 'placement.edge1' and 'placement.edge2' must be different edges",
            param="placement.edge2",
            reason="same_edge",
        )
    offset1 = length_value(ctx, spec["offset1"], param="placement.offset1")
    offset2 = length_value(ctx, spec["offset2"], param="placement.offset2")
    return _Placement(
        "setPositionByPlaneAndOffsets", (face, point, edge1, offset1, edge2, offset2), face, 1
    )


def _hole_kind(ctx: Any, params: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    """``(create method, arguments)`` of the hole type, every type field checked."""
    kind = check_choice("kind", _opt(params, "kind", "simple"), HOLE_KINDS)
    for other, names in _KIND_FIELDS.items():
        for name in names:
            if other != kind and params.get(name) is not None:
                raise _invalid(
                    f"parameter '{name}' applies only to kind '{other}', not '{kind}'",
                    param=name,
                    kind=kind,
                )
    diameter_raw = params.get("diameter")
    if diameter_raw is None:
        raise _invalid("missing required parameter 'diameter'", param="diameter")
    _positive(diameter_raw, "diameter")
    diameter = length_value(ctx, diameter_raw, param="diameter")
    if kind == "simple":
        return _KIND_METHODS[kind], (diameter,)
    if kind == "counterbore":
        for name in ("cb_diameter", "cb_depth"):
            if params.get(name) is None:
                raise _invalid(f"kind 'counterbore' needs '{name}'", param=name, kind=kind)
            _positive(params[name], name)
        _larger(params["cb_diameter"], diameter_raw, "cb_diameter", "diameter")
        cb_diameter = length_value(ctx, params["cb_diameter"], param="cb_diameter")
        cb_depth = length_value(ctx, params["cb_depth"], param="cb_depth")
        return _KIND_METHODS[kind], (diameter, cb_diameter, cb_depth)
    if params.get("cs_diameter") is None:
        raise _invalid("kind 'countersink' needs 'cs_diameter'", param="cs_diameter", kind=kind)
    _positive(params["cs_diameter"], "cs_diameter")
    _larger(params["cs_diameter"], diameter_raw, "cs_diameter", "diameter")
    cs_angle_raw = _opt(params, "cs_angle", DEFAULT_CS_ANGLE)
    _angle_range(cs_angle_raw, "cs_angle", upper_inclusive=False)
    cs_diameter = length_value(ctx, params["cs_diameter"], param="cs_diameter")
    cs_angle = angle_value(ctx, cs_angle_raw, "cs_angle")
    return _KIND_METHODS[kind], (diameter, cs_diameter, cs_angle)


def _hole_extent(ctx: Any, raw: Any) -> tuple[str, tuple[Any, ...]]:
    if raw is None:
        raise _invalid("missing required parameter 'extent'", param="extent")
    spec, kind = _typed(raw, "extent", HOLE_EXTENT_TYPES, _EXTENT_FIELDS)
    if kind == "distance":
        _positive(spec["depth"], "extent.depth")
        return "setDistanceExtent", (length_value(ctx, spec["depth"], param="extent.depth"),)
    if kind == "through_all":
        return "setAllExtent", (adsk.fusion.ExtentDirections.PositiveExtentDirection,)
    entity = resolve_one(ctx, spec["entity"], EXTENT_ENTITY_TYPES, "extent.entity")
    return "setOneSideToExtent", (entity, False)


def _bore_faces(hole: Any) -> tuple[list[Any], float]:
    """Cylindrical side faces of the smallest radius (the bore, not a counterbore) and that
    radius in cm; ``FUSION_API_ERROR`` if the hole has none."""
    cylinders = [
        f for f in _items(getattr(hole, "sideFaces", None)) if describe_kind(f) == _CYLINDER
    ]
    if not cylinders:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            "the hole has no cylindrical side face to put the thread on",
            {"feature_type": "HoleFeature"},
        )
    radius = min(float(f.geometry.radius) for f in cylinders)
    bore = [f for f in cylinders if math.isclose(float(f.geometry.radius), radius, abs_tol=1e-6)]
    return bore, radius


@handler("feature.hole", mutating=True)
def feature_hole(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Simple / counterbore / countersink holes (D15), optionally tapped (``thread``, D16);
    result per D8 plus ``holes`` and ``thread``."""
    method, kind_args = _hole_kind(ctx, params)
    tip_raw = _opt(params, "tip_angle", DEFAULT_TIP_ANGLE)
    _angle_range(tip_raw, "tip_angle", upper_inclusive=True)
    tip_angle = angle_value(ctx, tip_raw, "tip_angle")
    placement = _placement(ctx, ctx.require(params, "placement", dict))
    extent_setter, extent_args = _hole_extent(ctx, params.get("extent"))
    flip = ctx.require(params, "flip", bool, False)
    bodies = participant_bodies(ctx, params.get("participant_bodies"))
    component = target_component(placement.anchor)
    thread: tuple[_ThreadPlan, Any] | None = None
    if params.get("thread") is not None:
        thread = _hole_thread_plan(ctx, params["thread"], component)

    before = _snapshot(component, bodies)
    holes = component.features.holeFeatures
    inp = getattr(holes, method)(*kind_args)
    inp.tipAngle = tip_angle
    _call("HoleFeature", inp, placement.setter, *placement.args)
    _call("HoleFeature", inp, extent_setter, *extent_args)
    inp.isDefaultDirection = not flip
    if bodies is not None:
        inp.participantBodies = list(bodies)
    feature = holes.add(inp)
    created = [feature]
    try:
        result = feature_result(ctx, feature, before, operation="cut")
        result["holes"] = placement.count
        if thread is not None:
            plan, query = thread
            result["thread"] = _tap(ctx, component, feature, plan, query, created)
    except Exception:
        if not _parametric(ctx):
            for item in reversed(created):
                if item is not None:
                    with contextlib.suppress(Exception):
                        item.deleteMe()
        raise
    return result


def _hole_thread_plan(ctx: Any, spec: Any, component: Any) -> tuple[_ThreadPlan, Any]:
    """Validated ``thread`` of a hole (always internal) and the ``ThreadDataQuery``; the type,
    a given designation and a given class are checked before the hole is added."""
    if not isinstance(spec, dict):
        raise _invalid("parameter 'thread' must be an object (ThreadSpec)", param="thread")
    unknown = sorted(set(spec) - _THREAD_FIELDS)
    if unknown:
        raise _invalid(f"parameter 'thread' has unknown fields {unknown}", param="thread")
    plan = _thread_plan(ctx, spec, "thread.")
    query = _thread_query(component)
    _check_type(query, plan.thread_type, "thread.thread_type")
    if plan.designation is not None:
        _find_designation(query, plan.thread_type, plan.designation, "thread.designation")
        _check_class(query, True, plan, plan.designation, plan.thread_class, "thread.")
    return plan, query


def _tap(
    ctx: Any, component: Any, hole: Any, plan: _ThreadPlan, query: Any, created: list[Any]
) -> dict[str, Any]:
    """Thread on the bore of ``hole`` (D15); raises on any failure (the caller rolls back)."""
    faces, radius = _bore_faces(hole)
    designation, thread_class = _thread_data(ctx, query, plan, True, 2 * radius, "thread.")
    before = BodySnapshot.take([component])
    feature = _add_thread(component, faces, plan, True, designation, thread_class)
    created.append(feature)
    result = feature_result(ctx, feature, before, operation="join")
    info = _thread_info(plan, designation, thread_class, True)
    info["feature"] = result["feature"]
    info["health"] = result["health"]
    info["faces"] = len(faces)
    return info


# --- thread (D16) ----------------------------------------------------------------------------


@dataclass
class _ThreadPlan:
    thread_type: str
    designation: str | None
    thread_class: str | None
    modeled: bool
    full_length: bool
    length: Any  # ValueInput or None (full length)
    offset: Any  # ValueInput or None (full length)
    location: Any  # adsk.fusion.ThreadLocations member


def _thread_plan(ctx: Any, spec: dict[str, Any], where: str) -> _ThreadPlan:
    """ThreadSpec fields of ``spec`` (names prefixed by ``where`` in errors), validated."""
    thread_type = _text(spec, "thread_type", where) or DEFAULT_THREAD_TYPE
    designation = _text(spec, "designation", where)
    thread_class = _text(spec, "thread_class", where)
    modeled = _flag(spec, "modeled", False, where)
    full_length = _flag(spec, "full_length", True, where)
    location_name = check_choice(
        f"{where}location", _opt(spec, "location", "high"), THREAD_LOCATIONS
    )
    location = getattr(adsk.fusion.ThreadLocations, _LOCATION_ATTRS[location_name])
    length = offset = None
    if full_length:
        if spec.get("length") is not None:
            raise _invalid(
                f"parameter '{where}length' needs full_length=false", param=f"{where}length"
            )
        if not _is_zero(spec.get("offset")):
            raise _invalid(
                f"parameter '{where}offset' needs full_length=false", param=f"{where}offset"
            )
    else:
        if spec.get("length") is None:
            raise _invalid(f"full_length=false needs '{where}length'", param=f"{where}length")
        _positive(spec["length"], f"{where}length")
        length = length_value(ctx, spec["length"], param=f"{where}length")
        offset = length_value(ctx, _opt(spec, "offset", 0), param=f"{where}offset")
    return _ThreadPlan(
        thread_type, designation, thread_class, modeled, full_length, length, offset, location
    )


def _thread_query(component: Any) -> Any:
    threads = getattr(component.features, "threadFeatures", None)
    query = getattr(threads, "threadDataQuery", None) if threads is not None else None
    if query is None:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            "ThreadFeatures.threadDataQuery is not available in this Fusion API version",
            {"feature_type": "ThreadFeature"},
        )
    return query


def _strings(values: Any) -> list[str]:
    return [str(v) for v in (values or [])]


def _check_type(query: Any, thread_type: str, param: str) -> None:
    types = _strings(query.allThreadTypes)
    if thread_type not in types:
        raise _invalid(
            f"unknown thread type {thread_type!r}",
            param=param,
            value=thread_type,
            available=types[:MAX_AVAILABLE],
        )


def _find_designation(query: Any, thread_type: str, designation: str, param: str) -> str:
    """Size that offers ``designation``; else ``INVALID_PARAMS`` with ``details.available``."""
    seen: list[str] = []
    for size in _strings(query.allSizes(thread_type)):
        designations = _strings(query.allDesignations(thread_type, size))
        if designation in designations:
            return size
        seen.extend(designations)
    raise _invalid(
        f"thread type {thread_type!r} has no designation {designation!r}",
        param=param,
        value=designation,
        thread_type=thread_type,
        available=seen[:MAX_AVAILABLE],
    )


def _check_class(
    query: Any,
    internal: bool,
    plan: _ThreadPlan,
    designation: str,
    thread_class: str | None,
    where: str,
) -> str:
    """``thread_class`` if Fusion offers it for ``designation`` (or the first class when it is
    ``None``); else ``INVALID_PARAMS`` with ``details.available``."""
    side = "internal" if internal else "external"
    classes = _strings(query.allClasses(internal, plan.thread_type, designation))
    if not classes:
        raise _invalid(
            f"designation {designation!r} has no {side} thread classes",
            param=f"{where}designation",
            value=designation,
            internal=internal,
        )
    if thread_class is None:
        return classes[0]
    if thread_class not in classes:
        raise _invalid(
            f"designation {designation!r} has no {side} class {thread_class!r}",
            param=f"{where}thread_class",
            value=thread_class,
            internal=internal,
            available=classes[:MAX_AVAILABLE],
        )
    return thread_class


def _recommend(
    query: Any, diameter_cm: float, internal: bool, thread_type: str
) -> tuple[bool, str, str]:
    """``recommendThreadData`` -> ``(found, designation, class)``."""
    answer = query.recommendThreadData(diameter_cm, internal, thread_type)
    ok, designation, thread_class = answer[0], answer[1], answer[2]
    return bool(ok) and bool(designation), str(designation or ""), str(thread_class or "")


def _thread_data(
    ctx: Any,
    query: Any,
    plan: _ThreadPlan,
    internal: bool,
    diameter_cm: float | None,
    where: str,
) -> tuple[str, str]:
    """``(designation, class)``: the given ones checked, or Fusion's recommendation for
    ``diameter_cm`` (D16)."""
    designation = plan.designation
    thread_class = plan.thread_class
    if designation is None:
        if diameter_cm is None:
            raise _invalid(
                f"parameter '{where}designation' is required", param=f"{where}designation"
            )
        ok, designation, recommended = _recommend(query, diameter_cm, internal, plan.thread_type)
        if not ok:
            raise _invalid(
                f"no {plan.thread_type!r} thread fits a diameter of "
                f"{ctx.units.length_out(diameter_cm):g} {ctx.units.units}; pass a designation "
                "(see feature_thread_options)",
                param=f"{where}designation",
                reason="no_recommendation",
                diameter=ctx.units.length_out(diameter_cm),
                units=ctx.units.units,
                internal=internal,
            )
        thread_class = thread_class or recommended or None
    else:
        _find_designation(query, plan.thread_type, designation, f"{where}designation")
    return designation, _check_class(query, internal, plan, designation, thread_class, where)


def _add_thread(
    component: Any,
    faces: list[Any],
    plan: _ThreadPlan,
    internal: bool,
    designation: str,
    thread_class: str,
) -> Any:
    threads = component.features.threadFeatures
    info = threads.createThreadInfo(internal, plan.thread_type, designation, thread_class)
    if info is None:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            "Fusion rejected the thread data (createThreadInfo)",
            {"feature_type": "ThreadFeature", "designation": designation, "class": thread_class},
        )
    inp = threads.createInput(object_collection(faces), info)
    inp.isModeled = plan.modeled
    inp.isFullLength = plan.full_length
    if not plan.full_length:
        inp.threadLength = plan.length
        inp.threadOffset = plan.offset
    inp.threadLocation = plan.location
    return threads.add(inp)


def _thread_info(
    plan: _ThreadPlan, designation: str, thread_class: str, internal: bool
) -> dict[str, Any]:
    return {
        "type": plan.thread_type,
        "designation": designation,
        "class": thread_class,
        "internal": internal,
        "modeled": plan.modeled,
    }


def check_cylinder(face: Any, param: str = "faces", index: int | None = None) -> Any:
    """``face`` if it is a cylindrical ``BRepFace``; else ``INVALID_PARAMS``."""
    actual = describe_kind(face)
    if actual != _CYLINDER:
        where = param if index is None else f"{param}[{index}]"
        details: dict[str, Any] = {"param": param, "expected": [_CYLINDER], "actual": actual}
        if index is not None:
            details["index"] = index
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"parameter '{where}' must be a cylindrical face, got {actual}",
            details,
        )
    return face


def is_internal_face(face: Any, param: str = "faces", index: int | None = None) -> bool:
    """Whether a cylindrical face is a hole wall (D16): its normal at ``pointOnFace`` points
    towards the cylinder axis (``geometry.axis`` through ``geometry.origin``). A
    non-cylindrical face, or a face whose normal Fusion cannot evaluate, is
    ``INVALID_PARAMS``."""
    check_cylinder(face, param, index)
    geometry = face.geometry
    point = face.pointOnFace
    ok, normal = face.evaluator.getNormalAtPoint(point)
    if not ok or normal is None:
        raise _invalid(
            "cannot tell whether the face is a hole wall or a shaft; pass 'internal'",
            param=param,
            reason="orientation_unknown",
        )
    axis = geometry.axis.copy()
    axis.normalize()
    radial = geometry.origin.vectorTo(point)
    along = axis.copy()
    along.scaleBy(radial.dotProduct(axis))
    radial.subtract(along)  # from the axis out to the point
    return bool(normal.dotProduct(radial) < 0)


def _orientation(faces: list[Any]) -> bool:
    flags = [is_internal_face(f, "faces", i) for i, f in enumerate(faces)]
    if len(set(flags)) > 1:
        raise _invalid(
            "parameter 'faces' mixes hole walls and shafts; thread them in separate calls or "
            "pass 'internal'",
            param="faces",
            reason="mixed_internal_external",
        )
    return flags[0]


def _common_diameter(faces: list[Any]) -> float:
    radii = [float(f.geometry.radius) for f in faces]
    if any(not math.isclose(r, radii[0], abs_tol=1e-6) for r in radii):
        raise _invalid(
            "parameter 'faces' has cylinders of different diameters; pass 'designation' or "
            "thread them in separate calls",
            param="faces",
            reason="different_diameters",
        )
    return 2 * radii[0]


@handler("feature.thread", mutating=True)
def feature_thread(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Cosmetic or modeled thread on cylindrical faces (D16); result per D8 plus ``thread``."""
    faces = resolve_many(ctx, ctx.require(params, "faces", list), ("BRepFace",), "faces")
    for index, face in enumerate(faces):
        check_cylinder(face, "faces", index)
    explicit = ctx.require(params, "internal", bool, None)
    plan = _thread_plan(ctx, params, "")
    internal = explicit if explicit is not None else _orientation(faces)
    component = target_component(faces[0])
    query = _thread_query(component)
    _check_type(query, plan.thread_type, "thread_type")
    diameter = _common_diameter(faces) if plan.designation is None else None
    designation, thread_class = _thread_data(ctx, query, plan, internal, diameter, "")

    before = _snapshot(component, None)
    feature = _add_thread(component, faces, plan, internal, designation, thread_class)
    result = _finish(ctx, feature, before, "join")
    result["thread"] = _thread_info(plan, designation, thread_class, internal)
    return result


def _diameter_cm(ctx: Any, value: Any) -> float:
    if _number(value):
        if not math.isfinite(value) or value <= 0:
            raise _invalid("parameter 'diameter' must be greater than 0", param="diameter")
        return float(length_to_internal(value, ctx.units.units))
    if not isinstance(value, str) or not value.strip():
        raise _invalid(
            "parameter 'diameter' must be a number or a Fusion expression", param="diameter"
        )
    try:
        cm = float(ctx.design.unitsManager.evaluateExpression(value, ctx.units.units))
    except Exception as exc:
        raise _invalid(
            f"parameter 'diameter': cannot evaluate {value!r}: {exc}",
            param="diameter",
            reason="invalid_expression",
        ) from None
    if cm <= 0:
        raise _invalid("parameter 'diameter' must be greater than 0", param="diameter")
    return cm


@handler("feature.thread_options", mutating=False)
def feature_thread_options(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Read-only ``ThreadDataQuery`` lookup (D16): types, sizes, designations, classes or a
    recommendation for a diameter."""
    thread_type = _text(params, "thread_type", "")
    size = _text(params, "size", "")
    designation = _text(params, "designation", "")
    internal = ctx.require(params, "internal", bool, False)
    diameter = params.get("diameter")
    if diameter is not None and (size is not None or designation is not None):
        raise _invalid(
            "parameter 'diameter' cannot be combined with 'size' or 'designation'",
            param="diameter",
        )
    query = _thread_query(ctx.root)
    if thread_type is None:
        if size is None and designation is None and diameter is None:
            return {"thread_types": _strings(query.allThreadTypes)}
        thread_type = DEFAULT_THREAD_TYPE
    _check_type(query, thread_type, "thread_type")
    out: dict[str, Any] = {"thread_type": thread_type}
    if diameter is not None:
        cm = _diameter_cm(ctx, diameter)
        ok, found, found_class = _recommend(query, cm, internal, thread_type)
        out.update(
            internal=internal,
            diameter=ctx.units.length_out(cm),
            units=ctx.units.units,
            recommended={"designation": found, "class": found_class} if ok else None,
        )
        return out
    if size is not None:
        sizes = _strings(query.allSizes(thread_type))
        if size not in sizes:
            raise _invalid(
                f"thread type {thread_type!r} has no size {size!r}",
                param="size",
                value=size,
                available=sizes[:MAX_AVAILABLE],
            )
    if designation is not None:
        if size is not None:
            offered = _strings(query.allDesignations(thread_type, size))
            if designation not in offered:
                raise _invalid(
                    f"size {size!r} has no designation {designation!r}",
                    param="designation",
                    value=designation,
                    available=offered[:MAX_AVAILABLE],
                )
        else:
            size = _find_designation(query, thread_type, designation, "designation")
        out.update(
            size=size,
            designation=designation,
            internal=internal,
            classes=_strings(query.allClasses(internal, thread_type, designation)),
        )
        return out
    if size is not None:
        out.update(size=size, designations=_strings(query.allDesignations(thread_type, size)))
        return out
    out["sizes"] = _strings(query.allSizes(thread_type))
    return out

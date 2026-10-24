"""``sheet_metal.*``: capability report, rules, sheet metal bodies (convert, base / contour /
edge flange), bends, unfold / refold and the flat pattern with its DXF / STEP export (c13).

Every mutating handler follows the c07 order (INV-5): parameters are read, refs resolved,
Fusion values built and preview API availability checked first (``INVALID_PARAMS`` /
``api_unavailable`` with no side effects), then the rule (if any) is applied and the feature
added. Timeline items are rolled back by the dispatcher (c02 D5); rules, active rule changes
and flat patterns are no timeline items and are undone by a ``Compensation`` (c09) in the
handler. Preview API calls go through ``core.sheet_metal_api`` only (D1).

Lengths are in the request ``units`` (default mm), angles in degrees; answers give lengths in
mm with the unit in the key (INV-6). Rules are addressed by name and source, flat patterns by
their component (INV-7).
"""

from __future__ import annotations

import contextlib
from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.exchange import EXTENSIONS, FILE_UNITS, detect_format
from fusion_mcp.protocol.features import check_choice
from fusion_mcp.protocol.sheet_metal import (
    EDGE_WIDTH_TYPES,
    FLANGE_DIRECTIONS,
    FLANGE_OPERATIONS,
    FLAT_EXPORT_FORMATS,
    RULE_SOURCES,
)
from fusion_mcp.protocol.units import length_from_internal, length_to_internal

from ..core import sheet_metal_api as api
from ..core.compensate import Compensation
from ..core.entity_kinds import is_planar
from ..core.exchange import run_export
from ..core.features import (
    EXTENT_ENTITY_TYPES,
    BodySnapshot,
    angle_value,
    feature_profiles,
    feature_result,
    length_value,
    operation_enum,
    resolve_many,
    resolve_one,
    target_component,
)
from ..core.refs import same_entity, type_name
from ..core.registry import handler
from ..core.sheet_metal import (
    OUT,
    RULE_VALUES,
    active_rule_name,
    apply_rule,
    check_k_factor,
    component_ref,
    default_library_rule,
    delete_entity,
    describe_flat_pattern,
    describe_rule,
    find_rule,
    flat_pattern_of,
    no_flat_pattern,
    resolve_component,
    rules_of,
    set_rule_value,
    sheet_metal_enum,
)
from ..core.surfaces import OPEN_PROFILE_TYPES, open_profile
from .export import DISTANCE_UNITS
from .feature_sketch import _bool, _given, _invalid, _opt, _snapshot

FACE = ("BRepFace",)
EDGE = ("BRepEdge",)
BODY = ("BRepBody",)
DESIGN_WORKSPACE = "FusionSolidEnvironment"
DEFAULT_POLYLINE_TOLERANCE_MM = 0.1

# Preview API the sheet metal tools use (D8, design R1).
PREVIEW_APIS = (
    "FlangeFeatures.createBaseFlangeInput",
    "FlangeFeatures.createContourFlangeInput",
    "FlangeFeatures.createEdgeFlangeInput",
    "UnfoldFeatures.createInput",
    "RefoldFeatures.createInput",
    "BRepBody.convertToSheetMetal",
    "BRepBody.getBendFaces",
    "ExportManager.createSTEPExportOptionsForFlatPattern",
)
NOTES = (
    "sketch_bend is always false: the Fusion API has no Bend-along-a-sketch-line feature; "
    "model the bend with sheet_metal_edge_flange or sheet_metal_contour_flange",
)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _api_error(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.FUSION_API_ERROR, message, details)


def _is_sheet_metal(body: Any) -> bool:
    return _get(body, "isSheetMetal", False) is True


def _require_sheet_metal(body: Any, param: str) -> None:
    if not _is_sheet_metal(body):
        raise _invalid(
            f"parameter '{param}' must be (on) a sheet metal body; convert the body with "
            "sheet_metal_convert or start with sheet_metal_base_flange / contour_flange",
            param=param,
            reason="not_sheet_metal",
            body=_get(body, "name"),
        )


def _sheet_metal_result(
    ctx: Any,
    component: Any,
    feature: Any,
    before: BodySnapshot,
    touched: list[Any] | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    """c07 ``feature_result`` + ``"rule"`` (active rule after the operation) and
    ``"is_sheet_metal"`` on every created / modified body (D5)."""
    touched = touched or []
    result = feature_result(ctx, feature, before, touched, operation=operation)
    after = BodySnapshot.take(before.components)
    flags: dict[str, bool] = {}
    for body in [*touched, *after.bodies.values(), *(_items(_get(feature, "bodies")))]:
        if body is not None:
            try:
                flags.setdefault(ctx.refs.to_ref(body), _is_sheet_metal(body))
            except Exception:
                continue
    for key in ("created_bodies", "modified_bodies"):
        for entry in result[key]:
            entry["is_sheet_metal"] = flags.get(entry["ref"], False)
    result["rule"] = active_rule_name(component)
    return result


def _items(collection: Any) -> list[Any]:
    if collection is None:
        return []
    if hasattr(collection, "count") and hasattr(collection, "item"):
        return [collection.item(i) for i in range(collection.count)]
    return list(collection)


def _rule_param(params: dict[str, Any]) -> tuple[Any, str]:
    """``(rule name or None, rule_source)`` of a flange / convert call."""
    source = check_choice("rule_source", _opt(params, "rule_source", "all"), RULE_SOURCES)
    rule = params.get("rule")
    if rule is not None and (not isinstance(rule, str) or not rule.strip()):
        raise _invalid("parameter 'rule' must be a rule name", param="rule")
    return rule, source


def _call(inp: Any, setter: str, *args: Any) -> None:
    """``inp.<setter>(*args)``; Fusion answers ``False`` for values it cannot use."""
    if getattr(inp, setter)(*args) is False:
        raise _api_error(f"Fusion rejected the sheet metal input ({setter})", setter=setter)


def _flange_operation(params: dict[str, Any]) -> tuple[str, Any]:
    operation = check_choice("operation", _opt(params, "operation", "new_body"), FLANGE_OPERATIONS)
    return operation, operation_enum(operation)


def _bend_radius(ctx: Any, params: dict[str, Any]) -> Any:
    return (
        length_value(ctx, params["bend_radius"], param="bend_radius")
        if _given(params, "bend_radius")
        else None
    )


def _set_bend_radius(inp: Any, radius: Any) -> None:
    if radius is None:
        return
    override = _get(inp, "bendRadiusOverride")
    if override is None:
        raise api.api_unavailable("FlangeFeatureInput.bendRadiusOverride")
    _call(override, "setOverride", radius)


# --- capabilities (D8) -----------------------------------------------------------------------


@handler("sheet_metal.capabilities")
def sheet_metal_capabilities(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Which sheet metal operations this Fusion API supports (D8); changes nothing."""
    design = ctx.design
    root = design.rootComponent
    features = _get(root, "features")
    flanges = _get(features, "flangeFeatures")
    unfolds = _get(features, "unfoldFeatures")
    refolds = _get(features, "refoldFeatures")
    body_api = _get(adsk.fusion, "BRepBody")
    manager = _get(design, "exportManager")
    return {
        "rules": api.has_api(design, "designSheetMetalRules")
        and api.has_api(design, "librarySheetMetalRules"),
        "flat_pattern": api.has_api(root, "createFlatPattern"),
        "flat_pattern_dxf": api.has_api(manager, "createDXFFlatPatternExportOptions"),
        "flat_pattern_step": api.has_api(manager, "createSTEPExportOptionsForFlatPattern"),
        "convert": api.has_api(body_api, "convertToSheetMetal"),
        "base_flange": api.has_api(flanges, "createBaseFlangeInput"),
        "contour_flange": api.has_api(flanges, "createContourFlangeInput"),
        "edge_flange": api.has_api(flanges, "createEdgeFlangeInput"),
        "list_bends": api.has_api(body_api, "getBendFaces"),
        "unfold": api.has_api(unfolds, "createInput"),
        "refold": api.has_api(refolds, "createInput"),
        "sketch_bend": False,
        "preview_apis": list(PREVIEW_APIS),
        "fusion_version": _get(ctx.app, "version"),
        "notes": list(NOTES),
    }


# --- rules (D3) ------------------------------------------------------------------------------


@handler("sheet_metal.list_rules")
def sheet_metal_list_rules(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Design and / or library rules with lengths in mm (D3)."""
    source = _opt(params, "source", "all")
    rules = [describe_rule(rule, where) for rule, where in rules_of(ctx, source)]
    return {"rules": rules, "count": len(rules)}


@handler("sheet_metal.create_rule", mutating=True)
def sheet_metal_create_rule(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Design rule copied from a base rule with the given values (D3); the copy is deleted
    when setting a value fails."""
    name = ctx.require(params, "name", str)
    if not name.strip():
        raise _invalid("parameter 'name' must not be empty", param="name")
    if _given(params, "base_rule"):
        base, base_source = find_rule(
            ctx, params["base_rule"], _opt(params, "base_source", "all"), "base_rule"
        )
    else:
        base, base_source = default_library_rule(ctx), "library"
    design_rules = ctx.design.designSheetMetalRules
    if design_rules.itemByName(name) is not None:
        raise _invalid(
            f"the design already has a sheet metal rule {name!r}", param="name", reason="exists"
        )
    k_factor = check_k_factor(params["k_factor"]) if _given(params, "k_factor") else None
    relief = (
        sheet_metal_enum("relief_shape", "relief_shape", params["relief_shape"])
        if _given(params, "relief_shape")
        else None
    )
    units = ctx.units.units

    with Compensation() as comp:
        rule = design_rules.addByCopy(base, name)
        if rule is None:
            raise _api_error(f"Fusion could not copy rule {base.name!r}")
        comp.add(lambda: delete_entity(rule), f"delete rule {name!r}")
        for key, attr in RULE_VALUES.items():
            if _given(params, key):
                set_rule_value(getattr(rule, attr), params[key], units, key)
        if k_factor is not None:
            rule.kFactor = k_factor
        if relief is not None:
            rule.reliefShape = relief
        result = {
            "rule": describe_rule(rule, "design"),
            "base": {"name": base.name, "source": base_source},
        }
    return result


@handler("sheet_metal.assign_rule", mutating=True)
def sheet_metal_assign_rule(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Make a rule the active rule of a component, copying a library rule into the design
    (D3); the previous rule is restored and the copy deleted on error."""
    component = resolve_component(ctx, params.get("component"))
    rule, source = _rule_param(params)
    if rule is None:
        raise _invalid("missing required parameter 'rule'", param="rule")
    with Compensation() as comp:
        info = apply_rule(ctx, component, rule, source, comp)
        result = {"component": component_ref(ctx, component), **info}
    return result


# --- convert (D4) ----------------------------------------------------------------------------


@handler("sheet_metal.convert", mutating=True)
def sheet_metal_convert(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Convert a solid body to sheet metal from a planar base face (D4, preview API)."""
    body = resolve_one(ctx, ctx.require(params, "body"), BODY, "body")
    if _is_sheet_metal(body):
        raise _invalid(
            "parameter 'body' is already a sheet metal body", param="body", reason="sheet_metal"
        )
    face = resolve_one(ctx, ctx.require(params, "base_face"), FACE, "base_face")
    if not same_entity(_get(face, "body"), body):
        raise _invalid(
            "parameter 'base_face' must be a face of 'body'", param="base_face", reason="other_body"
        )
    if not is_planar(face):
        raise _invalid(
            "parameter 'base_face' must be a planar face", param="base_face", reason="not_planar"
        )
    rule_name, source = _rule_param(params)
    api.require_api(body, "convertToSheetMetal", "BRepBody.convertToSheetMetal")
    component = target_component(body)

    with Compensation() as comp:
        if rule_name is not None:
            apply_rule(ctx, component, rule_name, source, comp)
        rule = _get(component, "activeSheetMetalRule") or default_library_rule(ctx)
        before = _snapshot(component, [body])
        if not api.convert_to_sheet_metal(body, face, rule):
            raise _api_error(
                "Fusion could not convert the body to sheet metal (BRepBody.convertToSheetMetal "
                "returned False)",
                body=_get(body, "name"),
            )
        after = BodySnapshot.take(before.components)
        converted = body if _get(body, "isValid", True) else None
        if converted is None:
            converted = next((b for t, b in after.bodies.items() if t not in before.tokens()), None)
        if converted is None:
            raise _api_error("the converted body was not found after the conversion")
        active = _get(component, "activeSheetMetalRule") or rule
        result = {
            "body": ctx.refs.to_ref(converted),
            "component": component_ref(ctx, component),
            "rule": describe_rule(active, "design"),
            "is_sheet_metal": _is_sheet_metal(converted),
        }
    return result


# --- flanges (D5) ----------------------------------------------------------------------------


def _first(entities: Any) -> Any:
    items = _items(entities) if isinstance(entities, adsk.core.ObjectCollection) else [entities]
    return items[0]


@handler("sheet_metal.base_flange", mutating=True)
def sheet_metal_base_flange(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Base flange from closed profiles; thickness from the active rule (D5, preview API)."""
    profiles = feature_profiles(ctx, ctx.require(params, "profiles"))
    profile_list = (
        _items(profiles) if isinstance(profiles, adsk.core.ObjectCollection) else [profiles]
    )
    orientation = sheet_metal_enum(
        "orientation", "orientation", _opt(params, "orientation", "side_one")
    )
    operation, op_enum = _flange_operation(params)
    rule_name, source = _rule_param(params)
    component = target_component(profile_list[0])
    inp = api.create_base_flange_input(component, profile_list)
    inp.orientation = orientation
    inp.operation = op_enum

    with Compensation() as comp:
        if rule_name is not None:
            apply_rule(ctx, component, rule_name, source, comp)
        before = _snapshot(component, None)
        feature = api.add_flange(component, inp)
        result = _sheet_metal_result(ctx, component, feature, before, operation=operation)
    return result


def _contour_profiles(ctx: Any, refs: Any) -> tuple[Any, Any]:
    """``(open profile(s), component)`` of a contour flange: profile refs as they are, sketch
    curves / edges through c11 ``open_profile``; the component is that of the first ref."""
    if not isinstance(refs, list) or not refs:
        raise _invalid(
            "parameter 'profiles' must be a non-empty list of sketch curve, edge or profile refs",
            param="profiles",
            reason="empty",
        )
    first = resolve_one(ctx, refs[0], ("Profile", *OPEN_PROFILE_TYPES), "profiles", 0)
    component = target_component(first)
    if type_name(first) == "Profile":
        profiles = resolve_many(ctx, refs, ("Profile",), "profiles")
        return (profiles[0] if len(profiles) == 1 else profiles), component
    return open_profile(ctx, refs, True, "profiles"), component


@handler("sheet_metal.contour_flange", mutating=True)
def sheet_metal_contour_flange(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Contour flange from an open profile, one-side / symmetric / two-sided (D5, preview)."""
    direction = check_choice("direction", _opt(params, "direction", "one_side"), FLANGE_DIRECTIONS)
    if not _given(params, "distance"):
        raise _invalid("missing required parameter 'distance'", param="distance")
    distance = length_value(ctx, params["distance"], param="distance")
    distance_two = None
    if direction == "two_sides":
        if not _given(params, "distance_two"):
            raise _invalid("direction 'two_sides' needs 'distance_two'", param="distance_two")
        distance_two = length_value(ctx, params["distance_two"], param="distance_two")
    elif _given(params, "distance_two"):
        raise _invalid(
            "parameter 'distance_two' applies only to direction 'two_sides'", param="distance_two"
        )
    orientation = sheet_metal_enum(
        "orientation", "orientation", _opt(params, "orientation", "side_one")
    )
    operation, op_enum = _flange_operation(params)
    radius = _bend_radius(ctx, params)
    rule_name, source = _rule_param(params)
    profile, component = _contour_profiles(ctx, params.get("profiles"))

    inp = api.create_contour_flange_input(component, profile, distance)
    if direction == "one_side":
        _call(inp, "setOneSideDirection", distance)
    elif direction == "symmetric":
        _call(inp, "setSymmetricDirection", distance)
    else:
        _call(inp, "setTwoSidesDirection", distance, distance_two)
    inp.orientation = orientation
    inp.operation = op_enum
    _set_bend_radius(inp, radius)

    with Compensation() as comp:
        if rule_name is not None:
            apply_rule(ctx, component, rule_name, source, comp)
        before = _snapshot(component, None)
        feature = api.add_flange(component, inp)
        result = _sheet_metal_result(ctx, component, feature, before, operation=operation)
    return result


def _width(ctx: Any, params: dict[str, Any]) -> tuple[str, list[Any]]:
    """``(width type, ValueInputs)`` of ``width`` (one for all edges)."""
    spec = _opt(params, "width", {"type": "full"})
    if not isinstance(spec, dict):
        raise _invalid("parameter 'width' must be an object with 'type'", param="width")
    kind = check_choice("width.type", spec.get("type"), EDGE_WIDTH_TYPES)
    fields = {
        "full": (),
        "symmetric": ("width",),
        "two_sided": ("width1", "width2"),
        "two_offsets": ("offset1", "offset2"),
    }[kind]
    unknown = set(spec) - {"type", *fields}
    if unknown:
        raise _invalid(
            f"parameter 'width' of type '{kind}' does not take {', '.join(sorted(unknown))}",
            param="width",
        )
    values = []
    for name in fields:
        if spec.get(name) is None:
            raise _invalid(f"width of type '{kind}' needs '{name}'", param=f"width.{name}")
        values.append(length_value(ctx, spec[name], param=f"width.{name}"))
    return kind, values


_WIDTH_METHODS = {
    "full": "addFullLengthEdge",
    "symmetric": "addSymmetricEdge",
    "two_sided": "addTwoSidedEdge",
    "two_offsets": "addTwoOffsetsEdge",
}


def _is_linear(edge: Any) -> bool:
    geometry = _get(edge, "geometry")
    kind = _get(geometry, "objectType", "")
    return isinstance(kind, str) and kind.endswith("Line3D")


@handler("sheet_metal.edge_flange", mutating=True)
def sheet_metal_edge_flange(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Edge flange on linear edges of one sheet metal body: height or to-entity extent,
    angle, width, height datum, bend position, mitered corners, flip, bend radius (D5)."""
    edges = resolve_many(ctx, ctx.require(params, "edges", list), EDGE, "edges")
    bodies: list[Any] = []
    for index, edge in enumerate(edges):
        if not _is_linear(edge):
            raise _invalid(
                f"parameter 'edges[{index}]' must be a linear edge",
                param="edges",
                index=index,
                reason="not_linear",
            )
        body = _get(edge, "body")
        if not any(same_entity(body, b) for b in bodies):
            bodies.append(body)
    if len(bodies) != 1:
        raise _invalid(
            "parameter 'edges' must be edges of one body", param="edges", reason="several_bodies"
        )
    body = bodies[0]
    _require_sheet_metal(body, "edges")

    has_height, has_entity = _given(params, "height"), _given(params, "to_entity")
    if has_height == has_entity:
        raise _invalid(
            "pass exactly one of 'height' and 'to_entity'", param="height", reason="extent"
        )
    height = length_value(ctx, params["height"], param="height") if has_height else None
    target = None
    offset = None
    if has_entity:
        if len(edges) > 1:
            raise _invalid(
                "'to_entity' works with a single edge", param="to_entity", count=len(edges)
            )
        target = resolve_one(
            ctx, params["to_entity"], (*EXTENT_ENTITY_TYPES, "BRepEdge"), "to_entity"
        )
        offset = length_value(ctx, _opt(params, "to_offset", 0), param="to_offset")
    elif _given(params, "to_offset"):
        raise _invalid("parameter 'to_offset' needs 'to_entity'", param="to_offset")
    angle = angle_value(ctx, _opt(params, "angle", 90), "angle")
    width_kind, width_values = _width(ctx, params)
    datum = sheet_metal_enum("height_datum", "height_datum", _opt(params, "height_datum", "outer"))
    position = sheet_metal_enum(
        "bend_position", "bend_position", _opt(params, "bend_position", "start_edge")
    )
    mitered = _bool(ctx, params, "mitered_corners", True)
    flip = _bool(ctx, params, "flip", False)
    radius = _bend_radius(ctx, params)
    component = target_component(edges[0])

    inp = api.create_edge_flange_input(component, [], height)
    definitions = _get(inp, "edgeDefinitions")
    if definitions is None:
        raise api.api_unavailable("EdgeFlangeFeatureInput.edgeDefinitions")
    add_edge = api.require_api(
        definitions,
        _WIDTH_METHODS[width_kind],
        f"EdgeFlangeEdgeDefinitions.{_WIDTH_METHODS[width_kind]}",
    )
    for edge in edges:
        if add_edge(edge, *width_values) is None:
            raise _api_error(
                f"Fusion rejected an edge of the edge flange ({_WIDTH_METHODS[width_kind]})",
                edge=ctx.refs.to_ref(edge),
            )
    if height is not None:
        _call(inp, "setDistanceExtent", height)
    else:
        _call(inp, "setToObjectExtent", target, offset)
    inp.angle = angle
    inp.heightDatumType = datum
    inp.bendPositionType = position
    inp.isMiteredCorners = mitered
    inp.isFlipped = flip
    _set_bend_radius(inp, radius)

    before = _snapshot(component, [body])
    feature = api.add_flange(component, inp)
    return _sheet_metal_result(ctx, component, feature, before, [body])


# --- bends, unfold, refold (D6) --------------------------------------------------------------


@handler("sheet_metal.list_bends")
def sheet_metal_list_bends(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Inner / outer bend face pairs of a sheet metal body with the inner radius in mm (D6)."""
    body = resolve_one(ctx, ctx.require(params, "body"), BODY, "body")
    _require_sheet_metal(body, "body")
    inner, outer = api.get_bend_faces(body)
    bends = []
    for inner_face, outer_face in zip(inner, outer, strict=False):
        radius = _get(_get(inner_face, "geometry"), "radius")
        bends.append(
            {
                "inner": ctx.refs.to_ref(inner_face),
                "outer": ctx.refs.to_ref(outer_face),
                "radius_mm": round(length_from_internal(float(radius), OUT), 6)
                if isinstance(radius, int | float)
                else None,
            }
        )
    return {"body": ctx.refs.to_ref(body), "bends": bends, "count": len(bends)}


@handler("sheet_metal.unfold", mutating=True)
def sheet_metal_unfold(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Unfold all bends or the given bend faces about a stationary face (D6, preview)."""
    face = resolve_one(ctx, ctx.require(params, "stationary_face"), FACE, "stationary_face")
    body = _get(face, "body")
    _require_sheet_metal(body, "stationary_face")
    bend_faces = None
    if params.get("bend_faces") is not None:
        bend_faces = resolve_many(ctx, params["bend_faces"], FACE, "bend_faces")
        for index, bend in enumerate(bend_faces):
            if not same_entity(_get(bend, "body"), body):
                raise _invalid(
                    f"parameter 'bend_faces[{index}]' is not a face of the stationary face's body",
                    param="bend_faces",
                    index=index,
                )
    component = target_component(face)
    inp = api.create_unfold_input(component, face)
    if bend_faces is None:
        inp.isUnfoldAllBends = True
    else:
        inp.isUnfoldAllBends = False
        inp.bendFaces = list(bend_faces)
    before = _snapshot(component, [body])
    feature = api.add_unfold(component, inp)
    return _sheet_metal_result(ctx, component, feature, before, [body])


@handler("sheet_metal.refold", mutating=True)
def sheet_metal_refold(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Refold the bends of an unfold feature (D6, preview)."""
    unfold = resolve_one(ctx, ctx.require(params, "unfold"), ("UnfoldFeature",), "unfold")
    component = target_component(unfold)
    inp = api.create_refold_input(component, unfold)
    bodies = [b for b in _items(_get(unfold, "bodies")) if b is not None]
    before = _snapshot(component, bodies)
    feature = api.add_refold(component, inp)
    return _sheet_metal_result(ctx, component, feature, before, bodies)


# --- flat pattern (D7) -----------------------------------------------------------------------


def _back_to_design(ctx: Any, design: Any) -> None:
    """``createFlatPattern`` switches to the flat pattern environment (R4): activate the design
    workspace again."""
    if ctx.app.activeProduct is design:
        return
    workspace = ctx.ui.workspaces.itemById(DESIGN_WORKSPACE)
    if workspace is None or workspace.activate() is False:
        raise _api_error("could not return to the design workspace after the flat pattern")


ATTRIBUTE_GROUP = "fusion_mcp"
ATTRIBUTE_FACE = "flat_pattern_face"


def _remember_face(component: Any, face: Any) -> None:
    """Keep the entity token of the stationary face in a component attribute (not a
    timeline item) so ``replace`` can recreate this flat pattern."""
    attributes = _get(component, "attributes")
    token = _get(face, "entityToken")
    if attributes is None or not isinstance(token, str) or not token:
        return
    attributes.add(ATTRIBUTE_GROUP, ATTRIBUTE_FACE, token)


def _forget_face(component: Any) -> None:
    """Drop the remembered face (the flat pattern it belonged to is gone)."""
    attributes = _get(component, "attributes")
    attribute = attributes.itemByName(ATTRIBUTE_GROUP, ATTRIBUTE_FACE) if attributes else None
    if attribute is not None:
        attribute.deleteMe()


def _remembered_face(ctx: Any, component: Any) -> Any:
    """The remembered stationary face of the component's flat pattern, or ``None``."""
    attributes = _get(component, "attributes")
    try:
        attribute = attributes.itemByName(ATTRIBUTE_GROUP, ATTRIBUTE_FACE) if attributes else None
        token = _get(attribute, "value")
        if not isinstance(token, str) or not token:
            return None
        found = ctx.design.findEntityByToken(token)
    except Exception:
        return None
    face = found[0] if found else None
    if face is None or type_name(face) != "BRepFace" or not _is_sheet_metal(_get(face, "body")):
        return None
    return face


@handler("sheet_metal.create_flat_pattern", mutating=True)
def sheet_metal_create_flat_pattern(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Flat pattern of a component from a stationary face of its sheet metal body (D7).

    A component has at most one flat pattern, so ``replace`` deletes the existing one first.
    To keep the call atomic (INV-5) the stationary face of every flat pattern this tool
    creates is remembered in a component attribute; if the new flat pattern fails, the old one
    is recreated from that face. A flat pattern whose face is unknown (made in the UI) or no
    longer resolvable is not replaced (``INVALID_PARAMS`` ``not_restorable``, nothing
    changed): delete it with ``sheet_metal.delete_flat_pattern`` first."""
    face = resolve_one(ctx, ctx.require(params, "stationary_face"), FACE, "stationary_face")
    _require_sheet_metal(_get(face, "body"), "stationary_face")
    replace = _bool(ctx, params, "replace", False)
    component = target_component(face)
    existing = flat_pattern_of(component)
    if existing is not None and not replace:
        raise _invalid(
            "the component already has a flat pattern: pass replace=true or delete it with "
            "sheet_metal_delete_flat_pattern",
            param="replace",
            reason="exists",
        )
    create = api.require_api(component, "createFlatPattern", "Component.createFlatPattern")
    old_face = None
    if existing is not None:
        old_face = _remembered_face(ctx, component)
        if old_face is None:
            raise _invalid(
                "the existing flat pattern cannot be restored if the new one fails (it was not "
                "created by this tool or its face changed): delete it with "
                "sheet_metal_delete_flat_pattern, then create the new one",
                param="replace",
                reason="not_restorable",
            )
    design = ctx.design

    with Compensation() as comp:
        if existing is not None:

            def restore_old() -> None:
                if create(old_face) is None:
                    raise RuntimeError("Fusion did not recreate the previous flat pattern")
                _remember_face(component, old_face)
                _back_to_design(ctx, design)

            delete_entity(existing)
            comp.add(restore_old, "recreate the previous flat pattern")
        flat = create(face)
        if flat is None:
            raise _api_error("Fusion did not create the flat pattern")
        comp.add(lambda: delete_entity(flat), "delete flat pattern")
        _remember_face(component, face)
        comp.add(lambda: _back_to_design(ctx, design), "return to the design workspace")
        _back_to_design(ctx, design)
        result = describe_flat_pattern(ctx, component, flat)
        result["replaced"] = existing is not None
    return result


@handler("sheet_metal.get_flat_pattern")
def sheet_metal_get_flat_pattern(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Size, thickness and bends of a component's flat pattern, or ``exists: false`` (D7)."""
    component = resolve_component(ctx, params.get("component"))
    flat = flat_pattern_of(component)
    if flat is None:
        return {"component": component_ref(ctx, component), "exists": False}
    return describe_flat_pattern(ctx, component, flat)


@handler("sheet_metal.delete_flat_pattern", mutating=True)
def sheet_metal_delete_flat_pattern(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Delete a component's flat pattern; none -> ``ENTITY_NOT_FOUND`` (D7)."""
    component = resolve_component(ctx, params.get("component"))
    flat = flat_pattern_of(component)
    if flat is None:
        raise no_flat_pattern(ctx, component)
    # Forget first: a stale face would let a later ``replace`` restore the wrong pattern,
    # a missing one only makes it refuse (``not_restorable``).
    with contextlib.suppress(Exception):
        _forget_face(component)
    delete_entity(flat)
    return {"component": component_ref(ctx, component), "deleted": True}


def _tolerance(ctx: Any, params: dict[str, Any]) -> float:
    value = params.get("polyline_tolerance")
    if value is None:
        return float(length_to_internal(DEFAULT_POLYLINE_TOLERANCE_MM, "mm"))
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise _invalid(
            "parameter 'polyline_tolerance' must be a positive number", param="polyline_tolerance"
        )
    return float(ctx.units.length(value))


@handler("sheet_metal.export_flat_pattern")
def sheet_metal_export_flat_pattern(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Export a component's flat pattern to DXF (stable API) or STEP (preview) through c12
    ``run_export`` (D7)."""
    component = resolve_component(ctx, params.get("component"))
    flat = flat_pattern_of(component)
    if flat is None:
        raise no_flat_pattern(ctx, component)
    path = params.get("path")
    if params.get("format") is None:
        fmt = detect_format(path if isinstance(path, str) else "", FLAT_EXPORT_FORMATS)
    else:
        fmt = check_choice("format", params["format"], FLAT_EXPORT_FORMATS)
    overwrite = _bool(ctx, params, "overwrite", False)
    manager = ctx.design.exportManager

    if fmt == "step":
        api.require_api(
            manager,
            "createSTEPExportOptionsForFlatPattern",
            "ExportManager.createSTEPExportOptionsForFlatPattern",
        )

        def options(target: str) -> Any:
            return api.create_flat_pattern_step_options(manager, target, flat)

    else:
        file_units = check_choice("file_units", _opt(params, "file_units", "mm"), FILE_UNITS)
        bend_lines = _bool(ctx, params, "bend_lines", True)
        extent_lines = _bool(ctx, params, "extent_lines", True)
        splines = _bool(ctx, params, "splines_to_polylines", False)
        tolerance = _tolerance(ctx, params)
        create = api.require_api(
            manager,
            "createDXFFlatPatternExportOptions",
            "ExportManager.createDXFFlatPatternExportOptions",
        )

        def options(target: str) -> Any:
            opts = create(target, flat)
            opts.units = getattr(adsk.fusion.DistanceUnits, DISTANCE_UNITS[file_units])
            opts.isCenterLinesExported = bend_lines
            opts.isExtentLinesExported = extent_lines
            opts.isSplineConvertedToPolyline = splines
            opts.convertToPolylineTolerance = tolerance
            return opts

    return run_export(
        ctx,
        options,
        path,
        overwrite,
        extensions=EXTENSIONS[fmt],
        fmt=fmt,
        scope=component_ref(ctx, component),
    )

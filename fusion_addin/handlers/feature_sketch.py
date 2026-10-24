"""``feature.*`` sketch-based solid features (c07 D10-D14): extrude, revolve, sweep, loft, rib
and web.

Every handler follows the same order (INV-5):

1. read and validate every parameter, resolve every ref and build every Fusion value
   (``ValueInput``, extent/start definitions) through ``core.features`` - nothing in the
   design changes yet, so a bad parameter fails with ``INVALID_PARAMS`` and no side effects;
2. ``BodySnapshot.take`` of the feature's component (D7) and of the participant bodies'
   components;
3. ``<x>Features.createInput``, the setters (a ``False`` answer -> ``FUSION_API_ERROR``),
   participant bodies, ``add``;
4. ``feature_result`` (D8): health per D9 - an ``error`` feature raises ``FUSION_API_ERROR``
   and the dispatcher rolls the timeline back (in a direct design the feature is deleted here).

Lengths are in the request ``units`` (default mm), angles in degrees, strings are Fusion
expressions; conversion happens only in ``core.features`` helpers (INV-6).
"""

from __future__ import annotations

import contextlib
from typing import Any

import adsk.core
import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import (
    DIRECTIONS,
    EXTRUDE_MODES,
    LOFT_END_CONDITIONS,
    RAIL_SCALINGS,
    RIB_DEPTH_TYPES,
    RIB_THICKNESS_LOCATIONS,
    SWEEP_ORIENTATIONS,
    THIN_LOCATIONS,
    check_choice,
)
from fusion_mcp.protocol.refs import is_origin_ref
from fusion_mcp.protocol.sketch_refs import is_profile_ref
from fusion_mcp.protocol.surfaces import SURFACE_OPERATIONS

from ..core.entity_kinds import describe_kind, is_face, is_planar, require_kind
from ..core.features import (
    BODY,
    EXTENT_ENTITY_TYPES,
    PATH_TYPES,
    BodySnapshot,
    angle_value,
    direction_enum,
    extent_definition,
    feature_profiles,
    feature_result,
    length_value,
    make_path,
    operation_enum,
    participant_bodies,
    resolve_many,
    resolve_one,
    scalar_value,
    start_definition,
    target_component,
)
from ..core.refs import type_name
from ..core.registry import handler
from ..core.surfaces import OPEN_PROFILE_TYPES, open_profile
from ..sketch.resolve import CURVE_TYPES

REVOLVE_EXTENT_TYPES = ("angle", "two_angles", "full", "to_entity")

_THIN_ATTRS = {"side1": "Side1", "side2": "Side2", "center": "Center"}


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _rejected(feature_type: str, setter: str) -> BridgeError:
    return BridgeError(
        ErrorCode.FUSION_API_ERROR,
        f"Fusion rejected the {feature_type} input ({setter})",
        {"feature_type": feature_type, "setter": setter},
    )


def _call(feature_type: str, inp: Any, setter: str, *args: Any) -> None:
    """``inp.<setter>(*args)``; Fusion answers ``False`` for inputs it cannot use."""
    if getattr(inp, setter)(*args) is False:
        raise _rejected(feature_type, setter)


def _first(entities: Any) -> Any:
    """First entity of ``feature_profiles``' answer (one object or an ``ObjectCollection``)."""
    if isinstance(entities, adsk.core.ObjectCollection):
        return entities.item(0)
    return entities


def _bool(ctx: Any, params: dict[str, Any], name: str, default: bool) -> bool:
    value: bool = ctx.require(params, name, bool, default)
    return value


def _given(params: dict[str, Any], name: str) -> bool:
    return params.get(name) is not None


def _opt(mapping: dict[str, Any], name: str, default: Any) -> Any:
    """``mapping[name]``, or ``default`` when it is absent or ``None``."""
    value = mapping.get(name)
    return default if value is None else value


def _only_in(params: dict[str, Any], name: str, mode: str, allowed: tuple[str, ...]) -> None:
    if _given(params, name) and mode not in allowed:
        raise _invalid(
            f"parameter '{name}' applies only to mode {' | '.join(allowed)}, not '{mode}'",
            param=name,
            mode=mode,
        )


def _snapshot(component: Any, bodies: list[Any] | None) -> BodySnapshot:
    components = [component]
    for body in bodies or []:
        with contextlib.suppress(Exception):
            components.append(target_component(body))
    return BodySnapshot.take(components)


def _parametric(ctx: Any) -> bool:
    try:
        return bool(ctx.design.designType == adsk.fusion.DesignTypes.ParametricDesignType)
    except Exception:
        return True


def _finish(
    ctx: Any,
    feature: Any,
    before: BodySnapshot,
    operation: str | None,
    touched: Any = (),
) -> dict[str, Any]:
    """D8 result (``touched``: bodies a modifier changed); on failure a direct design (no
    timeline rollback, c02 D5) loses the feature here."""
    try:
        return feature_result(ctx, feature, before, touched, operation=operation)
    except Exception:
        if feature is not None and not _parametric(ctx):
            with contextlib.suppress(Exception):
                feature.deleteMe()
        raise


def _is_solid(ctx: Any, params: dict[str, Any], operation: str) -> bool:
    """``is_solid`` (c11 D12, default true); a surface (``false``) only creates bodies, so
    ``operation`` must be ``new_body`` / ``new_component``."""
    is_solid = _bool(ctx, params, "is_solid", True)
    if not is_solid and operation not in SURFACE_OPERATIONS:
        raise _invalid(
            f"operation '{operation}' needs a solid; with is_solid=false use "
            f"{' | '.join(SURFACE_OPERATIONS)}",
            param="operation",
            value=operation,
            expected=list(SURFACE_OPERATIONS),
        )
    return is_solid


def _open_refs(ctx: Any, specs: Any) -> list[Any] | None:
    """The refs of ``specs`` if every one is a sketch curve or an edge (an open profile,
    c11 D12); ``None`` if none is; a mix with closed profiles / faces -> ``INVALID_PARAMS``."""
    spec_list = specs if isinstance(specs, list) else [specs]
    kinds: list[bool] = []
    for spec in spec_list:
        if isinstance(spec, dict) or not isinstance(spec, str) or is_profile_ref(spec):
            kinds.append(False)
            continue
        try:
            entity = ctx.refs.resolve(spec)
        except BridgeError as exc:
            exc.details.setdefault("param", "profiles")
            raise
        kinds.append(type_name(entity) in OPEN_PROFILE_TYPES)
    if not any(kinds):
        return None
    if not all(kinds):
        raise _invalid(
            "parameter 'profiles' mixes open curves / edges with closed profiles or faces",
            param="profiles",
            reason="mixed_profile",
        )
    return list(spec_list)


def _common(
    ctx: Any, params: dict[str, Any], allow_text: bool, allow_open: bool = False
) -> tuple[Any, str, Any, Any, bool, Any]:
    """``(profiles, operation, operation enum, participant bodies or None, is_solid,
    component)``, validated. With ``allow_open`` and ``is_solid = false`` sketch curves /
    edges become an open profile (c11 D12)."""
    operation = ctx.require(params, "operation", str, "new_body")
    op_enum = operation_enum(operation)
    is_solid = _is_solid(ctx, params, operation)
    specs = ctx.require(params, "profiles", (list, str, dict))
    open_refs = _open_refs(ctx, specs) if allow_open and not is_solid else None
    if open_refs is not None:
        profiles = open_profile(ctx, open_refs, True, "profiles")
        component = target_component(resolve_one(ctx, open_refs[0], OPEN_PROFILE_TYPES, "profiles"))
    else:
        profiles = feature_profiles(ctx, specs, allow_faces=True, allow_text=allow_text)
        component = target_component(_first(profiles))
    bodies = participant_bodies(ctx, params.get("participant_bodies"), operation)
    return profiles, operation, op_enum, bodies, is_solid, component


# --- extrude (D10) -------------------------------------------------------------------------


def _thin(ctx: Any, params: dict[str, Any], mode: str) -> tuple[Any, ...] | None:
    """``setThinExtrude`` arguments or ``None`` without ``thin``."""
    spec = params.get("thin")
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise _invalid(
            "parameter 'thin' must be an object {thickness, location?, thickness2?}", param="thin"
        )
    unknown = sorted(set(spec) - {"thickness", "location", "thickness2"})
    if unknown:
        raise _invalid(f"parameter 'thin' has unknown fields {unknown}", param="thin")
    if spec.get("thickness") is None:
        raise _invalid("parameter 'thin' needs 'thickness'", param="thin.thickness")
    location = check_choice("thin.location", _opt(spec, "location", "side1"), THIN_LOCATIONS)
    wall = getattr(adsk.fusion.ThinExtrudeWallLocation, _THIN_ATTRS[location])
    thickness = length_value(ctx, spec["thickness"], param="thin.thickness")
    if spec.get("thickness2") is None:
        return (wall, thickness)
    if mode != "two_sides":
        raise _invalid(
            "parameter 'thin.thickness2' applies only to mode 'two_sides'",
            param="thin.thickness2",
            mode=mode,
        )
    thickness2 = length_value(ctx, spec["thickness2"], param="thin.thickness2")
    return (wall, thickness, wall, thickness2)


def _extrude_extent(ctx: Any, params: dict[str, Any], mode: str) -> tuple[str, tuple[Any, ...]]:
    """``(setter, arguments)`` of the extent of ``mode``."""
    side1 = ctx.require(params, "side1", dict)
    for name, allowed in (
        ("side2", ("two_sides",)),
        ("taper2", ("two_sides",)),
        ("direction", ("one_side",)),
    ):
        _only_in(params, name, mode, allowed)
    full_length = _bool(ctx, params, "symmetric_full_length", False)
    if full_length:
        _only_in(params, "symmetric_full_length", mode, ("symmetric",))
    taper1 = angle_value(ctx, _opt(params, "taper1", 0), "taper1")

    if mode == "one_side":
        extent = extent_definition(ctx, side1, param="side1")
        direction = direction_enum(_opt(params, "direction", "positive"))
        return "setOneSideExtent", (extent, direction, taper1)
    if mode == "two_sides":
        side2 = ctx.require(params, "side2", dict)
        extent1 = extent_definition(ctx, side1, param="side1")
        extent2 = extent_definition(ctx, side2, param="side2")
        taper2 = angle_value(ctx, _opt(params, "taper2", 0), "taper2")
        return "setTwoSidesExtent", (extent1, extent2, taper1, taper2)
    if side1.get("type") != "distance":
        raise _invalid(
            "mode 'symmetric' needs side1 of type 'distance'",
            param="side1.type",
            value=side1.get("type"),
            expected=["distance"],
        )
    if side1.get("distance") is None:
        raise _invalid(
            "parameter 'side1' of type 'distance' needs 'distance'", param="side1.distance"
        )
    distance = length_value(ctx, side1["distance"], param="side1.distance")
    return "setSymmetricExtent", (distance, full_length, taper1)


@handler("feature.extrude", mutating=True)
def feature_extrude(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Extrude profiles / planar faces / sketch texts (D10), or open curves / edges as a
    surface (c11 D12); result per D8."""
    if not _bool(ctx, params, "is_solid", True) and _given(params, "thin"):
        raise _invalid("parameter 'thin' cannot be used with is_solid=false", param="thin")
    profiles, operation, op_enum, bodies, is_solid, component = _common(
        ctx, params, allow_text=True, allow_open=True
    )
    mode = check_choice("mode", _opt(params, "mode", "one_side"), EXTRUDE_MODES)
    setter, extent_args = _extrude_extent(ctx, params, mode)
    start = params.get("start")
    start_def = start_definition(ctx, start) if start is not None else None
    thin = _thin(ctx, params, mode)

    before = _snapshot(component, bodies)
    extrudes = component.features.extrudeFeatures
    inp = extrudes.createInput(profiles, op_enum)
    if not is_solid:
        inp.isSolid = False
    _call("ExtrudeFeature", inp, setter, *extent_args)
    if start_def is not None:
        inp.startExtent = start_def
    if thin is not None:
        _call("ExtrudeFeature", inp, "setThinExtrude", *thin)
    if bodies is not None:
        inp.participantBodies = list(bodies)
    feature = extrudes.add(inp)
    return _finish(ctx, feature, before, operation)


# --- revolve (D11) -------------------------------------------------------------------------


def _profile_normal(entity: Any) -> Any:
    """Normal of the plane ``entity`` (a profile / sketch text or a planar face) lies in, as a
    ``Vector3D``: sketch xDirection cross yDirection for sketch entities, the plane normal for
    a face."""
    native = getattr(entity, "nativeObject", None) or entity
    sketch = getattr(native, "parentSketch", None)
    if sketch is not None:
        normal = sketch.xDirection.crossProduct(sketch.yDirection)
    else:
        geometry = getattr(native, "geometry", None)
        base = getattr(geometry, "normal", None)
        if base is None:
            raise _invalid(
                "cannot determine the plane of the profile for a 'to_entity' revolve",
                param="profiles",
            )
        normal = base.copy()
    normal.normalize()
    return normal


def _revolve_extent(ctx: Any, spec: Any, profiles: Any) -> tuple[str, tuple[Any, ...]]:
    """``(setter, arguments)`` of a RevolveExtent (D11)."""
    if spec is None:
        spec = {"type": "full"}
    if not isinstance(spec, dict):
        raise _invalid(
            f"parameter 'extent' must be an object with 'type' ({', '.join(REVOLVE_EXTENT_TYPES)})",
            param="extent",
        )
    kind = check_choice("extent.type", spec.get("type"), REVOLVE_EXTENT_TYPES)
    if kind == "full":
        return "setAngleExtent", (False, angle_value(ctx, "360 deg", "extent.angle"))
    if kind == "angle":
        symmetric = spec.get("symmetric", False)
        if symmetric is None:
            symmetric = False
        if not isinstance(symmetric, bool):
            raise _invalid(
                "parameter 'extent.symmetric' must be a boolean", param="extent.symmetric"
            )
        raw = spec.get("angle")
        angle = angle_value(ctx, 360 if raw is None else raw, "extent.angle")
        return "setAngleExtent", (symmetric, angle)
    if kind == "two_angles":
        for name in ("angle1", "angle2"):
            if spec.get(name) is None:
                raise _invalid(
                    f"parameter 'extent' of type 'two_angles' needs '{name}'",
                    param=f"extent.{name}",
                )
        angle1 = angle_value(ctx, spec["angle1"], "extent.angle1")
        angle2 = angle_value(ctx, spec["angle2"], "extent.angle2")
        return "setTwoSideAngleExtent", (angle1, angle2)
    if spec.get("entity") is None:
        raise _invalid(
            "parameter 'extent' of type 'to_entity' needs 'entity'", param="extent.entity"
        )
    entity = resolve_one(ctx, spec["entity"], EXTENT_ENTITY_TYPES, "extent.entity")
    direction = check_choice("extent.direction", _opt(spec, "direction", "positive"), DIRECTIONS)
    hint = _profile_normal(_first(profiles))
    if direction == "negative":
        hint.scaleBy(-1.0)
    return "setOneSideToExtent", (entity, hint)


def _axis(ctx: Any, ref: Any, component: Any) -> Any:
    """Axis ref -> a sketch line, construction axis or straight edge; ``origin:X|Y|Z`` is the
    origin axis of the feature's component."""
    raw = ref.get("ref") if isinstance(ref, dict) else ref
    try:
        entity = ctx.refs.resolve(ref, component) if is_origin_ref(raw) else ctx.refs.resolve(ref)
    except BridgeError as exc:
        exc.details.setdefault("param", "axis")
        raise
    return require_kind(entity, "linear", "axis")


@handler("feature.revolve", mutating=True)
def feature_revolve(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Revolve profiles / planar faces around an axis (D11); result per D8."""
    profiles, operation, op_enum, bodies, is_solid, component = _common(
        ctx, params, allow_text=False
    )
    axis = _axis(ctx, ctx.require(params, "axis", (str, dict)), component)
    setter, extent_args = _revolve_extent(ctx, params.get("extent"), profiles)

    before = _snapshot(component, bodies)
    revolves = component.features.revolveFeatures
    inp = revolves.createInput(profiles, axis, op_enum)
    if not is_solid:
        inp.isSolid = False
    if not hasattr(inp, setter):
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"RevolveFeatureInput.{setter} is not available in this Fusion API version",
            {"feature_type": "RevolveFeature", "setter": setter},
        )
    _call("RevolveFeature", inp, setter, *extent_args)
    if bodies is not None:
        inp.participantBodies = list(bodies)
    feature = revolves.add(inp)
    return _finish(ctx, feature, before, operation)


# --- sweep (D12) ---------------------------------------------------------------------------

_ORIENTATION_ATTRS = {
    "perpendicular": "PerpendicularOrientationType",
    "parallel": "ParallelOrientationType",
}
_SCALING_ATTRS = {
    "scale": "SweepProfileScaleOption",
    "stretch": "SweepProfileStretchOption",
    "none": "SweepProfileNoScalingOption",
}


def _is_zero(value: Any) -> bool:
    """``None`` or a plain number equal to 0 (an expression string never counts as zero)."""
    if value is None:
        return True
    return isinstance(value, int | float) and not isinstance(value, bool) and value == 0


def _path_extent(params: dict[str, Any]) -> float:
    """``path_extent``: share of the path to sweep along, a number in (0, 1]."""
    value = params.get("path_extent")
    if value is None:
        return 1.0
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _invalid(
            "parameter 'path_extent' must be a number in (0, 1]",
            param="path_extent",
            value=repr(value),
        )
    number = float(value)
    if not (0.0 < number <= 1.0):
        raise _invalid(
            f"parameter 'path_extent' must be in (0, 1], got {number}",
            param="path_extent",
            value=number,
        )
    return number


@handler("feature.sweep", mutating=True)
def feature_sweep(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Sweep profiles / planar faces along a path of sketch curves or edges (D12); optional
    guide rail with profile scaling; result per D8."""
    profiles, operation, op_enum, bodies, is_solid, component = _common(
        ctx, params, allow_text=False, allow_open=True
    )
    path_refs = ctx.require(params, "path", list)
    chain = _bool(ctx, params, "path_chain", True)
    rail_refs = ctx.require(params, "guide_rail", list, None)
    orientation = check_choice(
        "orientation", _opt(params, "orientation", "perpendicular"), SWEEP_ORIENTATIONS
    )
    twist = taper = scaling = None
    if rail_refs is not None:
        for name in ("twist_angle", "taper_angle"):
            if not _is_zero(params.get(name)):
                raise _invalid(
                    f"parameter '{name}' cannot be used with guide_rail (the rail drives the "
                    "profile, twist and taper apply only to a sweep without one)",
                    param=name,
                )
        scaling_name = check_choice(
            "rail_scaling", _opt(params, "rail_scaling", "scale"), RAIL_SCALINGS
        )
        scaling = getattr(adsk.fusion.SweepProfileScalingOptions, _SCALING_ATTRS[scaling_name])
    else:
        if _given(params, "rail_scaling"):
            raise _invalid(
                "parameter 'rail_scaling' applies only together with guide_rail",
                param="rail_scaling",
            )
        twist = angle_value(ctx, _opt(params, "twist_angle", 0), "twist_angle")
        taper = angle_value(ctx, _opt(params, "taper_angle", 0), "taper_angle")
    extent = scalar_value(_path_extent(params), "path_extent")
    flip = _bool(ctx, params, "flip", False)
    path = make_path(ctx, component, path_refs, chain, "path")
    rail = None
    if rail_refs is not None:
        rail = make_path(ctx, component, rail_refs, chain, "guide_rail")

    before = _snapshot(component, bodies)
    sweeps = component.features.sweepFeatures
    inp = sweeps.createInput(profiles, path, op_enum)
    if not is_solid:
        inp.isSolid = False
    inp.orientation = getattr(adsk.fusion.SweepOrientationTypes, _ORIENTATION_ATTRS[orientation])
    if rail is not None:
        inp.guideRail = rail
        inp.profileScaling = scaling
    else:
        inp.twistAngle = twist
        inp.taperAngle = taper
    inp.distanceOne = extent
    inp.isDirectionFlipped = flip
    if bodies is not None:
        inp.participantBodies = list(bodies)
    feature = sweeps.add(inp)
    return _finish(ctx, feature, before, operation)


# --- loft (D13) ----------------------------------------------------------------------------

LOFT_POINT_TYPES = ("SketchPoint", "ConstructionPoint", "BRepVertex")
_LOFT_SECTION_FIELDS = frozenset({"entity", "end_condition", "angle", "weight"})
_WEIGHTED_ENDS = ("tangent", "smooth", "direction", "point_tangent")
_POINT_ENDS = ("point_sharp", "point_tangent")
_LOFT_SETTERS = {
    "free": "setFreeEndCondition",
    "tangent": "setTangentEndCondition",
    "smooth": "setSmoothEndCondition",
    "direction": "setDirectionEndCondition",
    "point_sharp": "setPointSharpEndCondition",
    "point_tangent": "setPointTangentEndCondition",
}
_LOFT_EXPECTED = ["Profile", "BRepFace (planar)", *LOFT_POINT_TYPES]


def _loft_entity(ctx: Any, spec: Any, where: str) -> tuple[Any, bool]:
    """``(entity, is_point)`` of one loft section: exactly one profile (c05 spec), a planar
    face, or a point (sketch point, construction point, vertex)."""
    if isinstance(spec, dict) or (isinstance(spec, str) and is_profile_ref(spec)):
        found = feature_profiles(ctx, [spec], allow_faces=True, param=where)
        if isinstance(found, adsk.core.ObjectCollection):
            raise _invalid(
                f"parameter '{where}' must select exactly one profile, got {found.count}",
                param=where,
                count=found.count,
                reason="several_profiles",
            )
        return found, False
    if not isinstance(spec, str) or not spec:
        raise _invalid(
            f"parameter '{where}' must be a profile ref, a profile spec object or an entity ref",
            param=where,
            expected=_LOFT_EXPECTED,
        )
    try:
        entity = ctx.refs.resolve(spec)
    except BridgeError as exc:
        exc.details.setdefault("param", where)
        raise
    kind = type_name(entity)
    if kind in LOFT_POINT_TYPES:
        return entity, True
    if kind == "Profile" or (is_face(entity) and is_planar(entity)):
        return entity, False
    actual = describe_kind(entity)
    raise _invalid(
        f"parameter '{where}' must be {' | '.join(_LOFT_EXPECTED)}, got {actual}",
        param=where,
        ref=spec,
        expected=_LOFT_EXPECTED,
        actual=actual,
    )


def _loft_weight(section: dict[str, Any], where: str) -> Any:
    value = _opt(section, "weight", 1.0)
    if isinstance(value, int | float) and not isinstance(value, bool) and value <= 0:
        raise _invalid(
            f"parameter '{where}.weight' must be greater than 0", param=f"{where}.weight"
        )
    return scalar_value(value, f"{where}.weight")


def _loft_section(
    ctx: Any, section: Any, index: int, last: int
) -> tuple[Any, bool, str, tuple[Any, ...]]:
    """``(entity, is_point, setter, setter arguments)`` of ``sections[index]``, validated."""
    where = f"sections[{index}]"
    if not isinstance(section, dict):
        raise _invalid(
            f"parameter '{where}' must be an object (entity, end_condition?, angle?, weight?)",
            param=where,
        )
    unknown = sorted(set(section) - _LOFT_SECTION_FIELDS)
    if unknown:
        raise _invalid(f"parameter '{where}' has unknown fields {unknown}", param=where)
    if section.get("entity") is None:
        raise _invalid(f"parameter '{where}' needs 'entity'", param=f"{where}.entity")
    condition = check_choice(
        f"{where}.end_condition", _opt(section, "end_condition", "free"), LOFT_END_CONDITIONS
    )
    if not _is_zero(section.get("angle")) and condition != "direction":
        raise _invalid(
            f"parameter '{where}.angle' applies only to end_condition 'direction'",
            param=f"{where}.angle",
        )
    weight_raw = section.get("weight")
    default_weight = (
        isinstance(weight_raw, int | float) and not isinstance(weight_raw, bool) and weight_raw == 1
    )
    if weight_raw is not None and not default_weight and condition not in _WEIGHTED_ENDS:
        raise _invalid(
            f"parameter '{where}.weight' applies only to end_condition "
            f"{' | '.join(_WEIGHTED_ENDS)}",
            param=f"{where}.weight",
        )
    entity, is_point = _loft_entity(ctx, section["entity"], f"{where}.entity")
    if is_point and index not in (0, last):
        raise _invalid(
            f"parameter '{where}.entity': a point section must be the first or the last section",
            param=f"{where}.entity",
            index=index,
            reason="point_in_middle",
        )
    if condition in _POINT_ENDS and not is_point:
        raise _invalid(
            f"parameter '{where}.end_condition' '{condition}' needs a point section",
            param=f"{where}.end_condition",
            value=condition,
        )
    args: tuple[Any, ...] = ()
    if condition == "direction":
        angle = angle_value(ctx, _opt(section, "angle", 0), f"{where}.angle")
        args = (angle, _loft_weight(section, where))
    elif condition in _WEIGHTED_ENDS:
        args = (_loft_weight(section, where),)
    return entity, is_point, _LOFT_SETTERS[condition], args


@handler("feature.loft", mutating=True)
def feature_loft(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Loft through profile / planar face / point sections with end conditions, optional
    rails or a centerline (D13); result per D8."""
    operation = ctx.require(params, "operation", str, "new_body")
    op_enum = operation_enum(operation)
    is_solid = _is_solid(ctx, params, operation)
    raw_sections = ctx.require(params, "sections", list)
    if len(raw_sections) < 2:
        raise _invalid(
            f"parameter 'sections' needs at least 2 sections, got {len(raw_sections)}",
            param="sections",
            count=len(raw_sections),
            reason="too_few_sections",
        )
    rail_refs = ctx.require(params, "rails", list, [])
    centerline_ref = ctx.require(params, "centerline", (str, dict), None)
    if rail_refs and centerline_ref is not None:
        raise _invalid(
            "parameters 'rails' and 'centerline' cannot be used together",
            param="centerline",
            reason="rails_and_centerline",
        )
    closed = _bool(ctx, params, "closed", False)
    merge = _bool(ctx, params, "merge_tangent_edges", True)
    last = len(raw_sections) - 1
    sections = [_loft_section(ctx, s, i, last) for i, s in enumerate(raw_sections)]
    if closed and any(is_point for _, is_point, _, _ in sections):
        raise _invalid(
            "parameter 'closed' cannot be used with a point section",
            param="closed",
            reason="closed_with_point",
        )
    rails = [resolve_one(ctx, ref, PATH_TYPES, "rails", i) for i, ref in enumerate(rail_refs)]
    centerline = None
    if centerline_ref is not None:
        centerline = resolve_one(ctx, centerline_ref, PATH_TYPES, "centerline")
    bodies = participant_bodies(ctx, params.get("participant_bodies"), operation)
    component = target_component(sections[0][0])

    before = _snapshot(component, bodies)
    lofts = component.features.loftFeatures
    inp = lofts.createInput(op_enum)
    if not is_solid:
        inp.isSolid = False
    for index, (entity, _, setter, args) in enumerate(sections):
        section = inp.loftSections.add(entity)
        if section is None:
            raise _rejected("LoftFeature", "loftSections.add")
        # Inner sections keep Fusion's default (free) unless asked otherwise.
        if index in (0, last) or setter != _LOFT_SETTERS["free"]:
            _call("LoftFeature", section, setter, *args)
    guides = inp.centerLineOrRails
    for rail in rails:
        _call("LoftFeature", guides, "addRail", rail)
    if centerline is not None:
        _call("LoftFeature", guides, "addCenterLine", centerline)
    inp.isClosed = closed
    inp.isTangentEdgesMerged = merge
    if bodies is not None:
        inp.participantBodies = list(bodies)
    feature = lofts.add(inp)
    return _finish(ctx, feature, before, operation)


# --- rib and web (D14) ---------------------------------------------------------------------

_RIB_COLLECTIONS = {"RibFeature": "ribFeatures", "WebFeature": "webFeatures"}
_RIB_NAMES = {"RibFeature": "Rib", "WebFeature": "Web"}
_CLOSED_CURVE_TYPES = ("SketchCircle", "SketchEllipse")


def _is_closed_curve(curve: Any) -> bool:
    if type_name(curve) in _CLOSED_CURVE_TYPES:
        return True
    try:
        return getattr(curve, "isClosed", False) is True
    except Exception:
        return False


def _open_curves(ctx: Any, params: dict[str, Any]) -> list[Any]:
    curves = resolve_many(ctx, ctx.require(params, "curves", list), CURVE_TYPES, "curves")
    for index, curve in enumerate(curves):
        if _is_closed_curve(curve):
            raise _invalid(
                f"parameter 'curves[{index}]' must be an open sketch curve, got a closed "
                f"{type_name(curve)}",
                param="curves",
                index=index,
                actual=type_name(curve),
                reason="closed_curve",
            )
    return curves


def _not_available(feature_type: str, what: str) -> BridgeError:
    return BridgeError(
        ErrorCode.FUSION_API_ERROR,
        f"{what} not available in this Fusion API version",
        {"feature_type": feature_type},
    )


def _set(feature_type: str, inp: Any, name: str, value: Any) -> None:
    """``inp.<name> = value`` if this Fusion version has the property (design R2)."""
    if not hasattr(inp, name):
        raise _not_available(feature_type, f"{feature_type}Input.{name} is")
    setattr(inp, name, value)


def _call_opt(feature_type: str, inp: Any, setter: str, *args: Any) -> None:
    """``_call`` of a setter this Fusion version may lack (design R2)."""
    if not hasattr(inp, setter):
        raise _not_available(feature_type, f"{feature_type}Input.{setter} is")
    _call(feature_type, inp, setter, *args)


def _rib_depth(ctx: Any, params: dict[str, Any]) -> Any:
    """``None`` for ``to_next`` (default), else the ``ValueInput`` of the distance."""
    depth = _opt(params, "depth", {"type": "to_next"})
    if not isinstance(depth, dict):
        raise _invalid(
            f"parameter 'depth' must be an object with 'type' ({', '.join(RIB_DEPTH_TYPES)})",
            param="depth",
        )
    depth_type = check_choice("depth.type", depth.get("type"), RIB_DEPTH_TYPES)
    allowed = {"type", "distance"} if depth_type == "distance" else {"type"}
    unknown = sorted(k for k in set(depth) - allowed if depth[k] is not None)
    if unknown:
        raise _invalid(
            f"parameter 'depth' of type '{depth_type}' has unknown fields {unknown}",
            param="depth",
        )
    if depth_type == "to_next":
        return None
    if depth.get("distance") is None:
        raise _invalid(
            "parameter 'depth' of type 'distance' needs 'distance'", param="depth.distance"
        )
    return length_value(ctx, depth["distance"], param="depth.distance")


# Curve-to-body distance (cm) up to which a sketch curve counts as touching a body.
TOUCH_TOL_CM = 1e-4


def _body_brief(ctx: Any, body: Any) -> dict[str, Any]:
    return {"ref": ctx.refs.to_ref(body), "name": getattr(body, "name", None)}


def _touches(ctx: Any, curve: Any, body: Any) -> bool:
    try:
        distance = ctx.app.measureManager.measureMinimumDistance(curve, body).value
    except Exception:
        return False
    return float(distance) <= TOUCH_TOL_CM


def _rib_target(ctx: Any, params: dict[str, Any], curves: list[Any], component: Any) -> Any:
    """``targetBody`` of a rib / web (c15 3.1): the ``target_body`` ref, else the one solid body
    of the curves' component that a curve touches; none or several -> ``INVALID_PARAMS`` with
    the candidate bodies in ``details.candidates``."""
    ref = params.get("target_body")
    if ref is not None:
        return resolve_one(ctx, ref, (BODY,), "target_body")
    solids = [b for b in component.bRepBodies if getattr(b, "isSolid", True)]
    touching = [b for b in solids if any(_touches(ctx, curve, b) for curve in curves)]
    if len(touching) == 1:
        return touching[0]
    candidates = touching or solids
    reason = "ambiguous_target_body" if touching else "no_target_body"
    what = "several bodies" if touching else "no solid body"
    raise _invalid(
        f"the curves touch {what}: give 'target_body'",
        param="target_body",
        reason=reason,
        candidates=[_body_brief(ctx, b) for b in candidates],
    )


def _rib_like(ctx: Any, params: dict[str, Any], feature_type: str) -> dict[str, Any]:
    """Rib / web from open sketch curves (D14) on ``target_body`` (c15 3.1), joined to it."""
    curves = _open_curves(ctx, params)
    if params.get("thickness") is None:
        raise _invalid("missing required parameter 'thickness'", param="thickness")
    thickness = length_value(ctx, params["thickness"], param="thickness")
    location = check_choice(
        "thickness_location",
        _opt(params, "thickness_location", "symmetric"),
        RIB_THICKNESS_LOCATIONS,
    )
    distance = _rib_depth(ctx, params)
    flip = _bool(ctx, params, "flip", False)
    component = target_component(curves[0])
    features = component.features
    attr = _RIB_COLLECTIONS[feature_type]
    if not hasattr(features, attr):
        raise _not_available(feature_type, f"{_RIB_NAMES[feature_type]} features are")
    target = _rib_target(ctx, params, curves, component)

    before = _snapshot(component, None)
    collection = getattr(features, attr)
    # Live API (c15 3.1): createInput(curves: list, thickness, targetBody); depth through
    # setToNextExtent / setDistanceExtent, direction through isFlipped.
    inp = collection.createInput(list(curves), thickness, target)
    _set(feature_type, inp, "isSymmetric", location == "symmetric")
    if location == "side2":
        # The input has no thickness-side flip in the live API: side2 fails "not available".
        _set(feature_type, inp, "isThicknessFlipped", True)
    if distance is None:
        _call_opt(feature_type, inp, "setToNextExtent")
    else:
        _call_opt(feature_type, inp, "setDistanceExtent", distance)
    _set(feature_type, inp, "isFlipped", flip)
    feature = collection.add(inp)
    return _finish(ctx, feature, before, "join")


@handler("feature.rib", mutating=True)
def feature_rib(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Rib from open sketch curves (D14); result per D8."""
    return _rib_like(ctx, params, "RibFeature")


@handler("feature.web", mutating=True)
def feature_web(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Web (a network of thin walls) from open sketch curves (D14); result per D8."""
    return _rib_like(ctx, params, "WebFeature")

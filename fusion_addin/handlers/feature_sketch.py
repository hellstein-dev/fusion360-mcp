"""``feature.*`` sketch-based solid features (c07 D10, D11): extrude and revolve.

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
    THIN_LOCATIONS,
    check_choice,
)
from fusion_mcp.protocol.refs import is_origin_ref

from ..core.entity_kinds import require_kind
from ..core.features import (
    EXTENT_ENTITY_TYPES,
    BodySnapshot,
    angle_value,
    direction_enum,
    extent_definition,
    feature_profiles,
    feature_result,
    length_value,
    operation_enum,
    participant_bodies,
    resolve_one,
    start_definition,
    target_component,
)
from ..core.registry import handler

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


def _finish(ctx: Any, feature: Any, before: BodySnapshot, operation: str) -> dict[str, Any]:
    """D8 result; on failure a direct design (no timeline rollback, c02 D5) loses the feature
    here."""
    try:
        return feature_result(ctx, feature, before, operation=operation)
    except Exception:
        if feature is not None and not _parametric(ctx):
            with contextlib.suppress(Exception):
                feature.deleteMe()
        raise


def _common(ctx: Any, params: dict[str, Any], allow_text: bool) -> tuple[Any, str, Any, Any]:
    """``(profiles, operation, operation enum, participant bodies or None)``, validated."""
    operation = ctx.require(params, "operation", str, "new_body")
    op_enum = operation_enum(operation)
    profiles = feature_profiles(
        ctx,
        ctx.require(params, "profiles", (list, str, dict)),
        allow_faces=True,
        allow_text=allow_text,
    )
    bodies = participant_bodies(ctx, params.get("participant_bodies"), operation)
    return profiles, operation, op_enum, bodies


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
    """Extrude profiles / planar faces / sketch texts (D10); result per D8."""
    profiles, operation, op_enum, bodies = _common(ctx, params, allow_text=True)
    mode = check_choice("mode", _opt(params, "mode", "one_side"), EXTRUDE_MODES)
    setter, extent_args = _extrude_extent(ctx, params, mode)
    start = params.get("start")
    start_def = start_definition(ctx, start) if start is not None else None
    thin = _thin(ctx, params, mode)
    component = target_component(_first(profiles))

    before = _snapshot(component, bodies)
    extrudes = component.features.extrudeFeatures
    inp = extrudes.createInput(profiles, op_enum)
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
    profiles, operation, op_enum, bodies = _common(ctx, params, allow_text=False)
    component = target_component(_first(profiles))
    axis = _axis(ctx, ctx.require(params, "axis", (str, dict)), component)
    setter, extent_args = _revolve_extent(ctx, params.get("extent"), profiles)

    before = _snapshot(component, bodies)
    revolves = component.features.revolveFeatures
    inp = revolves.createInput(profiles, axis, op_enum)
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

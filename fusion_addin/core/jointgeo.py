"""Joint geometry and joint motion (c10 D2, D4, D5).

``build(ctx, spec)`` turns a parsed ``JointGeometrySpec`` (``protocol/assembly.py:
parse_joint_geometry``) into the ``adsk.fusion.JointGeometry`` of its kind, or the existing
``JointOrigin`` for ``kind = joint_origin``. Joints are created in root, so the entities are
coerced to the root context (``in_root_context``); joint origins are created in the
component owning the native geometry (``native=True``). ``apply_motion`` configures a
``JointInput`` or ``AsBuiltJointInput`` with the ``setAs*JointMotion`` call of its type.
"""

from __future__ import annotations

from typing import Any

import adsk.fusion

from fusion_mcp.protocol.assembly import (
    CUSTOM_DIRECTION_ENUM,
    DIRECTION_ENUMS,
    JOINT_TYPE_ENUMS,
    KEY_POINT_ENUMS,
)
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.refs import with_context

from . import refs


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _items(collection: Any) -> list[Any]:
    if collection is None:
        return []
    return [collection.item(i) for i in range(collection.count)]


def _component_of(occurrence: Any) -> Any:
    try:
        return occurrence.component
    except Exception:
        return None


def in_root_context(ctx: Any, entity: Any) -> Any:
    """``entity`` as root-context geometry (D2).

    Proxies, occurrences and entities owned by root are returned as is. A native entity of
    another component becomes its proxy through the component's only occurrence; zero or
    several occurrences -> ``INVALID_PARAMS`` with ``details.candidates``.
    """
    if refs.assembly_context(entity) is not None or refs.type_name(entity) == "Occurrence":
        return entity
    owner = refs.owner_component(refs.native_of(entity))
    root = ctx.root
    if owner is None or refs.same_entity(owner, root):
        return entity
    found = [o for o in _items(root.allOccurrences) if refs.same_entity(_component_of(o), owner)]
    if len(found) == 1:
        return entity.createForAssemblyContext(found[0])
    inner = refs.to_ref(entity)
    candidates = [with_context(inner, o.fullPathName) for o in found]
    raise _invalid(
        f"ambiguous assembly context: component {owner.name!r} has {len(found)} occurrences; "
        "pass the ref in an occurrence context ('<ref>@<occurrence path>')",
        ref=inner,
        component=owner.name,
        candidates=candidates,
    )


# --- geometry (D4) -----------------------------------------------------------------------------

# Entity types each kind accepts (``objectType`` without the namespace).
KIND_TYPES: dict[str, tuple[str, ...]] = {
    "point": ("BRepVertex", "SketchPoint", "ConstructionPoint"),
    "planar_face": ("BRepFace",),
    "nonplanar_face": ("BRepFace",),
    "curve": (
        "BRepEdge",
        "SketchLine",
        "SketchArc",
        "SketchCircle",
        "SketchEllipse",
        "SketchEllipticalArc",
        "SketchFittedSpline",
        "SketchFixedSpline",
        "SketchConicCurve",
    ),
    "profile": ("Profile",),
    "joint_origin": ("JointOrigin",),
}
CURVE_TYPES = KIND_TYPES["curve"]


def key_point_type(name: str) -> Any:
    return getattr(adsk.fusion.JointKeyPointTypes, KEY_POINT_ENUMS[name])


def _entity(ctx: Any, ref: str, param: str, types: tuple[str, ...], native: bool) -> Any:
    try:
        entity = ctx.refs.resolve(ref)
    except BridgeError as exc:
        exc.details.setdefault("param", param)
        raise
    kind = refs.type_name(entity)
    if kind not in types:
        raise _invalid(
            f"{param} must be a {' or '.join(types)}, got {kind}", param=param, type=kind
        )
    if native:
        return refs.native_of(entity)
    return in_root_context(ctx, entity)


def build(ctx: Any, spec: dict[str, Any], param: str = "geometry", native: bool = False) -> Any:
    """``JointGeometry`` (or ``JointOrigin``) for a parsed spec; ``native`` keeps native
    entities (joint origins) instead of coercing them to the root context (joints)."""
    kind = spec["kind"]
    entity = _entity(ctx, spec["entity"], f"{param}.entity", KIND_TYPES[kind], native)
    if kind == "joint_origin":
        return entity
    factory = adsk.fusion.JointGeometry
    # Every kind but point has a key point (``parse_joint_geometry`` fills the default).
    key: Any = key_point_type(spec["key_point"]) if spec["key_point"] else None
    try:
        if kind == "point":
            return factory.createByPoint(entity)
        if kind == "planar_face":
            if not _is_planar(entity):
                raise _invalid(f"{param}.entity is not a planar face", param=f"{param}.entity")
            edge = None
            if spec["edge"] is not None:
                edge = _entity(ctx, spec["edge"], f"{param}.edge", ("BRepEdge",), native)
            return factory.createByPlanarFace(entity, edge, key)
        if kind == "nonplanar_face":
            return factory.createByNonPlanarFace(entity, key)
        if kind == "curve":
            return factory.createByCurve(entity, key)
        curve = None
        if spec["curve"] is not None:
            curve = _entity(ctx, spec["curve"], f"{param}.curve", CURVE_TYPES, native)
        return factory.createByProfile(entity, curve, key)
    except BridgeError:
        raise
    except Exception as exc:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"Fusion cannot snap {param} ({kind}): {str(exc) or type(exc).__name__}",
            {"param": param},
        ) from exc


def _is_planar(face: Any) -> bool:
    geometry = getattr(face, "geometry", None)
    return geometry is None or refs.type_name(geometry) == "Plane"


# --- motion (D5) -------------------------------------------------------------------------------


def joint_type_enum(joint_type: str) -> Any:
    return getattr(adsk.fusion.JointTypes, JOINT_TYPE_ENUMS[joint_type])


def joint_type_name(enum_value: Any) -> str | None:
    """String joint type of a ``JointTypes`` value (``None`` if unknown)."""
    for name, member in JOINT_TYPE_ENUMS.items():
        if getattr(adsk.fusion.JointTypes, member, None) == enum_value:
            return name
    return None


DIRECTION_TYPES = ("BRepEdge", "ConstructionAxis", "BRepFace", "SketchLine")


def _direction(ctx: Any, value: str, param: str) -> tuple[Any, Any]:
    """``(JointDirections value, custom entity | None)`` for ``x|y|z`` or a ref."""
    directions = adsk.fusion.JointDirections
    if value in DIRECTION_ENUMS:
        return getattr(directions, DIRECTION_ENUMS[value]), None
    entity = _entity(ctx, value, param, DIRECTION_TYPES, native=False)
    return getattr(directions, CUSTOM_DIRECTION_ENUM), entity


def apply_motion(
    joint_input: Any, joint_type: str, motion: dict[str, Any], ctx: Any, param: str = "motion"
) -> None:
    """Call the ``setAs*JointMotion`` of ``joint_type`` on a ``JointInput`` or
    ``AsBuiltJointInput``; refs are custom directions in the root context."""

    def direction(key: str) -> tuple[Any, Any]:
        return _direction(ctx, motion[key], f"{param}.{key}")

    if joint_type == "rigid":
        done = joint_input.setAsRigidJointMotion()
    elif joint_type in ("revolute", "cylindrical"):
        axis, custom = direction("axis")
        method = (
            joint_input.setAsRevoluteJointMotion
            if joint_type == "revolute"
            else joint_input.setAsCylindricalJointMotion
        )
        done = method(axis, custom)
    elif joint_type == "slider":
        axis, custom = direction("direction")
        done = joint_input.setAsSliderJointMotion(axis, custom)
    elif joint_type == "pin_slot":
        axis, custom_axis = direction("axis")
        slide, custom_slide = direction("slide_direction")
        done = joint_input.setAsPinSlotJointMotion(axis, slide, custom_axis, custom_slide)
    elif joint_type == "planar":
        normal, custom = direction("normal")
        primary = None
        if motion["primary_slide_direction"] is not None:
            primary = _entity(
                ctx,
                motion["primary_slide_direction"],
                f"{param}.primary_slide_direction",
                DIRECTION_TYPES,
                native=False,
            )
        done = joint_input.setAsPlanarJointMotion(normal, custom, primary)
    else:
        pitch, custom_pitch = direction("pitch_direction")
        yaw, custom_yaw = direction("yaw_direction")
        done = joint_input.setAsBallJointMotion(pitch, yaw, custom_pitch, custom_yaw)
    if done is False:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"Fusion refused the {joint_type} motion settings",
            {"param": param},
        )

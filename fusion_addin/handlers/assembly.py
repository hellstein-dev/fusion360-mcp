"""``assembly.*``: components, occurrences, joints and interference (c10).

Joints, as-built joints, rigid groups and interference work on root-context geometry:
native refs of a component inserted exactly once are coerced to that occurrence
(``_in_root_context``, D2). Entities seen through an occurrence are addressed as
``<native ref>@<occurrence path>`` (``core/refs.py``, D1). Changes the timeline rollback does
not see (names, grounding, light bulb, transforms, joint values, limits, the active
component) are registered in a ``Compensation`` before they are made (D7, INV-5).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any

import adsk.core
import adsk.fusion

from fusion_mcp.protocol.assembly import (
    DOF_ATTRS,
    DOFS_BY_TYPE,
    ROTATIONAL_DOFS,
    check_dof,
    check_joint_type,
    parse_joint_geometry,
    parse_motion,
    value_key,
)
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.refs import with_context
from fusion_mcp.protocol.units import USER_ROUND_DIGITS, point_to_internal

from ..core import features, geometry, jointgeo, refs, transforms
from ..core.compensate import Compensation
from ..core.describe import describe
from ..core.health import check_health, entity_health
from ..core.registry import handler


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _safe(obj: Any, attr: str, default: Any = None) -> Any:
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def _items(collection: Any) -> list[Any]:
    if collection is None:
        return []
    return [collection.item(i) for i in range(collection.count)]


# --- shared helpers (D2, D8) ---------------------------------------------------------------


def _owner_component(entity: Any) -> Any:
    """Component owning the native entity behind ``entity`` (``None`` if unknown)."""
    return refs.owner_component(refs.native_of(entity))


def _in_root_context(ctx: Any, entity: Any) -> Any:
    """``entity`` as root-context geometry (D2); see ``core/jointgeo.py: in_root_context``."""
    return jointgeo.in_root_context(ctx, entity)


def _all_joints(design: Any) -> Iterator[Any]:
    """Joints and as-built joints of every component (``Component.allJoints`` may not exist)."""
    for component in _items(design.allComponents):
        for attr in ("joints", "asBuiltJoints"):
            yield from _items(_safe(component, attr))


def _joint_paths(joint: Any) -> tuple[str | None, str | None]:
    one, two = (_safe(_safe(joint, a), "fullPathName") for a in ("occurrenceOne", "occurrenceTwo"))
    return one, two


def _joint_count(design: Any, occurrence: Any) -> int:
    path = occurrence.fullPathName
    return sum(1 for joint in _all_joints(design) if path in _joint_paths(joint))


def _component_ref(component: Any) -> str:
    return refs.COMPONENT_PREFIX + component.name


def _occurrence_ref(occurrence: Any) -> str:
    return refs.OCCURRENCE_PREFIX + occurrence.fullPathName


def _occ(ctx: Any, occurrence: Any) -> dict[str, Any]:
    """Outward form of an occurrence (D8): c03 ``describe(..., "full")`` with name refs
    (``occurrence:<path>``, ``component:<name>``) plus the light bulb and the number of
    joints that reference it."""
    data = describe(occurrence, ctx.units, "full")
    data["ref"] = _occurrence_ref(occurrence)
    data["component"] = _component_ref(occurrence.component)
    data["is_light_bulb_on"] = bool(_safe(occurrence, "isLightBulbOn", True))
    data["joint_count"] = _joint_count(ctx.design, occurrence)
    return data


def _api_error(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.FUSION_API_ERROR, message, details)


def _resolve(ctx: Any, ref: Any, param: str) -> Any:
    if not isinstance(ref, str) or not ref:
        raise _invalid(f"parameter '{param}' must be a non-empty ref string", param=param)
    try:
        return ctx.refs.resolve(ref)
    except BridgeError as exc:
        exc.details.setdefault("param", param)
        raise


def _occurrence(ctx: Any, ref: Any, param: str = "occurrence") -> Any:
    entity = _resolve(ctx, ref, param)
    if refs.type_name(entity) != "Occurrence":
        raise _invalid(
            f"parameter '{param}' must be an occurrence ('occurrence:<path>'), "
            f"got {refs.type_name(entity)}",
            param=param,
        )
    return entity


def _component(ctx: Any, ref: Any, param: str = "component") -> Any:
    """A component from ``component:<name>`` (or its token); an occurrence ref gives its
    component."""
    entity = _resolve(ctx, ref, param)
    kind = refs.type_name(entity)
    if kind == "Occurrence":
        return entity.component
    if kind != "Component":
        raise _invalid(
            f"parameter '{param}' must be a component ('component:<name>'), got {kind}",
            param=param,
        )
    return entity


def _name(ctx: Any, params: dict[str, Any]) -> str:
    value: str = ctx.require(params, "name", str)
    if not value.strip():
        raise _invalid("parameter 'name' must not be empty", param="name")
    return value


def _point_param(ctx: Any, params: dict[str, Any], key: str) -> list[float] | None:
    """``[x, y, z]`` numbers in mm (validated here, converted by ``units``) or ``None``."""
    value = ctx.require(params, key, list, None)
    if value is None:
        return None
    if len(value) != 3 or not all(
        isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) for v in value
    ):
        raise _invalid(f"parameter '{key}' must be [x, y, z] numbers in mm", param=key)
    return [float(v) for v in value]


def _matrix(translate_mm: list[float] | None) -> Any:
    matrix = adsk.core.Matrix3D.create()
    if translate_mm is not None:
        matrix.translation = adsk.core.Vector3D.create(*point_to_internal(translate_mm))
    return matrix


def _rename(comp: Compensation, component: Any, name: str) -> None:
    old = component.name
    comp.add(lambda: setattr(component, "name", old), f"name {old}")
    component.name = name


def _root_occurrences(ctx: Any, component: Any) -> list[Any]:
    """Every occurrence of ``component`` seen from root (nested ones as paths)."""
    return [
        o
        for o in _items(ctx.root.allOccurrences)
        if refs.same_entity(_safe(o, "component"), component)
    ]


def _check_unique_name(ctx: Any, name: str, component: Any = None) -> None:
    for other in _items(ctx.design.allComponents):
        if other.name == name and not refs.same_entity(other, component):
            raise _invalid(f"a component named {name!r} already exists", name=name)


def _in_parent(occurrence: Any, parent: Any) -> Any:
    """A native occurrence created inside ``parent.component`` as seen through ``parent``."""
    if parent is None:
        return occurrence
    return occurrence.createForAssemblyContext(parent)


# --- 2. components ---------------------------------------------------------------------------

MAX_LIST = 5000
DEFAULT_LIST = 500


def _component_info(ctx: Any, component: Any) -> dict[str, Any]:
    part_number = _safe(component, "partNumber")
    return {
        "ref": _component_ref(component),
        "name": component.name,
        "is_root": refs.same_entity(component, ctx.root),
        "occurrence_count": len(_root_occurrences(ctx, component)),
        "body_count": len(_items(_safe(component, "bRepBodies"))),
        "part_number": part_number if isinstance(part_number, str) else None,
    }


def _active(ctx: Any) -> dict[str, Any]:
    design = ctx.design
    active = _safe(design, "activeComponent") or ctx.root
    occurrence = _safe(design, "activeOccurrence")
    return {
        "active_component": _component_ref(active),
        "active_occurrence": _occurrence_ref(occurrence) if occurrence is not None else None,
    }


@handler("assembly.list")
def assembly_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    limit = ctx.require(params, "limit", int, DEFAULT_LIST)
    if not 1 <= limit <= MAX_LIST:
        raise _invalid(f"parameter 'limit' must be 1..{MAX_LIST}", param="limit")
    design = ctx.design
    components = _items(design.allComponents)
    occurrences = _items(ctx.root.allOccurrences)
    return {
        "active_component": _active(ctx)["active_component"],
        "components": [_component_info(ctx, c) for c in components[:limit]],
        "occurrences": [_occ(ctx, o) for o in occurrences[:limit]],
        "joint_count": sum(1 for _ in _all_joints(design)),
        "truncated": len(components) > limit or len(occurrences) > limit,
    }


@handler("assembly.new_component", mutating=True)
def assembly_new_component(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    name = _name(ctx, params)
    parent_ref = ctx.require(params, "parent", str, None)
    translate_mm = _point_param(ctx, params, "translate_mm")
    parent = _occurrence(ctx, parent_ref, "parent") if parent_ref is not None else None
    _check_unique_name(ctx, name)
    target = parent.component if parent is not None else ctx.root
    matrix = _matrix(translate_mm)
    with Compensation() as comp:
        occurrence = target.occurrences.addNewComponent(matrix)
        if occurrence is None:
            raise _api_error("Fusion did not create the component")
        comp.add(occurrence.deleteMe, "new component")
        component = occurrence.component
        _rename(comp, component, name)
        result = {
            "component": _component_ref(component),
            "occurrence": _occ(ctx, _in_parent(occurrence, parent)),
        }
    return result


def _bodies(ctx: Any, refs_in: list[Any]) -> list[Any]:
    if not refs_in:
        raise _invalid("parameter 'bodies' must list at least one body", param="bodies")
    bodies: list[Any] = []
    for i, ref in enumerate(refs_in):
        entity = refs.native_of(_resolve(ctx, ref, f"bodies[{i}]"))
        if refs.type_name(entity) != "BRepBody":
            raise _invalid(
                f"bodies[{i}] must be a body, got {refs.type_name(entity)}", param=f"bodies[{i}]"
            )
        if any(refs.same_entity(entity, b) for b in bodies):
            raise _invalid(f"bodies[{i}] is listed twice", param=f"bodies[{i}]")
        bodies.append(entity)
    owners = [_owner_component(b) for b in bodies]
    if any(not refs.same_entity(o, owners[0]) for o in owners[1:]):
        raise _invalid(
            "all bodies must belong to the same component; got "
            + ", ".join(sorted({o.name for o in owners if o is not None})),
            owners=[_component_ref(o) for o in owners if o is not None],
        )
    return bodies


def _new_occurrence_of(owner: Any, component: Any) -> Any:
    """The occurrence of ``component`` directly inside ``owner`` (native context)."""
    for occurrence in _items(owner.occurrences):
        if refs.same_entity(_safe(occurrence, "component"), component):
            return occurrence
    return None


def _create_component(owner: Any, first: Any) -> tuple[Any, Any | None]:
    """``first.createComponent()`` -> ``(occurrence, moved body | None)``; without the method
    (or an unusable result) ``(new empty occurrence, None)`` - the caller moves ``first``."""
    create = getattr(first, "createComponent", None)
    if create is not None:
        result = create()
        kind = refs.type_name(result) if result is not None else None
        if kind == "Occurrence":
            return result, None
        if kind == "BRepBody":
            context = refs.assembly_context(result)
            occurrence = context or _new_occurrence_of(owner, _owner_component(result))
            if occurrence is not None:
                return occurrence, result
    occurrence = owner.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    if occurrence is None:
        raise _api_error("Fusion did not create the component")
    # No deleteMe undo: it would run before the timeline rollback and delete bodies already
    # moved into the occurrence. The D5 rollback undoes the moves first, then the occurrence.
    return occurrence, None


def _moved(body: Any, occurrence: Any) -> Any:
    result = body.moveToComponent(occurrence)
    if result is None or result is False:
        raise _api_error(f"Fusion did not move body {body.name!r}", body=refs.to_ref(body))
    return result


@handler("assembly.component_from_bodies", mutating=True)
def assembly_component_from_bodies(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    bodies = _bodies(ctx, ctx.require(params, "bodies", list))
    name = _name(ctx, params)
    _check_unique_name(ctx, name)
    owner = _owner_component(bodies[0])
    with Compensation() as comp:
        occurrence, first_moved = _create_component(owner, bodies[0])
        moved = [first_moved] if first_moved is not None else [_moved(bodies[0], occurrence)]
        moved.extend(_moved(body, occurrence) for body in bodies[1:])
        component = occurrence.component
        _rename(comp, component, name)
        seen = _root_occurrences(ctx, component)
        result = {
            "component": _component_ref(component),
            "occurrence": _occ(ctx, seen[0] if seen else occurrence),
            "bodies": [refs.to_ref(b) for b in moved],
        }
    return result


def _restore_active(design: Any) -> Any:
    occurrence = _safe(design, "activeOccurrence")

    def undo() -> None:
        if occurrence is not None:
            occurrence.activate()
        else:
            design.activateRootComponent()

    return undo


@handler("assembly.activate", mutating=True)
def assembly_activate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    ref = ctx.require(params, "occurrence", str, None)
    occurrence = _occurrence(ctx, ref) if ref is not None else None
    design = ctx.design
    with Compensation() as comp:
        comp.add(_restore_active(design), "active component")
        done = design.activateRootComponent() if occurrence is None else occurrence.activate()
        if done is False:
            raise _api_error("Fusion did not activate " + (ref or "the root component"))
    result = _active(ctx)
    if occurrence is not None:
        result["active_occurrence"] = _occurrence_ref(occurrence)
        result["active_component"] = _component_ref(occurrence.component)
    return result


@handler("assembly.rename_component", mutating=True)
def assembly_rename_component(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    component = _component(ctx, ctx.require(params, "component", str))
    name = _name(ctx, params)
    if refs.same_entity(component, ctx.root):
        raise _invalid(
            "the root component cannot be renamed here (it is named after the document)",
            param="component",
        )
    _check_unique_name(ctx, name, component)
    with Compensation() as comp:
        _rename(comp, component, name)
    return {
        "component": _component_ref(component),
        "occurrences": [_occurrence_ref(o) for o in _root_occurrences(ctx, component)],
    }


# --- 3. occurrences ----------------------------------------------------------------------------


@handler("assembly.add_occurrence", mutating=True)
def assembly_add_occurrence(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    component = _component(ctx, ctx.require(params, "component", str))
    parent_ref = ctx.require(params, "parent", str, None)
    independent = ctx.require(params, "independent", bool, False)
    translate_mm = _point_param(ctx, params, "translate_mm")
    parent = _occurrence(ctx, parent_ref, "parent") if parent_ref is not None else None
    if refs.same_entity(component, ctx.root):
        raise _invalid("the root component cannot be inserted", param="component")
    target = parent.component if parent is not None else ctx.root
    if refs.same_entity(component, target):
        raise _invalid("a component cannot be inserted into itself", param="parent")
    matrix = _matrix(translate_mm)
    with Compensation() as comp:
        if independent:
            try:
                occurrence = target.occurrences.addNewComponentCopy(component, matrix)
            except AttributeError as exc:
                raise _api_error(
                    "independent copy not supported by this Fusion version; insert a "
                    "referenced occurrence (independent=false) instead"
                ) from exc
        else:
            occurrence = target.occurrences.addExistingComponent(component, matrix)
        if occurrence is None:
            raise _api_error(f"Fusion did not insert component {component.name!r}")
        comp.add(occurrence.deleteMe, "new occurrence")
        result = {"occurrence": _occ(ctx, _in_parent(occurrence, parent))}
    return result


def _capture(design: Any, enabled: bool) -> bool:
    """Fix a pending position change with a design snapshot (D3); ``False`` if not done."""
    if not enabled or not _is_parametric(design):
        return False
    snapshots = _safe(design, "snapshots")
    if snapshots is None or not _safe(snapshots, "hasPendingSnapshot", False):
        return False
    return bool(snapshots.add())


def _is_parametric(design: Any) -> bool:
    return bool(design.designType == adsk.fusion.DesignTypes.ParametricDesignType)


def _restore_transform(occurrence: Any) -> Any:
    current = occurrence.transform2
    old = current.copy() if hasattr(current, "copy") else current

    def undo() -> None:
        occurrence.transform2 = old

    return undo


@handler("assembly.transform_occurrence", mutating=True)
def assembly_transform_occurrence(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    occurrence = _occurrence(ctx, ctx.require(params, "occurrence", str))
    translate_mm = _point_param(ctx, params, "translate_mm")
    rotate = ctx.require(params, "rotate", dict, None)
    absolute = ctx.require(params, "absolute", dict, None)
    capture = ctx.require(params, "capture_position", bool, True)
    if absolute is not None and (translate_mm is not None or rotate is not None):
        raise _invalid("'absolute' excludes 'translate_mm' and 'rotate': pass one or the other")
    if absolute is None and translate_mm is None and rotate is None:
        raise _invalid("nothing to do: pass 'translate_mm' and/or 'rotate', or 'absolute'")
    if occurrence.isGrounded:
        raise _invalid(
            f"occurrence {occurrence.fullPathName!r} is grounded; unground it first with "
            "assembly_set_occurrence_state(grounded=false)",
            param="occurrence",
        )
    if absolute is not None:
        unknown = sorted(set(absolute) - {"origin_mm", "x_axis", "y_axis"})
        if unknown:
            raise _invalid(f"parameter 'absolute' has unknown fields {unknown}", param="absolute")
        matrix = transforms.placement_matrix(
            absolute.get("origin_mm"), absolute.get("x_axis"), absolute.get("y_axis")
        )
    else:
        matrix = transforms.relative_matrix(occurrence.transform2, translate_mm, rotate)
    design = ctx.design
    with Compensation() as comp:
        comp.add(_restore_transform(occurrence), "transform")
        occurrence.transform2 = matrix
        captured = _capture(design, capture)
    return {
        "occurrence": _occ(ctx, occurrence),
        "captured": captured,
        "constrained_by_joints": _joint_count(design, occurrence) > 0,
    }


def _restore_flag(occurrence: Any, attr: str) -> Any:
    old = bool(getattr(occurrence, attr))

    def undo() -> None:
        if bool(getattr(occurrence, attr)) != old:
            setattr(occurrence, attr, old)

    return undo


@handler("assembly.set_occurrence_state", mutating=True)
def assembly_set_occurrence_state(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    occurrence = _occurrence(ctx, ctx.require(params, "occurrence", str))
    changes = {
        attr: ctx.require(params, key, bool, None)
        for key, attr in (("grounded", "isGrounded"), ("light_bulb", "isLightBulbOn"))
    }
    changes = {attr: value for attr, value in changes.items() if value is not None}
    if not changes:
        raise _invalid("nothing to change: pass 'grounded' and/or 'light_bulb'")
    with Compensation() as comp:
        for attr, value in changes.items():
            comp.add(_restore_flag(occurrence, attr), attr)
            setattr(occurrence, attr, value)
            if bool(getattr(occurrence, attr)) != value:
                raise _api_error(f"Fusion did not change {attr} of {occurrence.fullPathName!r}")
    return {"occurrence": _occ(ctx, occurrence)}


DELETABLE = ("Occurrence", "Joint", "AsBuiltJoint", "JointOrigin", "RigidGroup")


@handler("assembly.delete", mutating=True)
def assembly_delete(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    ref = ctx.require(params, "item", str)
    entity = _resolve(ctx, ref, "item")
    kind = refs.type_name(entity)
    if kind not in DELETABLE:
        raise _invalid(
            f"assembly_delete removes {', '.join(DELETABLE)}; got {kind} (use body_delete, "
            "sketch_delete or timeline_delete for other items)",
            param="item",
            type=kind,
        )
    name = _safe(entity, "fullPathName") if kind == "Occurrence" else _safe(entity, "name")
    if entity.deleteMe() is False:
        raise _api_error(f"Fusion did not delete {kind} {name!r}", item=ref)
    return {"deleted": {"name": name, "type": kind}}


# --- 4. joints: outward form (D8) --------------------------------------------------------------

JOINT_KINDS = ("Joint", "AsBuiltJoint")
# ``(response key, enabled attribute, value attribute)`` of ``JointLimits``.
LIMIT_FIELDS = (
    ("min", "isMinimumValueEnabled", "minimumValue"),
    ("max", "isMaximumValueEnabled", "maximumValue"),
    ("rest", "isRestValueEnabled", "restValue"),
)
CLAMP_TOLERANCE = 1e-6  # internal units (cm / radians)


def _dof_in(ctx: Any, dof: str, value: float) -> float:
    """A dof value from the agent (degrees / mm) -> internal units (radians / cm)."""
    converted = ctx.units.angle(value) if dof in ROTATIONAL_DOFS else ctx.units.length(value)
    return float(converted)


def _dof_out(ctx: Any, dof: str, internal: float) -> float:
    """Internal dof value -> degrees / mm, rounded like other inspection output."""
    value = (
        ctx.units.angle_out(internal) if dof in ROTATIONAL_DOFS else ctx.units.length_out(internal)
    )
    return round(value, USER_ROUND_DIGITS) + 0.0


def _joint_type(joint: Any) -> str | None:
    return jointgeo.joint_type_name(_safe(_safe(joint, "jointMotion"), "jointType"))


def _limits(ctx: Any, motion: Any, dof: str) -> dict[str, float | None]:
    """``{"min", "max", "rest"}`` of a dof in degrees / mm; ``None`` where disabled."""
    limits = _safe(motion, DOF_ATTRS[dof] + "Limits")
    result: dict[str, float | None] = {}
    for key, enabled, value in LIMIT_FIELDS:
        raw = _safe(limits, value) if _safe(limits, enabled, False) else None
        result[key] = _dof_out(ctx, dof, raw) if raw is not None else None
    return result


def _joint(ctx: Any, joint: Any) -> dict[str, Any]:
    """Outward form of a joint or as-built joint (D8): values and limits per dof of its type
    in degrees / mm, the occurrences it connects, suppression and health."""
    joint_type = _joint_type(joint)
    motion = _safe(joint, "jointMotion")
    values: dict[str, float | None] = {}
    limits: dict[str, dict[str, float | None]] = {}
    for dof in DOFS_BY_TYPE.get(joint_type or "", ()):
        raw = _safe(motion, DOF_ATTRS[dof] + "Value")
        values[value_key(dof)] = _dof_out(ctx, dof, raw) if raw is not None else None
        limits[dof] = _limits(ctx, motion, dof)
    one, two = (_safe(joint, attr) for attr in ("occurrenceOne", "occurrenceTwo"))
    health = entity_health(joint)
    return {
        "ref": refs.to_ref(joint),
        "name": _safe(joint, "name"),
        "type": joint_type or "unknown",
        "occurrence_one": _occurrence_ref(one) if one is not None else None,
        "occurrence_two": _occurrence_ref(two) if two is not None else None,
        "values": values,
        "limits": limits,
        "is_suppressed": bool(_safe(joint, "isSuppressed", False)),
        "health": health["state"],
        "message": health["message"],
    }


def _joint_entity(ctx: Any, ref: Any, param: str = "joint") -> Any:
    entity = _resolve(ctx, ref, param)
    kind = refs.type_name(entity)
    if kind not in JOINT_KINDS:
        raise _invalid(
            f"parameter '{param}' must be a joint or as-built joint, got {kind}", param=param
        )
    return entity


def _typed_joint(ctx: Any, ref: Any) -> tuple[Any, str]:
    joint = _joint_entity(ctx, ref)
    joint_type = _joint_type(joint)
    if joint_type is None:
        raise _api_error(f"cannot tell the type of joint {_safe(joint, 'name')!r}", joint=ref)
    return joint, joint_type


def _optional_name(ctx: Any, params: dict[str, Any]) -> str | None:
    name: str | None = ctx.require(params, "name", str, None)
    if name is not None and not name.strip():
        raise _invalid("parameter 'name' must not be empty", param="name")
    return name


def _finish(comp: Compensation, created: Any, name: str | None, what: str) -> None:
    """Register the undo of a newly created object, name it and check its health."""
    if created is None:
        raise _api_error(f"Fusion did not create the {what}")
    comp.add(created.deleteMe, what)
    if name is not None:
        created.name = name
    check_health(created)


# --- 5. joint origins ----------------------------------------------------------------------------


def _single_occurrence(ctx: Any, component: Any) -> Any:
    if refs.same_entity(component, ctx.root):
        return None
    found = _root_occurrences(ctx, component)
    return found[0] if len(found) == 1 else None


def _joint_origin(ctx: Any, origin: Any, context: Any = None) -> dict[str, Any]:
    """``{"ref", "native_ref", "name", "component"}``; ``ref`` is in the context of
    ``context`` (or of the component's only occurrence), else the native ref."""
    native = refs.native_of(origin)
    owner = _owner_component(native)
    native_ref = refs.to_ref(native)
    if context is None:
        context = refs.assembly_context(origin)
    if context is None and owner is not None:
        context = _single_occurrence(ctx, owner)
    ref = with_context(native_ref, context.fullPathName) if context is not None else native_ref
    return {
        "ref": ref,
        "native_ref": native_ref,
        "name": _safe(native, "name"),
        "component": _component_ref(owner) if owner is not None else None,
    }


def _offsets(ctx: Any, params: dict[str, Any]) -> list[Any] | None:
    value = ctx.require(params, "offset_mm", list, None)
    if value is None:
        return None
    if len(value) != 3:
        raise _invalid("parameter 'offset_mm' must be [x, y, z] in mm", param="offset_mm")
    return [features.length_value(ctx, v, param=f"offset_mm[{i}]") for i, v in enumerate(value)]


def _angle(ctx: Any, params: dict[str, Any], key: str = "angle") -> Any:
    value = params.get(key)
    return features.angle_value(ctx, value, param=key) if value is not None else None


@handler("assembly.create_joint_origin", mutating=True)
def assembly_create_joint_origin(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    spec = parse_joint_geometry(ctx.require(params, "geometry", dict), "geometry")
    if spec["kind"] == "joint_origin":
        raise _invalid(
            "a joint origin snaps to geometry (point, face, curve, profile), not to another "
            "joint origin",
            param="geometry.kind",
        )
    name = _optional_name(ctx, params)
    offsets = _offsets(ctx, params)
    angle = _angle(ctx, params)
    flip = ctx.require(params, "flip", bool, False)
    source = _resolve(ctx, spec["entity"], "geometry.entity")
    owner = _owner_component(source)
    if owner is None:
        raise _invalid("cannot tell which component owns geometry.entity", param="geometry")
    for key in ("edge", "curve"):
        if spec[key] is not None:
            other = _owner_component(_resolve(ctx, spec[key], f"geometry.{key}"))
            if not refs.same_entity(other, owner):
                raise _invalid(
                    f"geometry.{key} must belong to component {owner.name!r} like the entity",
                    param=f"geometry.{key}",
                )
    geometry = jointgeo.build(ctx, spec, "geometry", native=True)
    origins = owner.jointOrigins
    origin_input = origins.createInput(geometry)
    if offsets is not None:
        origin_input.offsetX, origin_input.offsetY, origin_input.offsetZ = offsets
    if angle is not None:
        origin_input.angle = angle
    if flip:
        origin_input.isFlipped = True
    with Compensation() as comp:
        origin = origins.add(origin_input)
        _finish(comp, origin, name, "joint origin")
        result = {"joint_origin": _joint_origin(ctx, origin, refs.assembly_context(source))}
    return result


# --- 5. joints and as-built joints ---------------------------------------------------------------


def _joint_geometry(ctx: Any, params: dict[str, Any], key: str) -> Any:
    spec = parse_joint_geometry(ctx.require(params, key, dict), key)
    return jointgeo.build(ctx, spec, key)


@handler("assembly.create_joint", mutating=True)
def assembly_create_joint(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    joint_type = check_joint_type(ctx.require(params, "type", str))
    motion = parse_motion(joint_type, ctx.require(params, "motion", dict, None))
    offset = params.get("offset")
    offset_value = (
        features.length_value(ctx, offset, param="offset") if offset is not None else None
    )
    angle = _angle(ctx, params)
    flip = ctx.require(params, "flip", bool, False)
    name = _optional_name(ctx, params)
    one = _joint_geometry(ctx, params, "geometry_one")
    two = _joint_geometry(ctx, params, "geometry_two")
    joints = ctx.root.joints
    joint_input = joints.createInput(one, two)
    jointgeo.apply_motion(joint_input, joint_type, motion, ctx)
    if offset_value is not None:
        joint_input.offset = offset_value
    if angle is not None:
        joint_input.angle = angle
    if flip:
        joint_input.isFlipped = True
    with Compensation() as comp:
        joint = joints.add(joint_input)
        _finish(comp, joint, name, "joint")
        result = {"joint": _joint(ctx, joint)}
    return result


@handler("assembly.create_as_built_joint", mutating=True)
def assembly_create_as_built_joint(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    joint_type = check_joint_type(ctx.require(params, "type", str))
    motion = parse_motion(joint_type, ctx.require(params, "motion", dict, None))
    name = _optional_name(ctx, params)
    one = _occurrence(ctx, ctx.require(params, "occurrence_one", str), "occurrence_one")
    two = _occurrence(ctx, ctx.require(params, "occurrence_two", str), "occurrence_two")
    if refs.same_entity(one, two) or one.fullPathName == two.fullPathName:
        raise _invalid("an as-built joint needs two different occurrences", param="occurrence_two")
    raw = ctx.require(params, "geometry", dict, None)
    if raw is None and joint_type != "rigid":
        raise _invalid(
            f"parameter 'geometry' is required for a {joint_type} as-built joint "
            "(only rigid works without it)",
            param="geometry",
        )
    geometry = None
    if raw is not None:
        spec = parse_joint_geometry(raw, "geometry")
        if spec["kind"] == "joint_origin":
            raise _invalid(
                "as-built joints snap to geometry (point, face, curve, profile), not to a joint "
                "origin",
                param="geometry.kind",
            )
        geometry = jointgeo.build(ctx, spec, "geometry")
    built = ctx.root.asBuiltJoints
    joint_input = built.createInput(one, two, geometry)
    jointgeo.apply_motion(joint_input, joint_type, motion, ctx)
    with Compensation() as comp:
        joint = built.add(joint_input)
        _finish(comp, joint, name, "as-built joint")
        result = {"joint": _joint(ctx, joint)}
    return result


# --- 5. limits and driving -----------------------------------------------------------------------


def _number(value: Any, param: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _invalid(f"parameter '{param}' must be a finite number", param=param)
    return float(value)


def _limit_param(params: dict[str, Any], key: str) -> float | str | None:
    value = params.get(key)
    if value is None or value == "off":
        return value
    if isinstance(value, str):
        raise _invalid(f"parameter '{key}' must be a number, \"off\" or null", param=key)
    return _number(value, key)


def _restore_limits(limits: Any) -> Any:
    saved = [(attr, getattr(limits, attr)) for _, _, attr in LIMIT_FIELDS]
    saved += [(attr, getattr(limits, attr)) for _, attr, _ in LIMIT_FIELDS]

    def undo() -> None:
        for attr, value in saved:
            setattr(limits, attr, value)

    return undo


@handler("assembly.set_joint_limits", mutating=True)
def assembly_set_joint_limits(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    joint, joint_type = _typed_joint(ctx, ctx.require(params, "joint", str))
    dof = check_dof(joint_type, ctx.require(params, "dof", str))
    changes = {key: _limit_param(params, key) for key, _, _ in LIMIT_FIELDS}
    changes = {key: value for key, value in changes.items() if value is not None}
    if not changes:
        raise _invalid("nothing to change: pass 'min', 'max' and/or 'rest' (a number or \"off\")")
    motion = joint.jointMotion
    merged = _limits(ctx, motion, dof)
    merged.update({k: None if v == "off" else v for k, v in changes.items()})  # type: ignore[misc]
    low, high = merged["min"], merged["max"]
    if low is not None and high is not None and low > high:
        unit = "degrees" if dof in ROTATIONAL_DOFS else "mm"
        raise _invalid(
            f"{dof} limits: min {low} is greater than max {high} ({unit})", min=low, max=high
        )
    limits = getattr(motion, DOF_ATTRS[dof] + "Limits")
    fields = {key: (enabled, value) for key, enabled, value in LIMIT_FIELDS}
    with Compensation() as comp:
        comp.add(_restore_limits(limits), f"{dof} limits")
        for key, change in changes.items():
            enabled, value = fields[key]
            if change == "off":
                setattr(limits, enabled, False)
                continue
            setattr(limits, value, _dof_in(ctx, dof, change))  # type: ignore[arg-type]
            setattr(limits, enabled, True)
    return {"joint": _joint(ctx, joint)}


@handler("assembly.drive_joint", mutating=True)
def assembly_drive_joint(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    joint, joint_type = _typed_joint(ctx, ctx.require(params, "joint", str))
    values = ctx.require(params, "values", dict)
    capture = ctx.require(params, "capture_position", bool, True)
    if not values:
        raise _invalid("parameter 'values' must set at least one degree of freedom", param="values")
    requested = {
        check_dof(joint_type, dof, "values"): _number(value, f"values.{dof}")
        for dof, value in values.items()
    }
    internal = {dof: _dof_in(ctx, dof, value) for dof, value in requested.items()}
    motion = joint.jointMotion
    attrs = {dof: DOF_ATTRS[dof] + "Value" for dof in internal}
    old = {dof: getattr(motion, attr) for dof, attr in attrs.items()}

    def undo() -> None:
        for dof, value in old.items():
            if getattr(motion, attrs[dof]) != value:
                setattr(motion, attrs[dof], value)

    with Compensation() as comp:
        comp.add(undo, "joint values")
        for dof, value in internal.items():
            setattr(motion, attrs[dof], value)
        clamped = [
            dof
            for dof, value in internal.items()
            if abs(float(getattr(motion, attrs[dof])) - value) > CLAMP_TOLERANCE
        ]
        captured = _capture(ctx.design, capture)
    return {
        "joint": _joint(ctx, joint),
        "requested": requested,
        "clamped": clamped,
        "captured": captured,
    }


# --- 6. rigid groups and joint listing -----------------------------------------------------------


def _rigid_group(group: Any, fallback: list[Any] | None = None) -> dict[str, Any]:
    """``{"ref", "name", "occurrences", "include_children", "is_suppressed"}``."""
    members = _items(_safe(group, "occurrences")) or (fallback or [])
    include = _safe(group, "includeChildren")
    return {
        "ref": refs.to_ref(group),
        "name": _safe(group, "name"),
        "occurrences": [_occurrence_ref(o) for o in members],
        "include_children": bool(include) if include is not None else None,
        "is_suppressed": bool(_safe(group, "isSuppressed", False)),
    }


@handler("assembly.create_rigid_group", mutating=True)
def assembly_create_rigid_group(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    refs_in = ctx.require(params, "occurrences", list)
    include_children = ctx.require(params, "include_children", bool, True)
    name = _optional_name(ctx, params)
    occurrences: list[Any] = []
    for i, ref in enumerate(refs_in):
        occurrence = _in_root_context(ctx, _occurrence(ctx, ref, f"occurrences[{i}]"))
        if any(o.fullPathName == occurrence.fullPathName for o in occurrences):
            raise _invalid(f"occurrences[{i}] is listed twice", param=f"occurrences[{i}]")
        occurrences.append(occurrence)
    if len(occurrences) < 2:
        raise _invalid(
            "a rigid group needs at least two occurrences", param="occurrences", count=len(refs_in)
        )
    collection = adsk.core.ObjectCollection.create()
    for occurrence in occurrences:
        collection.add(occurrence)
    with Compensation() as comp:
        group = ctx.root.rigidGroups.add(collection, include_children)
        _finish(comp, group, name, "rigid group")
        result = {"rigid_group": _rigid_group(group, occurrences)}
    return result


@handler("assembly.list_joints")
def assembly_list_joints(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    joints: list[dict[str, Any]] = []
    as_built: list[dict[str, Any]] = []
    origins: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    for component in _items(ctx.design.allComponents):
        joints.extend(_joint(ctx, j) for j in _items(_safe(component, "joints")))
        as_built.extend(_joint(ctx, j) for j in _items(_safe(component, "asBuiltJoints")))
        origins.extend(_joint_origin(ctx, o) for o in _items(_safe(component, "jointOrigins")))
        groups.extend(_rigid_group(g) for g in _items(_safe(component, "rigidGroups")))
    return {
        "joints": joints,
        "as_built_joints": as_built,
        "joint_origins": origins,
        "rigid_groups": groups,
    }


# --- 6. interference -----------------------------------------------------------------------------

INTERFERENCE_TYPES = ("Occurrence", "BRepBody")


def _interference_items(ctx: Any, refs_in: list[Any] | None) -> list[Any]:
    """Root-context occurrences and bodies to check; default: every occurrence and the
    bodies of root."""
    if refs_in is None:
        root = ctx.root
        return _items(root.allOccurrences) + _items(_safe(root, "bRepBodies"))
    items: list[Any] = []
    for i, ref in enumerate(refs_in):
        param = f"items[{i}]"
        entity = _resolve(ctx, ref, param)
        kind = refs.type_name(entity)
        if kind not in INTERFERENCE_TYPES:
            raise _invalid(f"{param} must be an occurrence or a body, got {kind}", param=param)
        entity = _in_root_context(ctx, entity)
        if any(_entity_ref(e) == _entity_ref(entity) for e in items):
            raise _invalid(f"{param} is listed twice", param=param)
        items.append(entity)
    return items


def _entity_ref(entity: Any) -> str | None:
    if entity is None:
        return None
    if refs.type_name(entity) == "Occurrence":
        return _occurrence_ref(entity)
    return refs.to_ref(entity)


@handler("assembly.analyze_interference")
def assembly_analyze_interference(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    refs_in = ctx.require(params, "items", list, None)
    coincident = ctx.require(params, "include_coincident_faces", bool, False)
    items = _interference_items(ctx, refs_in)
    if len(items) < 2:
        raise _invalid(
            f"interference needs at least two occurrences or bodies, got {len(items)}",
            param="items",
            count=len(items),
        )
    design = ctx.design
    if not hasattr(design, "analyzeInterference"):
        raise _api_error("interference analysis is not available in this Fusion version")
    collection = adsk.core.ObjectCollection.create()
    for item in items:
        collection.add(item)
    interference_input = design.createInterferenceInput(collection)
    interference_input.areCoincidentFacesIncluded = coincident
    results = design.analyzeInterference(interference_input)
    found = []
    # The interference bodies are transient: nothing is added to the design (no createBodies).
    for result in _items(results):
        body = _safe(result, "interferenceBody")
        volume = _safe(body, "volume")
        box = _safe(body, "boundingBox")
        found.append(
            {
                "entity_one": _entity_ref(_safe(result, "entityOne")),
                "entity_two": _entity_ref(_safe(result, "entityTwo")),
                "volume_mm3": (
                    round(ctx.units.volume_out(volume), USER_ROUND_DIGITS)
                    if volume is not None
                    else None
                ),
                "bbox": geometry.bbox(box, ctx.units) if box is not None else None,
            }
        )
    return {"checked": len(items), "count": len(found), "interferences": found}

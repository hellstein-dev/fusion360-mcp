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

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.refs import with_context
from fusion_mcp.protocol.units import point_to_internal

from ..core import refs, transforms
from ..core.compensate import Compensation
from ..core.describe import describe
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
    """``entity`` as root-context geometry (D2).

    Proxies, occurrences and entities owned by root are returned as is. A native entity of
    another component becomes its proxy through the component's only occurrence; zero or
    several occurrences -> ``INVALID_PARAMS`` with ``details.candidates``.
    """
    if refs.assembly_context(entity) is not None or refs.type_name(entity) == "Occurrence":
        return entity
    owner = _owner_component(entity)
    root = ctx.root
    if owner is None or refs.same_entity(owner, root):
        return entity
    found = [
        o for o in _items(root.allOccurrences) if refs.same_entity(_safe(o, "component"), owner)
    ]
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


def _create_component(comp: Compensation, owner: Any, first: Any) -> tuple[Any, Any | None]:
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
        occurrence, first_moved = _create_component(comp, owner, bodies[0])
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

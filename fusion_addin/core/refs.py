"""Stable entity references (design D6, INV-7).

An entity goes out as ``{"ref": <entityToken>, "type": "BRepFace", "name": ...}``. Objects
without a token get a typed name path: ``param:<name>``, ``component:<name>``,
``occurrence:<fullPathName>``. Origin planes/axes/point go out as ``origin:<NAME>`` (c03
D15). Timeline groups (no token) go out as ``timeline:<name>``, which resolves to the
``TimelineObject`` of that name (c09 D1). ``resolve`` turns a ref back into the entity or
raises ``ENTITY_NOT_FOUND``.

Entities seen through an occurrence (proxies, ``assemblyContext`` set) go out as
``<native ref>@<occurrence fullPathName>`` and resolve back to a proxy built with
``createForAssemblyContext`` (c10 D1). Occurrences keep their own form: the path is their
context already.
"""

from __future__ import annotations

from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.refs import (
    format_origin_ref,
    is_origin_ref,
    parse_origin_ref,
    split_context,
    with_context,
)

PARAM_PREFIX = "param:"
COMPONENT_PREFIX = "component:"
OCCURRENCE_PREFIX = "occurrence:"
TIMELINE_PREFIX = "timeline:"


def type_name(entity: Any) -> str:
    """``"adsk::fusion::BRepFace"`` -> ``"BRepFace"``."""
    object_type = getattr(entity, "objectType", None)
    if isinstance(object_type, str) and object_type:
        return object_type.rsplit("::", 1)[-1]
    return type(entity).__name__


def _token(entity: Any) -> str | None:
    try:
        token = entity.entityToken
    except Exception:
        return None
    return token if isinstance(token, str) and token else None


ORIGIN_ATTRS = {
    "XY": "xYConstructionPlane",
    "XZ": "xZConstructionPlane",
    "YZ": "yZConstructionPlane",
    "X": "xConstructionAxis",
    "Y": "yConstructionAxis",
    "Z": "zConstructionAxis",
    "O": "originConstructionPoint",
}
CONSTRUCTION_TYPES = ("ConstructionPlane", "ConstructionAxis", "ConstructionPoint")


def _owner(entity: Any) -> Any:
    for attr in ("component", "parentComponent"):
        try:
            owner = getattr(entity, attr, None)
        except Exception:
            owner = None
        if owner is not None:
            return owner
    return None


def origin_name(entity: Any) -> str | None:
    """``"XY"``... if ``entity`` is an origin plane/axis/point of its own component."""
    if type_name(entity) not in CONSTRUCTION_TYPES:
        return None
    owner = _owner(entity)
    if owner is None:
        return None
    token = _token(entity)
    for name, attr in ORIGIN_ATTRS.items():
        try:
            element = getattr(owner, attr)
        except Exception:
            continue
        if element is entity or (token is not None and _token(element) == token):
            return name
    return None


def _get(entity: Any, attr: str) -> Any:
    try:
        return getattr(entity, attr, None)
    except Exception:
        return None


def assembly_context(entity: Any) -> Any:
    """The occurrence a proxy is seen through; ``None`` for native entities."""
    return _get(entity, "assemblyContext")


def native_of(entity: Any) -> Any:
    """``nativeObject`` of an assembly-context proxy, else the entity itself."""
    native = _get(entity, "nativeObject")
    return native if native is not None else entity


def owner_component(entity: Any) -> Any:
    """Component that owns ``entity``: ``parentComponent``, through the body for B-Rep
    faces/edges/vertices and through the sketch for sketch entities; ``None`` if unknown."""
    for path in (
        ("parentComponent",),
        ("body", "parentComponent"),
        ("parentSketch", "parentComponent"),
    ):
        value = entity
        for attr in path:
            value = _get(value, attr)
            if value is None:
                break
        if value is not None:
            return value
    return None


def same_entity(a: Any, b: Any) -> bool:
    """Fusion hands out a new wrapper per access: compare by identity, ``==`` or token."""
    if a is b:
        return True
    if a is None or b is None:
        return False
    try:
        if a == b:
            return True
    except Exception:
        pass
    token_a, token_b = _token(a), _token(b)
    return token_a is not None and token_a == token_b


def to_ref(entity: Any) -> str:
    context = assembly_context(entity)
    if context is not None and type_name(entity) != "Occurrence":
        native = _get(entity, "nativeObject")
        if native is not None and native is not entity:
            try:
                return with_context(to_ref(native), context.fullPathName)
            except ValueError:
                pass
    origin = origin_name(entity)
    if origin is not None:
        return format_origin_ref(origin)
    token = _token(entity)
    if token:
        return token
    kind = type_name(entity)
    if kind == "Occurrence":
        return OCCURRENCE_PREFIX + entity.fullPathName
    if kind == "Component":
        return COMPONENT_PREFIX + entity.name
    if kind.endswith("Parameter"):
        return PARAM_PREFIX + entity.name
    if kind == "TimelineGroup":
        return TIMELINE_PREFIX + entity.name
    raise BridgeError(
        ErrorCode.INTERNAL, f"{kind} has neither an entityToken nor a name path", {"type": kind}
    )


def describe(entity: Any) -> dict[str, Any]:
    """Brief outward form of an entity: ``core.describe.describe(entity, detail="brief")``."""
    from .describe import describe as describe_entity

    return describe_entity(entity, None, "brief")


def _not_found(ref: str, why: str) -> BridgeError:
    return BridgeError(ErrorCode.ENTITY_NOT_FOUND, f"entity {ref!r} not found: {why}", {"ref": ref})


def resolve(design: Any, ref: Any, component: Any = None) -> Any:
    """Return the entity for ``ref`` (a string or a ``{"ref": ...}`` dict).

    ``origin:<NAME>`` is an origin element of ``component`` (default: the root component);
    an unknown name is ``INVALID_PARAMS``. ``<inner>@<path>`` is the proxy of ``inner``
    (resolved in the occurrence's component) in the context of occurrence ``path``.
    """
    if isinstance(ref, dict):
        ref = ref.get("ref")
    if not isinstance(ref, str) or not ref:
        raise BridgeError(ErrorCode.INVALID_PARAMS, "ref must be a non-empty string", {"ref": ref})
    try:
        inner, path = split_context(ref)
    except ValueError as exc:
        raise BridgeError(ErrorCode.INVALID_PARAMS, str(exc), {"ref": ref}) from exc
    if path is not None:
        return _resolve_in_context(design, ref, inner, path)
    if is_origin_ref(ref):
        name = parse_origin_ref(ref)
        owner = component if component is not None else design.rootComponent
        return getattr(owner, ORIGIN_ATTRS[name])
    try:
        entity = _lookup(design, ref)
    except BridgeError:
        raise
    except Exception as exc:
        raise _not_found(ref, str(exc) or type(exc).__name__) from exc
    if entity is None:
        raise _not_found(ref, "it was deleted or never existed")
    return entity


def _resolve_in_context(design: Any, ref: str, inner: str, path: str) -> Any:
    """``<inner>@<path>`` -> ``native.createForAssemblyContext(occurrence)`` (c10 D1)."""
    occurrence = resolve(design, OCCURRENCE_PREFIX + path)
    component = occurrence.component
    native = native_of(resolve(design, inner, component))
    owner = owner_component(native)
    if owner is not None and not same_entity(owner, component):
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"entity is not in {component.name}: {ref!r} belongs to {owner.name}",
            {"ref": ref, "component": component.name, "owner": owner.name},
        )
    try:
        return native.createForAssemblyContext(occurrence)
    except Exception as exc:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"cannot put {type_name(native)} into the context of {path!r}: "
            + (str(exc) or type(exc).__name__),
            {"ref": ref},
        ) from exc


def _parent_group(item: Any) -> Any:
    try:
        return getattr(item, "parentGroup", None)
    except Exception:
        return None


def walk_timeline(timeline: Any) -> list[Any]:
    """Timeline items in order; the members of a group follow the group right after it.

    Works whether ``Timeline.item`` lists group members at the top level (they are
    skipped there, ``parentGroup`` is set) or only inside their group.
    """
    found: list[Any] = []
    for index in range(timeline.count):
        item = timeline.item(index)
        if _parent_group(item) is not None:
            continue
        found.append(item)
        if getattr(item, "isGroup", False):
            found.extend(item.item(j) for j in range(item.count))
    return found


def _timeline_lookup(design: Any, ref: str) -> Any:
    """``timeline:<name>`` -> the ``TimelineObject`` with that name (top level or in a group)."""
    name = ref[len(TIMELINE_PREFIX) :]
    matches = [item for item in walk_timeline(design.timeline) if item.name == name]
    if len(matches) > 1:
        candidates = []
        for item in matches:
            entity = None if getattr(item, "isGroup", False) else getattr(item, "entity", None)
            kind = "TimelineGroup" if entity is None else type_name(entity)
            candidates.append({"index": item.index, "type": kind})
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"{len(matches)} timeline items are named {name!r}: use the entity ref instead",
            {"ref": ref, "candidates": candidates},
        )
    return matches[0] if matches else None


def _lookup(design: Any, ref: str) -> Any:
    if ref.startswith(TIMELINE_PREFIX):
        return _timeline_lookup(design, ref)
    if ref.startswith(PARAM_PREFIX):
        return design.allParameters.itemByName(ref[len(PARAM_PREFIX) :])
    if ref.startswith(COMPONENT_PREFIX):
        return design.allComponents.itemByName(ref[len(COMPONENT_PREFIX) :])
    if ref.startswith(OCCURRENCE_PREFIX):
        path = ref[len(OCCURRENCE_PREFIX) :]
        occurrences = design.rootComponent.allOccurrences
        for i in range(occurrences.count):
            occurrence = occurrences.item(i)
            if occurrence.fullPathName == path:
                return occurrence
        return None
    found = design.findEntityByToken(ref)
    return found[0] if found else None


class Refs:
    """``ctx.refs``: resolver bound to the request's design."""

    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    def resolve(self, ref: Any, component: Any = None) -> Any:
        return resolve(self._ctx.design, ref, component)

    def to_ref(self, entity: Any) -> str:
        return to_ref(entity)

    def describe(self, entity: Any) -> dict[str, Any]:
        return describe(entity)

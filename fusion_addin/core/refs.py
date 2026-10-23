"""Stable entity references (design D6, INV-7).

An entity goes out as ``{"ref": <entityToken>, "type": "BRepFace", "name": ...}``. Objects
without a token get a typed name path: ``param:<name>``, ``component:<name>``,
``occurrence:<fullPathName>``. Origin planes/axes/point go out as ``origin:<NAME>`` (c03
D15). ``resolve`` turns a ref back into the entity or raises ``ENTITY_NOT_FOUND``.
"""

from __future__ import annotations

from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.refs import format_origin_ref, is_origin_ref, parse_origin_ref

PARAM_PREFIX = "param:"
COMPONENT_PREFIX = "component:"
OCCURRENCE_PREFIX = "occurrence:"


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


def to_ref(entity: Any) -> str:
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
    an unknown name is ``INVALID_PARAMS``.
    """
    if isinstance(ref, dict):
        ref = ref.get("ref")
    if not isinstance(ref, str) or not ref:
        raise BridgeError(ErrorCode.INVALID_PARAMS, "ref must be a non-empty string", {"ref": ref})
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


def _lookup(design: Any, ref: str) -> Any:
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

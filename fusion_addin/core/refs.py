"""Stable entity references (design D6, INV-7).

An entity goes out as ``{"ref": <entityToken>, "type": "BRepFace", "name": ...}``. Objects
without a token get a typed name path: ``param:<name>``, ``component:<name>``,
``occurrence:<fullPathName>``. ``resolve`` turns a ref back into the entity or raises
``ENTITY_NOT_FOUND``.
"""

from __future__ import annotations

from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

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


def to_ref(entity: Any) -> str:
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
    """Minimal outward form of an entity; groups extend it in their own handlers."""
    data: dict[str, Any] = {"ref": to_ref(entity), "type": type_name(entity)}
    name = getattr(entity, "name", None)
    if isinstance(name, str):
        data["name"] = name
    return data


def _not_found(ref: str, why: str) -> BridgeError:
    return BridgeError(ErrorCode.ENTITY_NOT_FOUND, f"entity {ref!r} not found: {why}", {"ref": ref})


def resolve(design: Any, ref: Any) -> Any:
    """Return the entity for ``ref`` (a string or a ``{"ref": ...}`` dict)."""
    if isinstance(ref, dict):
        ref = ref.get("ref")
    if not isinstance(ref, str) or not ref:
        raise BridgeError(ErrorCode.INVALID_PARAMS, "ref must be a non-empty string", {"ref": ref})
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

    def resolve(self, ref: Any) -> Any:
        return resolve(self._ctx.design, ref)

    def to_ref(self, entity: Any) -> str:
        return to_ref(entity)

    def describe(self, entity: Any) -> dict[str, Any]:
        return describe(entity)

"""``assembly.*``: components, occurrences, joints and interference (c10).

Joints, as-built joints, rigid groups and interference work on root-context geometry:
native refs of a component inserted exactly once are coerced to that occurrence
(``_in_root_context``, D2). Entities seen through an occurrence are addressed as
``<native ref>@<occurrence path>`` (``core/refs.py``, D1). Changes the timeline rollback does
not see (names, grounding, light bulb, transforms, joint values, limits, the active
component) are registered in a ``Compensation`` before they are made (D7, INV-5).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.refs import with_context

from ..core import refs
from ..core.describe import describe


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


def _occ(ctx: Any, occurrence: Any) -> dict[str, Any]:
    """Outward form of an occurrence (D8): c03 ``describe(..., "full")`` plus the light bulb
    and the number of joints that reference it."""
    data = describe(occurrence, ctx.units, "full")
    data["is_light_bulb_on"] = bool(_safe(occurrence, "isLightBulbOn", True))
    data["joint_count"] = _joint_count(ctx.design, occurrence)
    return data

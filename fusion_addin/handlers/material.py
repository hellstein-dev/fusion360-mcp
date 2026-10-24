"""``material.*``: physical material libraries, lookup and assignment (c12 D4, D5).

A material is assigned to a body, a component or — through its component — an occurrence
(``Occurrence`` has no ``material``: the change applies to every occurrence of the
component). A library material is assigned directly (Fusion copies it into the design).
Material changes add no timeline item, so ``material.assign`` registers the return of the
previous material in a c09 ``Compensation`` before the change (INV-5).
"""

from __future__ import annotations

from typing import Any

from ..core.compensate import Compensation
from ..core.features import resolve_one
from ..core.materials import (
    APPEARANCES,
    describe_item,
    describe_material,
    find_material,
    iter_libraries,
    list_items,
)
from ..core.refs import type_name
from ..core.registry import handler

MATERIAL_TARGETS = ("BRepBody", "Component", "Occurrence")
OCCURRENCE_NOTE = "applies to all occurrences of the component"


@handler("material.list_libraries")
def material_list_libraries(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    libraries = [
        {
            "id": lib.id,
            "name": lib.name,
            "is_native": bool(lib.isNative),
            "material_count": lib.materials.count,
            "appearance_count": lib.appearances.count,
        }
        for lib in iter_libraries(ctx.app)
    ]
    return {"libraries": libraries}


@handler("material.list")
def material_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return list_items(ctx, "materials", params)


def _material_owner(target: Any) -> Any:
    """The object whose ``material`` is read/set: the body/component, or the occurrence's
    component."""
    return target.component if type_name(target) == "Occurrence" else target


@handler("material.get")
def material_get(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    target = resolve_one(ctx, params.get("target"), MATERIAL_TARGETS, "target")
    kind = type_name(target)
    material = _material_owner(target).material
    appearance = getattr(target, "appearance", None) if kind != "Component" else None
    return {
        "target": ctx.refs.to_ref(target),
        "material": describe_material(ctx, material),
        "appearance": describe_material(ctx, appearance, APPEARANCES),
    }


@handler("material.assign", mutating=True)
def material_assign(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    target = resolve_one(ctx, params.get("target"), MATERIAL_TARGETS, "target")
    material, library = find_material(ctx, params.get("material"), params.get("library"))
    owner = _material_owner(target)
    previous_material = owner.material
    previous = describe_material(ctx, previous_material)
    with Compensation() as undo:

        def restore() -> None:
            owner.material = previous_material

        undo.add(restore, "restore material")
        owner.material = material
    result: dict[str, Any] = {
        "target": ctx.refs.to_ref(target),
        "applied_to": ctx.refs.to_ref(owner),
        "material": describe_material(ctx, owner.material) or describe_item(material, library),
        "previous": previous,
    }
    if owner is not target:
        result["note"] = OCCURRENCE_NOTE
    return result

"""``appearance.*``: appearance lookup, assignment and simple colour overrides (c12 D4, D6).

Targets are bodies, faces and occurrences (``Component`` has no ``appearance``). A library
appearance is copied into the design first (``design.appearances.addByCopy``) unless the
design already has it, so the result does not depend on Fusion's implicit copying; library
appearances are never modified. Changes add no timeline item, so a c09 ``Compensation``
returns the previous appearance and deletes an unused copy made by the call when anything
fails after the change (INV-5).
"""

from __future__ import annotations

from typing import Any

import adsk.core

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.exchange import format_color, parse_color

from ..core.compensate import Compensation
from ..core.features import resolve_one
from ..core.materials import (
    APPEARANCES,
    DESIGN,
    describe_item,
    describe_material,
    find_appearance,
    list_items,
)
from ..core.refs import type_name
from ..core.registry import handler

APPEARANCE_TARGETS = ("BRepBody", "BRepFace", "Occurrence", "Component")
# ``ColorProperty`` ids tried, in order, when an appearance has no ``color`` (older API).
COLOR_PROPERTY_IDS = ("opaque_albedo", "metal_f0", "transparent_color", "generic_diffuse")


@handler("appearance.list")
def appearance_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return list_items(ctx, APPEARANCES, params)


def _target(ctx: Any, params: dict[str, Any]) -> Any:
    target = resolve_one(ctx, params.get("target"), APPEARANCE_TARGETS, "target")
    if type_name(target) == "Component":
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            "a component has no appearance: assign it to its bodies or to an occurrence",
            {"param": "target", "ref": ctx.refs.to_ref(target), "actual": "Component"},
        )
    return target


def _drop_copy(copy: Any) -> None:
    if not copy.isUsed:
        copy.deleteMe()


def _assign(undo: Compensation, target: Any, appearance: Any) -> None:
    previous = target.appearance

    def restore() -> None:
        target.appearance = previous

    undo.add(restore, "restore appearance")
    target.appearance = appearance


def _in_design(ctx: Any, undo: Compensation, appearance: Any, label: str) -> tuple[Any, bool]:
    """The design's copy of ``appearance`` (made now if missing) and whether it was made."""
    if label == DESIGN:
        return appearance, False
    appearances = ctx.design.appearances
    existing = appearances.itemById(appearance.id)
    if existing is not None:
        return existing, False
    copy = appearances.addByCopy(appearance, appearance.name)
    undo.add(lambda: _drop_copy(copy), "delete appearance copy")
    return copy, True


@handler("appearance.assign", mutating=True)
def appearance_assign(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    target = _target(ctx, params)
    appearance, label = find_appearance(ctx, params.get("appearance"), params.get("library"))
    previous = describe_material(ctx, target.appearance, APPEARANCES)
    with Compensation() as undo:
        used, copied = _in_design(ctx, undo, appearance, label)
        _assign(undo, target, used)
    return {
        "target": ctx.refs.to_ref(target),
        "appearance": describe_item(used, DESIGN),
        "previous": previous,
        "copied": copied,
    }


def _color_property(appearance: Any) -> Any:
    properties = getattr(appearance, "appearanceProperties", None)
    if properties is None:
        return None
    for prop_id in COLOR_PROPERTY_IDS:
        prop = properties.itemById(prop_id)
        if prop is not None and hasattr(prop, "value"):
            return prop
    return None


def _rgba_of(appearance: Any) -> tuple[int, int, int, int] | None:
    color = getattr(appearance, "color", None)
    if color is None:
        prop = _color_property(appearance)
        color = prop.value if prop is not None else None
    if color is None:
        return None
    return (color.red, color.green, color.blue, color.opacity)


def _set_rgba(appearance: Any, rgba: tuple[int, int, int, int]) -> None:
    color = adsk.core.Color.create(*rgba)
    if hasattr(appearance, "color"):
        appearance.color = color
        return
    prop = _color_property(appearance)
    if prop is None:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"appearance {appearance.name!r} has no editable color: pick another 'base'",
            {"param": "base", "appearance": appearance.name},
        )
    prop.value = color


def _default_name(base: Any, rgba: tuple[int, int, int, int]) -> str:
    hex_color = format_color(rgba)
    return f"MCP {base.name} {hex_color[:7] if rgba[3] == 255 else hex_color}"


@handler("appearance.set_color", mutating=True)
def appearance_set_color(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    rgba = parse_color(params.get("color"))
    target = _target(ctx, params)
    current = None
    if params.get("base") is not None:
        base, _ = find_appearance(ctx, params["base"], param="base")
    else:
        base = target.appearance
        if base is None:
            raise BridgeError(
                ErrorCode.INVALID_PARAMS,
                "the target has no appearance to start from: pass 'base' (see appearance_list)",
                {"param": "base"},
            )
        current = base
    name = ctx.require(params, "name", str, None)
    appearances = ctx.design.appearances
    if current is not None and name in (None, current.name) and _rgba_of(current) == rgba:
        # The target already shows this colour (a repeated call, c15 5.2): reuse its
        # appearance instead of copying it under a "MCP MCP ..." name.
        return _color_result(ctx, target, current, False, rgba)
    name = name or _default_name(base, rgba)
    with Compensation() as undo:
        existing = appearances.itemByName(name)
        if existing is not None and _rgba_of(existing) == rgba:
            appearance, created = existing, False
        else:
            appearance = appearances.addByCopy(base, name)
            undo.add(lambda: _drop_copy(appearance), "delete appearance copy")
            created = True
            _set_rgba(appearance, rgba)
        _assign(undo, target, appearance)
    return _color_result(ctx, target, appearance, created, rgba)


def _color_result(
    ctx: Any, target: Any, appearance: Any, created: bool, rgba: tuple[int, int, int, int]
) -> dict[str, Any]:
    return {
        "target": ctx.refs.to_ref(target),
        "appearance": describe_item(appearance, DESIGN),
        "created": created,
        "color": format_color(rgba),
    }

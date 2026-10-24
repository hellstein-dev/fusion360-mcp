"""Sheet metal rules, enums and flat pattern helpers of the ``sheet_metal.*`` handlers (c13
D2, D3, D7). Preview API calls live in ``core.sheet_metal_api``.

- Enums (D2): ``sheet_metal_enum(kind, param, value)`` checks ``value`` against the vocabulary
  of ``fusion_mcp.protocol.sheet_metal`` (``INVALID_PARAMS``) and maps it to the
  ``adsk.fusion`` enum value; an enum this Fusion lacks is ``api_unavailable``.
- Rules (D3): a rule is addressed by ``(name, source)``, never by index (INV-7).
  ``describe_rule`` gives every length in mm with its expression (INV-6); ``find_rule``
  looks in the design first, then in the library; ``set_rule_value`` sets a number through
  ``.value`` (cm, converted from the request units) and a string through ``.expression``.
  ``apply_rule`` makes a rule the active rule of a component, copying a library rule into the
  design first; every change registers an undo in a ``Compensation`` (rules are no timeline
  items, the dispatcher rollback does not remove them).
- Flat pattern (D7): ``describe_flat_pattern``, ``resolve_component``, ``component_ref``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import check_choice
from fusion_mcp.protocol.sheet_metal import (
    BEND_POSITIONS,
    FLANGE_ORIENTATIONS,
    HEIGHT_DATUMS,
    RELIEF_SHAPES,
    RULE_SOURCES,
)
from fusion_mcp.protocol.units import angle_from_internal, length_from_internal, length_to_internal

from .compensate import Compensation
from .features import resolve_one
from .refs import same_entity, type_name
from .sheet_metal_api import api_unavailable

OUT = "mm"  # every length of a sheet metal answer is in mm

# kind -> (adsk.fusion enum class, vocabulary, value -> enum attribute names to try in order).
SHEET_METAL_ENUMS: dict[str, tuple[str, Sequence[str], dict[str, tuple[str, ...]]]] = {
    "relief_shape": (
        "BendReliefShapes",
        RELIEF_SHAPES,
        {
            "straight": ("StraightBendReliefShape",),
            "tear": ("TearBendReliefShape",),
            "round": ("RoundBendReliefShape",),
        },
    ),
    "orientation": (
        "FlangeOrientations",
        FLANGE_ORIENTATIONS,
        {
            "side_one": ("SideOneFlangeOrientation", "Side1FlangeOrientation"),
            "side_two": ("SideTwoFlangeOrientation", "Side2FlangeOrientation"),
            "centered": ("CenteredFlangeOrientation", "CenterFlangeOrientation"),
        },
    ),
    "height_datum": (
        "FlangeHeightDatumTypes",
        HEIGHT_DATUMS,
        {
            "inner": ("InnerFacesFlangeHeightDatumType",),
            "outer": ("OuterFacesFlangeHeightDatumType",),
            "tangent": ("TangentToBendFlangeHeightDatumType",),
        },
    ),
    "bend_position": (
        "BendPositionTypes",
        BEND_POSITIONS,
        {
            "start_edge": ("StartEdgeBendPositionType",),
            "inside": ("InsideBendPositionType",),
            "outside": ("OutsideBendPositionType",),
            "tangent_to_side": ("TangentToSideBendPositionType",),
        },
    ),
}

# Rule properties holding a ``SheetMetalRuleValue``: answer key -> Fusion attribute.
RULE_VALUES = {
    "thickness": "thickness",
    "bend_radius": "bendRadius",
    "gap": "gap",
    "relief_width": "reliefWidth",
    "relief_depth": "reliefDepth",
    "relief_remnant": "reliefRemnant",
}


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _items(collection: Any) -> list[Any]:
    if collection is None:
        return []
    if hasattr(collection, "count") and hasattr(collection, "item"):
        return [collection.item(i) for i in range(collection.count)]
    return list(collection)


# --- enums (D2) ------------------------------------------------------------------------------


def sheet_metal_enum(kind: str, param: str, value: Any) -> Any:
    """``value`` of vocabulary ``kind`` (a key of ``SHEET_METAL_ENUMS``) -> the
    ``adsk.fusion`` enum value; unknown value -> ``INVALID_PARAMS``."""
    enum_name, choices, attrs = SHEET_METAL_ENUMS[kind]
    choice = check_choice(param, value, choices)
    enum = getattr(adsk.fusion, enum_name, None)
    for attr in attrs[choice]:
        if enum is not None and hasattr(enum, attr):
            return getattr(enum, attr)
    raise api_unavailable(f"{enum_name}.{attrs[choice][0]}")


def enum_name(kind: str, value: Any) -> str | None:
    """Vocabulary name of the ``adsk.fusion`` enum ``value`` of ``kind`` (``None`` if
    unknown)."""
    enum_cls, _, attrs = SHEET_METAL_ENUMS[kind]
    enum = getattr(adsk.fusion, enum_cls, None)
    for name, candidates in attrs.items():
        for attr in candidates:
            if enum is not None and hasattr(enum, attr) and getattr(enum, attr) == value:
                return name
    return None


# --- rules (D3) ------------------------------------------------------------------------------


def _rule_collections(ctx: Any) -> dict[str, Any]:
    design = ctx.design
    return {
        "design": _get(design, "designSheetMetalRules"),
        "library": _get(design, "librarySheetMetalRules"),
    }


def rules_of(ctx: Any, source: str) -> list[tuple[Any, str]]:
    """``(rule, source)`` of ``source`` (design / library / all, design first)."""
    source = check_choice("source", source, RULE_SOURCES)
    collections = _rule_collections(ctx)
    if collections["design"] is None and collections["library"] is None:
        raise api_unavailable("Design.designSheetMetalRules")
    out: list[tuple[Any, str]] = []
    for name in ("design", "library"):
        if source in (name, "all"):
            out.extend((rule, name) for rule in _items(collections[name]))
    return out


def _value(rule_value: Any) -> dict[str, Any]:
    cm = _get(rule_value, "value")
    data: dict[str, Any] = {
        "mm": round(length_from_internal(float(cm), OUT), 6)
        if isinstance(cm, int | float)
        else None
    }
    expression = _get(rule_value, "expression")
    if isinstance(expression, str):
        data["expression"] = expression
    return data


def describe_rule(rule: Any, source: str) -> dict[str, Any]:
    """D3: ``{"name", "source", "units", <lengths>: {"mm", "expression"}, "k_factor",
    "relief_shape", "is_default" (library) | "is_used" (design)}``."""
    data: dict[str, Any] = {"name": _get(rule, "name"), "source": source}
    units = _get(rule, "units")
    if isinstance(units, str):
        data["units"] = units
    for key, attr in RULE_VALUES.items():
        value = _get(rule, attr)
        if value is not None:
            data[key] = _value(value)
    k_factor = _get(rule, "kFactor")
    if isinstance(k_factor, int | float) and not isinstance(k_factor, bool):
        data["k_factor"] = float(k_factor)
    data["relief_shape"] = enum_name("relief_shape", _get(rule, "reliefShape"))
    if source == "library":
        data["is_default"] = bool(_get(rule, "isDefault", False))
    else:
        data["is_used"] = bool(_get(rule, "isUsed", False))
    return data


def find_rule(ctx: Any, name: Any, source: Any = "all", param: str = "rule") -> tuple[Any, str]:
    """``(rule, source)`` of the rule called ``name``: design first, then library; none ->
    ``ENTITY_NOT_FOUND`` with the known names."""
    if not isinstance(name, str) or not name.strip():
        raise _invalid(f"parameter '{param}' must be a rule name", param=param)
    rules = rules_of(ctx, source)
    for rule, where in rules:
        if _get(rule, "name") == name:
            return rule, where
    raise BridgeError(
        ErrorCode.ENTITY_NOT_FOUND,
        f"no sheet metal rule {name!r} in {source}",
        {
            "param": param,
            "rule": name,
            "source": source,
            "known": [_get(r, "name") for r, _ in rules],
        },
    )


def default_library_rule(ctx: Any) -> Any:
    """The library rule marked ``isDefault`` (else the first library rule)."""
    rules = [rule for rule, _ in rules_of(ctx, "library")]
    for rule in rules:
        if _get(rule, "isDefault", False):
            return rule
    if not rules:
        raise BridgeError(ErrorCode.ENTITY_NOT_FOUND, "the sheet metal rule library is empty")
    return rules[0]


def set_rule_value(rule_value: Any, value: Any, units: str, param: str) -> None:
    """A number (``units``, >= 0) -> ``.value`` in cm; a string -> ``.expression`` as is
    (may use ``Thickness``)."""
    if isinstance(value, str):
        if not value.strip():
            raise _invalid(f"parameter '{param}' must not be an empty expression", param=param)
        rule_value.expression = value
        return
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _invalid(
            f"parameter '{param}' must be a length (number) or an expression (string)",
            param=param,
            value=repr(value),
        )
    if value < 0:
        raise _invalid(f"parameter '{param}' must be >= 0", param=param, value=value)
    rule_value.value = float(length_to_internal(value, units))


def check_k_factor(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise _invalid("parameter 'k_factor' must be a number in [0, 1]", param="k_factor")
    if not 0.0 <= float(value) <= 1.0:
        raise _invalid(
            f"parameter 'k_factor' must be in [0, 1], got {value}", param="k_factor", value=value
        )
    return float(value)


def delete_entity(entity: Any) -> None:
    if entity.deleteMe() is False:
        raise RuntimeError("deleteMe returned False")


def apply_rule(
    ctx: Any, component: Any, name: Any, source: Any, comp: Compensation
) -> dict[str, Any]:
    """Make rule ``name`` the active rule of ``component`` (D3). A library rule is copied into
    the design first (unless the design has a rule of that name). Undos (in ``comp``):
    restore the previous active rule, then delete the copy. Returns ``{"rule", "previous",
    "copied_from_library"}``."""
    rule, where = find_rule(ctx, name, source)
    copied = False
    if where == "library":
        design_rules = _rule_collections(ctx)["design"]
        existing = design_rules.itemByName(rule.name) if design_rules is not None else None
        if existing is not None:
            rule = existing
        else:
            if design_rules is None:
                raise api_unavailable("Design.designSheetMetalRules")
            rule = design_rules.addByCopy(rule, rule.name)
            if rule is None:
                raise BridgeError(
                    ErrorCode.FUSION_API_ERROR, f"Fusion could not copy rule {name!r}"
                )
            copy = rule
            comp.add(lambda: delete_entity(copy), f"delete rule copy {copy.name!r}")
            copied = True
    previous = _get(component, "activeSheetMetalRule")
    previous_name = _get(previous, "name") if previous is not None else None
    if previous is None or not same_entity(previous, rule):

        def restore() -> None:
            # ``None`` too: a component without sheet metal data had no rule; if Fusion
            # refuses that, the failure is reported in ``compensation_errors``.
            component.activeSheetMetalRule = previous

        comp.add(restore, "restore active sheet metal rule")
        component.activeSheetMetalRule = rule
    return {
        "rule": describe_rule(rule, "design"),
        "previous": previous_name,
        "copied_from_library": copied,
    }


def active_rule_name(component: Any) -> str | None:
    rule = _get(component, "activeSheetMetalRule")
    return _get(rule, "name") if rule is not None else None


# --- components and flat patterns (D7) -------------------------------------------------------


def resolve_component(ctx: Any, ref: Any, param: str = "component") -> Any:
    """``None`` / ``"root"`` -> the root component; a component or occurrence ref -> the
    component."""
    if ref is None or ref == "root":
        return ctx.root
    entity = resolve_one(ctx, ref, ("Component", "Occurrence"), param)
    return entity.component if type_name(entity) == "Occurrence" else entity


def component_ref(ctx: Any, component: Any) -> str:
    """``"root"`` for the root component, else its ref."""
    if same_entity(component, ctx.root):
        return "root"
    ref: str = ctx.refs.to_ref(component)
    return ref


def flat_pattern_of(component: Any) -> Any:
    """``component.flatPattern`` or ``None`` (also for an invalid one)."""
    flat = _get(component, "flatPattern")
    if flat is None or _get(flat, "isValid", True) is False:
        return None
    return flat


def no_flat_pattern(ctx: Any, component: Any) -> BridgeError:
    return BridgeError(
        ErrorCode.ENTITY_NOT_FOUND,
        "the component has no flat pattern: create it with sheet_metal_create_flat_pattern",
        {"param": "component", "component": component_ref(ctx, component)},
    )


def _bend_info(flat: Any, edge: Any) -> dict[str, Any] | None:
    try:
        answer = flat.getBendInfo(edge)
    except Exception:
        return None
    if not isinstance(answer, tuple | list):
        return None
    if len(answer) == 3:
        ok, is_up, angle = answer
        if ok is False:
            return None
    elif len(answer) == 2:
        is_up, angle = answer
    else:
        return None
    return {"angle_deg": round(angle_from_internal(float(angle)), 6), "is_up": bool(is_up)}


def describe_flat_pattern(ctx: Any, component: Any, flat: Any) -> dict[str, Any]:
    """D7: ``{"component", "exists": true, "size_mm": [dx, dy], "thickness_mm", "bend_count",
    "bends": [{"angle_deg", "is_up"}]}``."""
    data: dict[str, Any] = {"component": component_ref(ctx, component), "exists": True}
    box = _get(_get(flat, "flatBody"), "boundingBox")
    thickness = None
    if box is not None:
        lo, hi = box.minPoint, box.maxPoint
        data["size_mm"] = [
            round(length_from_internal(hi.x - lo.x, OUT), 6),
            round(length_from_internal(hi.y - lo.y, OUT), 6),
        ]
        thickness = hi.z - lo.z
    rule = _get(component, "activeSheetMetalRule")
    rule_thickness = _get(_get(rule, "thickness"), "value")
    if isinstance(rule_thickness, int | float) and not isinstance(rule_thickness, bool):
        thickness = float(rule_thickness)
    data["thickness_mm"] = (
        round(length_from_internal(thickness, OUT), 6) if thickness is not None else None
    )
    edges = _items(_get(_get(flat, "bendLinesBody"), "edges"))
    data["bend_count"] = len(edges)
    data["bends"] = [info for info in (_bend_info(flat, e) for e in edges) if info is not None]
    return data

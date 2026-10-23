"""``design.*``: design type and distance units of the active design (c04 D4).

Display units only change how Fusion shows lengths and reads unitless expressions; tool
inputs stay in mm (or their explicit ``units``) whatever is set here (INV-6).
"""

from __future__ import annotations

from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

from ..core import documents as docs
from ..core import refs
from ..core.registry import handler

DESIGN_TYPES = ("parametric", "direct")


def settings(design: Any) -> dict[str, Any]:
    root = design.rootComponent
    parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType
    return {
        "design_type": docs.design_type(design),
        "units": docs.design_units(design),
        "document": docs.document_ref(design.parentDocument),
        "root_component": {"ref": refs.to_ref(root), "type": "Component", "name": root.name},
        "parameter_count": design.allParameters.count,
        "timeline_count": design.timeline.count if parametric else None,
    }


@handler("design.get_settings")
def design_get_settings(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return settings(ctx.design)


@handler("design.set_units")
def design_set_units(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    value = docs.units_to_fusion(ctx.require(params, "units", str))
    design = ctx.design
    design.fusionUnitsManager.distanceDisplayUnits = value
    return settings(design)


@handler("design.set_type", mutating=True)
def design_set_type(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    wanted = ctx.require(params, "design_type", str)
    if wanted not in DESIGN_TYPES:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"parameter 'design_type' must be one of {', '.join(DESIGN_TYPES)}",
            {"param": "design_type", "value": wanted},
        )
    confirm = ctx.require(params, "confirm_discard_history", bool, False)
    design = ctx.design
    current = docs.design_type(design)
    if current == wanted:
        return {**settings(design), "changed": False, "history_discarded": False}
    discards_history = current == "parametric" and wanted == "direct"
    if discards_history and not confirm:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            "switching to direct deletes the timeline (all feature history) and cannot be "
            "undone: pass confirm_discard_history=true only if the user asked for it",
            {"param": "confirm_discard_history"},
        )
    types = adsk.fusion.DesignTypes
    design.designType = types.DirectDesignType if wanted == "direct" else types.ParametricDesignType
    return {**settings(design), "changed": True, "history_discarded": discards_history}

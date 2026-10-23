"""``status.get``: what the agent needs to know about the running Fusion (``fusion_status``)."""

from __future__ import annotations

import sys
from typing import Any

import adsk.fusion

from ..core.registry import handler
from ..core.version import ADDIN_VERSION


@handler("status.get")
def status_get(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    app = ctx.app
    design = ctx.design_or_none
    document = app.activeDocument
    design_type = None
    units = None
    if design is not None:
        parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType
        design_type = "parametric" if parametric else "direct"
        units = design.fusionUnitsManager.defaultLengthUnits
    return {
        "fusion_version": app.version,
        "addin_version": ADDIN_VERSION,
        "python_version": sys.version,
        "document": document.name if document is not None else None,
        "design_type": design_type,
        "units": units,
    }

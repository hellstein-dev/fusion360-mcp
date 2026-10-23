"""``viewport.screenshot``: PNG of the active viewport for the agent to look at (c03 D11).

The camera is left where the shot was taken: the user sees what the agent saw.
"""

from __future__ import annotations

import base64
import contextlib
import os
import tempfile
from typing import Any

import adsk
import adsk.core

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

from ..core.registry import handler

ORIENTATIONS = {
    "current": None,
    "front": "FrontViewOrientation",
    "back": "BackViewOrientation",
    "top": "TopViewOrientation",
    "bottom": "BottomViewOrientation",
    "left": "LeftViewOrientation",
    "right": "RightViewOrientation",
    "iso_top_right": "IsoTopRightViewOrientation",
    "iso_top_left": "IsoTopLeftViewOrientation",
    "iso_bottom_right": "IsoBottomRightViewOrientation",
    "iso_bottom_left": "IsoBottomLeftViewOrientation",
}
VISUAL_STYLES = {
    "shaded": "ShadedVisualStyle",
    "shaded_edges": "ShadedWithVisibleEdgesOnlyVisualStyle",
    "wireframe": "WireframeVisualStyle",
}
DEFAULT_ORIENTATION = "iso_top_right"
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 768
MIN_SIZE = 64
MAX_SIZE = 2048


def _invalid(param: str, message: str) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, {"param": param})


def _choice(params: dict[str, Any], name: str, choices: dict[str, Any], default: Any) -> Any:
    value = params.get(name)
    if value is None:
        return default
    if value not in choices:
        raise _invalid(name, f"parameter '{name}' must be one of {', '.join(choices)}")
    return value


def _size(ctx: Any, params: dict[str, Any], name: str, default: int) -> int:
    value: int = ctx.require(params, name, int, default)
    if not MIN_SIZE <= value <= MAX_SIZE:
        raise _invalid(name, f"parameter '{name}' must be within {MIN_SIZE}..{MAX_SIZE} px")
    return value


@handler("viewport.screenshot")
def viewport_screenshot(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    orientation = _choice(params, "orientation", ORIENTATIONS, DEFAULT_ORIENTATION)
    style = _choice(params, "visual_style", VISUAL_STYLES, None)
    fit = ctx.require(params, "fit", bool, True)
    width = _size(ctx, params, "width", DEFAULT_WIDTH)
    height = _size(ctx, params, "height", DEFAULT_HEIGHT)

    viewport = ctx.app.activeViewport
    if viewport is None:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "no active viewport")
    camera = viewport.camera  # a copy: changes apply when it is assigned back
    camera.isSmoothTransition = False
    enum_name = ORIENTATIONS[orientation]
    if enum_name is not None:
        camera.viewOrientation = getattr(adsk.core.ViewOrientations, enum_name)
    camera.isFitView = fit
    viewport.camera = camera
    if style is not None:
        viewport.visualStyle = getattr(adsk.core.VisualStyles, VISUAL_STYLES[style])
    viewport.refresh()
    adsk.doEvents()

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        if not viewport.saveAsImageFile(path, width, height):
            raise BridgeError(
                ErrorCode.FUSION_API_ERROR,
                "Viewport.saveAsImageFile failed",
                {"width": width, "height": height},
            )
        with open(path, "rb") as file:
            data = file.read()
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
    return {
        "image_base64": base64.b64encode(data).decode("ascii"),
        "mime": "image/png",
        "width": width,
        "height": height,
        "orientation": orientation,
    }

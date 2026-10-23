"""``viewport_screenshot``: the agent looks at the model (c03 D11, D12)."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.utilities.types import Image
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import error_result
from fusion_mcp.server.bridge import Bridge

Orientation = Literal[
    "current",
    "front",
    "back",
    "top",
    "bottom",
    "left",
    "right",
    "iso_top_right",
    "iso_top_left",
    "iso_bottom_right",
    "iso_bottom_left",
]
VisualStyle = Literal["shaded", "shaded_edges", "wireframe"]
Pixels = Annotated[int, Field(ge=64, le=2048)]


def screenshot_result(result: dict[str, Any]) -> CallToolResult:
    """Bridge answer -> an image block plus a text block with the metadata as JSON."""
    try:
        data = base64.b64decode(result["image_base64"], validate=True)
    except (KeyError, TypeError, binascii.Error) as exc:
        return error_result(
            BridgeError(ErrorCode.INTERNAL, f"malformed screenshot from the add-in: {exc}")
        )
    meta = {k: result.get(k) for k in ("width", "height", "orientation")}
    return CallToolResult(
        content=[
            Image(data=data, format="png").to_image_content(),
            TextContent(type="text", text=json.dumps(meta)),
        ]
    )


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="viewport_screenshot")
    async def viewport_screenshot(
        orientation: Annotated[
            Orientation, Field(description="Camera direction; 'current' keeps the view")
        ] = "iso_top_right",
        fit: Annotated[bool, Field(description="Zoom to fit the whole model")] = True,
        width: Pixels = 1024,
        height: Pixels = 768,
        visual_style: Annotated[
            VisualStyle | None, Field(description="Change the display style; default: keep")
        ] = None,
    ) -> CallToolResult:
        """PNG picture of the active Fusion viewport, to check the model visually.

        Sets the camera (and optionally the visual style) and leaves it there, so the user
        sees the same view. Size 64..2048 px. Returns an image and a JSON text with
        {"width", "height", "orientation"}.

        Example: viewport_screenshot(orientation="top", visual_style="shaded_edges").
        """
        params: dict[str, Any] = {
            "orientation": orientation,
            "fit": fit,
            "width": width,
            "height": height,
        }
        if visual_style is not None:
            params["visual_style"] = visual_style
        try:
            result = await bridge.call("viewport.screenshot", params)
        except BridgeError as error:
            return error_result(error)
        return screenshot_result(result)

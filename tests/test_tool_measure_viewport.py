"""``measure_*`` and ``viewport_screenshot`` tools on ``FakeBridge`` (c03 5.6, 5.7)."""

import asyncio
import base64
import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ImageContent, TextContent

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text


class Rejected:
    """Arguments refused by the tool's input schema (``ToolError``) before the bridge."""

    is_error = True

    def __init__(self, message: str) -> None:
        self.message = message


PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 10


def call(name: str, arguments: dict[str, Any], bridge: FakeBridge | None = None) -> Any:
    bridge = bridge or FakeBridge()
    try:
        result = asyncio.run(call_tool(create_server(bridge), name, arguments))
    except ToolError as exc:
        return Rejected(str(exc)), bridge
    return result, bridge


# --- 5.6 measure -------------------------------------------------------------------------------


def test_measure_distance_refs_and_points() -> None:
    answer = {"distance_mm": 30.0, "point_a_mm": [0, 0, 30], "point_b_mm": [0, 0, 0]}
    bridge = FakeBridge({"measure.distance": answer})
    result, _ = call("measure_distance", {"a": "face-top", "b": [0, 0, 0]}, bridge)
    assert result.is_error is False
    assert bridge.last.method == "measure.distance"
    assert bridge.last.params == {"a": "face-top", "b": [0.0, 0.0, 0.0]}
    assert json.loads(result_text(result)) == answer


def test_measure_angle_two_and_three_operands() -> None:
    bridge = FakeBridge({"measure.angle": {"angle_deg": 90.0}})
    result, _ = call("measure_angle", {"a": "f1", "b": "origin:XY"}, bridge)
    assert bridge.last.method == "measure.angle"
    assert bridge.last.params == {"a": "f1", "b": "origin:XY"}
    assert json.loads(result_text(result)) == {"angle_deg": 90.0}
    call("measure_angle", {"a": [1, 0, 0], "b": [0, 0, 0], "c": [0, 1, 0]}, bridge)
    assert bridge.last.params == {"a": [1.0, 0.0, 0.0], "b": [0.0, 0.0, 0.0], "c": [0.0, 1.0, 0.0]}


def test_measure_rejects_bad_point() -> None:
    result, bridge = call("measure_distance", {"a": [0, 0], "b": "x"})
    assert result.is_error is True
    assert bridge.calls == []


# --- 5.7 viewport_screenshot -------------------------------------------------------------------


def screenshot_answer(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "image_base64": base64.b64encode(PNG).decode(),
        "mime": "image/png",
        "width": params["width"],
        "height": params["height"],
        "orientation": params["orientation"],
    }


def test_screenshot_returns_image_and_meta() -> None:
    bridge = FakeBridge({"viewport.screenshot": screenshot_answer})
    result, _ = call("viewport_screenshot", {"orientation": "top", "width": 640}, bridge)
    assert result.is_error is False
    assert bridge.last.method == "viewport.screenshot"
    assert bridge.last.params == {"orientation": "top", "fit": True, "width": 640, "height": 768}
    image, text = result.content
    assert isinstance(image, ImageContent)
    assert image.mime_type == "image/png"
    assert base64.b64decode(image.data) == PNG
    assert isinstance(text, TextContent)
    assert json.loads(text.text) == {"width": 640, "height": 768, "orientation": "top"}


def test_screenshot_defaults_and_visual_style() -> None:
    bridge = FakeBridge({"viewport.screenshot": screenshot_answer})
    call("viewport_screenshot", {"visual_style": "wireframe"}, bridge)
    assert bridge.last.params == {
        "orientation": "iso_top_right",
        "fit": True,
        "width": 1024,
        "height": 768,
        "visual_style": "wireframe",
    }


def test_screenshot_validation_and_errors() -> None:
    for bad in ({"width": 10}, {"height": 4096}, {"orientation": "isometric"}):
        result, bridge = call("viewport_screenshot", bad)
        assert result.is_error is True
        assert bridge.calls == []
    error = BridgeError(ErrorCode.FUSION_API_ERROR, "no active viewport")
    result, _ = call("viewport_screenshot", {}, FakeBridge({"viewport.screenshot": error}))
    assert result.is_error is True
    assert result_text(result).startswith("FUSION_API_ERROR: no active viewport")
    result, _ = call(
        "viewport_screenshot", {}, FakeBridge({"viewport.screenshot": {"image_base64": "**"}})
    )
    assert result.is_error is True
    assert result_text(result).startswith("INTERNAL")

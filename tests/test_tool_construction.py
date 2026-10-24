"""``construction_*`` tools on ``FakeBridge`` (c06 D5, tasks 4.1-4.3)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text


class Rejected:
    """Arguments refused by the tool's input schema (``ToolError``) before the bridge."""

    is_error = True

    def __init__(self, message: str) -> None:
        self.message = message


def call(name: str, arguments: dict[str, Any], bridge: FakeBridge | None = None) -> Any:
    bridge = bridge or FakeBridge()
    try:
        result = asyncio.run(call_tool(create_server(bridge), name, arguments))
    except ToolError as exc:
        return Rejected(str(exc)), bridge
    return result, bridge


PLANE = {
    "ref": "plane-1",
    "type": "ConstructionPlane",
    "name": "Plane1",
    "origin_mm": [0.0, 0.0, 15.0],
    "normal": [0.0, 0.0, 1.0],
    "health": "ok",
}


# --- 4.1 construction_plane ------------------------------------------------------------------


def test_plane_offset_sends_only_given_fields() -> None:
    bridge = FakeBridge({"construction.plane": PLANE})
    result, _ = call(
        "construction_plane", {"method": "offset", "plane": "origin:XY", "distance": 15}, bridge
    )
    assert bridge.last.method == "construction.plane"
    assert bridge.last.params == {"method": "offset", "plane": "origin:XY", "distance": 15}
    assert result.is_error is False
    assert json.loads(result_text(result)) == PLANE


def test_plane_expression_component_and_name_pass_through() -> None:
    bridge = FakeBridge({"construction.plane": PLANE})
    call(
        "construction_plane",
        {
            "method": "offset",
            "plane": "origin:XY",
            "distance": "height / 2",
            "component": "component:Lid",
            "name": "Lid plane",
        },
        bridge,
    )
    assert bridge.last.params == {
        "method": "offset",
        "plane": "origin:XY",
        "distance": "height / 2",
        "component": "component:Lid",
        "name": "Lid plane",
    }


def test_plane_validation_error_does_not_call_the_bridge() -> None:
    result, bridge = call("construction_plane", {"method": "offset", "plane": "origin:XY"})
    assert bridge.calls == []
    assert result.is_error is True
    text = result_text(result)
    assert text.startswith("INVALID_PARAMS:")
    assert "distance" in text
    result, bridge = call(
        "construction_plane",
        {"method": "offset", "plane": "origin:XY", "distance": 1, "edge": "e"},
    )
    assert bridge.calls == [] and result.is_error is True
    assert '"unexpected": [\n    "edge"\n  ]' in result_text(result)
    result, bridge = call(
        "construction_plane", {"method": "along_path", "path": "e", "proportion": 1.5}
    )
    assert bridge.calls == [] and "proportion" in result_text(result)


def test_plane_unknown_method_is_refused_by_the_schema() -> None:
    rejected, bridge = call("construction_plane", {"method": "mirror", "plane": "origin:XY"})
    assert rejected.is_error is True and bridge.calls == []


def test_plane_bridge_error_reaches_the_agent() -> None:
    error = BridgeError(
        ErrorCode.INVALID_PARAMS,
        "parameter 'plane' must be a planar face or a construction plane, got BRepFace (cylinder)",
        {"param": "plane", "expected": "planar", "got": "BRepFace (cylinder)"},
    )
    bridge = FakeBridge({"construction.plane": error})
    result, _ = call(
        "construction_plane", {"method": "offset", "plane": "cyl", "distance": 5}, bridge
    )
    assert result.is_error is True
    assert '"expected": "planar"' in result_text(result)


def test_plane_methods_send_their_fields() -> None:
    cases: list[dict[str, Any]] = [
        {"method": "angle", "edge": "origin:X", "angle": 30, "plane": "origin:XY"},
        {"method": "midplane", "plane": "top", "plane2": "bottom"},
        {"method": "two_edges", "edge": "a", "edge2": "b"},
        {"method": "three_points", "points": ["a", "b", "c"]},
        {"method": "tangent", "face": "cyl", "plane": "origin:XZ"},
        {"method": "tangent_at_point", "face": "cyl", "point": "v"},
        {"method": "along_path", "path": "e", "proportion": 0.5},
    ]
    for arguments in cases:
        bridge = FakeBridge({"construction.plane": PLANE})
        result, _ = call("construction_plane", arguments, bridge)
        assert result.is_error is False, arguments
        assert bridge.last.params == arguments


# --- 4.2 construction_axis -------------------------------------------------------------------

AXIS = {
    "ref": "axis-1",
    "type": "ConstructionAxis",
    "name": "Axis1",
    "origin_mm": [0.0, 0.0, 0.0],
    "direction": [0.0, 0.0, 1.0],
    "health": "ok",
}


def test_axis_methods_send_their_fields() -> None:
    cases: list[dict[str, Any]] = [
        {"method": "two_points", "points": ["a", "origin:O"]},
        {"method": "edge", "edge": "origin:Z"},
        {"method": "cylinder", "face": "hole", "name": "Hinge axis"},
        {"method": "perpendicular_at_point", "face": "top", "point": "v"},
        {"method": "two_planes", "plane": "origin:XZ", "plane2": "origin:YZ"},
    ]
    for arguments in cases:
        bridge = FakeBridge({"construction.axis": AXIS})
        result, _ = call("construction_axis", arguments, bridge)
        assert result.is_error is False, arguments
        assert bridge.last.method == "construction.axis"
        assert bridge.last.params == arguments
    assert json.loads(result_text(result)) == AXIS


def test_axis_two_points_with_one_ref_does_not_call_the_bridge() -> None:
    result, bridge = call("construction_axis", {"method": "two_points", "points": ["a"]})
    assert bridge.calls == []
    assert result.is_error is True
    assert result_text(result).startswith("INVALID_PARAMS:")
    assert '"expected_count": 2' in result_text(result)


def test_axis_parallel_planes_error_reaches_the_agent() -> None:
    error = BridgeError(ErrorCode.FUSION_API_ERROR, "The planes are parallel")
    bridge = FakeBridge({"construction.axis": error})
    result, _ = call(
        "construction_axis",
        {"method": "two_planes", "plane": "origin:XY", "plane2": "plane-offset"},
        bridge,
    )
    assert result.is_error is True
    assert result_text(result) == "FUSION_API_ERROR: The planes are parallel"


def test_axis_has_no_plane_only_fields() -> None:
    # Arguments outside the schema are dropped by the MCP layer and never reach the bridge.
    _, bridge = call("construction_axis", {"method": "edge", "edge": "e", "distance": 5})
    assert bridge.last.params == {"method": "edge", "edge": "e"}
    result, bridge = call("construction_axis", {"method": "edge", "edge": "e", "plane": "p"})
    assert result.is_error is True and bridge.calls == []
    rejected, bridge = call("construction_axis", {"method": "offset", "plane": "origin:XY"})
    assert rejected.is_error is True and bridge.calls == []

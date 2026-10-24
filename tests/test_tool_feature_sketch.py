"""``feature_extrude`` / ``feature_revolve`` tools on ``FakeBridge`` (c07 D10, D11; tasks 4.4,
5.3)."""

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


def tool_schema(name: str) -> dict[str, Any]:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    tool = next(t for t in tools if t.name == name)
    return tool.input_schema


RESULT = {
    "feature": {"ref": "feat-1", "type": "ExtrudeFeature", "name": "Extrude1"},
    "health": {"state": "ok", "message": ""},
    "created_bodies": [{"ref": "body-1", "type": "BRepBody", "name": "Body1"}],
    "modified_bodies": [],
    "removed_bodies": [],
}
PROFILE = "profile:sk-1@10,10"
DIST = {"type": "distance", "distance": 10}


# --- 4.4 feature_extrude -------------------------------------------------------------------


def test_extrude_defaults_and_result_pass_through() -> None:
    bridge = FakeBridge({"feature.extrude": RESULT})
    result, _ = call("feature_extrude", {"profiles": [PROFILE], "side1": DIST}, bridge)
    assert bridge.last.method == "feature.extrude"
    assert bridge.last.params == {
        "profiles": [PROFILE],
        "operation": "new_body",
        "mode": "one_side",
        "side1": {"type": "distance", "distance": 10.0},
        "taper1": 0.0,
        "is_solid": True,
        "units": "mm",
    }
    assert result.is_error is False
    assert json.loads(result_text(result)) == RESULT


def test_extrude_all_fields_are_sent() -> None:
    bridge = FakeBridge({"feature.extrude": RESULT})
    arguments = {
        "profiles": [PROFILE, {"sketch": "sk-2", "all": True}, {"sketch": "sk-3", "point": [1, 2]}],
        "operation": "cut",
        "mode": "two_sides",
        "side1": {"type": "through_all"},
        "side2": {"type": "to_entity", "entity": "face-1"},
        "taper1": "draft",
        "taper2": 3,
        "start": {"type": "offset", "offset": 5},
        "thin": {"thickness": 2, "location": "center", "thickness2": 1},
        "participant_bodies": ["body-1"],
        "units": "in",
    }
    result, _ = call("feature_extrude", arguments, bridge)
    assert result.is_error is False, result_text(result)
    assert bridge.last.params == {
        "profiles": [
            PROFILE,
            {"sketch": "sk-2", "all": True},
            {"sketch": "sk-3", "point": [1.0, 2.0]},
        ],
        "operation": "cut",
        "mode": "two_sides",
        "side1": {"type": "through_all"},
        "side2": {"type": "to_entity", "entity": "face-1", "offset": 0.0, "chained": True},
        "taper1": "draft",
        "taper2": 3.0,
        "start": {"type": "offset", "offset": 5.0},
        "thin": {"thickness": 2.0, "location": "center", "thickness2": 1.0},
        "is_solid": True,
        "participant_bodies": ["body-1"],
        "units": "in",
    }


def test_extrude_symmetric_full_length_and_direction() -> None:
    bridge = FakeBridge({"feature.extrude": RESULT})
    call(
        "feature_extrude",
        {"profiles": [PROFILE], "mode": "symmetric", "side1": DIST, "symmetric_full_length": True},
        bridge,
    )
    assert bridge.last.params["symmetric_full_length"] is True
    assert "direction" not in bridge.last.params
    call("feature_extrude", {"profiles": [PROFILE], "side1": DIST, "direction": "negative"}, bridge)
    assert bridge.last.params["direction"] == "negative"
    assert "symmetric_full_length" not in bridge.last.params


def test_extrude_mode_rules_fail_without_bridge() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        ({"mode": "symmetric", "side1": {"type": "through_all"}}, "side1.type"),
        ({"mode": "two_sides", "side1": DIST}, "side2"),
        ({"side1": DIST, "side2": DIST}, "side2"),
        ({"side1": DIST, "taper2": 1}, "taper2"),
        ({"mode": "symmetric", "side1": DIST, "direction": "positive"}, "direction"),
        ({"side1": DIST, "symmetric_full_length": True}, "symmetric_full_length"),
        ({"side1": DIST, "thin": {"thickness": 1, "thickness2": 2}}, "thin.thickness2"),
    ]
    for arguments, param in cases:
        result, bridge = call("feature_extrude", {"profiles": [PROFILE], **arguments})
        assert bridge.calls == [], arguments
        assert result.is_error is True
        text = result_text(result)
        assert text.startswith("INVALID_PARAMS:"), text
        assert f'"param": "{param}"' in text


def test_extrude_schema_rejects_bad_shapes() -> None:
    for arguments in (
        {"profiles": [], "side1": DIST},
        {"profiles": [PROFILE], "side1": {"type": "blind", "distance": 1}},
        {"profiles": [PROFILE], "side1": DIST, "operation": "merge"},
        {"profiles": [PROFILE], "side1": {"type": "distance"}},
        {"profiles": [PROFILE], "side1": DIST, "thin": {"location": "center"}},
        {"profiles": [PROFILE], "side1": DIST, "units": "km"},
    ):
        result, bridge = call("feature_extrude", arguments)
        assert result.is_error is True, arguments
        assert bridge.calls == []


def test_extrude_bridge_error_reaches_the_agent() -> None:
    error = BridgeError(
        ErrorCode.FUSION_API_ERROR,
        "ExtrudeFeature failed: profile is open",
        {"health": {"state": "error", "message": "profile is open"}},
    )
    result, _ = call(
        "feature_extrude",
        {"profiles": [PROFILE], "side1": DIST},
        FakeBridge({"feature.extrude": error}),
    )
    assert result.is_error is True
    assert result_text(result).startswith("FUSION_API_ERROR: ExtrudeFeature failed")


def test_extrude_schema_and_docstring() -> None:
    schema = tool_schema("feature_extrude")
    assert set(schema["required"]) == {"profiles", "side1"}
    text = json.dumps(schema)
    assert '"discriminator"' in text or '"oneOf"' in text
    for name in ("operation", "mode", "side2", "start", "thin", "participant_bodies", "units"):
        assert name in schema["properties"]
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    doc = next(t for t in tools if t.name == "feature_extrude").description or ""
    assert "mm" in doc and "created_bodies" in doc


# --- 5.3 feature_revolve -------------------------------------------------------------------


def test_revolve_default_extent_is_not_sent() -> None:
    bridge = FakeBridge({"feature.revolve": RESULT})
    result, _ = call("feature_revolve", {"profiles": [PROFILE], "axis": "origin:Z"}, bridge)
    assert bridge.last.method == "feature.revolve"
    assert bridge.last.params == {
        "profiles": [PROFILE],
        "axis": "origin:Z",
        "operation": "new_body",
        "is_solid": True,
        "units": "mm",
    }
    assert json.loads(result_text(result)) == RESULT


def test_revolve_extents_are_sent() -> None:
    cases = [
        ({"type": "full"}, {"type": "full"}),
        (
            {"type": "angle", "angle": 90, "symmetric": True},
            {"type": "angle", "angle": 90.0, "symmetric": True},
        ),
        ({"type": "angle"}, {"type": "angle", "angle": 360.0, "symmetric": False}),
        (
            {"type": "two_angles", "angle1": 10, "angle2": "a2"},
            {"type": "two_angles", "angle1": 10.0, "angle2": "a2"},
        ),
        (
            {"type": "to_entity", "entity": "face-1"},
            {"type": "to_entity", "entity": "face-1", "direction": "positive"},
        ),
    ]
    for extent, sent in cases:
        bridge = FakeBridge({"feature.revolve": RESULT})
        result, _ = call(
            "feature_revolve",
            {
                "profiles": [PROFILE],
                "axis": "line-1",
                "extent": extent,
                "operation": "cut",
                "participant_bodies": ["body-1"],
            },
            bridge,
        )
        assert result.is_error is False, extent
        assert bridge.last.params["extent"] == sent
        assert bridge.last.params["operation"] == "cut"
        assert bridge.last.params["participant_bodies"] == ["body-1"]


def test_revolve_schema_rejects_bad_shapes() -> None:
    for arguments in (
        {"profiles": [PROFILE]},
        {"profiles": [PROFILE], "axis": ""},
        {"profiles": [PROFILE], "axis": "origin:Z", "extent": {"type": "spin"}},
        {"profiles": [PROFILE], "axis": "origin:Z", "extent": {"type": "two_angles"}},
    ):
        result, bridge = call("feature_revolve", arguments)
        assert result.is_error is True, arguments
        assert bridge.calls == []


def test_revolve_invalid_axis_error_reaches_the_agent() -> None:
    error = BridgeError(
        ErrorCode.INVALID_PARAMS,
        "parameter 'axis' must be a straight edge, a sketch line or a construction axis, "
        "got BRepEdge (circle)",
        {"param": "axis", "expected": "linear", "got": "BRepEdge (circle)"},
    )
    result, _ = call(
        "feature_revolve",
        {"profiles": [PROFILE], "axis": "edge-1"},
        FakeBridge({"feature.revolve": error}),
    )
    assert result.is_error is True
    assert '"expected": "linear"' in result_text(result)
    schema = tool_schema("feature_revolve")
    assert set(schema["required"]) == {"profiles", "axis"}

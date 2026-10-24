"""``feature_fillet`` / ``feature_rule_fillet`` / ``feature_chamfer`` / ``feature_shell`` /
``feature_draft`` / ``feature_offset_faces`` tools on ``FakeBridge`` (c07 D17-D19; tasks 11.5,
11.6, 11.9, 12.4-12.6)."""

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


def tool(name: str) -> Any:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    return next(t for t in tools if t.name == name)


def result_of(kind: str) -> dict[str, Any]:
    return {
        "feature": {"ref": "feat-1", "type": kind, "name": f"{kind[:-7]}1"},
        "health": {"state": "ok", "message": ""},
        "created_bodies": [],
        "modified_bodies": [{"ref": "body-1", "type": "BRepBody", "name": "Body1"}],
        "removed_bodies": [],
    }


def assert_rules(name: str, base: dict[str, Any], cases: list[tuple[dict[str, Any], str]]) -> None:
    """Each case fails with ``INVALID_PARAMS`` naming ``param`` without a bridge call."""
    for arguments, param in cases:
        result, bridge = call(name, {**base, **arguments})
        assert bridge.calls == [], arguments
        assert result.is_error is True
        text = result_text(result)
        assert text.startswith("INVALID_PARAMS:"), text
        assert f'"param": "{param}"' in text, text


def assert_schema_rejects(name: str, cases: list[dict[str, Any]]) -> None:
    for arguments in cases:
        result, bridge = call(name, arguments)
        assert result.is_error is True, arguments
        assert bridge.calls == []


# --- 11.5 feature_fillet -------------------------------------------------------------------


def test_constant_fillet_is_sent() -> None:
    result_data = result_of("FilletFeature")
    bridge = FakeBridge({"feature.fillet": result_data})
    result, _ = call("feature_fillet", {"edges": ["e1", "e2"], "radius": 3}, bridge)
    assert bridge.last.method == "feature.fillet"
    assert bridge.last.params == {
        "edges": ["e1", "e2"],
        "mode": "constant",
        "radius": 3.0,
        "tangent_chain": True,
        "corner": "rolling_ball",
        "g2": False,
        "units": "mm",
    }
    assert result.is_error is False
    assert json.loads(result_text(result)) == result_data


def test_variable_and_chord_fillets_are_sent() -> None:
    bridge = FakeBridge({"feature.fillet": result_of("FilletFeature")})
    result, _ = call(
        "feature_fillet",
        {
            "edges": ["e1"],
            "mode": "variable",
            "start_radius": 2,
            "end_radius": "r_end",
            "mid": [{"position": 0.5, "radius": 3}],
            "corner": "setback",
            "g2": True,
            "tangent_chain": False,
            "units": "in",
        },
        bridge,
    )
    assert result.is_error is False, result_text(result)
    assert bridge.last.params == {
        "edges": ["e1"],
        "mode": "variable",
        "start_radius": 2.0,
        "end_radius": "r_end",
        "mid": [{"position": 0.5, "radius": 3.0}],
        "tangent_chain": False,
        "corner": "setback",
        "g2": True,
        "units": "in",
    }
    call("feature_fillet", {"edges": ["e1"], "mode": "chord", "chord_length": 4}, bridge)
    assert bridge.last.params["chord_length"] == 4.0
    assert "radius" not in bridge.last.params


def test_fillet_mode_rules_fail_without_bridge() -> None:
    assert_rules(
        "feature_fillet",
        {"edges": ["e1"]},
        [
            ({}, "radius"),
            ({"mode": "variable", "start_radius": 1}, "end_radius"),
            ({"mode": "variable", "start_radius": 1, "end_radius": 2, "radius": 1}, "radius"),
            ({"radius": 1, "mid": [{"position": 0.5, "radius": 1}]}, "mid"),
            ({"radius": 1, "chord_length": 2}, "chord_length"),
            ({"mode": "chord"}, "chord_length"),
        ],
    )


def test_fillet_schema_and_errors() -> None:
    assert_schema_rejects(
        "feature_fillet",
        [
            {"radius": 1},
            {"edges": [], "radius": 1},
            {"edges": ["e"], "radius": 1, "mode": "round"},
            {"edges": ["e"], "radius": 1, "corner": "sharp"},
            {"edges": ["e"], "radius": True},
            {
                "edges": ["e"],
                "mode": "variable",
                "start_radius": 1,
                "end_radius": 2,
                "mid": [{"position": 1.5, "radius": 1}],
            },
        ],
    )
    error = BridgeError(
        ErrorCode.FUSION_API_ERROR,
        "Chord length fillets are not available in this Fusion API version",
        {"feature_type": "FilletFeature"},
    )
    result, _ = call(
        "feature_fillet",
        {"edges": ["e"], "mode": "chord", "chord_length": 1},
        FakeBridge({"feature.fillet": error}),
    )
    assert result_text(result).startswith("FUSION_API_ERROR: Chord length fillets")
    fillet = tool("feature_fillet")
    assert set(fillet.input_schema["required"]) == {"edges"}
    doc = fillet.description or ""
    assert "mm" in doc and "variable" in doc and "modified_bodies" in doc and "rolled back" in doc


# --- 11.6 feature_rule_fillet --------------------------------------------------------------


def test_rule_fillets_are_sent() -> None:
    bridge = FakeBridge({"feature.rule_fillet": result_of("RuleFilletFeature")})
    result, _ = call("feature_rule_fillet", {"faces": ["boss"], "radius": 1}, bridge)
    assert bridge.last.method == "feature.rule_fillet"
    assert bridge.last.params == {
        "faces": ["boss"],
        "radius": 1.0,
        "rule": "all_edges",
        "units": "mm",
    }
    assert json.loads(result_text(result))["feature"]["type"] == "RuleFilletFeature"
    call(
        "feature_rule_fillet",
        {"faces": ["f1"], "faces2": ["f2", "f3"], "rule": "between_faces", "radius": "r"},
        bridge,
    )
    assert bridge.last.params == {
        "faces": ["f1"],
        "radius": "r",
        "rule": "between_faces",
        "faces2": ["f2", "f3"],
        "units": "mm",
    }


def test_rule_fillet_rules_schema_and_unavailable() -> None:
    assert_rules(
        "feature_rule_fillet",
        {"faces": ["f1"], "radius": 1},
        [({"rule": "between_faces"}, "faces2"), ({"faces2": ["f2"]}, "faces2")],
    )
    assert_schema_rejects(
        "feature_rule_fillet",
        [{"faces": ["f"]}, {"faces": [], "radius": 1}, {"faces": ["f"], "radius": 1, "rule": "x"}],
    )
    error = BridgeError(
        ErrorCode.FUSION_API_ERROR,
        "Rule fillets are not available in this Fusion API version",
        {"feature_type": "RuleFilletFeature"},
    )
    result, _ = call(
        "feature_rule_fillet",
        {"faces": ["f"], "radius": 1},
        FakeBridge({"feature.rule_fillet": error}),
    )
    assert "not available" in result_text(result)
    rule = tool("feature_rule_fillet")
    assert set(rule.input_schema["required"]) == {"faces", "radius"}
    assert "not available" in (rule.description or "")


# --- 11.9 feature_chamfer ------------------------------------------------------------------


def test_equal_chamfer_is_sent() -> None:
    result_data = result_of("ChamferFeature")
    bridge = FakeBridge({"feature.chamfer": result_data})
    result, _ = call("feature_chamfer", {"edges": ["e"], "distance": 1}, bridge)
    assert bridge.last.method == "feature.chamfer"
    assert bridge.last.params == {
        "edges": ["e"],
        "distance": 1.0,
        "mode": "equal",
        "flip": False,
        "tangent_chain": True,
        "units": "mm",
    }
    assert json.loads(result_text(result)) == result_data


def test_two_distance_and_angle_chamfers_are_sent() -> None:
    bridge = FakeBridge({"feature.chamfer": result_of("ChamferFeature")})
    call(
        "feature_chamfer",
        {"edges": ["e"], "mode": "two_distances", "distance": 2, "distance2": 3, "flip": True},
        bridge,
    )
    assert bridge.last.params["distance2"] == 3.0 and bridge.last.params["flip"] is True
    call(
        "feature_chamfer",
        {"edges": ["e"], "mode": "distance_angle", "distance": 2, "angle": 30},
        bridge,
    )
    assert bridge.last.params["angle"] == 30.0
    assert bridge.last.params["mode"] == "distance_angle"
    call("feature_chamfer", {"edges": ["e"], "mode": "distance_angle", "distance": 2}, bridge)
    assert "angle" not in bridge.last.params


def test_chamfer_rules_and_schema() -> None:
    assert_rules(
        "feature_chamfer",
        {"edges": ["e"], "distance": 1},
        [
            ({"distance2": 2}, "distance2"),
            ({"mode": "two_distances"}, "distance2"),
            ({"angle": 30}, "angle"),
            ({"flip": True}, "flip"),
        ],
    )
    assert_schema_rejects(
        "feature_chamfer",
        [{"edges": ["e"]}, {"distance": 1}, {"edges": ["e"], "distance": 1, "mode": "bevel"}],
    )
    chamfer = tool("feature_chamfer")
    assert set(chamfer.input_schema["required"]) == {"edges", "distance"}
    doc = chamfer.description or ""
    assert "distance_angle" in doc and "45" in doc


# --- 12.4 feature_shell --------------------------------------------------------------------


def test_shell_is_sent() -> None:
    result_data = result_of("ShellFeature")
    bridge = FakeBridge({"feature.shell": result_data})
    result, _ = call("feature_shell", {"faces": ["top"], "inside_thickness": 2}, bridge)
    assert bridge.last.method == "feature.shell"
    assert bridge.last.params == {
        "faces": ["top"],
        "inside_thickness": 2.0,
        "tangent_chain": True,
        "units": "mm",
    }
    assert json.loads(result_text(result)) == result_data
    call(
        "feature_shell",
        {"bodies": ["b"], "outside_thickness": "wall", "faces": [], "tangent_chain": False},
        bridge,
    )
    assert bridge.last.params == {
        "bodies": ["b"],
        "outside_thickness": "wall",
        "tangent_chain": False,
        "units": "mm",
    }


def test_shell_rules() -> None:
    assert_rules(
        "feature_shell",
        {},
        [
            ({"inside_thickness": 2}, "faces"),
            ({"faces": [], "bodies": [], "inside_thickness": 2}, "faces"),
            ({"faces": ["f"], "bodies": ["b"], "inside_thickness": 2}, "bodies"),
            ({"faces": ["f"]}, "inside_thickness"),
        ],
    )
    assert_schema_rejects("feature_shell", [{"faces": ["f"], "inside_thickness": True}])
    shell = tool("feature_shell")
    assert shell.input_schema.get("required", []) == []
    assert "inside_thickness" in (shell.description or "")


# --- 12.5 feature_draft --------------------------------------------------------------------


def test_draft_is_sent() -> None:
    result_data = result_of("DraftFeature")
    bridge = FakeBridge({"feature.draft": result_data})
    result, _ = call(
        "feature_draft", {"faces": ["f1", "f2"], "plane": "origin:XY", "angle": 2}, bridge
    )
    assert bridge.last.method == "feature.draft"
    assert bridge.last.params == {
        "faces": ["f1", "f2"],
        "plane": "origin:XY",
        "angle": 2.0,
        "symmetric": False,
        "flip": False,
        "tangent_chain": True,
    }
    assert json.loads(result_text(result)) == result_data
    call(
        "feature_draft",
        {"faces": ["f"], "plane": "p", "angle": 2, "angle2": "a2", "flip": True},
        bridge,
    )
    assert bridge.last.params["angle2"] == "a2" and bridge.last.params["flip"] is True


def test_draft_rules_and_schema() -> None:
    assert_rules(
        "feature_draft",
        {"faces": ["f"], "plane": "origin:XY", "angle": 2},
        [({"symmetric": True, "angle2": 3}, "angle2")],
    )
    assert_schema_rejects(
        "feature_draft",
        [
            {"faces": ["f"], "plane": "origin:XY"},
            {"faces": [], "plane": "origin:XY", "angle": 2},
            {"faces": ["f"], "angle": 2},
        ],
    )
    draft = tool("feature_draft")
    assert set(draft.input_schema["required"]) == {"faces", "plane", "angle"}
    assert "pull direction" in (draft.description or "")


# --- 12.6 feature_offset_faces -------------------------------------------------------------


def test_offset_faces_is_sent() -> None:
    result_data = result_of("OffsetFacesFeature")
    bridge = FakeBridge({"feature.offset_faces": result_data})
    result, _ = call("feature_offset_faces", {"faces": ["f"], "distance": 2}, bridge)
    assert bridge.last.method == "feature.offset_faces"
    assert bridge.last.params == {"faces": ["f"], "distance": 2.0, "chain": True, "units": "mm"}
    assert json.loads(result_text(result)) == result_data
    call(
        "feature_offset_faces",
        {"faces": ["f"], "distance": -0.1, "chain": False, "units": "in"},
        bridge,
    )
    assert bridge.last.params == {"faces": ["f"], "distance": -0.1, "chain": False, "units": "in"}


def test_offset_faces_schema_and_docstring() -> None:
    assert_schema_rejects(
        "feature_offset_faces",
        [{"faces": ["f"]}, {"distance": 1}, {"faces": [], "distance": 1}],
    )
    offset = tool("feature_offset_faces")
    assert set(offset.input_schema["required"]) == {"faces", "distance"}
    doc = offset.description or ""
    assert "feature_extrude" in doc and "feature_fillet" in doc
    assert "Press-pull of a sketch profile = feature_extrude" in doc

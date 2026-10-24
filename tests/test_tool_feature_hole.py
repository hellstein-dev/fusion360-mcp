"""``feature_hole`` / ``feature_thread`` / ``feature_thread_options`` tools on ``FakeBridge``
(c07 D15, D16; tasks 9.4, 10.5)."""

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


def result_of(kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "feature": {"ref": "feat-1", "type": kind, "name": f"{kind[:-7]}1"},
        "health": {"state": "ok", "message": ""},
        "created_bodies": [],
        "modified_bodies": [{"ref": "body-1", "type": "BRepBody", "name": "Body1"}],
        "removed_bodies": [],
        **extra,
    }


POINTS = {"type": "sketch_points", "points": ["pt-a", "pt-b", "pt-c", "pt-d"]}
THROUGH = {"type": "through_all"}


# --- 9.4 / 10.5 feature_hole ---------------------------------------------------------------


def test_counterbore_hole_is_sent() -> None:
    result_data = result_of("HoleFeature", holes=4)
    bridge = FakeBridge({"feature.hole": result_data})
    result, _ = call(
        "feature_hole",
        {
            "placement": POINTS,
            "kind": "counterbore",
            "diameter": 5.5,
            "cb_diameter": 10,
            "cb_depth": 5.5,
            "extent": THROUGH,
        },
        bridge,
    )
    assert bridge.last.method == "feature.hole"
    assert bridge.last.params == {
        "placement": POINTS,
        "diameter": 5.5,
        "extent": THROUGH,
        "kind": "counterbore",
        "cb_diameter": 10.0,
        "cb_depth": 5.5,
        "tip_angle": 118.0,
        "flip": False,
        "units": "mm",
    }
    assert result.is_error is False
    assert json.loads(result_text(result)) == result_data


def test_hole_placements_extents_and_options_are_sent() -> None:
    bridge = FakeBridge({"feature.hole": result_of("HoleFeature", holes=1)})
    offsets = {
        "type": "face_offsets",
        "face": "face-1",
        "point": [10, 15, 0],
        "edge1": "e1",
        "offset1": 10,
        "edge2": "e2",
        "offset2": "edge_gap",
    }
    result, _ = call(
        "feature_hole",
        {
            "placement": offsets,
            "kind": "countersink",
            "diameter": "bolt_d",
            "cs_diameter": 9,
            "cs_angle": 82,
            "tip_angle": 180,
            "extent": {"type": "distance", "depth": 12},
            "flip": True,
            "participant_bodies": ["body-1"],
            "units": "in",
        },
        bridge,
    )
    assert result.is_error is False, result_text(result)
    params = bridge.last.params
    assert params["placement"] == {**offsets, "point": [10.0, 15.0, 0.0]}
    assert params["diameter"] == "bolt_d" and params["cs_angle"] == 82.0
    assert params["extent"] == {"type": "distance", "depth": 12.0}
    assert params["flip"] is True and params["participant_bodies"] == ["body-1"]
    assert params["units"] == "in" and "thread" not in params
    call(
        "feature_hole",
        {
            "placement": {"type": "center", "face": "f", "edge": "rim"},
            "diameter": 3,
            "extent": {"type": "to_entity", "entity": "face-2"},
        },
        bridge,
    )
    assert bridge.last.params["placement"] == {"type": "center", "face": "f", "edge": "rim"}
    assert bridge.last.params["extent"] == {"type": "to_entity", "entity": "face-2"}


def test_tapped_hole_sends_thread_spec() -> None:
    result_data = result_of(
        "HoleFeature",
        holes=1,
        thread={
            "type": "ISO Metric profile",
            "designation": "M6x1",
            "class": "6H",
            "internal": True,
            "modeled": False,
            "feature": {"ref": "feat-2", "type": "ThreadFeature", "name": "Thread1"},
            "health": {"state": "ok", "message": ""},
            "faces": 1,
        },
    )
    bridge = FakeBridge({"feature.hole": result_data})
    result, _ = call(
        "feature_hole",
        {
            "placement": {"type": "point", "face": "f", "point": [0, 0, 0]},
            "diameter": 5,
            "extent": THROUGH,
            "thread": {"designation": "M6x1"},
        },
        bridge,
    )
    assert bridge.last.params["thread"] == {
        "thread_type": "ISO Metric profile",
        "designation": "M6x1",
        "modeled": False,
        "full_length": True,
        "offset": 0.0,
        "location": "high",
    }
    assert json.loads(result_text(result))["thread"]["designation"] == "M6x1"


def test_hole_kind_rules_fail_without_bridge() -> None:
    base = {"placement": POINTS, "diameter": 5, "extent": THROUGH}
    cases: list[tuple[dict[str, Any], str]] = [
        ({"kind": "counterbore", "cb_diameter": 10}, "cb_depth"),
        ({"kind": "counterbore", "cb_depth": 3}, "cb_diameter"),
        ({"kind": "countersink"}, "cs_diameter"),
        ({"cb_depth": 3}, "cb_depth"),
        ({"kind": "countersink", "cs_diameter": 9, "cb_diameter": 10}, "cb_diameter"),
        ({"kind": "counterbore", "cb_diameter": 9, "cb_depth": 2, "cs_angle": 90}, "cs_angle"),
    ]
    for arguments, param in cases:
        result, bridge = call("feature_hole", {**base, **arguments})
        assert bridge.calls == [], arguments
        assert result.is_error is True
        text = result_text(result)
        assert text.startswith("INVALID_PARAMS:"), text
        assert f'"param": "{param}"' in text


def test_hole_schema_rejects_bad_shapes() -> None:
    base = {"placement": POINTS, "diameter": 5, "extent": THROUGH}
    for arguments in (
        {"diameter": 5, "extent": THROUGH},
        {"placement": POINTS, "extent": THROUGH},
        {"placement": POINTS, "diameter": 5},
        {**base, "kind": "tapped"},
        {**base, "placement": {"type": "sketch_points", "points": []}},
        {**base, "placement": {"type": "point", "face": "f", "point": [0, 0]}},
        {**base, "placement": {"type": "center", "face": "f"}},
        {**base, "extent": {"type": "distance"}},
        {**base, "extent": {"type": "blind", "depth": 3}},
        {**base, "thread": {"pitch": 1}},
        {**base, "thread": {"length": 5}},
        {**base, "diameter": True},
    ):
        result, bridge = call("feature_hole", arguments)
        assert result.is_error is True, arguments
        assert bridge.calls == []


def test_hole_thread_rollback_error_reaches_the_agent_and_schema() -> None:
    error = BridgeError(
        ErrorCode.INVALID_PARAMS,
        "thread type 'ISO Metric profile' has no designation 'M99x9'",
        {"param": "thread.designation", "available": ["M6x1", "M8x1.25"]},
    )
    result, _ = call(
        "feature_hole",
        {
            "placement": POINTS,
            "diameter": 5,
            "extent": THROUGH,
            "thread": {"designation": "M99x9"},
        },
        FakeBridge({"feature.hole": error}),
    )
    assert result.is_error is True
    text = result_text(result)
    assert '"available"' in text and "M8x1.25" in text and "thread.designation" in text
    hole = tool("feature_hole")
    assert set(hole.input_schema["required"]) == {"placement", "diameter", "extent"}
    for name in ("kind", "cb_diameter", "cs_angle", "tip_angle", "thread", "participant_bodies"):
        assert name in hole.input_schema["properties"]
    doc = hole.description or ""
    assert "mm" in doc and "thread" in doc and "holes" in doc and "rolled back" in doc


# --- 10.5 feature_thread -------------------------------------------------------------------


def test_thread_defaults_are_sent() -> None:
    result_data = result_of(
        "ThreadFeature",
        thread={
            "type": "ISO Metric profile",
            "designation": "M10x1.5",
            "class": "6g",
            "internal": False,
            "modeled": False,
        },
    )
    bridge = FakeBridge({"feature.thread": result_data})
    result, _ = call("feature_thread", {"faces": ["face-1"]}, bridge)
    assert bridge.last.method == "feature.thread"
    assert bridge.last.params == {
        "faces": ["face-1"],
        "thread_type": "ISO Metric profile",
        "modeled": False,
        "full_length": True,
        "location": "high",
        "units": "mm",
    }
    assert json.loads(result_text(result)) == result_data


def test_thread_all_fields_are_sent() -> None:
    bridge = FakeBridge({"feature.thread": result_of("ThreadFeature")})
    result, _ = call(
        "feature_thread",
        {
            "faces": ["face-1", "face-2"],
            "thread_type": "ANSI Unified Screw Threads",
            "designation": "1/4-20 UNC",
            "thread_class": "2A",
            "internal": False,
            "modeled": True,
            "full_length": False,
            "length": 0.5,
            "offset": 0.1,
            "location": "low",
            "units": "in",
        },
        bridge,
    )
    assert result.is_error is False, result_text(result)
    assert bridge.last.params == {
        "faces": ["face-1", "face-2"],
        "thread_type": "ANSI Unified Screw Threads",
        "designation": "1/4-20 UNC",
        "thread_class": "2A",
        "internal": False,
        "modeled": True,
        "full_length": False,
        "length": 0.5,
        "offset": 0.1,
        "location": "low",
        "units": "in",
    }


def test_thread_rules_fail_without_bridge() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        ({"length": 5}, "length"),
        ({"offset": 1}, "offset"),
        ({"full_length": False}, "length"),
    ]
    for arguments, param in cases:
        result, bridge = call("feature_thread", {"faces": ["face-1"], **arguments})
        assert bridge.calls == [], arguments
        text = result_text(result)
        assert text.startswith("INVALID_PARAMS:"), text
        assert f'"param": "{param}"' in text


def test_thread_schema_and_errors() -> None:
    cases: tuple[dict[str, Any], ...] = (
        {"faces": []},
        {},
        {"faces": ["f"], "location": "middle"},
        {"faces": ["f"], "designation": ""},
        {"faces": ["f"], "modeled": "maybe"},
    )
    for arguments in cases:
        result, bridge = call("feature_thread", arguments)
        assert result.is_error is True, arguments
        assert bridge.calls == []
    error = BridgeError(
        ErrorCode.INVALID_PARAMS,
        "parameter 'faces[0]' must be a cylindrical face, got BRepFace (plane)",
        {"param": "faces", "expected": ["BRepFace (cylinder)"], "actual": "BRepFace (plane)"},
    )
    result, _ = call("feature_thread", {"faces": ["f"]}, FakeBridge({"feature.thread": error}))
    assert result_text(result).startswith("INVALID_PARAMS: parameter 'faces[0]'")
    thread = tool("feature_thread")
    assert set(thread.input_schema["required"]) == {"faces"}
    doc = thread.description or ""
    assert "feature_thread_options" in doc and "internal" in doc and "designation" in doc


# --- 10.5 feature_thread_options -----------------------------------------------------------


def test_thread_options_levels_are_sent() -> None:
    bridge = FakeBridge({"feature.thread_options": {"thread_types": ["ISO Metric profile"]}})
    result, _ = call("feature_thread_options", {}, bridge)
    assert bridge.last.method == "feature.thread_options"
    assert bridge.last.params == {"internal": False, "units": "mm"}
    assert json.loads(result_text(result)) == {"thread_types": ["ISO Metric profile"]}
    call(
        "feature_thread_options",
        {"thread_type": "ISO Metric profile", "size": "6", "designation": "M6x1", "internal": True},
        bridge,
    )
    assert bridge.last.params == {
        "thread_type": "ISO Metric profile",
        "size": "6",
        "designation": "M6x1",
        "internal": True,
        "units": "mm",
    }
    call("feature_thread_options", {"diameter": 0.25, "units": "in"}, bridge)
    assert bridge.last.params == {"internal": False, "diameter": 0.25, "units": "in"}


def test_thread_options_rules_and_schema() -> None:
    result, bridge = call("feature_thread_options", {"diameter": 8, "size": "8"})
    assert bridge.calls == []
    assert '"param": "diameter"' in result_text(result)
    for arguments in ({"thread_type": ""}, {"diameter": True}, {"internal": "maybe"}):
        result, bridge = call("feature_thread_options", arguments)
        assert result.is_error is True, arguments
        assert bridge.calls == []
    options = tool("feature_thread_options")
    assert options.input_schema.get("required", []) == []
    doc = options.description or ""
    assert "read-only" in doc and "recommended" in doc and "sizes" in doc

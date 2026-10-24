"""``timeline_*`` tools on ``FakeBridge`` (c09 tasks 2.3, 3.x, 4.x, 5.x, 6.x, 7.x)."""

import asyncio
import json
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text
from fusion_mcp.server.tools import timeline


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


def rejected(name: str, arguments: dict[str, Any]) -> None:
    result, bridge = call(name, arguments)
    assert isinstance(result, Rejected), result
    assert bridge.calls == []


ITEM = {"index": 1, "name": "Extrude1", "ref": "ext-1", "health": "ok"}


def tool_names() -> set[str]:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    return {t.name for t in tools if t.name.startswith("timeline_")}


# --- 2.3 registration ----------------------------------------------------------------------------


def test_register_adds_all_timeline_tools() -> None:
    mcp: MCPServer[Any] = MCPServer(name="t")
    timeline.register(mcp, FakeBridge())
    assert len(tool_names()) == 18


def test_docstrings_are_written_for_the_agent() -> None:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    for tool in tools:
        if tool.name.startswith("timeline_"):
            text = tool.description or ""
            assert "Example" in text and "Returns" in text, tool.name
    descriptions = {t.name: t.description or "" for t in tools}
    assert "inserted at the marker" in descriptions["timeline_roll_to"]
    assert '"is_rolled_back"' in descriptions["timeline_list"]
    assert "available_keys" in descriptions["timeline_edit_hole"]


# --- 3. reading ----------------------------------------------------------------------------------


def test_timeline_list() -> None:
    answer = {"count": 1, "marker_position": 1, "items": [ITEM], "total": 1, "truncated": False}
    bridge = FakeBridge({"timeline.list": answer})
    result, _ = call("timeline_list", {}, bridge)
    assert bridge.last.method == "timeline.list"
    assert bridge.last.params == {"only_problems": False, "offset": 0, "limit": 200}
    assert json.loads(result_text(result)) == answer
    call("timeline_list", {"only_problems": True, "offset": 5, "limit": 10}, bridge)
    assert bridge.last.params == {"only_problems": True, "offset": 5, "limit": 10}
    rejected("timeline_list", {"limit": 0})
    rejected("timeline_list", {"limit": 1001})
    rejected("timeline_list", {"offset": -1})


def test_timeline_list_direct_design_error() -> None:
    error = BridgeError(ErrorCode.INVALID_PARAMS, "timeline requires a parametric design")
    result, _ = call("timeline_list", {}, FakeBridge({"timeline.list": error}))
    assert result.is_error is True
    assert "INVALID_PARAMS" in result_text(result)


def test_timeline_health() -> None:
    answer = {"ok": True, "errors": [], "warnings": []}
    bridge = FakeBridge({"timeline.health": answer})
    result, _ = call("timeline_health", {}, bridge)
    assert (bridge.last.method, bridge.last.params) == ("timeline.health", {})
    assert json.loads(result_text(result))["ok"] is True


def test_timeline_compute_all() -> None:
    bridge = FakeBridge({"timeline.compute_all": {"ok": False, "errors": [ITEM]}})
    result, _ = call("timeline_compute_all", {}, bridge)
    assert (bridge.last.method, bridge.last.params) == ("timeline.compute_all", {})
    assert json.loads(result_text(result))["ok"] is False


# --- 4. marker, suppression, deletion, names -----------------------------------------------------


def test_timeline_roll_to() -> None:
    bridge = FakeBridge()
    call("timeline_roll_to", {"position": "before", "item": "ext-1"}, bridge)
    assert bridge.last.method == "timeline.roll_to"
    assert bridge.last.params == {"position": "before", "item": "ext-1"}
    call("timeline_roll_to", {"position": "end"}, bridge)
    assert bridge.last.params == {"position": "end"}
    rejected("timeline_roll_to", {"position": "middle"})


def test_timeline_set_suppressed() -> None:
    bridge = FakeBridge()
    call("timeline_set_suppressed", {"items": ["fil-1"]}, bridge)
    assert bridge.last.method == "timeline.set_suppressed"
    assert bridge.last.params == {"items": ["fil-1"], "suppressed": True, "allow_errors": False}
    rejected("timeline_set_suppressed", {"items": []})
    rejected("timeline_set_suppressed", {"items": ["a"] * 101})
    error = BridgeError(ErrorCode.FUSION_API_ERROR, "Fillet1: edges gone", {"reverted": True})
    result, _ = call(
        "timeline_set_suppressed",
        {"items": ["fil-1"], "suppressed": False},
        FakeBridge({"timeline.set_suppressed": error}),
    )
    assert result.is_error is True and "reverted" in result_text(result)


def test_timeline_delete() -> None:
    bridge = FakeBridge()
    call("timeline_delete", {"item": "fil-1"}, bridge)
    assert (bridge.last.method, bridge.last.params) == (
        "timeline.delete",
        {"item": "fil-1", "force": False},
    )
    call("timeline_delete", {"item": "ext-1", "force": True}, bridge)
    assert bridge.last.params["force"] is True
    rejected("timeline_delete", {"item": ""})


def test_timeline_rename() -> None:
    bridge = FakeBridge()
    call("timeline_rename", {"item": "ext-1", "name": "Base"}, bridge)
    assert (bridge.last.method, bridge.last.params) == (
        "timeline.rename",
        {"item": "ext-1", "name": "Base"},
    )
    rejected("timeline_rename", {"item": "ext-1", "name": ""})


# --- 5. model parameters -------------------------------------------------------------------------


def test_timeline_feature_parameters() -> None:
    bridge = FakeBridge()
    call("timeline_feature_parameters", {"feature": "ext-1"}, bridge)
    assert (bridge.last.method, bridge.last.params) == (
        "timeline.feature_parameters",
        {"feature": "ext-1"},
    )


def test_timeline_set_model_parameter() -> None:
    bridge = FakeBridge()
    call("timeline_set_model_parameter", {"name": "d3", "value": 30}, bridge)
    assert bridge.last.method == "timeline.set_model_parameter"
    assert bridge.last.params == {"name": "d3", "value": 30, "units": "mm", "allow_errors": False}
    call("timeline_set_model_parameter", {"name": "d3", "value": "w * 2", "units": "in"}, bridge)
    assert bridge.last.params["value"] == "w * 2" and bridge.last.params["units"] == "in"
    rejected("timeline_set_model_parameter", {"name": "d3", "value": 1, "units": "yd"})


# --- 6. feature-specific edits -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "method", "arguments", "expected"),
    [
        (
            "timeline_edit_extrude",
            "timeline.edit_extrude",
            {"feature": "ext-1", "distance": 30},
            {"feature": "ext-1", "distance": 30, "units": "mm", "allow_errors": False},
        ),
        (
            "timeline_edit_revolve",
            "timeline.edit_revolve",
            {"feature": "rev-1", "angle_two": "a / 2"},
            {"feature": "rev-1", "angle_two": "a / 2", "allow_errors": False},
        ),
        (
            "timeline_edit_fillet",
            "timeline.edit_fillet",
            {"feature": "fil-1", "radius": 3, "units": "cm"},
            {"feature": "fil-1", "radius": 3, "units": "cm", "allow_errors": False},
        ),
        (
            "timeline_edit_chamfer",
            "timeline.edit_chamfer",
            {"feature": "ch-1", "distance": 1.5, "allow_errors": True},
            {"feature": "ch-1", "distance": 1.5, "units": "mm", "allow_errors": True},
        ),
        (
            "timeline_edit_hole",
            "timeline.edit_hole",
            {"feature": "h-1", "diameter": 6.6, "countersink_angle": 90},
            {
                "feature": "h-1",
                "diameter": 6.6,
                "countersink_angle": 90,
                "units": "mm",
                "allow_errors": False,
            },
        ),
        (
            "timeline_edit_shell",
            "timeline.edit_shell",
            {"feature": "sh-1", "outside_thickness": 2},
            {"feature": "sh-1", "outside_thickness": 2, "units": "mm", "allow_errors": False},
        ),
    ],
)
def test_edit_tools_pass_only_given_keys(
    tool: str, method: str, arguments: dict[str, Any], expected: dict[str, Any]
) -> None:
    answer = {"feature": ITEM, "changed": []}
    bridge = FakeBridge({method: answer})
    result, _ = call(tool, arguments, bridge)
    assert bridge.last.method == method
    assert bridge.last.params == expected
    assert json.loads(result_text(result)) == answer


def test_edit_tool_errors_and_schema() -> None:
    error = BridgeError(ErrorCode.INVALID_PARAMS, "nothing to change")
    result, _ = call(
        "timeline_edit_extrude", {"feature": "ext-1"}, FakeBridge({"timeline.edit_extrude": error})
    )
    assert result.is_error is True
    rejected("timeline_edit_fillet", {"feature": "fil-1"})  # radius is required
    rejected("timeline_edit_chamfer", {"feature": "", "distance": 1})


# --- 7. groups -----------------------------------------------------------------------------------


def test_timeline_create_group() -> None:
    bridge = FakeBridge()
    call("timeline_create_group", {"first": "sk-1", "last": "ext-1", "name": "Base"}, bridge)
    assert bridge.last.method == "timeline.create_group"
    assert bridge.last.params == {"first": "sk-1", "last": "ext-1", "name": "Base"}
    call("timeline_create_group", {"first": "sk-1", "last": "ext-1"}, bridge)
    assert "name" not in bridge.last.params


def test_timeline_set_group_collapsed() -> None:
    bridge = FakeBridge()
    call("timeline_set_group_collapsed", {"group": "timeline:Base", "collapsed": True}, bridge)
    assert (bridge.last.method, bridge.last.params) == (
        "timeline.set_group_collapsed",
        {"group": "timeline:Base", "collapsed": True},
    )


def test_timeline_ungroup() -> None:
    bridge = FakeBridge({"timeline.ungroup": {"members": [ITEM]}})
    result, _ = call("timeline_ungroup", {"group": "timeline:Base"}, bridge)
    assert (bridge.last.method, bridge.last.params) == (
        "timeline.ungroup",
        {"group": "timeline:Base"},
    )
    assert json.loads(result_text(result))["members"] == [ITEM]

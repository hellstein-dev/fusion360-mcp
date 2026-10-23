"""``design_*`` and ``parameter_*`` tools on ``FakeBridge`` (c04 tasks 5.4-5.6)."""

import asyncio
import json
from typing import Any

import pytest
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


SETTINGS = {
    "design_type": "parametric",
    "units": "mm",
    "document": {"ref": "document:Untitled", "type": "Document", "name": "Untitled"},
    "root_component": {"ref": "component:root", "type": "Component", "name": "root"},
    "parameter_count": 0,
    "timeline_count": 0,
}


# --- 5.4 design --------------------------------------------------------------------------------


def test_design_get_settings() -> None:
    bridge = FakeBridge({"design.get_settings": SETTINGS})
    result, _ = call("design_get_settings", {}, bridge)
    assert bridge.last.method == "design.get_settings"
    assert bridge.last.params == {}
    assert json.loads(result_text(result)) == SETTINGS


def test_design_set_units() -> None:
    bridge = FakeBridge({"design.set_units": {**SETTINGS, "units": "in"}})
    result, _ = call("design_set_units", {"units": "in"}, bridge)
    assert bridge.last.method == "design.set_units"
    assert bridge.last.params == {"units": "in"}
    assert json.loads(result_text(result))["units"] == "in"
    rejected, bridge = call("design_set_units", {"units": "yd"})
    assert rejected.is_error is True and bridge.calls == []


def test_design_set_type() -> None:
    bridge = FakeBridge()
    call("design_set_type", {"design_type": "direct"}, bridge)
    assert bridge.last.method == "design.set_type"
    assert bridge.last.params == {"design_type": "direct", "confirm_discard_history": False}
    call("design_set_type", {"design_type": "direct", "confirm_discard_history": True}, bridge)
    assert bridge.last.params["confirm_discard_history"] is True
    error = BridgeError(ErrorCode.INVALID_PARAMS, "switching to direct deletes the timeline")
    result, _ = call(
        "design_set_type", {"design_type": "direct"}, FakeBridge({"design.set_type": error})
    )
    assert result.is_error is True
    rejected, bridge = call("design_set_type", {"design_type": "mesh"})
    assert rejected.is_error is True and bridge.calls == []


# --- 5.5 parameter list / create ---------------------------------------------------------------


def test_parameter_list_defaults_and_filters() -> None:
    bridge = FakeBridge({"parameter.list": {"parameters": []}})
    result, _ = call("parameter_list", {}, bridge)
    assert result.is_error is False
    assert bridge.last.method == "parameter.list"
    assert bridge.last.params == {"kind": "all", "include_dependents": False}
    call(
        "parameter_list",
        {"kind": "model", "name_contains": "d", "component": "component:root"},
        bridge,
    )
    assert bridge.last.params == {
        "kind": "model",
        "name_contains": "d",
        "component": "component:root",
        "include_dependents": False,
    }


def test_parameter_create_number_and_expression() -> None:
    bridge = FakeBridge({"parameter.create": {"parameters": [], "warnings": []}})
    result, _ = call(
        "parameter_create",
        {
            "parameters": [
                {"name": "width", "expression": 40},
                {"name": "half", "expression": "width / 2", "comment": "derived"},
                {"name": "tilt", "expression": 30, "unit": "deg"},
            ]
        },
        bridge,
    )
    assert result.is_error is False
    assert bridge.last.method == "parameter.create"
    assert bridge.last.params == {
        "parameters": [
            {"name": "width", "expression": 40, "unit": "mm", "comment": ""},
            {"name": "half", "expression": "width / 2", "unit": "mm", "comment": "derived"},
            {"name": "tilt", "expression": 30, "unit": "deg", "comment": ""},
        ]
    }


@pytest.mark.parametrize(
    "parameters",
    [
        [],
        [{"name": "1bad", "expression": 1}],
        [{"name": "ok"}],
        [{"expression": 1}],
        [{"name": f"p{i}", "expression": 1} for i in range(101)],
    ],
)
def test_parameter_create_rejects(parameters: list[Any]) -> None:
    result, bridge = call("parameter_create", {"parameters": parameters})
    assert result.is_error is True and bridge.calls == []


def test_parameter_create_bridge_error() -> None:
    error = BridgeError(ErrorCode.FUSION_API_ERROR, "invalid expression", {"item": "parameters[1]"})
    bridge = FakeBridge({"parameter.create": error})
    result, _ = call("parameter_create", {"parameters": [{"name": "a", "expression": "x"}]}, bridge)
    assert result.is_error is True
    assert result_text(result).startswith("FUSION_API_ERROR")


# --- 5.6 parameter update / delete -------------------------------------------------------------


def test_parameter_update_sends_only_given_fields() -> None:
    bridge = FakeBridge({"parameter.update": {"parameters": [], "warnings": []}})
    call(
        "parameter_update",
        {
            "parameters": [
                {"name": "d1", "expression": "width * 2"},
                {"name": "width", "new_name": "w", "comment": "", "is_favorite": True},
            ]
        },
        bridge,
    )
    assert bridge.last.method == "parameter.update"
    assert bridge.last.params == {
        "parameters": [
            {"name": "d1", "expression": "width * 2"},
            {"name": "width", "new_name": "w", "comment": "", "is_favorite": True},
        ]
    }


def test_parameter_update_failed_features() -> None:
    error = BridgeError(
        ErrorCode.FUSION_API_ERROR,
        "the change puts features into the error state: Fillet1; nothing was changed",
        {"failed_features": [{"ref": "f1", "name": "Fillet1", "message": "too large"}]},
    )
    bridge = FakeBridge({"parameter.update": error})
    result, _ = call("parameter_update", {"parameters": [{"name": "d1", "expression": 99}]}, bridge)
    assert result.is_error is True
    assert "Fillet1" in result_text(result)
    rejected, bridge = call("parameter_update", {"parameters": [{"name": "d1", "new_name": "2x"}]})
    assert rejected.is_error is True and bridge.calls == []


def test_parameter_delete() -> None:
    bridge = FakeBridge({"parameter.delete": {"deleted": ["half", "width"], "warnings": []}})
    result, _ = call("parameter_delete", {"names": ["half", "width"]}, bridge)
    assert bridge.last.method == "parameter.delete"
    assert bridge.last.params == {"names": ["half", "width"]}
    assert json.loads(result_text(result))["deleted"] == ["half", "width"]
    error = BridgeError(ErrorCode.INVALID_PARAMS, "still used", {"dependents": ["half"]})
    result, _ = call(
        "parameter_delete", {"names": ["width"]}, FakeBridge({"parameter.delete": error})
    )
    assert result.is_error is True
    assert "half" in result_text(result)
    for bad in ([], ["has space"]):
        rejected, bridge = call("parameter_delete", {"names": bad})
        assert rejected.is_error is True and bridge.calls == []

"""``assembly_*`` tools on ``FakeBridge`` (c10 tasks 2.x, 3.x)."""

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


def rejected(name: str, arguments: dict[str, Any]) -> None:
    result, bridge = call(name, arguments)
    assert isinstance(result, Rejected), result
    assert bridge.calls == []


OCC = {"ref": "occurrence:Box:1", "component": "component:Box", "joint_count": 0}


def descriptions() -> dict[str, str]:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    return {t.name: t.description or "" for t in tools if t.name.startswith("assembly_")}


def test_docstrings_are_written_for_the_agent() -> None:
    docs = descriptions()
    for name, text in docs.items():
        assert "Example" in text and "Returns" in text, name
    assert "stops resolving" in docs["assembly_rename_component"]
    assert "grounded" in docs["assembly_transform_occurrence"].lower()


# --- 2. components -----------------------------------------------------------------------------


def test_assembly_list() -> None:
    answer = {"active_component": "component:root", "components": [], "occurrences": [OCC]}
    bridge = FakeBridge({"assembly.list": answer})
    result, _ = call("assembly_list", {}, bridge)
    assert bridge.last.method == "assembly.list"
    assert bridge.last.params == {"limit": 500}
    assert json.loads(result_text(result)) == answer
    rejected("assembly_list", {"limit": 0})


def test_assembly_new_component() -> None:
    bridge = FakeBridge({"assembly.new_component": {"component": "component:Box"}})
    call("assembly_new_component", {"name": "Box"}, bridge)
    assert bridge.last.params == {"name": "Box"}
    call(
        "assembly_new_component",
        {"name": "Hinge", "parent": "occurrence:Box:1", "translate_mm": [0, 0, 40]},
        bridge,
    )
    assert bridge.last.params == {
        "name": "Hinge",
        "parent": "occurrence:Box:1",
        "translate_mm": [0, 0, 40],
    }
    rejected("assembly_new_component", {"name": ""})
    rejected("assembly_new_component", {"name": "X", "translate_mm": [1, 2]})


def test_assembly_component_from_bodies() -> None:
    bridge = FakeBridge({"assembly.component_from_bodies": {"component": "component:Lid"}})
    call("assembly_component_from_bodies", {"bodies": ["a", "b"], "name": "Lid"}, bridge)
    assert bridge.last.method == "assembly.component_from_bodies"
    assert bridge.last.params == {"bodies": ["a", "b"], "name": "Lid"}
    rejected("assembly_component_from_bodies", {"bodies": [], "name": "Lid"})


def test_assembly_component_from_bodies_error_is_passed_on() -> None:
    error = BridgeError(ErrorCode.INVALID_PARAMS, "all bodies must belong to the same component")
    bridge = FakeBridge({"assembly.component_from_bodies": error})
    result, _ = call("assembly_component_from_bodies", {"bodies": ["a", "b"], "name": "P"}, bridge)
    assert result.is_error
    assert "same component" in result_text(result)


def test_assembly_activate() -> None:
    bridge = FakeBridge({"assembly.activate": {"active_component": "component:root"}})
    call("assembly_activate", {}, bridge)
    assert bridge.last.params == {}
    call("assembly_activate", {"occurrence": "occurrence:Lid:1"}, bridge)
    assert bridge.last.params == {"occurrence": "occurrence:Lid:1"}


def test_assembly_rename_component() -> None:
    answer = {"component": "component:Case", "occurrences": ["occurrence:Case:1"]}
    bridge = FakeBridge({"assembly.rename_component": answer})
    result, _ = call(
        "assembly_rename_component", {"component": "component:Box", "name": "Case"}, bridge
    )
    assert bridge.last.params == {"component": "component:Box", "name": "Case"}
    assert json.loads(result_text(result)) == answer
    rejected("assembly_rename_component", {"component": "component:Box", "name": ""})


# --- 3. occurrences ----------------------------------------------------------------------------


def test_assembly_add_occurrence() -> None:
    bridge = FakeBridge({"assembly.add_occurrence": {"occurrence": OCC}})
    call("assembly_add_occurrence", {"component": "component:Box"}, bridge)
    assert bridge.last.params == {"component": "component:Box", "independent": False}
    call(
        "assembly_add_occurrence",
        {"component": "component:Box", "independent": True, "translate_mm": [1, 2, 3]},
        bridge,
    )
    assert bridge.last.params == {
        "component": "component:Box",
        "independent": True,
        "translate_mm": [1, 2, 3],
    }


def test_assembly_transform_occurrence() -> None:
    bridge = FakeBridge({"assembly.transform_occurrence": {"occurrence": OCC, "captured": True}})
    call(
        "assembly_transform_occurrence",
        {"occurrence": "occurrence:Lid:1", "translate_mm": [0, 0, 40]},
        bridge,
    )
    assert bridge.last.params == {
        "occurrence": "occurrence:Lid:1",
        "translate_mm": [0, 0, 40],
        "capture_position": True,
    }
    call(
        "assembly_transform_occurrence",
        {"occurrence": "occurrence:Lid:1", "rotate": {"axis": [0, 0, 1], "angle_deg": 90}},
        bridge,
    )
    assert bridge.last.params["rotate"] == {
        "axis": [0, 0, 1],
        "angle_deg": 90,
        "origin_mm": [0, 0, 0],
    }
    absolute = {"origin_mm": [1, 2, 3], "x_axis": [1, 0, 0], "y_axis": [0, 1, 0]}
    call(
        "assembly_transform_occurrence",
        {"occurrence": "occurrence:Lid:1", "absolute": absolute, "capture_position": False},
        bridge,
    )
    assert bridge.last.params == {
        "occurrence": "occurrence:Lid:1",
        "absolute": absolute,
        "capture_position": False,
    }
    rejected(
        "assembly_transform_occurrence",
        {"occurrence": "occurrence:Lid:1", "rotate": {"axis": [0, 0, 0], "angle_deg": 90}},
    )


def test_assembly_set_occurrence_state() -> None:
    bridge = FakeBridge({"assembly.set_occurrence_state": {"occurrence": OCC}})
    call(
        "assembly_set_occurrence_state",
        {"occurrence": "occurrence:Box:1", "grounded": True},
        bridge,
    )
    assert bridge.last.params == {"occurrence": "occurrence:Box:1", "grounded": True}
    call(
        "assembly_set_occurrence_state",
        {"occurrence": "occurrence:Box:1", "light_bulb": False},
        bridge,
    )
    assert bridge.last.params == {"occurrence": "occurrence:Box:1", "light_bulb": False}


def test_assembly_delete() -> None:
    answer = {"deleted": {"name": "Box:2", "type": "Occurrence"}}
    bridge = FakeBridge({"assembly.delete": answer})
    result, _ = call("assembly_delete", {"item": "occurrence:Box:2"}, bridge)
    assert bridge.last.params == {"item": "occurrence:Box:2"}
    assert json.loads(result_text(result)) == answer
    rejected("assembly_delete", {"item": ""})

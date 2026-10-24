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


# --- 5. joint origins, joints, limits, driving ---------------------------------------------------

JOINT = {"ref": "Jt=", "type": "revolute", "values": {"rotation_deg": 0.0}}
EDGE_LID = {"kind": "curve", "entity": "E1=@Lid:1"}
EDGE_BOX = {"kind": "curve", "entity": "E2=@Box:1"}


def test_vocabularies_match_protocol() -> None:
    from typing import get_args

    from fusion_mcp.protocol import assembly as protocol
    from fusion_mcp.server.tools import assembly as tools

    assert get_args(tools.JointType) == protocol.JOINT_TYPES
    assert get_args(tools.Dof) == protocol.DOFS
    assert get_args(tools.JointGeometryKind) == protocol.GEOMETRY_KINDS
    assert get_args(tools.KeyPoint) == protocol.KEY_POINTS


def test_joint_docstrings() -> None:
    docs = descriptions()
    text = docs["assembly_create_joint"]
    assert "inspect_query" in text and "<token>@<occurrence" in text
    assert "MOVING" in docs["assembly_create_joint"] or "moves" in text
    assert "clamped" in docs["assembly_drive_joint"]
    assert '"off"' in docs["assembly_set_joint_limits"]


def test_assembly_create_joint_origin() -> None:
    answer = {"joint_origin": {"ref": "Jo=@Lid:1", "native_ref": "Jo="}}
    bridge = FakeBridge({"assembly.create_joint_origin": answer})
    geometry = {"kind": "planar_face", "entity": "F=@Lid:1", "key_point": "center"}
    result, _ = call(
        "assembly_create_joint_origin",
        {"geometry": geometry, "offset_mm": [0, 0, "5 mm"], "angle": 90},
        bridge,
    )
    assert bridge.last.params == {
        "geometry": geometry,
        "offset_mm": [0.0, 0.0, "5 mm"],
        "angle": 90.0,
        "flip": False,
    }
    assert json.loads(result_text(result)) == answer
    rejected("assembly_create_joint_origin", {"geometry": {"kind": "sphere", "entity": "x"}})
    rejected(
        "assembly_create_joint_origin",
        {"geometry": {"kind": "point", "entity": "v", "key_point": "center"}},
    )


def test_assembly_create_joint() -> None:
    bridge = FakeBridge({"assembly.create_joint": {"joint": JOINT}})
    result, _ = call(
        "assembly_create_joint",
        {
            "type": "revolute",
            "geometry_one": EDGE_LID,
            "geometry_two": EDGE_BOX,
            "motion": {"axis": "E2=@Box:1"},
            "angle": "45 deg",
        },
        bridge,
    )
    assert bridge.last.method == "assembly.create_joint"
    assert bridge.last.params == {
        "type": "revolute",
        "geometry_one": EDGE_LID,
        "geometry_two": EDGE_BOX,
        "motion": {"axis": "E2=@Box:1"},
        "angle": "45 deg",
        "flip": False,
    }
    assert json.loads(result_text(result)) == {"joint": JOINT}
    rejected(
        "assembly_create_joint",
        {"type": "hinge", "geometry_one": EDGE_LID, "geometry_two": EDGE_BOX},
    )
    rejected("assembly_create_joint", {"type": "rigid", "geometry_one": EDGE_LID})


def test_assembly_create_joint_error_is_passed_on() -> None:
    error = BridgeError(ErrorCode.INVALID_PARAMS, "motion: axis not valid for a slider joint")
    bridge = FakeBridge({"assembly.create_joint": error})
    result, _ = call(
        "assembly_create_joint",
        {
            "type": "slider",
            "geometry_one": EDGE_LID,
            "geometry_two": EDGE_BOX,
            "motion": {"axis": "x"},
        },
        bridge,
    )
    assert result.is_error and "slider" in result_text(result)


def test_assembly_create_as_built_joint() -> None:
    bridge = FakeBridge({"assembly.create_as_built_joint": {"joint": JOINT}})
    call(
        "assembly_create_as_built_joint",
        {
            "type": "rigid",
            "occurrence_one": "occurrence:Lid:1",
            "occurrence_two": "occurrence:Box:1",
        },
        bridge,
    )
    assert bridge.last.params == {
        "type": "rigid",
        "occurrence_one": "occurrence:Lid:1",
        "occurrence_two": "occurrence:Box:1",
    }
    call(
        "assembly_create_as_built_joint",
        {
            "type": "revolute",
            "occurrence_one": "occurrence:Lid:1",
            "occurrence_two": "occurrence:Box:1",
            "geometry": EDGE_BOX,
            "name": "AB",
        },
        bridge,
    )
    assert bridge.last.params["geometry"] == EDGE_BOX and bridge.last.params["name"] == "AB"
    rejected(
        "assembly_create_as_built_joint", {"type": "rigid", "occurrence_one": "occurrence:Lid:1"}
    )


def test_assembly_set_joint_limits() -> None:
    bridge = FakeBridge({"assembly.set_joint_limits": {"joint": JOINT}})
    call(
        "assembly_set_joint_limits",
        {"joint": "Jt=", "dof": "rotation", "min": "off", "max": 110},
        bridge,
    )
    assert bridge.last.params == {"joint": "Jt=", "dof": "rotation", "min": "off", "max": 110.0}
    rejected("assembly_set_joint_limits", {"joint": "Jt=", "dof": "twist", "max": 1})
    rejected("assembly_set_joint_limits", {"joint": "Jt=", "dof": "rotation", "max": "on"})


def test_assembly_drive_joint() -> None:
    answer = {"joint": JOINT, "requested": {"rotation": 90.0}, "clamped": [], "captured": True}
    bridge = FakeBridge({"assembly.drive_joint": answer})
    result, _ = call("assembly_drive_joint", {"joint": "Jt=", "values": {"rotation": 90}}, bridge)
    assert bridge.last.params == {
        "joint": "Jt=",
        "values": {"rotation": 90.0},
        "capture_position": True,
    }
    assert json.loads(result_text(result)) == answer
    rejected("assembly_drive_joint", {"joint": "Jt=", "values": {}})
    rejected("assembly_drive_joint", {"joint": "Jt=", "values": {"twist": 1}})

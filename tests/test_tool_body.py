"""``body_*`` tools on ``FakeBridge`` (c08 D8-D13; tasks 4.4-4.6, 5.4-5.5, 6.5-6.8,
7.4-7.6)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text

IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


class Rejected:
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


def feature_result(kind: str) -> dict[str, Any]:
    return {
        "feature": {"ref": "feat-1", "type": kind, "name": f"{kind[:-7]}1"},
        "health": {"state": "ok", "message": ""},
        "created_bodies": [],
        "modified_bodies": [{"ref": "body-1", "type": "BRepBody", "name": "Body1"}],
        "removed_bodies": [],
    }


def sent(name: str, method: str, arguments: dict[str, Any], data: Any = None) -> dict[str, Any]:
    """Call ``name`` and return the params sent as ``method``; the bridge answer is
    returned unchanged."""
    data = data if data is not None else feature_result("CombineFeature")
    bridge = FakeBridge({method: data})
    result, _ = call(name, arguments, bridge)
    assert result.is_error is False, result_text(result)
    assert bridge.last.method == method
    assert json.loads(result_text(result)) == data
    params: dict[str, Any] = bridge.last.params
    return params


def refused(name: str, arguments: dict[str, Any], param: str | None = None) -> None:
    result, bridge = call(name, arguments)
    assert bridge.calls == [], arguments
    assert result.is_error is True
    if param is not None:
        text = result_text(result)
        assert text.startswith("INVALID_PARAMS:"), text
        assert f'"param": "{param}"' in text, text


# --- 4.4 body_combine ----------------------------------------------------------------------


def test_combine() -> None:
    assert sent("body_combine", "body.combine", {"target": "a", "tools": ["b", "c"]}) == {
        "target": "a",
        "tools": ["b", "c"],
        "operation": "join",
        "keep_tools": False,
        "new_component": False,
    }
    params = sent(
        "body_combine",
        "body.combine",
        {"target": "a", "tools": ["b"], "operation": "cut", "keep_tools": True},
    )
    assert params["operation"] == "cut" and params["keep_tools"] is True
    refused("body_combine", {"target": "a", "tools": ["b", "a"]}, "tools")
    refused("body_combine", {"target": "a", "tools": []})
    refused("body_combine", {"target": "a", "tools": ["b"], "operation": "new_body"})


# --- 4.5 body_split ------------------------------------------------------------------------


def test_split() -> None:
    assert sent("body_split", "body.split", {"bodies": ["b"], "tool": "origin:XZ"}) == {
        "bodies": ["b"],
        "tool": "origin:XZ",
        "extend_tool": True,
    }
    refused("body_split", {"bodies": ["b"]})
    refused("body_split", {"bodies": [], "tool": "origin:XZ"})


# --- 4.6 body_split_face -------------------------------------------------------------------


def test_split_face() -> None:
    assert sent("body_split_face", "body.split_face", {"faces": ["f"], "tool": "p"}) == {
        "faces": ["f"],
        "tool": "p",
        "split_type": "intersection",
        "extend_tool": True,
    }
    params = sent(
        "body_split_face",
        "body.split_face",
        {"faces": ["f"], "tool": "p", "split_type": "along_vector", "direction": "e"},
    )
    assert params["direction"] == "e"
    refused(
        "body_split_face", {"faces": ["f"], "tool": "p", "split_type": "along_vector"}, "direction"
    )
    refused("body_split_face", {"faces": ["f"], "tool": "p", "direction": "e"}, "direction")
    refused("body_split_face", {"faces": ["f"], "tool": "p", "split_type": "slice"})


# --- 5.4 body_move -------------------------------------------------------------------------


def test_move_translate_and_copy() -> None:
    params = sent(
        "body_move",
        "body.move",
        {"bodies": ["b"], "transform": {"type": "translate", "x": 10, "z": "gap"}},
    )
    assert params == {
        "bodies": ["b"],
        "transform": {"type": "translate", "x": 10, "y": 0.0, "z": "gap", "design_space": True},
        "keep_original": False,
        "units": "mm",
    }
    params = sent(
        "body_move",
        "body.move",
        {
            "bodies": ["b"],
            "transform": {"type": "rotate", "axis": "origin:Z", "angle": 90},
            "keep_original": True,
            "units": "cm",
        },
    )
    assert params["transform"] == {"type": "rotate", "axis": "origin:Z", "angle": 90}
    assert params["keep_original"] is True and params["units"] == "cm"


def test_move_align_matrix_and_schema() -> None:
    params = sent(
        "body_move",
        "body.move",
        {"bodies": ["b"], "transform": {"type": "align", "from": "v", "to": "origin:O"}},
    )
    assert params["transform"] == {"type": "align", "from": "v", "to": "origin:O"}
    params = sent(
        "body_move",
        "body.move",
        {"bodies": ["b"], "transform": {"type": "matrix", "matrix": IDENTITY}},
    )
    assert len(params["transform"]["matrix"]) == 16
    refused("body_move", {"bodies": ["b"], "transform": {"type": "matrix", "matrix": IDENTITY[:4]}})
    refused("body_move", {"bodies": ["b"], "transform": {"type": "scale"}})
    refused("body_move", {"bodies": ["b"]})
    description = tool("body_move").description or ""
    assert "body_scale" in description and "pattern_" in description


def test_move_bridge_error_is_returned() -> None:
    error = BridgeError(
        ErrorCode.INVALID_PARAMS, "matrix is not rigid", {"param": "transform.matrix"}
    )
    bridge = FakeBridge({"body.move": error})
    result, _ = call(
        "body_move",
        {"bodies": ["b"], "transform": {"type": "matrix", "matrix": IDENTITY}},
        bridge,
    )
    assert result.is_error is True and "transform.matrix" in result_text(result)


# --- 5.5 body_scale ------------------------------------------------------------------------


def test_scale() -> None:
    assert sent("body_scale", "body.scale", {"bodies": ["b"], "factor": 2}) == {
        "bodies": ["b"],
        "factor": 2,
        "point": "origin:O",
    }
    params = sent(
        "body_scale",
        "body.scale",
        {"bodies": ["b"], "factors": {"x": 1, "y": 2, "z": 0.5}, "point": "v"},
    )
    assert params == {"bodies": ["b"], "factors": {"x": 1, "y": 2, "z": 0.5}, "point": "v"}
    refused("body_scale", {"bodies": ["b"], "factor": 0})
    refused(
        "body_scale", {"bodies": ["b"], "factor": 2, "factors": {"x": 1, "y": 1, "z": 1}}, "factors"
    )
    refused("body_scale", {"bodies": ["b"], "factors": {"x": 1, "y": -1, "z": 1}})


# --- 6.5 body_thicken ----------------------------------------------------------------------


def test_thicken() -> None:
    assert sent("body_thicken", "body.thicken", {"faces": ["f"], "thickness": 2}) == {
        "faces": ["f"],
        "thickness": 2.0,
        "symmetric": False,
        "operation": "new_body",
        "chain": True,
        "units": "mm",
    }
    refused("body_thicken", {"faces": ["f"]})
    refused("body_thicken", {"faces": ["f"], "thickness": 2, "operation": "merge"})


# --- 6.6 body_boundary_fill ----------------------------------------------------------------


def test_boundary_fill() -> None:
    assert sent("body_boundary_fill", "body.boundary_fill", {"tools": ["a", "origin:XY"]}) == {
        "tools": ["a", "origin:XY"],
        "operation": "new_body",
        "remove_tools": False,
        "units": "mm",
    }
    params = sent(
        "body_boundary_fill",
        "body.boundary_fill",
        {"tools": ["a"], "cell_points": [[5, 5, 5]], "remove_tools": True},
    )
    assert params["cell_points"] == [[5.0, 5.0, 5.0]] and params["remove_tools"] is True
    refused("body_boundary_fill", {"tools": ["a"], "cell_points": [[5, 5]]})
    refused("body_boundary_fill", {"tools": []})
    assert "details.cells" in (tool("body_boundary_fill").description or "")


# --- 6.7 body_delete_faces -----------------------------------------------------------------


def test_delete_faces() -> None:
    assert sent("body_delete_faces", "body.delete_faces", {"faces": ["f1", "f2"]}) == {
        "faces": ["f1", "f2"]
    }
    refused("body_delete_faces", {"faces": []})


# --- 6.8 body_replace_face -----------------------------------------------------------------


def test_replace_face() -> None:
    assert sent("body_replace_face", "body.replace_face", {"faces": ["f"], "target": "p"}) == {
        "faces": ["f"],
        "target": "p",
        "tangent_chain": True,
    }
    refused("body_replace_face", {"faces": ["f"]})


# --- 7.4 body_rename -----------------------------------------------------------------------


def test_rename() -> None:
    data = {"body": {"ref": "b", "type": "BRepBody", "name": "Lid"}, "previous_name": "Body1"}
    assert sent("body_rename", "body.rename", {"body": "b", "name": " Lid "}, data) == {
        "body": "b",
        "name": "Lid",
    }
    refused("body_rename", {"body": "b", "name": "   "}, "name")
    refused("body_rename", {"body": "b", "name": ""})


# --- 7.5 body_set_visibility ---------------------------------------------------------------


def test_set_visibility() -> None:
    data = {
        "bodies": [
            {
                "ref": "a",
                "type": "BRepBody",
                "name": "A",
                "light_bulb": False,
                "is_visible": False,
                "opacity": 0.3,
            }
        ]
    }
    params = sent(
        "body_set_visibility",
        "body.set_visibility",
        {"bodies": ["a"], "visible": False, "opacity": 0.3},
        data,
    )
    assert params == {"bodies": ["a"], "visible": False, "opacity": 0.3}
    assert sent(
        "body_set_visibility", "body.set_visibility", {"bodies": ["a"], "opacity": 1}, data
    ) == {
        "bodies": ["a"],
        "opacity": 1.0,
    }
    refused("body_set_visibility", {"bodies": ["a"]}, "visible")
    refused("body_set_visibility", {"bodies": ["a"], "opacity": 1.5})


# --- 7.6 body_remove -----------------------------------------------------------------------


def test_remove() -> None:
    data = {
        "removed": ["b"],
        "features": [{"ref": "r", "type": "RemoveFeature"}],
        "mode_used": "remove",
    }
    assert sent("body_remove", "body.remove", {"bodies": ["b"]}, data) == {
        "bodies": ["b"],
        "mode": "remove",
    }
    assert sent("body_remove", "body.remove", {"bodies": ["b"], "mode": "delete"}, data)[
        "mode"
    ] == ("delete")
    refused("body_remove", {"bodies": ["b"], "mode": "erase"})
    assert "direct design" in (tool("body_remove").description or "")

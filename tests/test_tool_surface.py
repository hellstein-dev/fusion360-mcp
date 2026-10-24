"""``surface_*`` tools on ``FakeBridge`` (c11 D4-D11, D13; tasks 3.1-3.9)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text

SURFACE_TOOLS = (
    "surface_patch",
    "surface_extend",
    "surface_trim_cells",
    "surface_trim",
    "surface_untrim",
    "surface_stitch",
    "surface_unstitch",
    "surface_ruled",
    "surface_offset",
)


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
        "created_bodies": [{"ref": "b-1", "type": "BRepBody", "name": "B1", "is_solid": False}],
        "modified_bodies": [],
        "removed_bodies": [],
    }


def sent(name: str, method: str, arguments: dict[str, Any], data: Any = None) -> dict[str, Any]:
    """Call ``name`` and return the params sent as ``method``; the answer is passed through."""
    data = data if data is not None else feature_result("PatchFeature")
    bridge = FakeBridge({method: data})
    result, _ = call(name, arguments, bridge)
    assert result.is_error is False, getattr(result, "message", None) or result_text(result)
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


@pytest.mark.parametrize("name", SURFACE_TOOLS)
def test_tools_have_schema_and_docstring(name: str) -> None:
    t = tool(name)
    assert t.description and "Example" in t.description
    assert t.input_schema["type"] == "object"


# --- 3.1 patch -----------------------------------------------------------------------------


def test_patch_defaults() -> None:
    assert sent("surface_patch", "surface.patch", {"boundary": ["profile:s@0,0"]}) == {
        "boundary": ["profile:s@0,0"],
        "continuity": "connected",
        "weight": 0.5,
        "flip_continuity_direction": False,
        "operation": "new_body",
    }


def test_patch_options_and_rejections() -> None:
    params = sent(
        "surface_patch",
        "surface.patch",
        {
            "boundary": ["e1", {"sketch": "s", "all": True}],
            "continuity": "curvature",
            "weight": 1,
            "flip_continuity_direction": True,
            "operation": "new_component",
        },
    )
    assert params["boundary"] == ["e1", {"sketch": "s", "all": True}]
    assert params["continuity"] == "curvature" and params["operation"] == "new_component"
    refused("surface_patch", {"boundary": ["e"], "operation": "join"})
    refused("surface_patch", {"boundary": ["e"], "weight": 2})
    refused("surface_patch", {"boundary": []})
    assert "surface_stitch" in (tool("surface_patch").description or "")


# --- 3.2 extend ----------------------------------------------------------------------------


def test_extend() -> None:
    assert sent("surface_extend", "surface.extend", {"edges": ["e"], "distance": 5}) == {
        "edges": ["e"],
        "distance": 5,
        "extend_type": "natural",
        "chain": True,
        "alignment": "free",
        "units": "mm",
    }
    params = sent(
        "surface_extend",
        "surface.extend",
        {
            "edges": ["e"],
            "distance": "d",
            "extend_type": "perpendicular",
            "chain": False,
            "alignment": "align",
            "units": "in",
        },
    )
    assert params["distance"] == "d" and params["alignment"] == "align"
    refused("surface_extend", {"edges": ["e"]})
    refused("surface_extend", {"edges": ["e"], "distance": 1, "extend_type": "x"})


# --- 3.3 trim cells ------------------------------------------------------------------------


def test_trim_cells() -> None:
    data = {"cells": [{"center": [0, 0, 0], "area_mm2": 1.0}], "count": 1}
    assert sent("surface_trim_cells", "surface.trim_cells", {"trim_tool": "origin:XY"}, data) == {
        "trim_tool": "origin:XY"
    }
    refused("surface_trim_cells", {})


# --- 3.4 trim ------------------------------------------------------------------------------


def test_trim() -> None:
    data = {**feature_result("TrimFeature"), "removed_cells": 1}
    params = sent(
        "surface_trim", "surface.trim", {"trim_tool": "p", "remove_points": [[0, 0, 20]]}, data
    )
    assert params == {
        "trim_tool": "p",
        "remove_points": [[0, 0, 20]],
        "max_distance": 1.0,
        "units": "mm",
    }
    params = sent(
        "surface_trim",
        "surface.trim",
        {"trim_tool": "p", "remove_points": [[0, 0, 1], [1, 1, 1]], "max_distance": 0.5},
        data,
    )
    assert params["max_distance"] == 0.5 and len(params["remove_points"]) == 2
    refused("surface_trim", {"trim_tool": "p", "remove_points": []})
    refused("surface_trim", {"trim_tool": "p", "remove_points": [[0, 0]]})
    refused("surface_trim", {"trim_tool": "p", "remove_points": [[0, 0, 0]], "max_distance": -1})
    description = tool("surface_trim").description or ""
    assert "surface_trim_cells" in description and "mm" in description


# --- 3.5 untrim ----------------------------------------------------------------------------


def test_untrim() -> None:
    assert sent("surface_untrim", "surface.untrim", {"faces": ["f"]}) == {
        "faces": ["f"],
        "loop_type": "all",
        "units": "mm",
    }
    params = sent(
        "surface_untrim",
        "surface.untrim",
        {"faces": ["f"], "loop_type": "external", "extension_distance": 2},
    )
    assert params["extension_distance"] == 2 and params["loop_type"] == "external"
    refused(
        "surface_untrim",
        {"faces": ["f"], "loop_type": "internal", "extension_distance": 1},
        "extension_distance",
    )
    refused("surface_untrim", {"faces": ["f"], "loop_type": "manual"})


# --- 3.6 stitch ----------------------------------------------------------------------------


def test_stitch() -> None:
    data = {**feature_result("StitchFeature"), "is_solid": True}
    assert sent("surface_stitch", "surface.stitch", {"surfaces": ["a", "b"]}, data) == {
        "surfaces": ["a", "b"],
        "tolerance": 0.1,
        "operation": "new_body",
        "units": "mm",
    }
    params = sent(
        "surface_stitch",
        "surface.stitch",
        {"surfaces": ["a", "b", "c"], "tolerance": "tol", "operation": "new_component"},
        data,
    )
    assert params["tolerance"] == "tol"
    refused("surface_stitch", {"surfaces": ["a"]})
    refused("surface_stitch", {"surfaces": ["a", "a"]}, "surfaces")
    refused("surface_stitch", {"surfaces": ["a", "b"], "operation": "join"})
    description = tool("surface_stitch").description or ""
    assert "is_solid" in description and "tolerance" in description


# --- 3.7 unstitch --------------------------------------------------------------------------


def test_unstitch() -> None:
    assert sent("surface_unstitch", "surface.unstitch", {"entities": ["f"]}) == {
        "entities": ["f"],
        "chain": True,
    }
    params = sent("surface_unstitch", "surface.unstitch", {"entities": ["f", "b"], "chain": False})
    assert params["chain"] is False
    refused("surface_unstitch", {"entities": []})


# --- 3.8 ruled -----------------------------------------------------------------------------


def test_ruled() -> None:
    assert sent("surface_ruled", "surface.ruled", {"edges": ["e"], "distance": 10}) == {
        "edges": ["e"],
        "distance": 10,
        "angle": 0.0,
        "ruled_type": "normal",
        "corner": "rounded",
        "alternate_face": False,
        "chain": True,
        "operation": "new_body",
        "units": "mm",
    }
    params = sent(
        "surface_ruled",
        "surface.ruled",
        {
            "edges": ["e"],
            "distance": 5,
            "angle": 15,
            "ruled_type": "direction",
            "direction": "origin:Z",
            "corner": "mitered",
        },
    )
    assert params["direction"] == "origin:Z" and params["ruled_type"] == "direction"
    refused(
        "surface_ruled", {"edges": ["e"], "distance": 1, "ruled_type": "direction"}, "direction"
    )
    refused("surface_ruled", {"edges": ["e"], "distance": 1, "direction": "origin:Z"}, "direction")
    refused("surface_ruled", {"edges": ["e"], "distance": 1, "operation": "cut"})


# --- 3.9 offset ----------------------------------------------------------------------------


def test_offset() -> None:
    assert sent("surface_offset", "surface.offset", {"faces": ["f"], "distance": 2}) == {
        "faces": ["f"],
        "distance": 2,
        "chain": True,
        "offset_type": "sharp",
        "operation": "new_body",
        "units": "mm",
    }
    params = sent(
        "surface_offset",
        "surface.offset",
        {"faces": ["f"], "distance": -1, "offset_type": "rounded", "chain": False},
    )
    assert params["distance"] == -1 and params["offset_type"] == "rounded"
    refused("surface_offset", {"faces": ["f"]})
    refused("surface_offset", {"faces": ["f"], "distance": 1, "operation": "join"})
    description = tool("surface_offset").description or ""
    assert "feature_offset_faces" in description and "body_thicken" in description

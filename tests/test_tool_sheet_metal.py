"""``sheet_metal_*`` tools on ``FakeBridge`` (c13 D9; tasks 5.1-5.15)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text

SHEET_METAL_TOOLS = (
    "sheet_metal_capabilities",
    "sheet_metal_list_rules",
    "sheet_metal_create_rule",
    "sheet_metal_assign_rule",
    "sheet_metal_convert",
    "sheet_metal_base_flange",
    "sheet_metal_contour_flange",
    "sheet_metal_edge_flange",
    "sheet_metal_list_bends",
    "sheet_metal_unfold",
    "sheet_metal_refold",
    "sheet_metal_create_flat_pattern",
    "sheet_metal_get_flat_pattern",
    "sheet_metal_delete_flat_pattern",
    "sheet_metal_export_flat_pattern",
)
PREVIEW_TOOLS = (
    "sheet_metal_convert",
    "sheet_metal_base_flange",
    "sheet_metal_contour_flange",
    "sheet_metal_edge_flange",
    "sheet_metal_list_bends",
    "sheet_metal_unfold",
    "sheet_metal_refold",
)
FEATURE = {
    "feature": {"ref": "feat-1", "type": "FlangeFeature", "name": "Flange1"},
    "health": {"state": "ok", "message": ""},
    "created_bodies": [{"ref": "b-1", "type": "BRepBody", "name": "B1", "is_sheet_metal": True}],
    "modified_bodies": [],
    "removed_bodies": [],
    "rule": "Steel",
}


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


def sent(name: str, method: str, arguments: dict[str, Any], data: Any = None) -> dict[str, Any]:
    """Call ``name`` and return the params sent as ``method``; the answer is passed through."""
    data = data if data is not None else FEATURE
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


def test_all_tools_registered_with_docstrings() -> None:
    names = {t.name for t in asyncio.run(create_server(FakeBridge()).list_tools())}
    assert set(SHEET_METAL_TOOLS) <= names
    for name in PREVIEW_TOOLS:
        assert "api_unavailable" in (tool(name).description or ""), name
    for name in ("sheet_metal_base_flange", "sheet_metal_contour_flange"):
        description = tool(name).description or ""
        assert "Bend along a sketch line is not available" in description
        assert "sheet_metal_export_flat_pattern" in description


# --- 5.1 capabilities --------------------------------------------------------------------------


def test_capabilities() -> None:
    data = {"rules": True, "sketch_bend": False, "contour_flange": False}
    assert sent("sheet_metal_capabilities", "sheet_metal.capabilities", {}, data) == {}


# --- 5.2-5.4 rules -----------------------------------------------------------------------------


def test_list_rules() -> None:
    data = {"rules": [], "count": 0}
    assert sent("sheet_metal_list_rules", "sheet_metal.list_rules", {}, data) == {"source": "all"}
    params = sent("sheet_metal_list_rules", "sheet_metal.list_rules", {"source": "library"}, data)
    assert params == {"source": "library"}
    refused("sheet_metal_list_rules", {"source": "cloud"})


def test_create_rule() -> None:
    data = {"rule": {"name": "Steel 2mm"}, "base": {"name": "Steel", "source": "library"}}
    params = sent(
        "sheet_metal_create_rule",
        "sheet_metal.create_rule",
        {
            "name": "Steel 2mm",
            "thickness": 2,
            "bend_radius": "Thickness * 1",
            "k_factor": 0.44,
            "relief_shape": "round",
        },
        data,
    )
    assert params == {
        "name": "Steel 2mm",
        "thickness": 2,
        "bend_radius": "Thickness * 1",
        "k_factor": 0.44,
        "relief_shape": "round",
        "units": "mm",
    }
    based = sent(
        "sheet_metal_create_rule",
        "sheet_metal.create_rule",
        {"name": "Al", "base_rule": "Aluminum", "gap": 0.1, "units": "in"},
        data,
    )
    assert based == {
        "name": "Al",
        "base_rule": "Aluminum",
        "base_source": "all",
        "gap": 0.1,
        "units": "in",
    }
    refused("sheet_metal_create_rule", {"name": "X", "k_factor": 1.5})
    refused("sheet_metal_create_rule", {"name": ""})


def test_assign_rule() -> None:
    data = {"component": "root", "rule": {"name": "Steel"}, "previous": None}
    params = sent("sheet_metal_assign_rule", "sheet_metal.assign_rule", {"rule": "Steel"}, data)
    assert params == {"rule": "Steel", "source": "all"}
    params = sent(
        "sheet_metal_assign_rule",
        "sheet_metal.assign_rule",
        {"rule": "Mine", "source": "design", "component": "comp-1"},
        data,
    )
    assert params == {"rule": "Mine", "source": "design", "component": "comp-1"}
    refused("sheet_metal_assign_rule", {})


# --- 5.5-5.8 sheet metal bodies ----------------------------------------------------------------


def test_convert() -> None:
    data = {"body": "b-1", "component": "root", "rule": {"name": "Steel"}, "is_sheet_metal": True}
    params = sent(
        "sheet_metal_convert", "sheet_metal.convert", {"body": "b-1", "base_face": "f-1"}, data
    )
    assert params == {"body": "b-1", "base_face": "f-1", "rule_source": "all"}
    assert "preview" in (tool("sheet_metal_convert").description or "")
    refused("sheet_metal_convert", {"body": "b-1"})


def test_base_flange() -> None:
    params = sent(
        "sheet_metal_base_flange",
        "sheet_metal.base_flange",
        {"profiles": ["profile:s@0,0"], "rule": "Steel"},
    )
    assert params == {
        "profiles": ["profile:s@0,0"],
        "orientation": "side_one",
        "operation": "new_body",
        "rule": "Steel",
        "rule_source": "all",
    }
    refused("sheet_metal_base_flange", {"profiles": ["p"], "operation": "join"})
    refused("sheet_metal_base_flange", {"profiles": []})


def test_contour_flange() -> None:
    params = sent(
        "sheet_metal_contour_flange",
        "sheet_metal.contour_flange",
        {"profiles": ["line1", "line2"], "distance": 40},
    )
    assert params == {
        "profiles": ["line1", "line2"],
        "distance": 40,
        "direction": "one_side",
        "orientation": "side_one",
        "operation": "new_body",
        "rule_source": "all",
        "units": "mm",
    }
    two = sent(
        "sheet_metal_contour_flange",
        "sheet_metal.contour_flange",
        {
            "profiles": ["line1"],
            "distance": 10,
            "distance_two": "d2",
            "direction": "two_sides",
            "bend_radius": 2,
        },
    )
    assert two["distance_two"] == "d2" and two["bend_radius"] == 2
    refused(
        "sheet_metal_contour_flange",
        {"profiles": ["l"], "distance": 10, "direction": "two_sides"},
        "distance_two",
    )
    refused(
        "sheet_metal_contour_flange",
        {"profiles": ["l"], "distance": 10, "distance_two": 5},
        "distance_two",
    )


def test_edge_flange_defaults() -> None:
    params = sent(
        "sheet_metal_edge_flange", "sheet_metal.edge_flange", {"edges": ["e-1"], "height": 20}
    )
    assert params == {
        "edges": ["e-1"],
        "height": 20,
        "angle": 90.0,
        "width": {"type": "full"},
        "height_datum": "outer",
        "bend_position": "start_edge",
        "mitered_corners": True,
        "flip": False,
        "units": "mm",
    }


def test_edge_flange_options_and_refusals() -> None:
    params = sent(
        "sheet_metal_edge_flange",
        "sheet_metal.edge_flange",
        {
            "edges": ["e-1"],
            "to_entity": "f-1",
            "to_offset": 2,
            "angle": 45,
            "width": {"type": "two_sided", "width1": 10, "width2": 5},
            "height_datum": "inner",
            "bend_position": "outside",
            "flip": True,
        },
    )
    assert params["to_entity"] == "f-1" and params["to_offset"] == 2
    assert params["width"] == {"type": "two_sided", "width1": 10, "width2": 5}
    assert "height" not in params
    refused("sheet_metal_edge_flange", {"edges": ["e"], "height": 5, "to_entity": "f"}, "height")
    refused("sheet_metal_edge_flange", {"edges": ["e"]}, "height")
    refused("sheet_metal_edge_flange", {"edges": ["e", "e2"], "to_entity": "f"}, "to_entity")
    refused("sheet_metal_edge_flange", {"edges": ["e"], "height": 5, "to_offset": 1}, "to_offset")
    refused(
        "sheet_metal_edge_flange", {"edges": ["e"], "height": 5, "width": {"type": "symmetric"}}
    )


# --- 5.9-5.11 bends ----------------------------------------------------------------------------


def test_list_bends() -> None:
    data = {"body": "b-1", "bends": [{"inner": "f1", "outer": "f2", "radius_mm": 1.0}], "count": 1}
    params = sent("sheet_metal_list_bends", "sheet_metal.list_bends", {"body": "b-1"}, data)
    assert params == {"body": "b-1"}


def test_unfold() -> None:
    assert sent("sheet_metal_unfold", "sheet_metal.unfold", {"stationary_face": "f-1"}) == {
        "stationary_face": "f-1"
    }
    params = sent(
        "sheet_metal_unfold",
        "sheet_metal.unfold",
        {"stationary_face": "f-1", "bend_faces": ["f-2", "f-3"]},
    )
    assert params["bend_faces"] == ["f-2", "f-3"]
    refused("sheet_metal_unfold", {"stationary_face": "f", "bend_faces": []})


def test_refold() -> None:
    assert sent("sheet_metal_refold", "sheet_metal.refold", {"unfold": "u-1"}) == {"unfold": "u-1"}
    refused("sheet_metal_refold", {})


# --- 5.12-5.15 flat pattern --------------------------------------------------------------------

FLAT = {"component": "root", "exists": True, "size_mm": [80, 30], "bend_count": 1, "bends": []}


def test_create_flat_pattern() -> None:
    params = sent(
        "sheet_metal_create_flat_pattern",
        "sheet_metal.create_flat_pattern",
        {"stationary_face": "f-1"},
        FLAT,
    )
    assert params == {"stationary_face": "f-1", "replace": False}


def test_get_flat_pattern() -> None:
    assert sent("sheet_metal_get_flat_pattern", "sheet_metal.get_flat_pattern", {}, FLAT) == {}
    params = sent(
        "sheet_metal_get_flat_pattern", "sheet_metal.get_flat_pattern", {"component": "c"}, FLAT
    )
    assert params == {"component": "c"}


def test_delete_flat_pattern() -> None:
    data = {"component": "root", "deleted": True}
    assert (
        sent("sheet_metal_delete_flat_pattern", "sheet_metal.delete_flat_pattern", {}, data) == {}
    )


def test_export_flat_pattern_params(tmp_path: Path) -> None:
    path = str(tmp_path / "bracket.dxf")
    data = {"files": [{"path": path, "size_bytes": 10}], "format": "dxf", "scope": "root"}
    bridge = FakeBridge({"sheet_metal.export_flat_pattern": data})
    result, _ = call("sheet_metal_export_flat_pattern", {"path": path}, bridge)
    assert result.is_error is False, result_text(result)
    assert bridge.last.params == {
        "path": path,
        "file_units": "mm",
        "bend_lines": True,
        "extent_lines": True,
        "splines_to_polylines": False,
        "polyline_tolerance": 0.1,
        "units": "mm",
        "overwrite": False,
    }
    assert bridge.last.timeout_s is not None and bridge.last.timeout_s >= 300


@pytest.mark.parametrize(
    "path", ["bracket.dxf", "relative/out.dxf", "C:/no-such-dir-c13/out.dxf", "C:/out/a.stl"]
)
def test_export_flat_pattern_bad_path_never_calls_bridge(path: str) -> None:
    result, bridge = call("sheet_metal_export_flat_pattern", {"path": path})
    assert result.is_error is True
    assert bridge.calls == []

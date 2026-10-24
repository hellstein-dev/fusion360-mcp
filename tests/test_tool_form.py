"""``form_*`` tools on ``FakeBridge`` (c14 D10; tasks 3.1-3.9)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text

FORM_TOOLS = (
    "form_list",
    "form_create_primitive",
    "form_create_from_tsm",
    "form_get_tsm",
    "form_inspect",
    "form_move",
    "form_crease",
    "form_insert_edge",
    "form_convert_to_brep",
)
BODY = {"ref": "ts-1", "type": "TSplineBody", "name": "Body1", "brep_body": "b-1"}
CREATED = {"form": "form-1", "body": BODY, "created_form": True}
EDITED = {"body": BODY, "changed": 1}


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
    data = data if data is not None else CREATED
    bridge = FakeBridge({method: data})
    result, _ = call(name, arguments, bridge)
    assert result.is_error is False, getattr(result, "message", None) or result_text(result)
    assert bridge.last.method == method
    assert json.loads(result_text(result)) == data
    params: dict[str, Any] = bridge.last.params
    return params


def refused(name: str, arguments: dict[str, Any], param: str | None = None) -> str:
    result, bridge = call(name, arguments)
    assert bridge.calls == [], arguments
    assert result.is_error is True
    if param is None:
        return ""
    text = result_text(result)
    assert text.startswith("INVALID_PARAMS:"), text
    assert f'"param": "{param}"' in text, text
    return text


def test_all_tools_registered_with_docstrings() -> None:
    names = {t.name for t in asyncio.run(create_server(FakeBridge()).list_tools())}
    assert set(FORM_TOOLS) <= names
    for name in FORM_TOOLS:
        assert len(tool(name).description or "") > 80, name
    assert "NOT parametric" in (tool("form_create_primitive").description or "")
    assert "torus: major_radius" in (tool("form_create_primitive").description or "")
    for name in ("form_move", "form_crease", "form_insert_edge"):
        assert "form_inspect" in (tool(name).description or ""), name
        assert "details.nearest" in (tool(name).description or ""), name
    assert "api_unavailable" in (tool("form_insert_edge").description or "")
    assert "preview" in (tool("form_insert_edge").description or "")
    assert "independent" in (tool("form_convert_to_brep").description or "")
    assert "form_get_tsm" in (tool("form_create_from_tsm").description or "")


# --- 3.1 list --------------------------------------------------------------------------------


def test_list() -> None:
    data = {"forms": [], "count": 0}
    assert sent("form_list", "form.list", {}, data) == {}
    assert sent("form_list", "form.list", {"component": "comp-1"}, data) == {"component": "comp-1"}


# --- 3.2 create_primitive --------------------------------------------------------------------


def test_create_box() -> None:
    params = sent(
        "form_create_primitive",
        "form.create_primitive",
        {"shape": "box", "x_size": 40, "y_size": 20, "z_size": 10, "x_faces": 4},
    )
    assert params == {
        "shape": "box",
        "x_size": 40,
        "y_size": 20,
        "z_size": 10,
        "x_faces": 4,
        "units": "mm",
    }


def test_create_with_options() -> None:
    params = sent(
        "form_create_primitive",
        "form.create_primitive",
        {
            "shape": "cylinder",
            "radius": 10,
            "height": 30,
            "symmetry": "circular",
            "symmetric_faces": 4,
            "origin": [0, 0, 5],
            "x_direction": [1, 0, 0],
            "y_direction": [0, 0, 1],
            "name": "Grip",
            "form": "form-1",
            "units": "cm",
        },
    )
    assert params["symmetry"] == "circular" and params["origin"] == [0, 0, 5]
    assert params["form"] == "form-1" and params["units"] == "cm" and params["name"] == "Grip"
    params = sent(
        "form_create_primitive",
        "form.create_primitive",
        {"shape": "plane", "x_size": 10, "y_size": 10, "symmetry": {"x": True}},
    )
    assert params["symmetry"] == {"x": True, "y": False, "z": False}


def test_create_refusals() -> None:
    text = refused(
        "form_create_primitive",
        {"shape": "box", "x_size": "width", "y_size": 1, "z_size": 1},
        "x_size",
    )
    assert "not parametric" in text
    refused("form_create_primitive", {"shape": "sphere", "radius": 5, "height": 10}, "height")
    refused(
        "form_create_primitive",
        {"shape": "box", "x_size": 1, "y_size": 1, "z_size": 1, "x_faces": 0},
    )
    refused("form_create_primitive", {"shape": "cube"})
    refused("form_create_primitive", {"shape": "torus", "symmetry": "radial"})


# --- 3.3 create_from_tsm, 3.4 get_tsm --------------------------------------------------------


def test_create_from_tsm(tmp_path: Path) -> None:
    params = sent("form_create_from_tsm", "form.create_from_tsm", {"tsm": "TSM", "name": "A"})
    assert params == {"tsm": "TSM", "name": "A"}
    path = tmp_path / "a.tsm"
    path.write_text("x", encoding="utf-8")
    params = sent("form_create_from_tsm", "form.create_from_tsm", {"path": str(path)})
    assert params == {"path": str(path)}
    refused("form_create_from_tsm", {}, "tsm")
    refused("form_create_from_tsm", {"tsm": "a", "path": str(path)}, "tsm")
    refused("form_create_from_tsm", {"path": str(tmp_path / "missing.tsm")}, "path")
    refused("form_create_from_tsm", {"path": "relative.tsm"}, "path")


def test_get_tsm(tmp_path: Path) -> None:
    data = {"body": "ts-1", "tsm": "TSM", "size_bytes": 3}
    assert sent("form_get_tsm", "form.get_tsm", {"body": "ts-1"}, data) == {
        "body": "ts-1",
        "overwrite": False,
    }
    target = tmp_path / "out.tsm"
    files = {"body": "ts-1", "files": [{"path": str(target), "size_bytes": 3}]}
    params = sent("form_get_tsm", "form.get_tsm", {"body": "ts-1", "path": str(target)}, files)
    assert params["path"] == str(target)
    target.write_text("old", encoding="utf-8")
    refused("form_get_tsm", {"body": "ts-1", "path": str(target)}, "path")
    params = sent(
        "form_get_tsm",
        "form.get_tsm",
        {"body": "ts-1", "path": str(target), "overwrite": True},
        files,
    )
    assert params["overwrite"] is True
    refused("form_get_tsm", {"body": "ts-1", "path": str(tmp_path / "a.step")}, "path")


# --- 3.5 inspect -----------------------------------------------------------------------------


def test_inspect() -> None:
    data = {"body": BODY, "vertices": [], "truncated": False}
    assert sent("form_inspect", "form.inspect", {"body": "ts-1"}, data) == {
        "body": "ts-1",
        "limit": 500,
    }
    params = sent(
        "form_inspect", "form.inspect", {"body": "ts-1", "include": ["vertices"], "limit": 9}, data
    )
    assert params == {"body": "ts-1", "include": ["vertices"], "limit": 9}
    refused("form_inspect", {"body": "ts-1", "include": ["normals"]})
    refused("form_inspect", {"body": "ts-1", "limit": 0})


# --- 3.6 move, 3.7 crease, 3.8 insert_edge ---------------------------------------------------


def test_move() -> None:
    params = sent(
        "form_move",
        "form.move",
        {"body": "ts-1", "kind": "face", "near": [[5, 5, 20]], "vector": [0, 0, 5]},
        EDITED,
    )
    assert params == {
        "body": "ts-1",
        "kind": "face",
        "near": [[5, 5, 20]],
        "vector": [0, 0, 5],
        "tolerance": 0.5,
        "units": "mm",
    }
    params = sent(
        "form_move", "form.move", {"body": "ts-1", "kind": "body", "vector": [1, 0, 0]}, EDITED
    )
    assert "near" not in params
    refused("form_move", {"body": "ts-1", "kind": "vertex", "vector": [0, 0, 1]}, "near")
    refused("form_move", {"body": "ts-1", "kind": "face", "near": [[1, 2]], "vector": [0, 0, 1]})
    refused("form_move", {"body": "ts-1", "kind": "edge", "near": [[1, 2, 3]], "vector": [0, 0, 1]})


@pytest.mark.parametrize("creased", [True, False])
def test_crease(creased: bool) -> None:
    params = sent(
        "form_crease",
        "form.crease",
        {"body": "ts-1", "near": [[5, 0, 20]], "creased": creased, "tolerance": 1},
        EDITED,
    )
    assert params == {
        "body": "ts-1",
        "near": [[5, 0, 20]],
        "creased": creased,
        "tolerance": 1,
        "units": "mm",
    }
    refused("form_crease", {"body": "ts-1", "near": []})


def test_insert_edge() -> None:
    params = sent(
        "form_insert_edge",
        "form.insert_edge",
        {"body": "ts-1", "near": [[5, 0, 20]]},
        {**EDITED, "new_edges": 1},
    )
    assert params == {
        "body": "ts-1",
        "near": [[5, 0, 20]],
        "position": 0.5,
        "mode": "simple",
        "both_sides": False,
        "tolerance": 0.5,
        "units": "mm",
    }
    refused("form_insert_edge", {"body": "ts-1", "near": [[5, 0, 20]], "position": 1.5})
    refused("form_insert_edge", {"body": "ts-1", "near": [[5, 0, 20]], "mode": "fast"})


# --- 3.9 convert_to_brep ---------------------------------------------------------------------


def test_convert_to_brep() -> None:
    data = {
        "body": "b-2",
        "name": "Shell",
        "source": "ts-1",
        "base_feature": "bf-1",
        "is_solid": True,
    }
    assert sent("form_convert_to_brep", "form.convert_to_brep", {"body": "ts-1"}, data) == {
        "body": "ts-1",
        "hide_form": True,
    }
    params = sent(
        "form_convert_to_brep",
        "form.convert_to_brep",
        {"body": "ts-1", "hide_form": False, "name": "Shell"},
        data,
    )
    assert params == {"body": "ts-1", "hide_form": False, "name": "Shell"}
    refused("form_convert_to_brep", {})

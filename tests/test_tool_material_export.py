"""``material_*``, ``appearance_*``, ``export_*`` and ``import_file`` tools on ``FakeBridge``
(c12 tasks 5.1-5.11)."""

import asyncio
from pathlib import Path
from typing import Any, get_args

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.exchange import (
    CAD_EXPORT_FORMATS,
    FILE_UNITS,
    IMPORT_FORMATS,
    MESH_EXPORT_FORMATS,
    REFINEMENTS,
)
from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text
from fusion_mcp.server.tools import export, importing

TOOLS = {
    "material_list_libraries",
    "material_list",
    "material_get",
    "material_assign",
    "appearance_list",
    "appearance_assign",
    "appearance_set_color",
    "export_cad",
    "export_mesh",
    "export_sketch_dxf",
    "import_file",
}


class Rejected:
    """Arguments refused by the tool's input schema before the bridge."""

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


def ok(name: str, arguments: dict[str, Any], method: str, answer: Any = None) -> FakeBridge:
    bridge = FakeBridge()
    bridge.respond(method, answer if answer is not None else {"ok": True})
    result, bridge = call(name, arguments, bridge)
    assert not result.is_error, result_text(result) if hasattr(result, "content") else result
    assert bridge.last.method == method
    return bridge


def test_tools_registered_with_agent_docstrings() -> None:
    tools = {t.name: t for t in asyncio.run(create_server(FakeBridge()).list_tools())}
    assert set(tools) >= TOOLS
    for name in TOOLS:
        text = tools[name].description or ""
        assert "Returns" in text and "Example" in text, name
    assert "inspect_physical_properties" in (tools["material_assign"].description or "")
    assert "material_list" in (tools["material_assign"].description or "")
    for name in ("export_cad", "export_mesh"):
        text = tools[name].description or ""
        assert "absolute" in text and "overwrite=true" in text and "does not save" in text


def test_literals_match_protocol_vocabularies() -> None:
    assert get_args(export.CadFormat) == CAD_EXPORT_FORMATS
    assert get_args(export.MeshFormat) == MESH_EXPORT_FORMATS
    assert get_args(export.Refinement) == REFINEMENTS
    assert get_args(export.FileUnits) == FILE_UNITS
    assert set(get_args(importing.ImportFormat)) == set(IMPORT_FORMATS)
    assert get_args(importing.MeshUnits) == FILE_UNITS


# --- 5.1-5.4 materials --------------------------------------------------------------------


def test_material_list_libraries() -> None:
    answer = {"libraries": [{"id": "lib-1", "name": "Fusion Material Library"}]}
    bridge = ok("material_list_libraries", {}, "material.list_libraries", answer)
    assert bridge.last.params == {}


def test_material_list_defaults_and_filters() -> None:
    bridge = ok("material_list", {}, "material.list")
    assert bridge.last.params == {"in_design": False, "limit": 100, "offset": 0}
    bridge = ok(
        "material_list", {"query": "steel", "library": "lib-1", "limit": 10}, "material.list"
    )
    assert bridge.last.params == {
        "in_design": False,
        "limit": 10,
        "offset": 0,
        "library": "lib-1",
        "query": "steel",
    }
    result, bridge = call("material_list", {"limit": 5000})
    assert isinstance(result, Rejected) and bridge.calls == []


def test_material_get() -> None:
    bridge = ok("material_get", {"target": "body-1"}, "material.get")
    assert bridge.last.params == {"target": "body-1"}


def test_material_assign() -> None:
    bridge = ok("material_assign", {"target": "body-1", "material": "mat-steel"}, "material.assign")
    assert bridge.last.params == {"target": "body-1", "material": "mat-steel"}
    bridge = ok(
        "material_assign",
        {"target": "occ-1", "material": "Steel", "library": "lib-1"},
        "material.assign",
    )
    assert bridge.last.params["library"] == "lib-1"


def test_material_assign_passes_bridge_errors() -> None:
    bridge = FakeBridge()
    bridge.respond(
        "material.assign",
        BridgeError(ErrorCode.INVALID_PARAMS, "2 materials are named 'Steel'", {"candidates": []}),
    )
    result, _ = call("material_assign", {"target": "b", "material": "Steel"}, bridge)
    assert result.is_error and "candidates" in result_text(result)


# --- 5.5-5.7 appearances ------------------------------------------------------------------


def test_appearance_list() -> None:
    bridge = ok("appearance_list", {"in_design": True}, "appearance.list")
    assert bridge.last.params == {"in_design": True, "limit": 100, "offset": 0}


def test_appearance_assign() -> None:
    bridge = ok(
        "appearance_assign", {"target": "face-1", "appearance": "app-red"}, "appearance.assign"
    )
    assert bridge.last.params == {"target": "face-1", "appearance": "app-red"}


@pytest.mark.parametrize("color", ["#FF0000", "#FF000080", [0, 128, 255], [0, 0, 0, 10]])
def test_appearance_set_color_valid_goes_to_bridge(color: Any) -> None:
    bridge = ok(
        "appearance_set_color", {"target": "body-1", "color": color}, "appearance.set_color"
    )
    assert bridge.last.params == {"target": "body-1", "color": color}


def test_appearance_set_color_base_and_name() -> None:
    args = {"target": "b", "color": "#00FF00", "base": "app-red", "name": "Green"}
    bridge = ok("appearance_set_color", args, "appearance.set_color")
    assert bridge.last.params == args


@pytest.mark.parametrize("color", ["red", "#FFF", [0, 256, 0], [1, 2]])
def test_appearance_set_color_invalid_never_reaches_bridge(color: Any) -> None:
    result, bridge = call("appearance_set_color", {"target": "body-1", "color": color})
    assert result.is_error and bridge.calls == []
    if not isinstance(result, Rejected):
        assert "INVALID_PARAMS" in result_text(result)


# --- 5.8-5.10 export ----------------------------------------------------------------------


def test_export_cad_params(tmp_path: Path) -> None:
    path = str(tmp_path / "part.step")
    bridge = ok("export_cad", {"path": path}, "export.cad")
    assert bridge.last.params == {"path": path, "overwrite": False}
    assert bridge.last.timeout_s == export.EXPORT_TIMEOUT_S
    args = {"path": str(tmp_path / "p.igs"), "format": "iges", "scope": "occ-1", "overwrite": True}
    assert ok("export_cad", args, "export.cad").last.params == args


def test_export_cad_relative_path_never_reaches_bridge() -> None:
    result, bridge = call("export_cad", {"path": "out/part.step"})
    assert result.is_error and bridge.calls == []
    assert "not_absolute" in result_text(result)


def test_export_cad_existing_file_and_bad_extension(tmp_path: Path) -> None:
    existing = tmp_path / "a.step"
    existing.write_bytes(b"old")
    result, bridge = call("export_cad", {"path": str(existing)})
    assert result.is_error and "exists" in result_text(result) and bridge.calls == []
    result, bridge = call("export_cad", {"path": str(tmp_path / "a.stl"), "format": "step"})
    assert "bad_extension" in result_text(result) and bridge.calls == []
    assert ok("export_cad", {"path": str(existing), "overwrite": True}, "export.cad")


def test_export_mesh_params(tmp_path: Path) -> None:
    path = str(tmp_path / "b.stl")
    bridge = ok(
        "export_mesh", {"path": path, "scope": "body-1", "refinement": "high"}, "export.mesh"
    )
    assert bridge.last.params == {
        "path": path,
        "scope": "body-1",
        "refinement": "high",
        "file_units": "mm",
        "binary": True,
        "units": "mm",
        "overwrite": False,
    }
    args = {"path": str(tmp_path / "c.obj"), "refinement": "custom", "surface_deviation": 0.05}
    assert ok("export_mesh", args, "export.mesh").last.params["surface_deviation"] == 0.05


def test_export_mesh_rejects_before_bridge(tmp_path: Path) -> None:
    result, bridge = call("export_mesh", {"path": str(tmp_path / "a.step")})
    assert result.is_error and bridge.calls == []
    result, bridge = call("export_mesh", {"path": str(tmp_path / "a.stl"), "refinement": "ultra"})
    assert isinstance(result, Rejected) and bridge.calls == []


def test_export_sketch_dxf(tmp_path: Path) -> None:
    path = str(tmp_path / "s.dxf")
    bridge = ok("export_sketch_dxf", {"sketch": "sketch-1", "path": path}, "export.sketch_dxf")
    assert bridge.last.params == {"sketch": "sketch-1", "path": path, "overwrite": False}
    result, bridge = call("export_sketch_dxf", {"sketch": "s", "path": str(tmp_path / "s.svg")})
    assert "bad_extension" in result_text(result) and bridge.calls == []


# --- 5.11 import --------------------------------------------------------------------------


def test_import_file_params(tmp_path: Path) -> None:
    source = tmp_path / "bolt.step"
    source.write_bytes(b"x")
    bridge = ok("import_file", {"path": str(source)}, "import.file")
    assert bridge.last.params == {"path": str(source), "mesh_units": "mm"}
    assert bridge.last.timeout_s == importing.IMPORT_TIMEOUT_S
    mesh = tmp_path / "part.stl"
    mesh.write_bytes(b"x")
    args = {"path": str(mesh), "format": "stl", "target": "comp-1", "mesh_units": "in"}
    assert ok("import_file", args, "import.file").last.params == args


def test_import_missing_file_never_reaches_bridge(tmp_path: Path) -> None:
    result, bridge = call("import_file", {"path": str(tmp_path / "none.step")})
    assert result.is_error and "not_found" in result_text(result) and bridge.calls == []
    result, bridge = call("import_file", {"path": "in/bolt.step"})
    assert "not_absolute" in result_text(result) and bridge.calls == []

"""Smoke tests of the material, appearance, export and import tools on a live Fusion 360
(``pytest -m fusion``, c12 6.1, 6.2).

``test_material_and_color``: a 20 mm cube (sketch + ``feature_extrude``, c07) gets the first
"Steel" material from ``material_list(query="Steel")`` (``material_assign``); its mass from
``inspect_physical_properties`` changes; ``appearance_set_color("#FF0000")`` colours it and
``material_get`` reports both.

``test_export[<ext>]``: the cube is exported to STEP, IGES and F3D (``export_cad``), to STL,
3MF and OBJ (``export_mesh``) and its sketch to DXF (``export_sketch_dxf``) in a temporary
directory, one test per format; every file is non-empty and a repeat without ``overwrite``
fails with ``reason = "exists"``. A format the Fusion license does not allow
(``details.reason = "license_restricted"``, c15 D3) is skipped with that reason.
``test_export_and_import``: the STEP and STL files are imported back (``import_file``) as a
new occurrence and a mesh body.

Every document is closed without saving. Skipped when the bridge is not reachable.
"""

import asyncio
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult

from fusion_mcp.server.app import create_server
from fusion_mcp.server.bridge import BridgeClient
from fusion_mcp.server.testing import result_text

pytestmark = pytest.mark.fusion

NEW_DOCUMENT = """
doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
design = adsk.fusion.Design.cast(app.activeProduct)
design.designType = adsk.fusion.DesignTypes.ParametricDesignType
result = {"document": doc.name}
"""
CLOSE = """
for i in range(app.documents.count):
    d = app.documents.item(i)
    if d.name == params["document"]:
        d.close(False)
        break
"""


def call_raw(name: str, arguments: dict[str, Any]) -> CallToolResult:
    async def go() -> CallToolResult:
        bridge = BridgeClient()
        try:
            result = await create_server(bridge).call_tool(name, arguments)
        finally:
            await bridge.close()
        assert isinstance(result, CallToolResult)
        return result

    return asyncio.run(go())


def call(name: str, arguments: dict[str, Any]) -> Any:
    result = call_raw(name, arguments)
    text = result_text(result)
    assert not result.is_error, text
    return json.loads(text)


def token(value: Any) -> str:
    while isinstance(value, dict):
        value = value["ref"]
    assert isinstance(value, str)
    return value


@pytest.fixture(scope="module")
def bridge() -> None:
    probe = call_raw("fusion_status", {})
    if probe.is_error and result_text(probe).startswith("NOT_CONNECTED"):
        pytest.skip(f"Fusion bridge not reachable: {result_text(probe).splitlines()[0]}")


@pytest.fixture
def document(bridge: None) -> Iterator[dict[str, Any]]:
    built = call("execute_python", {"code": NEW_DOCUMENT, "timeout_s": 60})["result"]
    try:
        yield built
    finally:
        call("execute_python", {"code": CLOSE, "params": {"document": built["document"]}})


def cube() -> tuple[str, str]:
    """A 20 mm cube on XY: ``(sketch ref, body ref)``."""
    sketch = token(call("sketch_create", {"plane": "origin:XY"})["sketch"])
    drawn = call(
        "sketch_draw",
        {
            "sketch": sketch,
            "items": [{"kind": "rectangle", "corner1": [0, 0], "corner2": [20, 20]}],
        },
    )
    (profile,) = drawn["profiles"]
    out = call(
        "feature_extrude",
        {"profiles": [token(profile)], "side1": {"type": "distance", "distance": 20}},
    )
    (body,) = out["created_bodies"]
    return sketch, token(body)


def mass(body: str) -> float:
    (item,) = call("inspect_physical_properties", {"refs": [body]})["items"]
    return float(item["mass_kg"])


def test_material_and_color(document: dict[str, Any]) -> None:
    _, body = cube()
    before = mass(body)
    steels = call("material_list", {"query": "Steel", "limit": 10})
    assert steels["items"], steels
    steel = steels["items"][0]
    assigned = call("material_assign", {"target": body, "material": steel["id"]})
    assert assigned["material"]["name"] == steel["name"]
    assert "previous" in assigned
    assert abs(mass(body) - before) > 1e-9

    colored = call("appearance_set_color", {"target": body, "color": "#FF0000"})
    assert colored["color"] == "#FF0000FF" and colored["created"] is True
    again = call("appearance_set_color", {"target": body, "color": "#FF0000"})
    assert again["created"] is False

    got = call("material_get", {"target": body})
    assert got["material"]["name"] == steel["name"]
    assert got["appearance"]["name"] == colored["appearance"]["name"]


# (file extension, tool, extra arguments); the sketch DXF gets the cube's sketch.
EXPORTS = [
    ("step", "export_cad", {}),
    ("igs", "export_cad", {}),
    ("f3d", "export_cad", {}),
    ("stl", "export_mesh", {"refinement": "high"}),
    ("3mf", "export_mesh", {}),
    ("obj", "export_mesh", {}),
    ("dxf", "export_sketch_dxf", {}),
]


def export(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """``tool`` result; skip when the Fusion license does not allow the format (c15 D3)."""
    result = call_raw(tool, args)
    text = result_text(result)
    if result.is_error and '"license_restricted"' in text:
        pytest.skip(f"license_restricted: {text.splitlines()[0]}")
    assert not result.is_error, text
    out: dict[str, Any] = json.loads(text)
    return out


@pytest.mark.parametrize(("ext", "tool", "extra"), EXPORTS, ids=[e[0] for e in EXPORTS])
def test_export(
    document: dict[str, Any], tmp_path: Path, ext: str, tool: str, extra: dict[str, Any]
) -> None:
    sketch, _ = cube()
    args = {"path": str(tmp_path / f"cube.{ext}"), **extra}
    if tool == "export_sketch_dxf":
        args["sketch"] = sketch
    out = export(tool, args)
    for written in out["files"]:
        assert written["size_bytes"] > 0, (tool, out)
        assert os.path.getsize(written["path"]) == written["size_bytes"]
    repeat = call_raw(tool, args)
    assert repeat.is_error and '"exists"' in result_text(repeat), result_text(repeat)


def test_export_and_import(document: dict[str, Any], tmp_path: Path) -> None:
    cube()
    export("export_cad", {"path": str(tmp_path / "cube.step")})
    export("export_mesh", {"path": str(tmp_path / "cube.stl"), "refinement": "high"})

    step = call("import_file", {"path": str(tmp_path / "cube.step")})
    assert step["format"] == "step"
    assert step["created"]["occurrences"] or step["created"]["bodies"], step
    stl = call("import_file", {"path": str(tmp_path / "cube.stl")})
    assert stl["format"] == "stl" and len(stl["created"]["mesh_bodies"]) == 1, stl

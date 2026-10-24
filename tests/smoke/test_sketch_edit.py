"""Smoke test of the sketch editing tools on a live Fusion 360 (``pytest -m fusion``, c05
10.3): offset, fillet, trim, mirror, circular and rectangular patterns (reporting whether
the parametric API path was used) and projecting the top face of a 40 x 40 x 10 mm box
built through ``execute_python``. The document is closed without saving. Skipped when the
bridge is not reachable.
"""

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest
from mcp.types import CallToolResult

from fusion_mcp.server.app import create_server
from fusion_mcp.server.bridge import BridgeClient
from fusion_mcp.server.testing import result_text

pytestmark = pytest.mark.fusion

BUILD = """
doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
design = adsk.fusion.Design.cast(app.activeProduct)
design.designType = adsk.fusion.DesignTypes.ParametricDesignType
root = design.rootComponent
P = adsk.core.Point3D.create
sk = root.sketches.add(root.xYConstructionPlane)
sk.sketchCurves.sketchLines.addTwoPointRectangle(P(-2, -2, 0), P(2, 2, 0))
box = root.features.extrudeFeatures.addSimple(
    sk.profiles.item(0),
    adsk.core.ValueInput.createByReal(1),
    adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
)
body = box.bodies.item(0)
top = None
for i in range(body.faces.count):
    f = body.faces.item(i)
    ok, normal = f.evaluator.getNormalAtPoint(f.pointOnFace)
    if ok and normal.z > 0.999:
        top = f
result = {"document": doc.name, "body": body.entityToken, "top": top.entityToken}
"""
MUTATING = {
    "sketch_draw",
    "sketch_offset",
    "sketch_fillet",
    "sketch_trim",
    "sketch_mirror",
    "sketch_pattern",
    "sketch_project",
}
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
    out = json.loads(text)
    if name in MUTATING:
        assert "status" in out, out
    return out


def curves(sketch: str) -> list[dict[str, Any]]:
    section = call("sketch_get", {"sketch": sketch, "include": ["curves"]})["curves"]
    items: list[dict[str, Any]] = section if isinstance(section, list) else section["items"]
    return items


def token(value: Any) -> str:
    while isinstance(value, dict):
        value = value["ref"]
    assert isinstance(value, str)
    return value


@pytest.fixture(scope="module")
def part() -> Iterator[dict[str, Any]]:
    probe = call_raw("fusion_status", {})
    if probe.is_error and result_text(probe).startswith("NOT_CONNECTED"):
        pytest.skip(f"Fusion bridge not reachable: {result_text(probe).splitlines()[0]}")
    built = call("execute_python", {"code": BUILD, "timeout_s": 120})["result"]
    try:
        yield built
    finally:
        call("execute_python", {"code": CLOSE, "params": {"document": built["document"]}})


@pytest.fixture(scope="module")
def sketch(part: dict[str, Any]) -> str:
    # Test geometry is drawn away from the box outline (x, y in -20..20 mm).
    created = call("sketch_create", {"plane": "origin:XY", "name": "Edits"})
    return token(created["sketch"])


def draw(sketch: str, items: list[dict[str, Any]]) -> dict[str, str]:
    out = call("sketch_draw", {"sketch": sketch, "items": items, "return_profiles": False})
    return {path: token(ref) for path, ref in out["refs"].items()}


def test_offset(sketch: str) -> None:
    refs = draw(
        sketch, [{"kind": "rectangle", "id": "r", "corner1": [100, 0], "corner2": [140, 30]}]
    )
    out = call(
        "sketch_offset",
        {
            "sketch": sketch,
            "curves": [refs["r.bottom"]],
            "distance": "3 mm",
            "side_point": [120, 15],
        },
    )
    assert len(out["created"]) == 4
    assert isinstance(out["parametric"], bool)
    if out["parametric"]:
        assert out["dimension"]["expression"] == "3 mm"


def test_fillet(sketch: str) -> None:
    refs = draw(
        sketch, [{"kind": "polyline", "id": "L", "points": [[160, 0], [200, 0], [200, 30]]}]
    )
    out = call(
        "sketch_fillet",
        {"sketch": sketch, "curve1": refs["L.e0"], "curve2": refs["L.e1"], "radius": 5},
    )
    assert token(out["arc"])
    assert out["dimension"]["value"] == pytest.approx(5.0, abs=1e-6)
    arc = next(c for c in curves(sketch) if token(c) == token(out["arc"]))
    assert arc["type"] == "arc"
    assert arc["geometry"]["radius"] == pytest.approx(5.0, abs=1e-6)


def test_trim(sketch: str) -> None:
    refs = draw(
        sketch,
        [
            {"kind": "circle", "id": "c", "center": [250, 0], "radius": 10},
            {"kind": "line", "id": "l", "start": [230, 0], "end": [270, 0]},
        ],
    )
    too_far = call_raw("sketch_trim", {"sketch": sketch, "curve": refs["l"], "point": [250, 20]})
    assert too_far.is_error and result_text(too_far).startswith("INVALID_PARAMS")
    out = call("sketch_trim", {"sketch": sketch, "curve": refs["l"], "point": [250, 0]})
    assert out["created"], out
    assert isinstance(out["deleted"], list)
    # Nothing is left of the line inside the circle.
    for curve in curves(sketch):
        if curve["type"] != "line":
            continue
        (x1, y1), (x2, y2) = curve["geometry"]["start"], curve["geometry"]["end"]
        crosses = min(x1, x2) < 250 < max(x1, x2) and abs(y1) < 1e-6 and abs(y2) < 1e-6
        assert not crosses, curve


def test_mirror(sketch: str) -> None:
    refs = draw(
        sketch,
        [
            {"kind": "polyline", "id": "M", "points": [[0, 50], [10, 50], [10, 60]]},
            {
                "kind": "line",
                "id": "axis",
                "start": [20, 40],
                "end": [20, 70],
                "construction": True,
            },
        ],
    )
    out = call(
        "sketch_mirror",
        {"sketch": sketch, "entities": [refs["M.e0"], refs["M.e1"]], "axis": refs["axis"]},
    )
    assert set(out["mapping"]) == {refs["M.e0"], refs["M.e1"]}
    assert len(out["constraints"]) >= 1


def test_circular_pattern(sketch: str) -> None:
    refs = draw(sketch, [{"kind": "circle", "id": "h", "center": [0, 110], "radius": 3}])
    out = call(
        "sketch_pattern",
        {
            "sketch": sketch,
            "entities": [refs["h"]],
            "kind": "circular",
            "center": [0, 100],
            "count": 6,
        },
    )
    assert len(out["instances"]) == 6
    assert isinstance(out["parametric"], bool)


def test_rectangular_pattern(sketch: str) -> None:
    refs = draw(sketch, [{"kind": "circle", "id": "h", "center": [50, 100], "radius": 2}])
    out = call(
        "sketch_pattern",
        {
            "sketch": sketch,
            "entities": [refs["h"]],
            "kind": "rectangular",
            "count_x": 3,
            "spacing_x": 10,
            "count_y": 2,
            "spacing_y": "10 mm",
        },
    )
    assert len(out["instances"]) == 6
    assert isinstance(out["parametric"], bool)


def test_project_box_face(part: dict[str, Any], sketch: str) -> None:
    out = call("sketch_project", {"sketch": sketch, "entities": [part["top"]]})
    created = out["created"][part["top"]]
    assert len(created) == 4
    projected = {token(c): c for c in curves(sketch) if c["reference"]}
    for ref in created:
        assert token(ref) in projected
        assert projected[token(ref)]["length_mm"] == pytest.approx(40.0, abs=1e-6)

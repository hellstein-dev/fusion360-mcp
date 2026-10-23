"""Smoke tests of the inspection tools on a live Fusion 360 (``pytest -m fusion``, c03 6.1).

A new design document gets a 10 x 20 x 30 mm box with a 10 mm hole through its largest
(20 x 30 mm, +X/-X) faces; the document is closed without saving afterwards. Skipped when
the bridge is not reachable.
"""

import asyncio
import base64
import json
import math
import struct
from collections.abc import Iterator
from typing import Any

import pytest
from mcp.types import CallToolResult, ImageContent

from fusion_mcp.server.app import create_server
from fusion_mcp.server.bridge import BridgeClient
from fusion_mcp.server.testing import result_text

pytestmark = pytest.mark.fusion

HOLE_AREA_MM2 = math.pi * 5.0**2
BUILD = """
doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
design = adsk.fusion.Design.cast(app.activeProduct)
design.designType = adsk.fusion.DesignTypes.ParametricDesignType
root = design.rootComponent
P = adsk.core.Point3D.create
sk = root.sketches.add(root.xYConstructionPlane)
sk.sketchCurves.sketchLines.addTwoPointRectangle(P(0, 0, 0), P(1, 2, 0))
extrudes = root.features.extrudeFeatures
box = extrudes.addSimple(
    sk.profiles.item(0),
    adsk.core.ValueInput.createByReal(3),
    adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
)
hole_sketch = root.sketches.add(root.yZConstructionPlane)
centre = hole_sketch.modelToSketchSpace(P(0, 1, 1.5))
hole_sketch.sketchCurves.sketchCircles.addByCenterRadius(centre, 0.5)
cut = extrudes.createInput(
    hole_sketch.profiles.item(0), adsk.fusion.FeatureOperations.CutFeatureOperation
)
cut.setSymmetricExtent(adsk.core.ValueInput.createByReal(4), True)
extrudes.add(cut)
result = {"document": doc.name, "body": box.bodies.item(0).entityToken}
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


def faces(**filters: Any) -> dict[str, Any]:
    out: dict[str, Any] = call("inspect_query", {"kind": "face", **filters})
    return out


def top_face() -> dict[str, Any]:
    found = faces(surface_type="plane", normal=[0, 0, 1])
    assert found["total"] == 1
    item: dict[str, Any] = found["items"][0]
    return item


def test_tree(part: dict[str, Any]) -> None:
    tree = call("inspect_tree", {})
    assert tree["design_type"] == "parametric"
    root = tree["root"]
    assert [b["ref"] for b in root["bodies"]] == [part["body"]]
    assert [f["type"] for f in root["features"]] == ["ExtrudeFeature", "ExtrudeFeature"]
    assert all(f["health"] == "ok" for f in root["features"])
    assert len(root["sketches"]) == 2
    assert root["sketches"][0]["plane"] == "origin:XY"


def test_query_top_largest_and_hole(part: dict[str, Any]) -> None:
    top = top_face()
    assert top["area_mm2"] == pytest.approx(200.0, abs=1e-3)
    assert top["centroid_mm"][2] == pytest.approx(30.0, abs=1e-6)
    largest = faces(sort_by="area", limit=1)
    assert largest["total"] == 7
    assert largest["truncated"] is True
    assert largest["items"][0]["area_mm2"] == pytest.approx(600.0 - HOLE_AREA_MM2, abs=1e-3)
    hole = faces(surface_type="cylinder", radius_min_mm=4.9, radius_max_mm=5.1)
    assert hole["total"] == 1
    assert hole["items"][0]["radius_mm"] == pytest.approx(5.0, abs=1e-6)
    # The hole's two circular edges (2 * pi * 5 = 31.4 mm) are long too: lines only.
    long_edges = call(
        "inspect_query",
        {"kind": "edge", "scope": [part["body"]], "curve_type": "line", "length_min_mm": 25},
    )
    assert long_edges["total"] == 4
    all_long = call("inspect_query", {"kind": "edge", "scope": [part["body"]], "length_min_mm": 25})
    assert all_long["total"] == 6


def test_describe(part: dict[str, Any]) -> None:
    top = top_face()
    out = call("inspect_describe", {"refs": [top["ref"], "origin:xy", part["body"]]})
    assert out["errors"] == []
    face, plane, body = out["items"]
    assert face["type"] == "BRepFace"
    assert face["normal"] == pytest.approx([0, 0, 1], abs=1e-9)
    assert face["edge_count"] == 4
    assert plane["ref"] == "origin:XY"
    assert plane["type"] == "ConstructionPlane"
    assert plane["normal"] == pytest.approx([0, 0, 1], abs=1e-9)
    assert body["face_count"] == 7


def test_bounding_box(part: dict[str, Any]) -> None:
    out = call("inspect_bounding_box", {})
    assert out["union"]["size_mm"] == pytest.approx([10, 20, 30], abs=1e-4)
    assert out["items"][0]["tight"] is True


def test_physical_properties(part: dict[str, Any]) -> None:
    out = call("inspect_physical_properties", {"refs": [part["body"]]})
    item = out["items"][0]
    assert item["volume_mm3"] == pytest.approx(6000.0 - HOLE_AREA_MM2 * 10, rel=1e-4)
    assert item["center_of_mass_mm"] == pytest.approx([5, 10, 15], abs=1e-3)


def test_measure(part: dict[str, Any]) -> None:
    top = top_face()["ref"]
    bottom = faces(surface_type="plane", normal=[0, 0, -1])["items"][0]["ref"]
    side = faces(surface_type="plane", normal=[1, 0, 0])["items"][0]["ref"]
    distance = call("measure_distance", {"a": top, "b": bottom})
    assert distance["distance_mm"] == pytest.approx(30.0, abs=1e-6)
    angle = call("measure_angle", {"a": top, "b": side})
    assert angle["angle_deg"] == pytest.approx(90.0, abs=1e-6)
    from_point = call("measure_distance", {"a": [5, 10, 40], "b": top})
    assert from_point["distance_mm"] == pytest.approx(10.0, abs=1e-6)


def test_viewport_screenshot(part: dict[str, Any]) -> None:
    result = call_raw("viewport_screenshot", {"width": 320, "height": 240})
    assert not result.is_error, result_text(result)
    image = result.content[0]
    assert isinstance(image, ImageContent)
    png = base64.b64decode(image.data)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    assert (width, height) == (320, 240)
    assert json.loads(result_text(result)) == {
        "width": 320,
        "height": 240,
        "orientation": "iso_top_right",
    }

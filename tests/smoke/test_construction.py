"""Smoke tests of the construction tools on a live Fusion 360 (``pytest -m fusion``, c06 5.1).

A new design document gets a 10 x 20 x 30 mm box with a 10 mm hole along X through its
largest (+X/-X) faces, centred at y = 10, z = 15 mm; the document is closed without saving
afterwards. Every created element is checked through ``inspect_describe``. Skipped when the
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


def described(ref: str) -> dict[str, Any]:
    """The full description of ``ref`` from ``inspect_describe``."""
    out = call("inspect_describe", {"refs": [ref], "detail": "full"})
    assert out["errors"] == []
    item: dict[str, Any] = out["items"][0]
    assert item["ref"] == ref
    return item


def query(kind: str, **filters: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = call("inspect_query", {"kind": kind, **filters})["items"]
    return items


def unsigned(vector: list[float]) -> list[float]:
    return [abs(c) for c in vector]


def timeline_count() -> int:
    count: int = call("design_get_settings", {})["timeline_count"]
    return count


def test_plane_offset_from_origin_xy(part: dict[str, Any]) -> None:
    out = call(
        "construction_plane",
        {"method": "offset", "plane": "origin:XY", "distance": 15, "name": "Mid offset"},
    )
    assert out["type"] == "ConstructionPlane"
    assert out["name"] == "Mid offset"
    assert out["health"] == "ok"
    plane = described(out["ref"])
    assert plane["origin_mm"][2] == pytest.approx(15.0, abs=1e-6)
    assert unsigned(plane["normal"]) == pytest.approx([0, 0, 1], abs=1e-9)


def test_midplane_of_top_and_bottom(part: dict[str, Any]) -> None:
    top = query("face", surface_type="plane", normal=[0, 0, 1])[0]["ref"]
    bottom = query("face", surface_type="plane", normal=[0, 0, -1])[0]["ref"]
    out = call("construction_plane", {"method": "midplane", "plane": top, "plane2": bottom})
    plane = described(out["ref"])
    assert plane["origin_mm"][2] == pytest.approx(15.0, abs=1e-6)
    assert unsigned(plane["normal"]) == pytest.approx([0, 0, 1], abs=1e-9)


def test_plane_along_a_straight_edge(part: dict[str, Any]) -> None:
    edges = query(
        "edge", scope=[part["body"]], curve_type="line", direction=[0, 0, 1], length_min_mm=29
    )
    out = call(
        "construction_plane", {"method": "along_path", "path": edges[0]["ref"], "proportion": 0.5}
    )
    plane = described(out["ref"])
    assert plane["origin_mm"][2] == pytest.approx(15.0, abs=1e-6)
    assert unsigned(plane["normal"]) == pytest.approx([0, 0, 1], abs=1e-9)


def test_axis_of_the_hole(part: dict[str, Any]) -> None:
    hole = query("face", surface_type="cylinder", radius_min_mm=4.9, radius_max_mm=5.1)
    out = call("construction_axis", {"method": "cylinder", "face": hole[0]["ref"]})
    axis = described(out["ref"])
    assert axis["type"] == "ConstructionAxis"
    assert unsigned(axis["direction"]) == pytest.approx([1, 0, 0], abs=1e-9)
    assert axis["origin_mm"][1:] == pytest.approx([10.0, 15.0], abs=1e-6)


def test_axis_of_two_origin_planes(part: dict[str, Any]) -> None:
    out = call(
        "construction_axis", {"method": "two_planes", "plane": "origin:XZ", "plane2": "origin:YZ"}
    )
    axis = described(out["ref"])
    assert unsigned(axis["direction"]) == pytest.approx([0, 0, 1], abs=1e-9)


def test_point_at_the_hole_edge_center(part: dict[str, Any]) -> None:
    circles = query("edge", scope=[part["body"]], curve_type="circle")
    assert len(circles) == 2
    out = call("construction_point", {"method": "center", "entity": circles[0]["ref"]})
    point = described(out["ref"])["point_mm"]
    assert point[0] in (pytest.approx(0.0, abs=1e-6), pytest.approx(10.0, abs=1e-6))
    assert point[1:] == pytest.approx([10.0, 15.0], abs=1e-6)


def test_point_of_three_origin_planes(part: dict[str, Any]) -> None:
    out = call(
        "construction_point",
        {
            "method": "three_planes",
            "plane": "origin:XY",
            "plane2": "origin:XZ",
            "plane3": "origin:YZ",
        },
    )
    assert described(out["ref"])["point_mm"] == pytest.approx([0, 0, 0], abs=1e-9)


def test_parallel_planes_leave_the_timeline_unchanged(part: dict[str, Any]) -> None:
    offset = call("construction_plane", {"method": "offset", "plane": "origin:XY", "distance": 5})
    before = timeline_count()
    result = call_raw(
        "construction_axis",
        {"method": "two_planes", "plane": "origin:XY", "plane2": offset["ref"]},
    )
    assert result.is_error, result_text(result)
    assert result_text(result).startswith("FUSION_API_ERROR")
    assert timeline_count() == before


def test_origin_ref_round_trip(part: dict[str, Any]) -> None:
    assert described("origin:XY")["type"] == "ConstructionPlane"
    out = call("inspect_describe", {"refs": ["origin:xy"]})
    assert out["items"][0]["ref"] == "origin:XY"

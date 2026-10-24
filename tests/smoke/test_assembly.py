"""Smoke test of the assembly tools on a live Fusion 360 (``pytest -m fusion``, c10 7.2, design
D9).

Components ``Box`` (100 x 60 x 40 mm body) and ``Lid`` (100 x 60 x 5 mm at z = 40) are built
with ``execute_python`` inside the components; Box is grounded. The back top edge of the box
and the back bottom edge of the lid (y = 60, z = 40, along X) are found with ``inspect_query``
scoped to each occurrence (``<token>@Box:1`` / ``<token>@Lid:1``) and joined by a revolute
hinge about the box edge. Driving it to 90 degrees must swing the lid up (Z size 60 mm, bottom
at z = 40); if the edge direction makes +90 swing it into the box, -90 is used instead.
``assembly_list_joints`` reports the angle, the lid only touches the box (no interference),
and a second occurrence of Box in the same place overlaps the first by the whole box volume.

The document is closed without saving. Skipped when the bridge is not reachable.
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
# A box params["size_mm"] = [x, y, z] from the component origin, starting at
# params["z0_mm"], inside params["component"].
BUILD_BOX = """
comp = design.allComponents.itemByName(params["component"])
x, y, h = [v / 10 for v in params["size_mm"]]
z0 = params.get("z0_mm", 0) / 10
sketch = comp.sketches.add(comp.xYConstructionPlane)
sketch.sketchCurves.sketchLines.addTwoPointRectangle(
    adsk.core.Point3D.create(0, 0, 0), adsk.core.Point3D.create(x, y, 0)
)
extrudes = comp.features.extrudeFeatures
ext_input = extrudes.createInput(
    sketch.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation
)
if z0:
    ext_input.startExtent = adsk.fusion.OffsetStartDefinition.create(
        adsk.core.ValueInput.createByReal(z0)
    )
ext_input.setDistanceExtent(False, adsk.core.ValueInput.createByReal(h))
extrudes.add(ext_input)
result = {"bodies": comp.bRepBodies.count}
"""
BOX_MM = (100, 60, 40)
LID_MM = (100, 60, 5)
# Around the hinge line y = 60, z = 40 (both edges lie on it).
HINGE_BOX = {"min_mm": [-1, 59, 39], "max_mm": [101, 61, 41]}
ANGLE_TOLERANCE_DEG = 0.01
LENGTH_TOLERANCE_MM = 0.01
VOLUME_TOLERANCE = 0.01  # relative


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


def build_box(component: str, size_mm: tuple[int, int, int], z0_mm: float = 0) -> None:
    params = {"component": component, "size_mm": list(size_mm), "z0_mm": z0_mm}
    out = call("execute_python", {"code": BUILD_BOX, "params": params, "timeout_s": 60})
    assert out["result"]["bodies"] == 1, out


def hinge_edge(path: str) -> str:
    found = call(
        "inspect_query",
        {
            "kind": "edge",
            "scope": [f"occurrence:{path}"],
            "direction": [1, 0, 0],
            "within_box": HINGE_BOX,
        },
    )
    (edge,) = found["items"]
    ref: str = edge["ref"]
    assert ref.endswith(f"@{path}"), ref
    assert edge["length_mm"] == pytest.approx(100, abs=LENGTH_TOLERANCE_MM)
    return ref


def lid_box() -> dict[str, Any]:
    out = call("inspect_bounding_box", {"refs": ["occurrence:Lid:1"]})
    (item,) = out["items"]
    return dict(item)


def interference(items: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = call("assembly_analyze_interference", {"items": items})
    return out


def test_box_lid_hinge(document: dict[str, Any]) -> None:
    box = call("assembly_new_component", {"name": "Box"})
    assert box["component"] == "component:Box"
    assert box["occurrence"]["ref"] == "occurrence:Box:1"
    assert box["occurrence"]["transform"]["translation_mm"] == [0, 0, 0]
    lid = call("assembly_new_component", {"name": "Lid"})
    assert lid["occurrence"]["ref"] == "occurrence:Lid:1"
    build_box("Box", BOX_MM)
    build_box("Lid", LID_MM, z0_mm=40)

    grounded = call(
        "assembly_set_occurrence_state", {"occurrence": "occurrence:Box:1", "grounded": True}
    )
    assert grounded["occurrence"]["is_grounded"] is True

    box_edge = hinge_edge("Box:1")
    lid_edge = hinge_edge("Lid:1")
    created = call(
        "assembly_create_joint",
        {
            "type": "revolute",
            "geometry_one": {"kind": "curve", "entity": lid_edge},
            "geometry_two": {"kind": "curve", "entity": box_edge},
            "motion": {"axis": box_edge},
            "name": "Hinge",
        },
    )["joint"]
    assert created["type"] == "revolute"
    assert created["values"]["rotation_deg"] == pytest.approx(0, abs=ANGLE_TOLERANCE_DEG)
    assert {created["occurrence_one"], created["occurrence_two"]} == {
        "occurrence:Lid:1",
        "occurrence:Box:1",
    }
    joint = created["ref"]

    angle = 90
    driven = call("assembly_drive_joint", {"joint": joint, "values": {"rotation": angle}})
    assert driven["clamped"] == []
    if lid_box()["min_mm"][2] < 40 - LENGTH_TOLERANCE_MM:
        # The edge points the other way: +90 swung the lid into the box.
        angle = -90
        call("assembly_drive_joint", {"joint": joint, "values": {"rotation": angle}})
    opened = lid_box()
    assert opened["min_mm"][2] == pytest.approx(40, abs=LENGTH_TOLERANCE_MM)
    assert opened["size_mm"][2] == pytest.approx(60, abs=LENGTH_TOLERANCE_MM)

    listed = call("assembly_list_joints", {})
    (hinge,) = [j for j in listed["joints"] if j["name"] == "Hinge"]
    assert hinge["values"]["rotation_deg"] == pytest.approx(angle, abs=ANGLE_TOLERANCE_DEG)
    assert hinge["health"] in ("ok", "unknown")

    assert interference(["occurrence:Box:1", "occurrence:Lid:1"])["count"] == 0

    copy = call("assembly_add_occurrence", {"component": "component:Box"})["occurrence"]
    assert copy["ref"] == "occurrence:Box:2"
    overlap = interference(["occurrence:Box:1", "occurrence:Box:2"])
    assert overlap["count"] >= 1
    volume = sum(item["volume_mm3"] for item in overlap["interferences"])
    assert volume == pytest.approx(BOX_MM[0] * BOX_MM[1] * BOX_MM[2], rel=VOLUME_TOLERANCE)

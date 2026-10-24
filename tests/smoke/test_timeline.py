"""Smoke tests of the timeline tools on a live Fusion 360 (``pytest -m fusion``, c09 8.1-8.2).

``test_edit_suppress_roll``: ``execute_python`` builds a sketch with a 40 x 30 mm rectangle,
a 20 mm extrude and a 2 mm fillet on a vertical edge (independent of the c05/c07 tool
names). Then ``timeline_list`` -> ``timeline_edit_extrude(distance=30)`` -> the bounding box
is 30 mm high -> the fillet is suppressed -> the marker goes before the extrude (extrude and
fillet rolled back) -> back to the end -> ``timeline_health`` is ok.

``test_rollback_with_marker_not_at_end`` (design D3): with the marker before the extrude, an
``execute_python`` that adds a construction plane and then raises leaves ``count`` and
``marker_position`` as they were.

Every document is closed without saving. Skipped when the bridge is not reachable.
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
BUILD = """
design = adsk.fusion.Design.cast(app.activeProduct)
root = design.rootComponent
sketch = root.sketches.add(root.xYConstructionPlane)
sketch.sketchCurves.sketchLines.addTwoPointRectangle(
    adsk.core.Point3D.create(0, 0, 0), adsk.core.Point3D.create(4, 3, 0)
)
extrude = root.features.extrudeFeatures.addSimple(
    sketch.profiles.item(0),
    adsk.core.ValueInput.createByString("20 mm"),
    adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
)
body = extrude.bodies.item(0)
edges = adsk.core.ObjectCollection.create()
for i in range(body.edges.count):
    edge = body.edges.item(i)
    if abs(edge.length - 2.0) < 1e-6:
        edges.add(edge)
        break
fillets = root.features.filletFeatures
fillet_input = fillets.createInput()
radius = adsk.core.ValueInput.createByString("2 mm")
try:
    fillet_input.edgeSetInputs.addConstantRadiusEdgeSet(edges, radius, True)
except Exception:
    fillet_input.addConstantRadiusEdgeSet(edges, radius, True)
fillet = fillets.add(fillet_input)
result = {
    "sketch": sketch.entityToken,
    "extrude": extrude.entityToken,
    "fillet": fillet.entityToken,
}
"""
ADD_PLANE_AND_FAIL = """
root = adsk.fusion.Design.cast(app.activeProduct).rootComponent
planes = root.constructionPlanes
plane_input = planes.createInput()
plane_input.setByOffset(root.xYConstructionPlane, adsk.core.ValueInput.createByString("5 mm"))
planes.add(plane_input)
raise RuntimeError("deliberate failure after adding a plane")
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
def bridge() -> None:
    probe = call_raw("fusion_status", {})
    if probe.is_error and result_text(probe).startswith("NOT_CONNECTED"):
        pytest.skip(f"Fusion bridge not reachable: {result_text(probe).splitlines()[0]}")


@pytest.fixture
def part(bridge: None) -> Iterator[dict[str, Any]]:
    """New document with Sketch1 -> Extrude (20 mm) -> Fillet (2 mm); closed unsaved."""
    built = call("execute_python", {"code": NEW_DOCUMENT, "timeout_s": 60})["result"]
    try:
        refs = call("execute_python", {"code": BUILD, "timeout_s": 60})["result"]
        yield refs
    finally:
        call("execute_python", {"code": CLOSE, "params": {"document": built["document"]}})


def by_ref(listing: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["ref"]: item for item in listing["items"]}


def test_edit_suppress_roll(part: dict[str, Any]) -> None:
    listing = call("timeline_list", {})
    assert listing["count"] == 3
    assert listing["marker_position"] == 3
    assert [i["ref"] for i in listing["items"]] == [part["sketch"], part["extrude"], part["fillet"]]
    assert all(i["health"] == "ok" for i in listing["items"]), listing

    edited = call("timeline_edit_extrude", {"feature": part["extrude"], "distance": 30})
    (change,) = edited["changed"]
    assert change["new_expression"] == "30 mm"
    assert change["value"] == pytest.approx(30.0)
    assert edited["feature"]["health"] == "ok"
    size = call("inspect_bounding_box", {})["union"]["size_mm"]
    assert size == pytest.approx([40, 30, 30], abs=1e-3)

    suppressed = call("timeline_set_suppressed", {"items": [part["fillet"]]})
    assert suppressed["items"][0]["is_suppressed"] is True

    rolled = call("timeline_roll_to", {"position": "before", "item": part["extrude"]})
    assert rolled["marker_position"] == 1
    items = by_ref(call("timeline_list", {}))
    assert items[part["extrude"]]["is_rolled_back"] is True
    assert items[part["fillet"]]["is_rolled_back"] is True
    assert items[part["sketch"]]["is_rolled_back"] is False

    assert call("timeline_roll_to", {"position": "end"})["marker_position"] == 3
    health = call("timeline_health", {})
    assert health["ok"] is True, health
    assert health["suppressed_count"] == 1


def test_rollback_with_marker_not_at_end(part: dict[str, Any]) -> None:
    call("timeline_roll_to", {"position": "before", "item": part["extrude"]})
    before = call("timeline_list", {})
    assert before["marker_position"] == 1

    failed = call_raw("execute_python", {"code": ADD_PLANE_AND_FAIL, "timeout_s": 60})
    assert failed.is_error, result_text(failed)

    after = call("timeline_list", {})
    assert (after["count"], after["marker_position"]) == (
        before["count"],
        before["marker_position"],
    )
    assert [i["ref"] for i in after["items"]] == [i["ref"] for i in before["items"]]
    call("timeline_roll_to", {"position": "end"})

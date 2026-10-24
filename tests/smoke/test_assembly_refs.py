"""Smoke test of assembly-context refs on a live Fusion 360 (``pytest -m fusion``, c10 7.1).

A component ``Part`` is inserted 50 mm along X and gets a 20 mm cube (built with
``execute_python`` inside the component). ``inspect_query`` scoped to ``occurrence:Part:1``
returns the top face as ``<token>@Part:1`` in assembly coordinates, and ``inspect_describe``
of that ref gives back the same face (round trip). The same is checked for a cube in a
component nested inside ``Part`` (``<token>@Part:1+Inner:1``).

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
# A box params["size_mm"] = [x, y, z] from the component origin, inside params["component"].
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
TOLERANCE_MM = 1e-3


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


def build_box(component: str, size_mm: list[float]) -> None:
    params = {"component": component, "size_mm": size_mm}
    out = call("execute_python", {"code": BUILD_BOX, "params": params, "timeout_s": 60})
    assert out["result"]["bodies"] == 1, out


def top_face_round_trip(path: str, centroid_mm: list[float]) -> None:
    found = call(
        "inspect_query",
        {"kind": "face", "scope": [f"occurrence:{path}"], "normal": [0, 0, 1]},
    )
    (face,) = found["items"]
    ref = face["ref"]
    token, _, context = ref.partition("@")
    assert token and context == path, ref
    assert face["centroid_mm"] == pytest.approx(centroid_mm, abs=TOLERANCE_MM)

    described = call("inspect_describe", {"refs": [ref]})
    assert described["errors"] == []
    (item,) = described["items"]
    assert item["ref"] == ref
    assert item["centroid_mm"] == pytest.approx(centroid_mm, abs=TOLERANCE_MM)
    assert item["area_mm2"] == pytest.approx(face["area_mm2"], abs=TOLERANCE_MM)


def test_proxy_refs_round_trip(document: dict[str, Any]) -> None:
    part = call("assembly_new_component", {"name": "Part", "translate_mm": [50, 0, 0]})
    assert part["occurrence"]["ref"] == "occurrence:Part:1"
    build_box("Part", [20, 20, 20])
    top_face_round_trip("Part:1", [60, 10, 20])

    inner = call(
        "assembly_new_component",
        {"name": "Inner", "parent": "occurrence:Part:1", "translate_mm": [0, 30, 0]},
    )
    assert inner["occurrence"]["ref"] == "occurrence:Part:1+Inner:1"
    build_box("Inner", [10, 10, 10])
    top_face_round_trip("Part:1+Inner:1", [55, 35, 10])

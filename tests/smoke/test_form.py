"""Smoke test of the form (T-spline) tools on a live Fusion 360 (``pytest -m fusion``, c14 4.1).

``test_box_form_round_trip``: ``form_create_primitive`` box 40 x 20 x 10 mm -> ``form_list``
(the body has a B-Rep, Fusion is back in the Design workspace, design R1) ->
``form_inspect`` -> ``form_move`` of a top face by 5 mm at the position from the inspection ->
the B-Rep bounding box (c03 ``inspect_bounding_box``) grows in Z (the limit surface follows the
control points only partly, so by more than 0 and at most 5 mm) -> ``form_crease`` of a top
edge -> ``form_insert_edge`` (step skipped when the preview API is missing) -> ``form_get_tsm``
-> ``form_create_from_tsm`` (same vertex count) -> ``form_convert_to_brep`` (a solid in a base
feature, the source hidden).

``test_sphere_and_torus``: both primitives get a B-Rep body.

After every step the timeline has no feature in error. Every document is closed without
saving. Skipped when the bridge is not reachable.
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
WORKSPACE = """
result = {"workspace": app.userInterface.activeWorkspace.id}
"""
TOLERANCE = 1e-3  # mm


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


# --- helpers ---------------------------------------------------------------------------------


def timeline_ok() -> None:
    health = call("timeline_health", {})
    assert health["ok"] is True, health


def size_z(brep: str) -> float:
    size: list[float] = call("inspect_bounding_box", {"refs": [brep]})["union"]["size_mm"]
    return size[2]


def listed_body(ref: str) -> dict[str, Any]:
    for form in call("form_list", {})["forms"]:
        for body in form["bodies"]:
            if body["ref"] == ref:
                return dict(body)
    raise AssertionError(f"T-spline body {ref} not in form_list")


# --- tests -----------------------------------------------------------------------------------


def test_box_form_round_trip(document: dict[str, Any]) -> None:
    created = call(
        "form_create_primitive", {"shape": "box", "x_size": 40, "y_size": 20, "z_size": 10}
    )
    assert created["created_form"] is True
    body = created["body"]["ref"]
    assert created["body"]["brep_body"] is not None
    timeline_ok()

    listed = listed_body(body)
    assert listed["brep_body"] is not None
    workspace = call("execute_python", {"code": WORKSPACE})["result"]["workspace"]
    assert workspace == "FusionSolidEnvironment"
    assert call("inspect_tree", {}), "inspect_tree must see the design after the form"

    mesh = call("form_inspect", {"body": body})
    assert mesh["truncated"] is False and mesh["faces"]
    top_z = max(f["center"][2] for f in mesh["faces"])
    top = next(f for f in mesh["faces"] if abs(f["center"][2] - top_z) < TOLERANCE)

    before = size_z(listed["brep_body"])
    moved = call(
        "form_move",
        {"body": body, "kind": "face", "near": [top["center"]], "vector": [0, 0, 5]},
    )
    assert moved["changed"] == 1 and moved["body"]["brep_body"] is not None
    timeline_ok()
    growth = size_z(moved["body"]["brep_body"]) - before
    assert 0 < growth <= 5 + TOLERANCE, growth

    mesh = call("form_inspect", {"body": body, "include": ["edges"]})
    top_edge_z = max(e["midpoint"][2] for e in mesh["edges"])
    edge = next(e for e in mesh["edges"] if abs(e["midpoint"][2] - top_edge_z) < TOLERANCE)
    creased = call("form_crease", {"body": body, "near": [edge["midpoint"]]})
    assert creased["changed"] == 1
    timeline_ok()

    inserted = call_raw("form_insert_edge", {"body": body, "near": [edge["midpoint"]]})
    if inserted.is_error:
        assert "api_unavailable" in result_text(inserted), result_text(inserted)
    else:
        assert json.loads(result_text(inserted))["new_edges"] >= 1
        timeline_ok()

    tsm = call("form_get_tsm", {"body": body})
    assert tsm["size_bytes"] > 0 and tsm["tsm"]
    copy = call("form_create_from_tsm", {"tsm": tsm["tsm"], "name": "FromTSM"})
    assert copy["body"]["vertex_count"] == listed_body(body)["vertex_count"]
    assert copy["body"]["brep_body"] is not None
    timeline_ok()

    converted = call("form_convert_to_brep", {"body": body, "name": "Solid"})
    assert converted["is_solid"] is True and converted["base_feature"] is not None
    assert listed_body(body)["is_visible"] is False
    timeline_ok()


def test_sphere_and_torus(document: dict[str, Any]) -> None:
    sphere = call("form_create_primitive", {"shape": "sphere", "radius": 15})
    assert sphere["body"]["brep_body"] is not None
    torus = call(
        "form_create_primitive",
        {"shape": "torus", "major_radius": 30, "minor_radius": 8, "origin": [100, 0, 0]},
    )
    assert torus["body"]["brep_body"] is not None
    timeline_ok()

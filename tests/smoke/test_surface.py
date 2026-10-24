"""Smoke tests of the surface tools on a live Fusion 360 (``pytest -m fusion``, c11 5.1).

``test_open_sheet_offset_extend``: an open sketch line is extruded as a surface
(``feature_extrude(is_solid=false)``), offset by 5 mm (``surface_offset``) and one free edge of
the sheet is extended by 5 mm (``surface_extend``).

``test_patch_circle``: a sketch circle is filled with ``surface_patch``.

``test_trim_by_plane``: a sheet crossing ``origin:YZ`` is cut into two cells
(``surface_trim_cells``), the cell at x < 0 is removed by a point (``surface_trim``) and the
model stays editable (the trim preview leaves no transaction open, design R1).

``test_stitch_cube_unstitch_ruled``: the four walls of a 20 mm cube (a surface extrude of a
square) plus a bottom and a top patch are stitched into a solid (``is_solid = true``); one face
is unstitched again and a ruled surface is grown from an edge.

After every step the timeline has no feature in error (``timeline_health``, c09). Every
document is closed without saving. Skipped when the bridge is not reachable.
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


# --- helpers ---------------------------------------------------------------------------------


def timeline_ok() -> None:
    health = call("timeline_health", {})
    assert health["ok"] is True, health


def step(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a feature tool, check its health and the whole timeline."""
    out: dict[str, Any] = call(name, arguments)
    assert out["health"]["state"] == "ok", out["health"]
    timeline_ok()
    return out


def draw(plane: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    sketch = token(call("sketch_create", {"plane": plane})["sketch"])
    drawn: dict[str, Any] = call("sketch_draw", {"sketch": sketch, "items": items})
    return drawn


def only(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    assert len(bodies) == 1, bodies
    return bodies[0]


def faces_of(body: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = call("inspect_query", {"kind": "face", "scope": [body]})["items"]
    return items


def edges_of(body: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = call("inspect_query", {"kind": "edge", "scope": [body]})["items"]
    return items


def sheet(start: list[float], end: list[float], height: float) -> str:
    """Surface extrude of an open line on XY: a vertical sheet ``height`` mm tall."""
    drawn = draw("origin:XY", [{"kind": "line", "id": "l", "start": start, "end": end}])
    out = step(
        "feature_extrude",
        {
            "profiles": [token(drawn["refs"]["l"])],
            "side1": {"type": "distance", "distance": height},
            "is_solid": False,
        },
    )
    body = only(out["created_bodies"])
    assert body["is_solid"] is False
    return token(body)


# --- tests -----------------------------------------------------------------------------------


def test_open_sheet_offset_extend(document: dict[str, Any]) -> None:
    wall = sheet([0, 0], [40, 0], 20)
    (face,) = faces_of(wall)
    offset = step("surface_offset", {"faces": [token(face)], "distance": 5})
    assert only(offset["created_bodies"])["is_solid"] is False

    top = [e for e in edges_of(wall) if abs(e["mid_mm"][2] - 20) < TOLERANCE]
    assert len(top) == 1, top
    extended = step("surface_extend", {"edges": [token(top[0])], "distance": 5})
    assert extended["modified_bodies"] or extended["created_bodies"]


def test_patch_circle(document: dict[str, Any]) -> None:
    drawn = draw("origin:XY", [{"kind": "circle", "center": [0, 0], "radius": 10}])
    profile = token(only(drawn["profiles"])["ref"])
    out = step("surface_patch", {"boundary": [profile]})
    assert only(out["created_bodies"])["is_solid"] is False


def test_trim_by_plane(document: dict[str, Any]) -> None:
    sheet([-20, 0], [20, 0], 30)
    preview = call("surface_trim_cells", {"trim_tool": "origin:YZ"})
    assert preview["count"] == 2, preview
    xs = sorted(cell["center"][0] for cell in preview["cells"])
    assert xs[0] < 0 < xs[1]
    timeline_ok()

    out = step("surface_trim", {"trim_tool": "origin:YZ", "remove_points": [[-10, 0, 15]]})
    assert out["removed_cells"] == 1
    # The preview left no open transaction: a new feature still works.
    sheet([0, 10], [20, 10], 10)


def test_stitch_cube_unstitch_ruled(document: dict[str, Any]) -> None:
    square = {"kind": "rectangle", "mode": "center", "center": "origin", "corner": [10, 10]}
    drawn = draw("origin:XY", [square])
    profile = token(only(drawn["profiles"])["ref"])
    walls_out = step(
        "feature_extrude",
        {"profiles": [profile], "side1": {"type": "distance", "distance": 20}, "is_solid": False},
    )
    walls = token(only(walls_out["created_bodies"]))
    assert len(faces_of(walls)) == 4

    bottom = step("surface_patch", {"boundary": [profile]})
    top_edges = [e for e in edges_of(walls) if abs(e["mid_mm"][2] - 20) < TOLERANCE]
    assert len(top_edges) == 4, top_edges
    top = step("surface_patch", {"boundary": [token(e) for e in top_edges]})

    stitched = step(
        "surface_stitch",
        {
            "surfaces": [
                walls,
                token(only(bottom["created_bodies"])),
                token(only(top["created_bodies"])),
            ]
        },
    )
    assert stitched["is_solid"] is True, stitched
    cube = stitched["created_bodies"] or stitched["modified_bodies"]
    cube_ref = token(only(cube))
    assert len(faces_of(cube_ref)) == 6

    edge = next(e for e in edges_of(cube_ref) if abs(e["mid_mm"][2] - 20) < TOLERANCE)
    ruled = step("surface_ruled", {"edges": [token(edge)], "distance": 10})
    assert only(ruled["created_bodies"])["is_solid"] is False

    found = call("inspect_query", {"kind": "face", "scope": [cube_ref], "normal": [0, 0, 1]})
    top_face = only(found["items"])
    unstitched = step("surface_unstitch", {"entities": [token(top_face)], "chain": False})
    assert unstitched["created_bodies"] or unstitched["modified_bodies"]

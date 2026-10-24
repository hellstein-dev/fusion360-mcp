"""Smoke tests of the solid feature tools on a live Fusion 360 (``pytest -m fusion``, c07
13.1-13.2).

The main scenario builds an open housing: an 80 x 50 mm rectangle on ``origin:XY``
(``sketch_draw``, profile from its ``profiles``) is extruded 20 mm, its four vertical edges
(``inspect_query``) get r5 fillets, the bottom face is removed by a 3 mm shell, four
counterbore holes go through the top through sketch points on the top face, and one more
hole is tapped M5x0.8 in the same call. Every feature must be healthy and the design must
keep one body.

The other tests each make a simple body in their own document and run one of revolve,
sweep, loft, rib, web, chamfer, draft and offset faces on it. Rib / web on a Fusion API
without them are skipped with the error text. Every document is closed without saving.
Skipped when the bridge is not reachable.
"""

import asyncio
import json
import math
from collections.abc import Iterator, Sequence
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
    """The ref string of a ``{"ref", "type", "name"}`` object (or the string itself)."""
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
    """A fresh parametric design document per test, closed without saving."""
    built = call("execute_python", {"code": NEW_DOCUMENT, "timeout_s": 60})["result"]
    try:
        yield built
    finally:
        call("execute_python", {"code": CLOSE, "params": {"document": built["document"]}})


# --- helpers ---------------------------------------------------------------------------------


def healthy(result: dict[str, Any]) -> dict[str, Any]:
    """``result`` after checking its feature is healthy."""
    assert result["health"]["state"] == "ok", result["health"]
    assert token(result["feature"])
    return result


def one_body() -> str:
    """The ref of the only body of the design."""
    bodies = call("inspect_query", {"kind": "body"})
    assert bodies["total"] == 1, bodies
    return token(bodies["items"][0])


def faces(**filters: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = call("inspect_query", {"kind": "face", **filters})["items"]
    return items


def edges(**filters: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = call("inspect_query", {"kind": "edge", **filters})["items"]
    return items


def face(normal: Sequence[float]) -> str:
    """The only planar face facing ``normal``."""
    found = faces(surface_type="plane", normal=normal)
    assert len(found) == 1, found
    return token(found[0])


def highest_face(normal: Sequence[float] = (0, 0, 1)) -> str:
    """The planar face facing ``normal`` that lies highest along it (the top of a housing
    whose counterbore floors also face +Z)."""
    found = faces(surface_type="plane", normal=list(normal))
    assert found, "no planar face facing the normal"

    def height(item: dict[str, Any]) -> float:
        return sum(c * n for c, n in zip(item["centroid_mm"], normal, strict=True))

    top = max(height(item) for item in found)
    highest = [item for item in found if abs(height(item) - top) < 1e-6]
    assert len(highest) == 1, highest
    return token(highest[0])


def sketch_on(plane: str) -> str:
    return token(call("sketch_create", {"plane": plane})["sketch"])


def draw(sketch: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    drawn: dict[str, Any] = call("sketch_draw", {"sketch": sketch, "items": items})
    return drawn


def only_profile(drawn: dict[str, Any]) -> str:
    profiles = drawn["profiles"]
    assert len(profiles) == 1, profiles
    return token(profiles[0]["ref"])


def volume(body: str) -> float:
    out = call("inspect_physical_properties", {"refs": [body]})
    value: float = out["items"][0]["volume_mm3"]
    return value


def box(width: float, depth: float, height: float) -> dict[str, Any]:
    """A ``width`` x ``depth`` box centred on the origin in XY, z from 0 to ``height``."""
    sketch = sketch_on("origin:XY")
    drawn = draw(
        sketch,
        [
            {
                "kind": "rectangle",
                "mode": "center",
                "id": "r",
                "center": "origin",
                "corner": [width / 2, depth / 2],
            }
        ],
    )
    out = healthy(
        call(
            "feature_extrude",
            {
                "profiles": [only_profile(drawn)],
                "side1": {"type": "distance", "distance": height},
            },
        )
    )
    assert len(out["created_bodies"]) == 1
    return out


def bracket() -> None:
    """An L bracket (5 mm walls, 40 mm legs) drawn on ``origin:XZ`` and extruded 30 mm
    symmetric, so the XZ plane cuts it in the middle."""
    sketch = sketch_on("origin:XZ")
    corners = [[0, 0, 0], [40, 0, 0], [40, 0, 5], [5, 0, 5], [5, 0, 40], [0, 0, 40]]
    drawn = draw(
        sketch,
        [{"kind": "polyline", "points": [{"model": c} for c in corners], "closed": True}],
    )
    healthy(
        call(
            "feature_extrude",
            {
                "profiles": [only_profile(drawn)],
                "mode": "symmetric",
                "side1": {"type": "distance", "distance": 30},
                "symmetric_full_length": True,
            },
        )
    )


def thin_wall_or_skip(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """``tool`` result; skip when this Fusion API has no such feature."""
    result = call_raw(tool, arguments)
    text = result_text(result)
    if result.is_error and "not available" in text:
        pytest.skip(text.splitlines()[0])
    assert not result.is_error, text
    out: dict[str, Any] = json.loads(text)
    return out


# --- 13.1: housing ---------------------------------------------------------------------------


def test_housing_extrude_fillet_shell_holes_thread(document: dict[str, Any]) -> None:
    extruded = box(80, 50, 20)
    assert one_body() == token(extruded["created_bodies"][0])

    vertical = edges(curve_type="line", direction=[0, 0, 1])
    assert len(vertical) == 4, vertical
    filleted = healthy(call("feature_fillet", {"edges": [token(e) for e in vertical], "radius": 5}))
    assert len(filleted["modified_bodies"]) == 1
    one_body()

    shelled = healthy(call("feature_shell", {"faces": [face([0, 0, -1])], "inside_thickness": 3}))
    assert len(shelled["modified_bodies"]) == 1
    one_body()

    top = face([0, 0, 1])
    points = {"p1": [-30, -15], "p2": [30, -15], "p3": [30, 15], "p4": [-30, 15]}
    drawn = draw(
        sketch_on(top),
        [{"kind": "point", "id": p, "at": {"model": [x, y, 20]}} for p, (x, y) in points.items()],
    )
    holes = healthy(
        call(
            "feature_hole",
            {
                "placement": {
                    "type": "sketch_points",
                    "points": [token(drawn["refs"][p]) for p in points],
                },
                "kind": "counterbore",
                "diameter": 5.5,
                "cb_diameter": 9,
                "cb_depth": 1.5,
                "extent": {"type": "through_all"},
            },
        )
    )
    assert holes["holes"] == 4
    assert len(holes["modified_bodies"]) == 1
    one_body()

    tapped = healthy(
        call(
            "feature_hole",
            {
                # The four counterbore floors face +Z too: take the top face by height.
                "placement": {"type": "point", "face": highest_face(), "point": [0, 0, 20]},
                "diameter": 4.2,
                "extent": {"type": "through_all"},
                "thread": {"designation": "M5x0.8"},
            },
        )
    )
    assert tapped["holes"] == 1
    thread = tapped["thread"]
    assert thread["designation"] == "M5x0.8"
    assert thread["internal"] is True
    assert thread["health"]["state"] == "ok", thread["health"]
    body = one_body()

    tree = call("inspect_tree", {})["root"]
    assert all(f["health"] == "ok" for f in tree["features"]), tree["features"]
    # The r 2.1 mm tapped bore; the shelled inner corner fillets (r 5 - 3 = 2 mm) stay out.
    bore = faces(scope=[body], surface_type="cylinder", radius_min_mm=2.05, radius_max_mm=2.15)
    assert len(bore) == 1, [{k: v for k, v in f.items() if k != "ref"} for f in bore]
    assert bore[0]["centroid_mm"][:2] == pytest.approx([0, 0], abs=1e-6)


# --- 13.2: one feature per body --------------------------------------------------------------


def test_revolve_ring_around_z(document: dict[str, Any]) -> None:
    drawn = draw(
        sketch_on("origin:XZ"),
        [
            {
                "kind": "rectangle",
                "corner1": {"model": [10, 0, 0]},
                "corner2": {"model": [20, 0, 30]},
            }
        ],
    )
    out = healthy(call("feature_revolve", {"profiles": [only_profile(drawn)], "axis": "origin:Z"}))
    assert len(out["created_bodies"]) == 1
    body = one_body()
    assert volume(body) == pytest.approx(math.pi * (20**2 - 10**2) * 30, rel=1e-3)


def test_sweep_circle_along_line_and_arc(document: dict[str, Any]) -> None:
    profile = only_profile(
        draw(sketch_on("origin:XY"), [{"kind": "circle", "center": "origin", "radius": 3}])
    )
    path = draw(
        sketch_on("origin:XZ"),
        [
            {
                "kind": "line",
                "id": "l1",
                "start": {"model": [0, 0, 0]},
                "end": {"model": [0, 0, 30]},
            },
            {
                "kind": "arc",
                "mode": "tangent",
                "id": "a1",
                "from": "l1.end",
                "to": {"model": [20, 0, 50]},
            },
        ],
    )
    out = healthy(
        call(
            "feature_sweep",
            {"profiles": [profile], "path": [token(path["refs"]["l1"]), token(path["refs"]["a1"])]},
        )
    )
    assert len(out["created_bodies"]) == 1
    body = one_body()
    length = 30 + math.pi * 20 / 2
    assert volume(body) == pytest.approx(math.pi * 3**2 * length, rel=1e-2)


def test_loft_square_to_circle(document: dict[str, Any]) -> None:
    square = only_profile(
        draw(
            sketch_on("origin:XY"),
            [{"kind": "rectangle", "mode": "center", "center": "origin", "corner": [20, 20]}],
        )
    )
    plane = call("construction_plane", {"method": "offset", "plane": "origin:XY", "distance": 30})
    circle = only_profile(
        draw(
            sketch_on(token(plane)),
            [{"kind": "circle", "center": {"model": [0, 0, 30]}, "radius": 10}],
        )
    )
    out = healthy(call("feature_loft", {"sections": [{"entity": square}, {"entity": circle}]}))
    assert len(out["created_bodies"]) == 1
    one_body()
    size = call("inspect_bounding_box", {})["union"]["size_mm"]
    assert size == pytest.approx([40, 40, 30], abs=1e-3)


def test_rib_in_an_l_bracket(document: dict[str, Any]) -> None:
    bracket()
    before = volume(one_body())
    line = draw(
        sketch_on("origin:XZ"),
        [
            {
                "kind": "line",
                "id": "l1",
                "start": {"model": [5, 0, 30]},
                "end": {"model": [30, 0, 5]},
            }
        ],
    )
    # The rib grows along (curve tangent x sketch normal); for this line that points away from
    # the corner (Fusion: warning, the rib runs to a far face), so it is flipped into the
    # corner: a healthy gusset (checked on the live Fusion, c15 3.1).
    out = healthy(
        thin_wall_or_skip(
            "feature_rib",
            {"curves": [token(line["refs"]["l1"])], "thickness": 3, "flip": True},
        )
    )
    assert len(out["modified_bodies"]) == 1
    assert volume(one_body()) > before


def test_web_in_an_l_bracket(document: dict[str, Any]) -> None:
    bracket()
    before = volume(one_body())
    lines = draw(
        sketch_on("origin:XZ"),
        [
            {
                "kind": "line",
                "id": "l1",
                "start": {"model": [5, 0, 20]},
                "end": {"model": [20, 0, 5]},
            },
            {
                "kind": "line",
                "id": "l2",
                "start": {"model": [5, 0, 35]},
                "end": {"model": [35, 0, 5]},
            },
        ],
    )
    curves = [token(lines["refs"]["l1"]), token(lines["refs"]["l2"])]
    out = healthy(
        thin_wall_or_skip(
            "feature_web",
            {"curves": curves, "thickness": 2, "depth": {"type": "distance", "distance": 10}},
        )
    )
    assert len(out["modified_bodies"]) == 1
    assert volume(one_body()) > before


def test_chamfer_top_edges(document: dict[str, Any]) -> None:
    box(30, 30, 30)
    before = volume(one_body())
    top_edges = edges(scope=[face([0, 0, 1])])
    assert len(top_edges) == 4, top_edges
    out = healthy(call("feature_chamfer", {"edges": [token(e) for e in top_edges], "distance": 2}))
    assert len(out["modified_bodies"]) == 1
    assert volume(one_body()) < before


def test_draft_side_faces(document: dict[str, Any]) -> None:
    box(30, 30, 30)
    before = volume(one_body())
    sides = [face(n) for n in ([1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0])]
    out = healthy(call("feature_draft", {"faces": sides, "plane": "origin:XY", "angle": 3}))
    assert len(out["modified_bodies"]) == 1
    assert volume(one_body()) != pytest.approx(before, rel=1e-4)


def test_offset_top_face(document: dict[str, Any]) -> None:
    box(30, 30, 30)
    out = healthy(call("feature_offset_faces", {"faces": [face([0, 0, 1])], "distance": 5}))
    assert len(out["modified_bodies"]) == 1
    one_body()
    size = call("inspect_bounding_box", {})["union"]["size_mm"]
    assert size == pytest.approx([30, 30, 35], abs=1e-3)

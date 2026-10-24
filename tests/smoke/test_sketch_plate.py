"""Smoke test of a fully constrained parametric plate sketch on a live Fusion 360
(``pytest -m fusion``, c05 10.1).

A new design document gets a sketch on ``origin:XY`` with a centre rectangle on the origin
(``plate_w = 100``, height ``plate_w * 0.6``) and four equal 8 mm holes 15 mm / 10 mm from
the corners, all in one ``sketch_draw``. The sketch must be fully constrained with five
profiles, the plate having four inner loops. Then ``plate_w`` becomes 110 and the plate
profile is found again by its interior point. The document is closed without saving.
Skipped when the bridge is not reachable.
"""

import asyncio
import json
import math
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
HOLE_AREA_MM2 = math.pi * 4.0**2


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


def section(state: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """A ``sketch_get`` section: a list, or ``{"items": [...], "truncated"}``."""
    value = state[name]
    items: list[dict[str, Any]] = value if isinstance(value, list) else value["items"]
    return items


@pytest.fixture(scope="module")
def document() -> Iterator[dict[str, Any]]:
    probe = call_raw("fusion_status", {})
    if probe.is_error and result_text(probe).startswith("NOT_CONNECTED"):
        pytest.skip(f"Fusion bridge not reachable: {result_text(probe).splitlines()[0]}")
    built = call("execute_python", {"code": NEW_DOCUMENT, "timeout_s": 60})["result"]
    try:
        yield built
    finally:
        call("execute_python", {"code": CLOSE, "params": {"document": built["document"]}})


def plate_draw(sketch: str) -> dict[str, Any]:
    holes = {"h1": [-35, 20], "h2": [35, 20], "h3": [-35, -20], "h4": [35, -20]}
    items: list[dict[str, Any]] = [
        {"kind": "rectangle", "mode": "center", "id": "r", "center": "origin", "corner": [50, 30]}
    ]
    items += [{"kind": "circle", "id": h, "center": c, "radius": 4} for h, c in holes.items()]
    constraints = [
        {"type": "equal", "curve1": "h1", "curve2": "h2"},
        {"type": "equal", "curve1": "h1", "curve2": "h3"},
        {"type": "equal", "curve1": "h1", "curve2": "h4"},
        {"type": "horizontal", "points": ["h1.center", "h2.center"]},
        {"type": "horizontal", "points": ["h3.center", "h4.center"]},
        {"type": "vertical", "points": ["h1.center", "h3.center"]},
        {"type": "vertical", "points": ["h2.center", "h4.center"]},
    ]

    def offset(corner: str, hole: str, orientation: str, value: float) -> dict[str, Any]:
        return {
            "type": "distance",
            "point1": corner,
            "point2": f"{hole}.center",
            "orientation": orientation,
            "value": value,
        }

    dimensions = [
        {"type": "distance", "line": "r.bottom", "value": 100, "parameter_name": "plate_w"},
        {"type": "distance", "line": "r.left", "value": "plate_w * 0.6"},
        {"type": "diameter", "curve": "h1", "value": 8},
        offset("r.tl", "h1", "horizontal", 15),
        offset("r.tl", "h1", "vertical", 10),
        offset("r.br", "h4", "horizontal", 15),
        offset("r.br", "h4", "vertical", 10),
    ]
    out: dict[str, Any] = call(
        "sketch_draw",
        {"sketch": sketch, "items": items, "constraints": constraints, "dimensions": dimensions},
    )
    return out


def plate_profile(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    plates = [p for p in profiles if len(p["inner_loops"]) == 4]
    assert len(plates) == 1, profiles
    return plates[0]


def test_fully_constrained_plate_and_parameter_change(document: dict[str, Any]) -> None:
    created = call("sketch_create", {"plane": "origin:XY", "name": "Plate"})
    sketch = token(created["sketch"])
    assert created["name"] == "Plate"
    assert created["transform"]["normal"] == pytest.approx([0, 0, 1], abs=1e-9)

    drawn = plate_draw(sketch)
    assert drawn["status"]["fully_constrained"] is True, drawn["status"]
    assert drawn["status"]["under_constrained_count"] == 0
    # A rectangle is four lines, not one entity: its refs are the sides and the centre point
    # (spec sketch-geometry "Center rectangle exposes center point"), there is no bare "r".
    for path in ("r.bottom", "r.right", "r.top", "r.left", "r.center", "h1", "h1.center"):
        assert path in drawn["refs"], path
    assert "h4.center" in drawn["refs"]
    assert "r" not in drawn["refs"]
    assert len(drawn["constraints"]) == 7
    plate_w = drawn["dimensions"][0]
    assert plate_w["parameter"] == "plate_w"
    assert plate_w["value"] == pytest.approx(100.0, abs=1e-6)
    assert drawn["dimensions"][1]["value"] == pytest.approx(60.0, abs=1e-6)

    assert len(drawn["profiles"]) == 5
    plate = plate_profile(drawn["profiles"])
    assert plate["area_mm2"] == pytest.approx(100 * 60 - 4 * HOLE_AREA_MM2, rel=1e-4)
    plate_ref = token(plate["ref"])
    assert plate_ref.startswith(f"profile:{sketch}@")
    point = plate["point"]

    holes = call("sketch_profiles", {"sketch": sketch, "contains": [-35, 20]})["profiles"]
    assert len(holes) == 1
    assert holes[0]["area_mm2"] == pytest.approx(HOLE_AREA_MM2, rel=1e-4)

    changed = call(
        "sketch_dimension_set",
        {"sketch": sketch, "updates": [{"dimension": token(plate_w), "value": 110}]},
    )
    assert changed["dimensions"][0]["value"] == pytest.approx(110.0, abs=1e-6)
    assert changed["status"]["fully_constrained"] is True

    again = call("sketch_profiles", {"sketch": sketch, "contains": point})["profiles"]
    assert len(again) == 1
    assert len(again[0]["inner_loops"]) == 4
    assert again[0]["area_mm2"] == pytest.approx(110 * 66 - 4 * HOLE_AREA_MM2, rel=1e-4)
    assert token(again[0]["ref"]).startswith(f"profile:{sketch}@")

    dims = section(call("sketch_get", {"sketch": sketch, "include": ["dimensions"]}), "dimensions")
    by_parameter = {d["parameter"]: d for d in dims}
    assert by_parameter["plate_w"]["expression"] == "110 mm"
    assert any(d["expression"] == "plate_w * 0.6" for d in dims)
    assert any(d["value"] == pytest.approx(66.0, abs=1e-6) for d in dims)

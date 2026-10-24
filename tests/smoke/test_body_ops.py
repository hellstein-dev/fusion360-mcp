"""Smoke tests of the pattern and body tools on a live Fusion 360 (``pytest -m fusion``, c08
8.1-8.2).

``test_pattern_mirror_combine``: a 20 x 20 x 10 mm cube centred on the origin (``sketch_draw``
+ ``feature_extrude``) is patterned as a body 3 x 2 with a 15 mm step (the copies overlap),
all six bodies are mirrored across ``origin:YZ`` and everything is joined into the first body.
Every feature must be healthy; the result is one body of 80 x 35 x 10 mm (volume checked with
``inspect_physical_properties``).

``test_circular_move_scale_split_manage``: a r5 cylinder at x = 30 mm is patterned six times
around ``origin:Z``, the six bodies are copied 50 mm up (``body_move`` with
``keep_original``), one body is scaled by 0.5, split by ``origin:XZ``, renamed, hidden and
removed; the body count is checked after every step.

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


def healthy(result: dict[str, Any]) -> dict[str, Any]:
    assert result["health"]["state"] == "ok", result["health"]
    assert token(result["feature"])
    return result


def bodies() -> list[str]:
    found = call("inspect_query", {"kind": "body", "limit": 200})
    return [token(item) for item in found["items"]]


def extrude_profile(plane: str, item: dict[str, Any], height: float) -> str:
    sketch = token(call("sketch_create", {"plane": plane})["sketch"])
    drawn = call("sketch_draw", {"sketch": sketch, "items": [item]})
    profiles = drawn["profiles"]
    assert len(profiles) == 1, profiles
    out = healthy(
        call(
            "feature_extrude",
            {
                "profiles": [token(profiles[0]["ref"])],
                "side1": {"type": "distance", "distance": height},
            },
        )
    )
    assert len(out["created_bodies"]) == 1
    return token(out["created_bodies"][0])


def volume(body: str) -> float:
    out = call("inspect_physical_properties", {"refs": [body]})
    value: float = out["items"][0]["volume_mm3"]
    return value


def test_pattern_mirror_combine(document: dict[str, Any]) -> None:
    cube = extrude_profile(
        "origin:XY",
        {"kind": "rectangle", "mode": "center", "center": "origin", "corner": [10, 10]},
        10,
    )
    assert bodies() == [cube]

    pattern = healthy(
        call(
            "pattern_rectangular",
            {
                "entities": [cube],
                "direction1": "origin:X",
                "quantity1": 3,
                "distance1": 15,
                "direction2": "origin:Y",
                "quantity2": 2,
                "distance2": 15,
            },
        )
    )
    assert len(pattern["created_bodies"]) == 5
    six = bodies()
    assert len(six) == 6

    mirror = healthy(call("pattern_mirror", {"entities": six, "plane": "origin:YZ"}))
    assert len(mirror["created_bodies"]) == 6
    twelve = bodies()
    assert len(twelve) == 12

    target = six[0] if six[0] in twelve else twelve[0]
    tools = [b for b in twelve if b != target]
    combined = healthy(call("body_combine", {"target": target, "tools": tools}))
    assert len(combined["removed_bodies"]) == 11
    (result,) = bodies()
    # x: -40 .. 40 (cubes at 0/15/30 mm and their mirror), y: -10 .. 25, z: 0 .. 10.
    assert volume(result) == pytest.approx(80 * 35 * 10, rel=VOLUME_TOLERANCE)


def test_circular_move_scale_split_manage(document: dict[str, Any]) -> None:
    cylinder = extrude_profile("origin:XY", {"kind": "circle", "center": [30, 0], "radius": 5}, 10)

    circular = healthy(
        call("pattern_circular", {"entities": [cylinder], "axis": "origin:Z", "quantity": 6})
    )
    assert len(circular["created_bodies"]) == 5
    six = bodies()
    assert len(six) == 6

    moved = healthy(
        call(
            "body_move",
            {
                "bodies": six,
                "transform": {"type": "translate", "z": 50},
                "keep_original": True,
            },
        )
    )
    assert len(moved["created_bodies"]) == 6
    assert moved["copy"]["feature"] is not None or moved["copy"]["method"] == "copyToComponent"
    twelve = bodies()
    assert len(twelve) == 12

    first = token(moved["created_bodies"][0])
    scaled = healthy(call("body_scale", {"bodies": [first], "factor": 0.5}))
    assert scaled["modified_bodies"]
    assert len(bodies()) == 12

    # The original cylinder at (30, 0) crosses the XZ plane (the copies only moved up).
    current = bodies()
    target = cylinder if cylinder in current else next(b for b in current if b in six)
    split = healthy(call("body_split", {"bodies": [target], "tool": "origin:XZ"}))
    assert len(split["created_bodies"]) >= 1
    after_split = bodies()
    assert len(after_split) == 12 + len(split["created_bodies"])

    renamed = call("body_rename", {"body": after_split[0], "name": "Smoke part"})
    assert renamed["body"]["name"] == "Smoke part"

    hidden = call(
        "body_set_visibility", {"bodies": [after_split[1]], "visible": False, "opacity": 0.5}
    )
    assert hidden["bodies"][0]["light_bulb"] is False
    assert hidden["bodies"][0]["opacity"] == pytest.approx(0.5)
    # inspect_query lists visible bodies by default: the hidden one drops out.
    assert len(bodies()) == len(after_split) - 1

    removed = call("body_remove", {"bodies": [after_split[-1]]})
    assert removed["mode_used"] == "remove"
    assert len(removed["features"]) == 1
    assert len(bodies()) == len(after_split) - 2

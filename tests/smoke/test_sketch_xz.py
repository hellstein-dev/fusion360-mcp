"""Smoke test of a sketch on ``origin:XZ`` on a live Fusion 360 (``pytest -m fusion``, c05
10.2): the sketch-to-model ``transform`` (sketch Y is model -Z), points given in model space
(``{"model": [x, y, z]}``) and profile centroids in sketch space. The document is closed
without saving. Skipped when the bridge is not reachable.
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


def section(state: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = state[name]
    items: list[dict[str, Any]] = value if isinstance(value, list) else value["items"]
    return items


def to_sketch(transform: dict[str, Any], model: list[float]) -> list[float]:
    """Model mm -> sketch mm through the reported transform (axes are orthonormal)."""
    d = [m - o for m, o in zip(model, transform["origin"], strict=True)]
    return [
        sum(a * b for a, b in zip(d, transform[axis], strict=True)) for axis in ("x_axis", "y_axis")
    ]


def to_model(transform: dict[str, Any], xy: list[float]) -> list[float]:
    return [
        o + xy[0] * x + xy[1] * y
        for o, x, y in zip(
            transform["origin"], transform["x_axis"], transform["y_axis"], strict=True
        )
    ]


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


def test_xz_transform_model_points_and_centroid(document: dict[str, Any]) -> None:
    created = call("sketch_create", {"plane": "origin:XZ"})
    sketch = token(created["sketch"])
    transform = created["transform"]
    assert transform["origin"] == pytest.approx([0, 0, 0], abs=1e-9)
    assert transform["x_axis"] == pytest.approx([1, 0, 0], abs=1e-9)
    # The documented pitfall: sketch Y on origin:XZ is model -Z.
    assert transform["y_axis"] == pytest.approx([0, 0, -1], abs=1e-9)
    assert abs(transform["normal"][1]) == pytest.approx(1.0, abs=1e-9)

    # A 40 x 20 mm rectangle from model (0, 0, 0) to (40, 0, 20) and a circle at model
    # (70, 0, 10), all given in model space.
    drawn = call(
        "sketch_draw",
        {
            "sketch": sketch,
            "items": [
                {
                    "kind": "rectangle",
                    "id": "r",
                    "corner1": {"model": [0, 0, 0]},
                    "corner2": {"model": [40, 0, 20]},
                },
                {"kind": "circle", "id": "c", "center": {"model": [70, 0, 10]}, "radius": 5},
            ],
        },
    )
    assert drawn["warnings"] == []
    state = call("sketch_get", {"sketch": sketch, "include": ["curves"]})
    circle = next(c for c in section(state, "curves") if c["type"] == "circle")
    centre = circle["geometry"]["center"]
    assert centre == pytest.approx(to_sketch(transform, [70, 0, 10]), abs=1e-6)
    assert centre[1] == pytest.approx(-10.0, abs=1e-6)
    assert to_model(transform, centre) == pytest.approx([70, 0, 10], abs=1e-6)

    profiles = drawn["profiles"]
    assert len(profiles) == 2
    rect = next(p for p in profiles if p["area_mm2"] == pytest.approx(800.0, rel=1e-4))
    disc = next(p for p in profiles if p is not rect)
    assert disc["area_mm2"] == pytest.approx(math.pi * 25, rel=1e-4)
    # Centroids are reported in sketch space, not model space.
    assert rect["centroid"] == pytest.approx(to_sketch(transform, [20, 0, 10]), abs=1e-4)
    assert disc["centroid"] == pytest.approx(to_sketch(transform, [70, 0, 10]), abs=1e-4)
    assert rect["bbox"]["min"] == pytest.approx([0, -20], abs=1e-4)
    assert rect["bbox"]["max"] == pytest.approx([40, 0], abs=1e-4)

    found = call("sketch_profiles", {"sketch": sketch, "contains": rect["point"]})["profiles"]
    assert [token(p["ref"]) for p in found] == [token(rect["ref"])]

    # A model point off the sketch plane is projected onto it, with a warning.
    off = call(
        "sketch_draw",
        {"sketch": sketch, "items": [{"kind": "point", "at": {"model": [5, 7, 5]}}]},
    )
    assert off["warnings"], off

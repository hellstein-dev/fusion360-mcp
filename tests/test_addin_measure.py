"""``measure.*`` handlers with a fake ``MeasureManager`` (c03 4.1, 4.2, D10)."""

import math
from typing import Any

import pytest

from fusion_addin.core.context import Context
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import measure
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import (
    FakeMeasureManager,
    FakeMeasureResults,
    P,
    fake_box_body,
    fake_part_design,
)
from tests.fakes.design import FakeApp


def setup(params: dict[str, Any]) -> tuple[Context, FakeMeasureManager, Any]:
    design = fake_part_design(fake_box_body(1, 2, 3, token="b"))
    app = FakeApp(design)
    manager = FakeMeasureManager()
    app.measureManager = manager  # type: ignore[attr-defined]
    return Context(app, params), manager, design


def test_registered_read_only() -> None:
    for name in ("measure.distance", "measure.angle"):
        spec = REGISTRY.get(name)
        assert spec is not None
        assert spec.mutating is False


def test_target_ref_and_point() -> None:
    ctx, _, design = setup({})
    assert measure._target(ctx, "b-face+z") is design.by_token["b-face+z"]
    point = measure._target(ctx, [10, -5, 2.5])
    assert (point.x, point.y, point.z) == (1.0, -0.5, 0.25)
    assert ctx.refs.resolve("origin:O") is measure._target(ctx, "origin:o")


def test_target_uses_request_units() -> None:
    ctx, _, _ = setup({"units": "cm"})
    point = measure._target(ctx, [1, 2, 3])
    assert (point.x, point.y, point.z) == (1.0, 2.0, 3.0)


@pytest.mark.parametrize(
    "value", [None, "", [1, 2], [1, 2, "3"], [True, 0, 0], 5, {"x": 1}, [0, 0, math.inf]]
)
def test_target_invalid(value: Any) -> None:
    ctx, _, _ = setup({})
    with pytest.raises(BridgeError) as info:
        measure._target(ctx, value, "a")
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["param"] == "a"


def test_distance_between_faces() -> None:
    params = {"a": "b-face+z", "b": "b-face-z"}
    ctx, manager, design = setup(params)
    manager.distance = FakeMeasureResults(3.0, P(0.5, 1, 3), P(0.5, 1, 0))
    out = measure.measure_distance(ctx, params)
    assert out == {
        "distance_mm": 30.0,
        "point_a_mm": [5.0, 10.0, 30.0],
        "point_b_mm": [5.0, 10.0, 0.0],
    }
    assert manager.calls == [("distance", design.by_token["b-face+z"], design.by_token["b-face-z"])]


def test_distance_point_to_entity_and_missing_positions() -> None:
    params = {"a": [0, 0, 100], "b": "b"}
    ctx, manager, _ = setup(params)
    manager.distance = FakeMeasureResults(7.0)
    out = measure.measure_distance(ctx, params)
    assert out["distance_mm"] == 70.0
    assert out["point_a_mm"] is None
    point = manager.calls[0][1]
    assert (point.x, point.y, point.z) == (0.0, 0.0, 10.0)


def test_distance_stale_ref() -> None:
    params = {"a": "gone", "b": "b"}
    ctx, _, _ = setup(params)
    with pytest.raises(BridgeError) as info:
        measure.measure_distance(ctx, params)
    assert info.value.code is ErrorCode.ENTITY_NOT_FOUND


def test_angle_two_entities() -> None:
    params = {"a": "b-face+z", "b": "b-face+x"}
    ctx, manager, design = setup(params)
    manager.angle = FakeMeasureResults(math.pi / 2)
    assert measure.measure_angle(ctx, params) == {"angle_deg": 90.0}
    assert manager.calls == [
        ("angle", design.by_token["b-face+z"], design.by_token["b-face+x"], None)
    ]


def test_angle_three_points_vertex_at_b() -> None:
    params = {"a": [10, 0, 0], "b": [0, 0, 0], "c": [0, 10, 0]}
    ctx, manager, _ = setup(params)
    manager.angle = FakeMeasureResults(math.pi / 4)
    assert measure.measure_angle(ctx, params)["angle_deg"] == 45.0
    _, a, b, c = manager.calls[0]
    assert (a.x, b.x, c.y) == (1.0, 0.0, 1.0)


def test_angle_invalid_third_operand() -> None:
    params = {"a": "b", "b": "b", "c": [1]}
    ctx, _, _ = setup(params)
    with pytest.raises(BridgeError) as info:
        measure.measure_angle(ctx, params)
    assert info.value.details["param"] == "c"

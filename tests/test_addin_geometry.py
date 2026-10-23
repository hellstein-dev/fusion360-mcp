"""``fusion_addin.core.geometry`` on fakes (c03 2.1, D3)."""

from fusion_addin.core import geometry
from fusion_mcp.protocol.units import Units
from tests.fakes.brep import FakeBoundingBox, P, V


def test_point_cm_to_mm() -> None:
    assert geometry.point(P(1, -2.5, 0.123456789)) == [10.0, -25.0, 1.234568]
    assert geometry.point(P(1, 2, 3), Units("in")) == [10.0, 20.0, 30.0]


def test_vector_normalised_without_touching_input() -> None:
    v = V(0, 0, 5)
    assert geometry.vector(v) == [0.0, 0.0, 1.0]
    assert v.z == 5.0
    assert geometry.vector(V(1, 1, 0)) == [0.707106781, 0.707106781, 0.0]
    assert geometry.vector(V(0, -0.0, -2)) == [0.0, 0.0, -1.0]


def test_direction() -> None:
    assert geometry.direction(P(1, 1, 1), P(1, 1, 4)) == [0.0, 0.0, 1.0]
    assert geometry.direction(P(0, 0, 0), P(0, 0, 0)) == [0.0, 0.0, 0.0]


def test_bbox() -> None:
    box = geometry.bbox(FakeBoundingBox((0, -1, 0), (1, 2, 3)))
    assert box == {
        "min_mm": [0.0, -10.0, 0.0],
        "max_mm": [10.0, 20.0, 30.0],
        "size_mm": [10.0, 30.0, 30.0],
    }


def test_union_bbox() -> None:
    a = geometry.bbox(FakeBoundingBox((0, 0, 0), (1, 1, 1)))
    b = geometry.bbox(FakeBoundingBox((-1, 0.5, 0), (0.5, 2, 1)))
    assert geometry.union_bbox([a, None, b]) == {
        "min_mm": [-10.0, 0.0, 0.0],
        "max_mm": [10.0, 20.0, 10.0],
        "size_mm": [20.0, 20.0, 10.0],
    }
    assert geometry.union_bbox([]) is None
    assert geometry.union_bbox([None]) is None

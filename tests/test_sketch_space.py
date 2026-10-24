"""``fusion_addin/sketch/space.py``: points mm <-> cm, model points, transform (c05 D2, 3.1)."""

from typing import Any

import pytest

from fusion_addin.sketch import space
from fusion_addin.sketch.specs import ModelXyz, Xy
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.units import Units
from tests.fakes.brep import P, V
from tests.fakes.fusion_sketch import make_sketch


def xyz(p: Any) -> tuple[float, float, float]:
    return (p.x, p.y, p.z)


def test_xy_mm_to_sketch_cm() -> None:
    sketch = make_sketch()
    point = space.to_point3d([12.5, -40], "mm", sketch)
    assert xyz(point) == pytest.approx((1.25, -4.0, 0.0))
    assert xyz(space.to_point3d(Xy(1, 2), Units("in"), sketch)) == pytest.approx((2.54, 5.08, 0))
    assert xyz(space.to_point3d([3, 4], None, sketch)) == pytest.approx((0.3, 0.4, 0.0))


def test_model_point_on_xz_sketch_goes_through_model_to_sketch_space() -> None:
    sketch = make_sketch(plane="XZ")
    warnings: list[dict[str, Any]] = []
    point = space.to_point3d({"model": [10, 0, 5]}, "mm", sketch, warnings)
    # XZ sketch: x = model X, y = model -Z.
    assert xyz(point) == pytest.approx((1.0, -0.5, 0.0))
    assert warnings == []


def test_model_point_off_plane_is_projected_with_warning() -> None:
    sketch = make_sketch(plane="XY")
    warnings: list[dict[str, Any]] = []
    point = space.to_point3d(ModelXyz(10, 20, 3), "mm", sketch, warnings, field="items[0].at")
    assert xyz(point) == pytest.approx((1.0, 2.0, 0.0))
    assert len(warnings) == 1
    warning = warnings[0]
    assert warning["code"] == space.PROJECTED_WARNING
    assert warning["field"] == "items[0].at"
    assert warning["distance_mm"] == pytest.approx(3.0)


def test_model_point_within_tolerance_has_no_warning() -> None:
    sketch = make_sketch()
    warnings: list[dict[str, Any]] = []
    space.to_point3d({"model": [1, 1, 1e-6]}, "mm", sketch, warnings)
    assert warnings == []


@pytest.mark.parametrize("spec", ["origin", "l1.end", "token/abc"])
def test_entity_spec_is_not_coordinates(spec: str) -> None:
    with pytest.raises(BridgeError) as info:
        space.to_point3d(spec, "mm", make_sketch())
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["value"] == spec


def test_string_coordinate_rejected() -> None:
    with pytest.raises(BridgeError) as info:
        space.to_point3d(["10 mm", 0], "mm", make_sketch(), field="p")
    assert info.value.details["reason"] == "string_coordinate"


def test_unknown_units() -> None:
    with pytest.raises(BridgeError) as info:
        space.to_point3d([0, 0], "furlong", make_sketch())
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["param"] == "units"


def test_to_xy_mm() -> None:
    assert space.to_xy_mm(P(1.25, -0.0000001, 7.0)) == [12.5, -0.000001]
    assert space.to_xy_mm(P(-0.0, 0.0, 0.0)) == [0.0, 0.0]


@pytest.mark.parametrize(
    ("plane", "x_axis", "y_axis", "normal"),
    [
        ("XY", [1, 0, 0], [0, 1, 0], [0, 0, 1]),
        ("XZ", [1, 0, 0], [0, 0, -1], [0, 1, 0]),
        ("YZ", [0, 0, -1], [0, 1, 0], [1, 0, 0]),
    ],
)
def test_describe_transform_origin_planes(
    plane: str, x_axis: list[float], y_axis: list[float], normal: list[float]
) -> None:
    transform = space.describe_transform(make_sketch(plane=plane))
    assert transform["origin"] == [0.0, 0.0, 0.0]
    assert transform["x_axis"] == pytest.approx(x_axis)
    assert transform["y_axis"] == pytest.approx(y_axis)
    assert transform["normal"] == pytest.approx(normal)


def test_describe_transform_origin_in_mm() -> None:
    sketch = make_sketch()
    matrix = sketch.transform
    matrix.translation = V(1.0, 2.0, 3.0)
    sketch.transform = matrix
    assert space.describe_transform(sketch)["origin"] == [10.0, 20.0, 30.0]

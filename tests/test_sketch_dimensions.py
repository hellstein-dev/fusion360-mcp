"""``fusion_addin/sketch/dimensions.py``: sketch dimensions with expressions, parameters,
driven state and edits (c05 D10, tasks 6.3-6.6)."""

import math
from typing import Any

import pytest

from fusion_addin.core.context import Context
from fusion_addin.sketch import math2d
from fusion_addin.sketch.dimensions import (
    DIMENSION_TYPES,
    FALLBACK_PERPENDICULAR,
    apply_dimension,
    apply_dimensions,
    apply_updates,
    set_dimension,
    validate_dimension,
    validate_dimensions,
    validate_updates,
)
from fusion_addin.sketch.journal import SketchJournal
from fusion_addin.sketch.resolve import BatchScope
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.design import FakeApp
from tests.fakes.fusion_sketch import (
    circle_curve,
    fake_sketch_design,
    make_sketch,
    simple_solver,
)

DO = 0  # adsk.fusion.DimensionOrientations.AlignedDimensionOrientation
DH = 1
DV = 2


class Env:
    def __init__(self, units: str | None = None) -> None:
        self.design = fake_sketch_design()
        self.params = self.design.parameter_set
        self.sketch = make_sketch(self.design)
        self.sketch.solver = simple_solver
        self.ctx = Context(FakeApp(self.design), {})
        self.scope = BatchScope(self.ctx, self.sketch, units)
        self.journal = SketchJournal(self.sketch)
        s = self.sketch
        p = s._new_point
        self.l1 = s._line(p((0, 0, 0)), p((4, 0, 0)))
        self.l2 = s._line(p((0, 2, 0)), p((4, 2, 0)))  # parallel to l1
        a = 4 * math.cos(math.radians(40))
        self.l3 = s._line(self.l1.startSketchPoint, p((a, 4 * math.sin(math.radians(40)), 0)))
        self.circle = circle_curve(s, (10, 0, 0), 1.0)
        self.arc = s._arc(p((6, 0, 0)), p((7, 0, 0)), p((6, 1, 0)))
        self.ellipse = s._ellipse(p((10, 5, 0)), (1.0, 0.0, 0.0), 2.0, 1.0)
        self.point = p((1, 3, 0), True)  # a free point (sketchPoints.add)

    def apply(self, item: dict[str, Any], index: int = 0) -> dict[str, Any]:
        return apply_dimension(self.scope, self.journal, item, f"dimensions[{index}]")

    def calls(self, prefix: str = "sketchDimensions.") -> list[tuple[str, tuple[Any, ...]]]:
        return [c for c in self.sketch.calls if c[0].startswith(prefix)]

    def last_dimension(self) -> Any:
        dims = self.sketch.sketchDimensions
        return dims.item(dims.count - 1)


def t(entity: Any) -> str:
    token: str = entity.entityToken
    return token


def invalid(fn: Any, *args: Any) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        fn(*args)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    return info.value


def xy(point3d: Any) -> tuple[float, float]:
    return (round(point3d.x, 9), round(point3d.y, 9))


@pytest.fixture
def env() -> Env:
    return Env()


# --- 6.3 distance ----------------------------------------------------------------------------


def test_numeric_distance_of_a_line(env: Env) -> None:
    out = env.apply({"type": "distance", "line": t(env.l1), "value": 80})
    name, args = env.calls()[-1]
    assert name == "sketchDimensions.addDistanceDimension"
    assert args[:3] == (env.l1.startSketchPoint, env.l1.endSketchPoint, DO)
    assert args[4] is True
    dim = env.last_dimension()
    assert dim.parameter.expression == "80 mm"
    assert env.l1.length == pytest.approx(8.0)
    assert out["type"] == "distance"
    assert out["value"] == pytest.approx(80.0)
    assert out["unit"] == "mm"
    assert out["expression"] == "80 mm"
    assert out["driving"] is True
    assert out["ref"]["ref"] == t(dim)
    assert env.journal.entries[-1].target is dim


def test_default_text_point_is_offset_from_the_line(env: Env) -> None:
    env.apply({"type": "distance", "line": t(env.l1)})
    text = env.calls()[-1][1][3]
    x, y = math2d.default_dimension_text_point(
        "distance", {"p1": (0, 0), "p2": (40, 0), "orientation": "aligned"}
    )
    assert xy(text) == (round(x / 10, 9), round(y / 10, 9))
    assert env.last_dimension().parameter.expression == "40 mm"


def test_explicit_text_at_uses_scope_units() -> None:
    env = Env(units="cm")
    env.apply({"type": "distance", "line": t(env.l1), "text_at": [2, 3]})
    assert xy(env.calls()[-1][1][3]) == (2.0, 3.0)


@pytest.mark.parametrize(("orientation", "code"), [("horizontal", DH), ("vertical", DV)])
def test_distance_between_points_with_orientation(env: Env, orientation: str, code: int) -> None:
    a, b = env.l1.startSketchPoint, env.point
    item = {"type": "distance", "point1": t(a), "point2": t(b), "orientation": orientation}
    out = env.apply(item)
    args = env.calls()[-1][1]
    assert args[:3] == (a, b, code)
    expected = 10.0 if orientation == "horizontal" else 30.0
    assert out["value"] == pytest.approx(expected)


def test_distance_form_and_type_errors(env: Env) -> None:
    invalid(env.apply, {"type": "distance", "point1": t(env.point)})
    invalid(env.apply, {"type": "distance", "line": t(env.l1), "point1": t(env.point)})
    same = {"type": "distance", "point1": t(env.point), "point2": t(env.point)}
    assert invalid(env.apply, same).details["reason"] == "same_entity"
    error = invalid(env.apply, {"type": "distance", "line": t(env.circle)})
    assert error.details["expected"] == ["SketchLine"]
    error = invalid(env.apply, {"type": "distance", "line": t(env.l1), "orientation": "diag"})
    assert error.details["field"] == "dimensions[0].orientation"
    assert invalid(env.apply, {"type": "chamfer"}).details["expected"] == list(DIMENSION_TYPES)
    assert env.calls() == []


def test_driven_distance_and_driven_with_value(env: Env) -> None:
    out = env.apply({"type": "distance", "line": t(env.l1), "driven": True})
    assert env.calls()[-1][1][4] is False
    assert out["driving"] is False and out["value"] == pytest.approx(40.0)
    error = invalid(env.apply, {"type": "distance", "line": t(env.l1), "driven": True, "value": 5})
    assert error.details["reason"] == "driven_with_value"


def test_parameter_name_chain_in_one_batch(env: Env) -> None:
    items: list[dict[str, Any]] = [
        {"type": "distance", "line": t(env.l1), "value": 100, "parameter_name": "plate_w"},
        {"type": "distance", "line": t(env.l2), "value": "plate_w * 0.6"},
    ]
    out = apply_dimensions(env.scope, env.journal, items)
    assert out[0]["parameter"] == "plate_w"
    assert out[1]["expression"] == "plate_w * 0.6"
    assert out[1]["value"] == pytest.approx(60.0)
    assert env.l2.length == pytest.approx(6.0)
    # The second dimension follows plate_w.
    env.params["plate_w"].expression = "120 mm"
    assert env.last_dimension().parameter.value == pytest.approx(7.2)


def test_invalid_expression_creates_nothing(env: Env) -> None:
    item = {"type": "distance", "line": t(env.l1), "value": "no_such_param * 2"}
    error = invalid(validate_dimension, env.scope, item, "dimensions[0]")
    assert error.details["reason"] == "invalid_expression"
    assert error.details["field"] == "dimensions[0].value"
    error = invalid(env.apply, item)
    assert error.details["reason"] == "invalid_expression"
    assert env.calls() == []
    assert env.sketch.sketchDimensions.count == 0
    # A length where an angle is expected is invalid too.
    angle = {"type": "angular", "line1": t(env.l1), "line2": t(env.l3), "value": "5 mm"}
    assert invalid(env.apply, angle).details["unit"] == "deg"


def test_validation_of_a_batch_and_parameter_names(env: Env) -> None:
    env.params.add_user("taken", "1 mm", "mm", "")
    items = [
        {"type": "distance", "line": t(env.l1), "value": 10, "parameter_name": "w"},
        {"type": "distance", "line": t(env.l2), "value": "w / 2", "parameter_name": "w"},
    ]
    error = invalid(validate_dimensions, env.scope, items)
    assert error.details["reason"] == "parameter_name_taken"
    assert error.details["index"] == 1
    taken = [{"type": "radial", "curve": t(env.circle), "parameter_name": "taken"}]
    assert invalid(validate_dimensions, env.scope, taken).details["index"] == 0
    bad = [{"type": "radial", "curve": t(env.circle), "parameter_name": "1x"}]
    assert invalid(validate_dimensions, env.scope, bad).details["reason"] == "invalid_name"
    late = [
        {"type": "distance", "line": t(env.l2), "value": "w / 2"},
        {"type": "distance", "line": t(env.l1), "value": 10, "parameter_name": "w"},
    ]
    assert invalid(validate_dimensions, env.scope, late).details["index"] == 0
    assert env.calls() == []


def test_validation_with_declared_local_paths(env: Env) -> None:
    env.scope.declare("r", "SketchLine", {"start": "SketchPoint", "end": "SketchPoint"})
    validate_dimension(env.scope, {"type": "distance", "line": "r", "value": 5}, "d")
    item = {"type": "distance", "point1": "r.start", "point2": "r.end"}
    validate_dimension(env.scope, item, "d")
    error = invalid(validate_dimension, env.scope, {"type": "radial", "curve": "r"}, "d")
    assert error.details["reason"] == "wrong_type"


def test_fusion_failure_rolls_back_the_batch(env: Env) -> None:
    env.sketch.fail_on("addRadialDimension", exc=RuntimeError("over-constrained"))
    items = [
        {"type": "distance", "line": t(env.l1), "value": 50},
        {"type": "radial", "curve": t(env.circle), "value": 5},
    ]
    with pytest.raises(BridgeError) as info, SketchJournal(env.sketch) as journal:
        apply_dimensions(env.scope, journal, items)
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert info.value.details["index"] == 1
    assert env.sketch.sketchDimensions.count == 0
    assert env.params.get("d1") is None


# --- 6.4 offset ------------------------------------------------------------------------------


def test_offset_between_parallel_lines(env: Env) -> None:
    out = env.apply({"type": "offset", "line": t(env.l1), "entity": t(env.l2), "value": 25})
    name, args = env.calls()[-1]
    assert name == "sketchDimensions.addOffsetDimension"
    assert args[:2] == (env.l1, env.l2) and args[3] is True
    assert out["type"] == "offset" and out["expression"] == "25 mm"
    assert "fallback" not in out
    text = args[2]
    x, y = math2d.default_dimension_text_point(
        "offset", {"line": [(0, 0), (40, 0)], "point": (0, 20)}
    )
    assert xy(text) == (round(x / 10, 9), round(y / 10, 9))


def test_offset_needs_parallel_lines(env: Env) -> None:
    error = invalid(env.apply, {"type": "offset", "line": t(env.l1), "entity": t(env.l3)})
    assert error.details["reason"] == "not_parallel"
    error = invalid(env.apply, {"type": "offset", "line": t(env.l1), "entity": t(env.circle)})
    assert error.details["expected"] == ["SketchLine", "SketchPoint"]
    assert env.calls() == []


def test_offset_line_to_point_direct(env: Env) -> None:
    out = env.apply({"type": "offset", "line": t(env.l1), "entity": t(env.point)})
    assert env.calls()[-1][1][:2] == (env.l1, env.point)
    assert out["type"] == "offset" and out["value"] == pytest.approx(30.0)
    assert len(env.journal.entries) == 1


def test_offset_line_to_point_fallback(env: Env) -> None:
    env.sketch.sketchDimensions.offset_accepts_point = False
    out = env.apply({"type": "offset", "line": t(env.l1), "entity": t(env.point), "value": 35})
    names = [n for n, _ in env.sketch.calls]
    assert names == [
        "sketchDimensions.addOffsetDimension",
        "sketchLines.addByTwoPoints",
        "geometricConstraints.addPerpendicular",
        "geometricConstraints.addCoincident",
        "sketchDimensions.addDistanceDimension",
    ]
    helper = env.sketch.calls[1]
    assert helper[1][0] is env.point
    assert xy(helper[1][1]) == (1.0, 0.0)
    line = env.scope.ctx.refs.resolve(out["helper_line"]["ref"])
    assert line.isConstruction is True
    assert out["fallback"] == FALLBACK_PERPENDICULAR
    assert out["type"] == "distance"
    assert out["expression"] == "35 mm"
    assert len(out["helper_constraints"]) == 2
    # helper line, perpendicular, coincident, dimension
    assert len(env.journal.entries) == 4
    assert env.journal.rollback() == []
    assert not line.isValid
    assert env.sketch.sketchDimensions.count == 0
    assert env.sketch.geometricConstraints.count == 0
    assert env.point.isValid


def test_offset_fallback_point_on_line(env: Env) -> None:
    env.sketch.sketchDimensions.offset_accepts_point = False
    on_line = env.sketch._new_point((2, 0, 0))
    error = invalid(env.apply, {"type": "offset", "line": t(env.l1), "entity": t(on_line)})
    assert error.details["reason"] == "on_line"
    assert env.journal.entries == []


def test_offset_line_to_line_failure_is_not_a_fallback(env: Env) -> None:
    env.sketch.fail_on("addOffsetDimension")
    with pytest.raises(BridgeError) as info:
        env.apply({"type": "offset", "line": t(env.l1), "entity": t(env.l2)})
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert len(env.sketch.calls) == 1


# --- 6.5 radial, diameter, angular, ellipse -----------------------------------------------------


def test_radial_and_diameter(env: Env) -> None:
    out = env.apply({"type": "radial", "curve": t(env.circle), "value": 15})
    assert env.calls()[-1][0] == "sketchDimensions.addRadialDimension"
    assert out["type"] == "radial" and out["value"] == pytest.approx(15.0)
    assert env.circle.radius == pytest.approx(1.5)
    out = env.apply({"type": "diameter", "curve": t(env.circle), "driven": True})
    assert env.calls()[-1][0] == "sketchDimensions.addDiameterDimension"
    assert out["driving"] is False
    assert out["value"] == pytest.approx(30.0)


def test_radial_default_text_points(env: Env) -> None:
    env.apply({"type": "radial", "curve": t(env.circle)})
    x, y = math2d.default_dimension_text_point("radial", {"center": (100, 0), "radius": 10})
    assert xy(env.calls()[-1][1][1]) == (round(x / 10, 9), round(y / 10, 9))
    env.apply({"type": "radial", "curve": t(env.arc)})
    # The arc spans 0..90 deg: the text sits at its middle (45 deg).
    x, y = math2d.default_dimension_text_point(
        "radial", {"center": (60, 0), "radius": 10, "angle": 45}
    )
    assert xy(env.calls()[-1][1][1]) == (round(x / 10, 9), round(y / 10, 9))
    error = invalid(env.apply, {"type": "diameter", "curve": t(env.ellipse)})
    assert error.details["expected"] == ["SketchCircle", "SketchArc"]


def test_angular_value_in_degrees(env: Env) -> None:
    out = env.apply({"type": "angular", "line1": t(env.l1), "line2": t(env.l3), "value": 30})
    name, args = env.calls()[-1]
    assert name == "sketchDimensions.addAngularDimension"
    assert args[:2] == (env.l1, env.l3)
    dim = env.last_dimension()
    assert dim.parameter.expression == "30 deg"
    assert out["unit"] == "deg" and out["value"] == pytest.approx(30.0)
    x, y = math2d.default_dimension_text_point(
        "angular",
        {
            "line1": [(0, 0), (40, 0)],
            "line2": [(0, 0), (40 * math.cos(math.radians(40)), 40 * math.sin(math.radians(40)))],
        },
    )
    assert xy(args[2]) == pytest.approx((x / 10, y / 10))


def test_angular_driven_measures_the_interior_angle(env: Env) -> None:
    out = env.apply({"type": "angular", "line1": t(env.l1), "line2": t(env.l3), "driven": True})
    assert out["value"] == pytest.approx(40.0)


def test_angular_errors(env: Env) -> None:
    error = invalid(env.apply, {"type": "angular", "line1": t(env.l1), "line2": t(env.l2)})
    assert error.details["reason"] == "degenerate_geometry"
    same = {"type": "angular", "line1": t(env.l1), "line2": t(env.l1)}
    assert invalid(env.apply, same).details["reason"] == "same_entity"
    assert env.calls() == []


@pytest.mark.parametrize(
    ("kind", "method", "value"),
    [
        ("ellipse_major", "addEllipseMajorRadiusDimension", 20.0),
        ("ellipse_minor", "addEllipseMinorRadiusDimension", 10.0),
    ],
)
def test_ellipse_radii(env: Env, kind: str, method: str, value: float) -> None:
    out = env.apply({"type": kind, "ellipse": t(env.ellipse), "driven": True})
    name, args = env.calls()[-1]
    assert name == f"sketchDimensions.{method}"
    assert out["type"] == kind and out["value"] == pytest.approx(value)
    angle = 0.0 if kind == "ellipse_major" else 90.0
    x, y = math2d.default_dimension_text_point(
        "radial", {"center": (100, 50), "radius": value, "angle": angle}
    )
    assert xy(args[1]) == pytest.approx((x / 10, y / 10))
    out = env.apply({"type": kind, "ellipse": t(env.ellipse), "value": value + 5})
    assert out["expression"] == f"{int(value + 5)} mm"


# --- 6.6 set_dimension -----------------------------------------------------------------------


def make_dims(env: Env) -> tuple[Any, Any]:
    apply_dimensions(
        env.scope,
        env.journal,
        [
            {"type": "distance", "line": t(env.l1), "value": 100, "parameter_name": "plate_w"},
            {"type": "distance", "line": t(env.l2), "value": "plate_w * 0.6"},
        ],
    )
    env.journal.entries.clear()
    dims = env.sketch.sketchDimensions
    return dims.item(0), dims.item(1)


def test_set_value_expression_and_undo(env: Env) -> None:
    _, second = make_dims(env)
    out = set_dimension(env.scope, env.journal, second, value="plate_w + 10 mm")
    assert out["expression"] == "plate_w + 10 mm"
    assert out["value"] == pytest.approx(110.0)
    assert env.journal.rollback() == []
    assert second.parameter.expression == "plate_w * 0.6"


def test_set_numeric_value_and_rename(env: Env) -> None:
    first, _ = make_dims(env)
    out = set_dimension(env.scope, env.journal, first, value=90, parameter_name="width")
    assert out["expression"] == "90 mm" and out["parameter"] == "width"
    assert env.sketch.sketchDimensions.item(1).parameter.expression == "width * 0.6"
    assert env.journal.rollback() == []
    assert first.parameter.name == "plate_w"
    assert first.parameter.expression == "100 mm"


def test_set_driven_and_back(env: Env) -> None:
    first, _ = make_dims(env)
    out = set_dimension(env.scope, env.journal, first, driven=True)
    assert out["driving"] is False
    assert env.journal.rollback() == []
    assert first.isDriving is True
    assert first.parameter.expression == "100 mm"
    # A driven dimension made driving again with a value.
    first.isDriving = False
    out = set_dimension(env.scope, env.journal, first, value=70, driven=False)
    assert out["driving"] is True and out["expression"] == "70 mm"
    assert env.journal.rollback() == []
    assert first.isDriving is False


def test_set_dimension_errors(env: Env) -> None:
    first, second = make_dims(env)
    assert "give" in invalid(set_dimension, env.scope, env.journal, first).message
    error = invalid(set_dimension, env.scope, env.journal, first, 5, True)
    assert error.details["reason"] == "driven_with_value"
    first.isDriving = False
    error = invalid(set_dimension, env.scope, env.journal, first, 5)
    assert error.details["reason"] == "driven_dimension"
    error = invalid(set_dimension, env.scope, env.journal, second, "nope * 2")
    assert error.details["reason"] == "invalid_expression"
    error = invalid(set_dimension, env.scope, env.journal, second, None, None, "plate_w")
    assert error.details["reason"] == "parameter_name_taken"
    # Keeping its own name is allowed.
    set_dimension(env.scope, env.journal, first, None, None, "plate_w")
    assert env.journal.entries == []


def test_apply_updates_resolves_refs_and_validates_first(env: Env) -> None:
    first, second = make_dims(env)
    updates = [
        {"dimension": t(first), "parameter_name": "w"},
        {"dimension": t(second), "value": "w / 2"},
    ]
    out = apply_updates(env.scope, env.journal, updates)
    assert out[1]["value"] == pytest.approx(50.0)
    error = invalid(validate_updates, env.scope, [{"dimension": t(env.l1), "value": 3}])
    assert error.details["expected"][0] == "SketchLinearDimension"
    assert error.details["index"] == 0


def test_rollback_of_updates_when_the_second_fails(env: Env) -> None:
    first, second = make_dims(env)
    env.params.fail_on.add(second.parameter.name)
    updates = [
        {"dimension": t(first), "value": 120},
        {"dimension": t(second), "value": 30},
    ]
    with pytest.raises(BridgeError) as info, SketchJournal(env.sketch) as journal:
        apply_updates(env.scope, journal, updates)
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert info.value.details["index"] == 1
    assert first.parameter.expression == "100 mm"
    assert second.parameter.expression == "plate_w * 0.6"

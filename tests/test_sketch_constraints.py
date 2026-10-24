"""``fusion_addin/sketch/constraints.py``: geometric constraints with type checks (c05 D9,
tasks 6.1-6.2)."""

from typing import Any

import pytest

from fusion_addin.core.context import Context
from fusion_addin.sketch.constraints import (
    CONSTRAINT_TYPES,
    apply_constraint,
    apply_constraints,
    validate_constraint,
    validate_constraints,
)
from fusion_addin.sketch.journal import SketchJournal
from fusion_addin.sketch.resolve import LINE_TYPES, BatchScope
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.design import FakeApp
from tests.fakes.fusion_sketch import circle_curve, fake_sketch_design, make_sketch


class Env:
    def __init__(self) -> None:
        self.design = fake_sketch_design()
        self.sketch = make_sketch(self.design)
        self.ctx = Context(FakeApp(self.design), {})
        self.scope = BatchScope(self.ctx, self.sketch)
        self.journal = SketchJournal(self.sketch)
        s = self.sketch
        p = s._new_point
        # Two lines, an arc, a circle, an ellipse, a spline and a free point (cm).
        self.l1 = s._line(p((0, 0, 0)), p((4, 0, 0)))
        self.l2 = s._line(p((0, 2, 0)), p((4, 3, 0)))
        self.arc = s._arc(p((6, 0, 0)), p((7, 0, 0)), p((6, 1, 0)))
        self.circle = circle_curve(s, (10, 0, 0), 1.0)
        self.ellipse = s._ellipse(p((10, 0, 0)), (1.0, 0.0, 0.0), 2.0, 1.0)
        self.spline = s._spline([p((0, 5, 0)), p((1, 6, 0)), p((2, 5, 0))])
        self.point = p((1, 1, 0))

    def apply(self, item: dict[str, Any]) -> dict[str, Any]:
        return apply_constraint(self.scope, self.journal, item, "constraints[0]")

    def last_call(self) -> tuple[str, tuple[Any, ...]]:
        return self.sketch.calls[-1]


def t(entity: Any) -> str:
    token: str = entity.entityToken
    return token


def invalid(fn: Any, *args: Any) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        fn(*args)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    return info.value


@pytest.fixture
def env() -> Env:
    return Env()


# --- 6.1 -----------------------------------------------------------------------------------


def test_coincident_point_on_curve(env: Env) -> None:
    out = env.apply({"type": "coincident", "point": t(env.point), "entity": t(env.l1)})
    name, args = env.last_call()
    assert name == "geometricConstraints.addCoincident"
    assert args == (env.point, env.l1)
    assert out["type"] == "coincident"
    assert out["ref"]["type"] == "CoincidentConstraint"
    assert len(env.journal.entries) == 1


def test_coincident_with_origin_and_local_path(env: Env) -> None:
    env.scope.register("a", env.l1, {"start": env.l1.startSketchPoint})
    env.apply({"type": "coincident", "point": "a.start", "entity": "origin"})
    assert env.last_call()[1] == (env.l1.startSketchPoint, env.sketch.originPoint)


@pytest.mark.parametrize("kind", ["horizontal", "vertical"])
def test_horizontal_vertical_line(env: Env, kind: str) -> None:
    out = env.apply({"type": kind, "line": t(env.l2)})
    name, args = env.last_call()
    assert name == f"geometricConstraints.add{kind.capitalize()}"
    assert args == (env.l2,)
    assert out["type"] == kind


@pytest.mark.parametrize("kind", ["horizontal", "vertical"])
def test_horizontal_vertical_points(env: Env, kind: str) -> None:
    p1, p2 = env.l1.startSketchPoint, env.l2.endSketchPoint
    out = env.apply({"type": kind, "points": [t(p1), t(p2)]})
    name, args = env.last_call()
    assert name == f"geometricConstraints.add{kind.capitalize()}Points"
    assert args == (p1, p2)
    assert out["ref"]["type"] == f"{kind.capitalize()}PointsConstraint"


def test_horizontal_form_errors(env: Env) -> None:
    error = invalid(env.apply, {"type": "horizontal"})
    assert "either" in error.message
    both = {"type": "vertical", "line": t(env.l1), "points": [t(env.point), t(env.point)]}
    invalid(env.apply, both)
    same = {"type": "vertical", "points": [t(env.point), t(env.point)]}
    invalid(env.apply, same)
    error = invalid(env.apply, {"type": "horizontal", "points": [t(env.l1), t(env.point)]})
    assert error.details["expected"] == ["SketchPoint"]
    assert error.details["field"] == "constraints[0].points[0]"
    error = invalid(env.apply, {"type": "horizontal", "line": t(env.circle)})
    assert error.details["expected"] == list(LINE_TYPES)


@pytest.mark.parametrize(
    ("kind", "method"),
    [
        ("parallel", "addParallel"),
        ("perpendicular", "addPerpendicular"),
        ("collinear", "addCollinear"),
    ],
)
def test_two_line_constraints(env: Env, kind: str, method: str) -> None:
    out = env.apply({"type": kind, "line1": t(env.l1), "line2": t(env.l2)})
    name, args = env.last_call()
    assert name == f"geometricConstraints.{method}"
    assert args == (env.l1, env.l2)
    assert out["type"] == kind


def test_parallel_with_circle_is_rejected_before_any_call(env: Env) -> None:
    item = {"type": "parallel", "line1": t(env.l1), "line2": t(env.circle)}
    error = invalid(validate_constraint, env.scope, item, "constraints[0]")
    assert error.details["expected"] == ["SketchLine"]
    assert error.details["got"] == "SketchCircle"
    assert error.details["field"] == "constraints[0].line2"
    error = invalid(apply_constraints, env.scope, env.journal, [item])
    assert error.details["index"] == 0
    assert env.sketch.calls == []
    assert env.journal.entries == []


def test_same_line_twice_is_invalid(env: Env) -> None:
    error = invalid(env.apply, {"type": "parallel", "line1": t(env.l1), "line2": t(env.l1)})
    assert error.details["reason"] == "same_entity"


def test_unknown_type_and_missing_fields(env: Env) -> None:
    error = invalid(env.apply, {"type": "glue"})
    assert error.details["expected"] == list(CONSTRAINT_TYPES)
    error = invalid(env.apply, {"type": "parallel", "line1": t(env.l1)})
    assert error.details["field"] == "constraints[0].line2"


def test_validation_accepts_declared_local_paths(env: Env) -> None:
    env.scope.declare("r", "SketchLine", {"start": "SketchPoint"})
    validate_constraint(env.scope, {"type": "horizontal", "line": "r"}, "c")
    validate_constraint(env.scope, {"type": "coincident", "point": "r.start", "entity": "r"}, "c")
    error = invalid(
        validate_constraint, env.scope, {"type": "midpoint", "point": "r", "curve": "r"}, "c"
    )
    assert error.details["reason"] == "wrong_type"
    error = invalid(validate_constraint, env.scope, {"type": "horizontal", "line": "zz"}, "c")
    assert error.details["reason"] == "unknown_local_id"


# --- 6.2 -----------------------------------------------------------------------------------


def test_tangent_line_and_arc(env: Env) -> None:
    out = env.apply({"type": "tangent", "curve1": t(env.l1), "curve2": t(env.arc)})
    assert env.last_call() == ("geometricConstraints.addTangent", (env.l1, env.arc))
    assert out["type"] == "tangent"


def test_tangent_of_two_lines_is_invalid(env: Env) -> None:
    error = invalid(env.apply, {"type": "tangent", "curve1": t(env.l1), "curve2": t(env.l2)})
    assert error.details["reason"] == "wrong_combination"
    assert env.sketch.calls == []


def test_equal_lines_and_circles(env: Env) -> None:
    env.apply({"type": "equal", "curve1": t(env.l1), "curve2": t(env.l2)})
    env.apply({"type": "equal", "curve1": t(env.arc), "curve2": t(env.circle)})
    assert env.sketch.call_names() == ["geometricConstraints.addEqual"] * 2
    error = invalid(env.apply, {"type": "equal", "curve1": t(env.l1), "curve2": t(env.circle)})
    assert error.details["reason"] == "wrong_combination"
    error = invalid(env.apply, {"type": "equal", "curve1": t(env.l1), "curve2": t(env.ellipse)})
    assert "SketchEllipse" not in error.details["expected"]


def test_concentric(env: Env) -> None:
    out = env.apply({"type": "concentric", "curve1": t(env.circle), "curve2": t(env.ellipse)})
    assert env.last_call() == ("geometricConstraints.addConcentric", (env.circle, env.ellipse))
    assert out["ref"]["type"] == "ConcentricConstraint"
    error = invalid(env.apply, {"type": "concentric", "curve1": t(env.l1), "curve2": t(env.arc)})
    assert "SketchEllipse" in error.details["expected"]


def test_midpoint(env: Env) -> None:
    out = env.apply({"type": "midpoint", "point": t(env.point), "curve": t(env.l1)})
    assert env.last_call() == ("geometricConstraints.addMidPoint", (env.point, env.l1))
    assert out["type"] == "midpoint"
    error = invalid(env.apply, {"type": "midpoint", "point": t(env.point), "curve": t(env.circle)})
    assert error.details["expected"] == ["SketchLine", "SketchArc"]


def test_symmetry_about_a_line(env: Env) -> None:
    a, b = env.l2.startSketchPoint, env.l2.endSketchPoint
    item = {"type": "symmetry", "entity1": t(a), "entity2": t(b), "line": t(env.l1)}
    out = env.apply(item)
    constraint = env.sketch.geometricConstraints.item(env.sketch.geometricConstraints.count - 1)
    assert constraint.entityOne is a
    assert constraint.entityTwo is b
    assert constraint.symmetryLine is env.l1
    assert out == {
        "ref": {"ref": constraint.entityToken, "type": "SymmetryConstraint"},
        "type": "symmetry",
    }


def test_symmetry_combination_errors(env: Env) -> None:
    mixed = {"type": "symmetry", "entity1": t(env.point), "entity2": t(env.l2), "line": t(env.l1)}
    assert invalid(env.apply, mixed).details["reason"] == "wrong_combination"
    axis = {"type": "symmetry", "entity1": t(env.l1), "entity2": t(env.l2), "line": t(env.l1)}
    assert invalid(env.apply, axis).details["reason"] == "same_entity"
    error = invalid(
        env.apply,
        {
            "type": "symmetry",
            "entity1": t(env.point),
            "entity2": t(env.point),
            "line": t(env.circle),
        },
    )
    assert error.details["field"] == "constraints[0].line"


def test_smooth(env: Env) -> None:
    out = env.apply({"type": "smooth", "spline": t(env.spline), "curve": t(env.arc)})
    assert env.last_call() == ("geometricConstraints.addSmooth", (env.spline, env.arc))
    assert out["type"] == "smooth"
    error = invalid(env.apply, {"type": "smooth", "spline": t(env.arc), "curve": t(env.spline)})
    assert "SketchFittedSpline" in error.details["expected"]


def test_fix_sets_is_fixed_without_a_constraint_and_undo_restores(env: Env) -> None:
    out = env.apply({"type": "fix", "entity": t(env.point)})
    assert env.point.isFixed is True
    assert out == {
        "entity": {"ref": t(env.point), "type": "SketchPoint"},
        "type": "fix",
        "fixed": True,
    }
    assert env.sketch.geometricConstraints.count == 0
    assert env.journal.rollback() == []
    assert env.point.isFixed is False


def test_unfix_and_noop_fix(env: Env) -> None:
    env.l1.isFixed = True
    out = env.apply({"type": "fix", "entity": t(env.l1), "fixed": False})
    assert out["fixed"] is False and env.l1.isFixed is False
    env.apply({"type": "fix", "entity": t(env.circle), "fixed": False})
    assert len(env.journal.entries) == 1
    error = invalid(env.apply, {"type": "fix", "entity": t(env.l1), "fixed": "yes"})
    assert error.details["field"] == "constraints[0].fixed"


def test_batch_returns_results_in_order(env: Env) -> None:
    items = [
        {"type": "horizontal", "line": t(env.l1)},
        {"type": "fix", "entity": t(env.point)},
        {"type": "tangent", "curve1": t(env.l2), "curve2": t(env.arc)},
    ]
    validate_constraints(env.scope, items)
    out = apply_constraints(env.scope, env.journal, items, validate=False)
    assert [o["type"] for o in out] == ["horizontal", "fix", "tangent"]


def test_over_constrained_rolls_back_with_index(env: Env) -> None:
    env.sketch.fail_on("addPerpendicular", exc=RuntimeError("over-constrained"))
    items = [
        {"type": "horizontal", "line": t(env.l1)},
        {"type": "fix", "entity": t(env.point)},
        {"type": "perpendicular", "line1": t(env.l1), "line2": t(env.l2)},
    ]
    with pytest.raises(BridgeError) as info, SketchJournal(env.sketch) as journal:
        apply_constraints(env.scope, journal, items)
    error = info.value
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.details["index"] == 2
    assert error.details["field"] == "constraints[2]"
    assert "over-constrained" in error.message
    assert env.sketch.geometricConstraints.count == 0
    assert env.point.isFixed is False

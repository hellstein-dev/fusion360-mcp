"""``sketch.draw|constrain|dimension|dimension_set`` handlers: batches, local ids, error
phases and all-or-nothing rollback (c05 D6, D7, D9, D10; tasks 7.1-7.4)."""

from __future__ import annotations

from typing import Any

import pytest

from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import sketch as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.design import FakeApp
from tests.fakes.fusion_sketch import (
    auto_profiles,
    circle_curve,
    fake_sketch_design,
    make_sketch,
    simple_solver,
)

TOKEN = "t" * 32


class Env:
    """A design with one empty sketch (``simple_solver`` applies driving dimensions)."""

    def __init__(self) -> None:
        self.design = fake_sketch_design()
        self.params = self.design.parameter_set
        self.sketch = make_sketch(self.design)
        self.sketch.solver = simple_solver

    def run(self, fn: Any, **params: Any) -> Any:
        params.setdefault("sketch", self.sketch.entityToken)
        return fn(Context(FakeApp(self.design), params), params)

    def error(self, fn: Any, **params: Any) -> BridgeError:
        with pytest.raises(BridgeError) as info:
            self.run(fn, **params)
        return info.value

    def counts(self) -> tuple[int, int, int, int]:
        s = self.sketch
        return (
            s.sketchCurves.count,
            s.sketchPoints.count,
            s.geometricConstraints.count,
            s.sketchDimensions.count,
        )

    def lines(self) -> tuple[Any, Any, Any]:
        """Two parallel horizontal lines and a circle, drawn outside of any handler."""
        s = self.sketch
        p = s._new_point
        l1 = s._line(p((0, 0, 0)), p((4, 0, 0)))
        l2 = s._line(p((0, 2, 0)), p((4, 2, 0)))
        circle = circle_curve(s, (10, 0, 0), 1.0)
        s.reset_calls()
        return l1, l2, circle


EMPTY = (0, 1, 0, 0)  # only the origin point


def t(entity: Any) -> str:
    token: str = entity.entityToken
    return token


@pytest.fixture
def env() -> Env:
    return Env()


def test_registration() -> None:
    for name in ("sketch.draw", "sketch.constrain", "sketch.dimension", "sketch.dimension_set"):
        spec = REGISTRY.get(name)
        assert spec is not None, name
        assert spec.mutating is True, name


# --- 7.1 sketch.draw ---------------------------------------------------------------------


PLATE_ITEMS: list[dict[str, Any]] = [
    {"kind": "rectangle", "mode": "center", "id": "r", "center": "origin", "corner": [50, 30]},
    {"kind": "circle", "id": "h", "center": [30, 15], "radius": 4},
    {"kind": "line", "id": "a", "start": "r.bl", "end": [10, 10], "construction": True},
]


def test_draw_batch_with_local_ids_constraints_and_dimensions(env: Env) -> None:
    env.sketch.profile_builder = auto_profiles
    out = env.run(
        h.sketch_draw,
        items=PLATE_ITEMS,
        constraints=[{"type": "horizontal", "points": ["origin", "a.end"]}],
        dimensions=[
            {"type": "distance", "line": "r.bottom", "value": 100, "parameter_name": "plate_w"},
            {"type": "distance", "line": "r.left", "value": "plate_w * 0.6"},
            {"type": "diameter", "curve": "h", "value": 8},
        ],
    )
    refs = out["refs"]
    for path in ("r.bottom", "r.left", "r.bl", "r.center", "h", "h.center", "a", "a.end"):
        assert path in refs, path
    assert "r" not in refs  # a group item has only sub paths
    a = env.sketch.sketchCurves.sketchLines
    line_a = next(line for line in a if t(line) == refs["a"]["ref"])
    assert line_a.startSketchPoint.entityToken == refs["r.bl"]["ref"]  # shared point
    assert line_a.isConstruction is True

    assert out["sketch"]["ref"] == t(env.sketch)
    assert [c["type"] for c in out["constraints"]] == ["horizontal"]
    created = env.sketch.geometricConstraints
    assert out["constraints"][0]["ref"]["ref"] == t(created.item(created.count - 1))
    dims = out["dimensions"]
    assert [d["expression"] for d in dims] == ["100 mm", "plate_w * 0.6", "8 mm"]
    assert dims[0]["parameter"] == "plate_w"
    assert dims[1]["value"] == pytest.approx(60.0)
    assert dims[2]["value"] == pytest.approx(8.0)
    assert set(out["status"]) >= {"fully_constrained", "under_constrained_count"}
    assert isinstance(out["profiles"], list)
    assert out["warnings"] == []
    # Deferred while drawing, restored before profiles.
    assert env.sketch.defer_log == [True, False]
    assert env.sketch.isComputeDeferred is False


def test_draw_without_profiles_and_with_model_point_warning(env: Env) -> None:
    out = env.run(
        h.sketch_draw,
        items=[{"kind": "point", "id": "p", "at": {"model": [1, 2, 5]}}],
        return_profiles=False,
    )
    assert "profiles" not in out
    assert out["refs"]["p"]["type"] == "SketchPoint"
    assert [w["code"] for w in out["warnings"]] == ["point_projected_to_sketch_plane"]


def test_draw_rolls_back_when_the_tenth_item_fails(env: Env) -> None:
    items: list[dict[str, Any]] = [
        {"kind": "line", "id": f"l{k}", "start": [k * 10, 0], "end": [k * 10, 5]} for k in range(9)
    ]
    items.append({"kind": "circle", "id": "c", "center": [0, 50], "radius": 5})
    env.sketch.fail_on("addByCenterRadius", exc=RuntimeError("boom"))
    error = env.error(h.sketch_draw, items=items)
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.details["phase"] == "geometry"
    assert error.details["index"] == 9
    assert error.details["id"] == "c"
    assert "rollback_error" not in error.details
    assert env.counts() == EMPTY
    assert env.sketch.isComputeDeferred is False
    assert env.sketch.defer_log == [True, False]


def test_draw_unknown_local_id_in_geometry_creates_nothing(env: Env) -> None:
    error = env.error(
        h.sketch_draw,
        items=[
            {"kind": "line", "id": "a", "start": [0, 0], "end": [10, 0]},
            {"kind": "line", "id": "b", "start": "nope.end", "end": [10, 10]},
        ],
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["reason"] == "unknown_local_id"
    assert (error.details["phase"], error.details["index"], error.details["id"]) == (
        "geometry",
        1,
        "b",
    )
    assert env.sketch.calls == []
    assert env.sketch.defer_log == []


def test_draw_unknown_local_id_in_constraints_is_found_before_mutation(env: Env) -> None:
    error = env.error(
        h.sketch_draw,
        items=[{"kind": "line", "id": "a", "start": [0, 0], "end": [10, 0]}],
        constraints=[
            {"type": "horizontal", "line": "a"},
            {"type": "vertical", "line": "zz"},
        ],
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["reason"] == "unknown_local_id"
    assert error.details["known_ids"] == ["a"]
    assert (error.details["phase"], error.details["index"], error.details["id"]) == (
        "constraints",
        1,
        None,
    )
    assert env.sketch.calls == []


def test_draw_dimension_of_a_group_id_is_refused_before_mutation(env: Env) -> None:
    error = env.error(
        h.sketch_draw,
        items=[{"kind": "rectangle", "id": "r", "corner1": [0, 0], "corner2": [10, 5]}],
        dimensions=[{"type": "distance", "line": "r", "value": 10}],
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["phase"] == "dimensions"
    assert error.details["index"] == 0
    assert env.sketch.calls == []


def test_draw_constraint_failure_rolls_back_the_geometry(env: Env) -> None:
    env.sketch.fail_on("addParallel", exc=RuntimeError("over-constrained"))
    error = env.error(
        h.sketch_draw,
        items=[
            {"kind": "line", "id": "a", "start": [0, 0], "end": [10, 0]},
            {"kind": "line", "id": "b", "start": [0, 5], "end": [10, 6]},
        ],
        constraints=[
            {"type": "horizontal", "line": "a"},
            {"type": "parallel", "line1": "a", "line2": "b"},
        ],
    )
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert (error.details["phase"], error.details["index"]) == ("constraints", 1)
    assert env.counts() == EMPTY
    assert env.sketch.isComputeDeferred is False


def test_draw_dimension_failure_rolls_back_everything(env: Env) -> None:
    env.sketch.fail_on("addDiameterDimension")
    error = env.error(
        h.sketch_draw,
        items=[
            {"kind": "line", "id": "a", "start": [0, 0], "end": [10, 0]},
            {"kind": "circle", "id": "c", "center": [0, 20], "radius": 5},
        ],
        constraints=[{"type": "horizontal", "line": "a"}],
        dimensions=[
            {"type": "distance", "line": "a", "value": 20},
            {"type": "diameter", "curve": "c", "value": 12},
        ],
    )
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert (error.details["phase"], error.details["index"]) == ("dimensions", 1)
    assert env.counts() == EMPTY
    assert len(env.params.params) == 0


@pytest.mark.parametrize("fail", [False, True])
def test_draw_restores_a_previously_deferred_sketch(env: Env, fail: bool) -> None:
    env.sketch.isComputeDeferred = True
    env.sketch.defer_log.clear()
    if fail:
        env.sketch.fail_on("addByTwoPoints")
        env.error(h.sketch_draw, items=[{"kind": "line", "start": [0, 0], "end": [1, 0]}])
    else:
        env.run(h.sketch_draw, items=[{"kind": "line", "start": [0, 0], "end": [1, 0]}])
    assert env.sketch.isComputeDeferred is True
    assert env.sketch.defer_log[-1] is True


def test_draw_param_checks(env: Env) -> None:
    error = env.error(h.sketch_draw, items=[])
    assert error.code is ErrorCode.INVALID_PARAMS
    item = {"kind": "point", "at": [0, 0]}
    error = env.error(h.sketch_draw, items=[item], constraints=[{}] * 501)
    assert error.details["reason"] == "too_many"
    error = env.error(h.sketch_draw, items=[item], units="parsec")
    assert error.details["param"] == "units"
    assert env.sketch.calls == []


def test_draw_through_the_dispatcher_reports_the_phase(env: Env) -> None:
    dispatcher = Dispatcher(TOKEN, context_factory=lambda p: Context(FakeApp(env.design), p))
    params = {"sketch": t(env.sketch), "items": [{"kind": "line", "start": [0, 0], "end": [0, 0]}]}
    response = dispatcher.dispatch(Request("1", TOKEN, "sketch.draw", params))
    assert response.ok is False
    assert response.error is not None
    assert response.error.details["phase"] == "geometry"
    assert response.error.details["index"] == 0


# --- 7.2 sketch.constrain ----------------------------------------------------------------


def test_constrain_batch(env: Env) -> None:
    l1, l2, circle = env.lines()
    out = env.run(
        h.sketch_constrain,
        constraints=[
            {"type": "horizontal", "points": [t(l1.startSketchPoint), t(l1.endSketchPoint)]},
            {"type": "parallel", "line1": t(l1), "line2": t(l2)},
            {"type": "fix", "entity": t(circle)},
        ],
    )
    kinds = [c["type"] for c in out["constraints"]]
    assert kinds == ["horizontal", "parallel", "fix"]
    assert env.sketch.geometricConstraints.count == 2
    assert out["constraints"][0]["ref"]["ref"] == t(env.sketch.geometricConstraints.item(0))
    assert out["constraints"][2] == {
        "entity": out["constraints"][2]["entity"],
        "type": "fix",
        "fixed": True,
    }
    assert circle.isFixed is True
    assert "status" in out and "profiles" in out
    names = env.sketch.call_names()
    assert "geometricConstraints.addHorizontalPoints" in names
    assert env.sketch.defer_log == []


def test_constrain_wrong_type_changes_nothing(env: Env) -> None:
    l1, _, circle = env.lines()
    error = env.error(
        h.sketch_constrain,
        constraints=[
            {"type": "horizontal", "line": t(l1)},
            {"type": "parallel", "line1": t(l1), "line2": t(circle)},
        ],
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["expected"] == ["SketchLine"]
    assert (error.details["phase"], error.details["index"], error.details["id"]) == (
        "constraints",
        1,
        None,
    )
    assert env.sketch.calls == []
    assert env.sketch.geometricConstraints.count == 0


def test_constrain_over_constrained_rolls_back(env: Env) -> None:
    l1, l2, circle = env.lines()
    env.sketch.fail_on("addVertical", exc=RuntimeError("over-constrained"))
    error = env.error(
        h.sketch_constrain,
        constraints=[
            {"type": "fix", "entity": t(circle)},
            {"type": "horizontal", "line": t(l1)},
            {"type": "vertical", "line": t(l1)},
        ],
    )
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert (error.details["phase"], error.details["index"]) == ("constraints", 2)
    assert env.sketch.geometricConstraints.count == 0
    assert circle.isFixed is False
    assert l1.isValid and l2.isValid


def test_constrain_requires_items(env: Env) -> None:
    error = env.error(h.sketch_constrain, constraints=[])
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == "constraints"


# --- 7.3 sketch.dimension ----------------------------------------------------------------


def test_dimension_parameter_name_chain(env: Env) -> None:
    l1, l2, _ = env.lines()
    out = env.run(
        h.sketch_dimension,
        dimensions=[
            {"type": "distance", "line": t(l1), "value": 100, "parameter_name": "plate_w"},
            {"type": "distance", "line": t(l2), "value": "plate_w * 0.6"},
        ],
        return_profiles=False,
    )
    first, second = out["dimensions"]
    assert first["parameter"] == "plate_w" and first["expression"] == "100 mm"
    assert second["expression"] == "plate_w * 0.6"
    assert second["value"] == pytest.approx(60.0)
    assert l2.length == pytest.approx(6.0)
    assert "profiles" not in out and "status" in out
    env.params["plate_w"].expression = "120 mm"
    assert env.sketch.sketchDimensions.item(1).parameter.value == pytest.approx(7.2)


def test_dimension_invalid_expression_creates_nothing(env: Env) -> None:
    l1, l2, _ = env.lines()
    error = env.error(
        h.sketch_dimension,
        dimensions=[
            {"type": "distance", "line": t(l1), "value": 50},
            {"type": "distance", "line": t(l2), "value": "no_such_param * 2"},
        ],
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["reason"] == "invalid_expression"
    assert (error.details["phase"], error.details["index"]) == ("dimensions", 1)
    assert env.sketch.sketchDimensions.count == 0
    assert env.sketch.calls == []


def test_dimension_driven_with_value_is_refused(env: Env) -> None:
    _, _, circle = env.lines()
    error = env.error(
        h.sketch_dimension,
        dimensions=[{"type": "diameter", "curve": t(circle), "driven": True, "value": 5}],
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["reason"] == "driven_with_value"
    assert error.details["phase"] == "dimensions"
    assert env.sketch.calls == []


def test_dimension_driven_reports_the_measured_value(env: Env) -> None:
    _, _, circle = env.lines()
    out = env.run(
        h.sketch_dimension, dimensions=[{"type": "diameter", "curve": t(circle), "driven": True}]
    )
    (dim,) = out["dimensions"]
    assert dim["driving"] is False
    assert dim["value"] == pytest.approx(20.0)


def test_dimension_api_failure_rolls_back_the_batch(env: Env) -> None:
    l1, _, circle = env.lines()
    env.sketch.fail_on("addRadialDimension", exc=RuntimeError("over-constrained"))
    error = env.error(
        h.sketch_dimension,
        dimensions=[
            {"type": "distance", "line": t(l1), "value": 50, "parameter_name": "w"},
            {"type": "radial", "curve": t(circle), "value": 5},
        ],
    )
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert (error.details["phase"], error.details["index"]) == ("dimensions", 1)
    assert env.sketch.sketchDimensions.count == 0
    assert len(env.params.params) == 0


# --- 7.4 sketch.dimension_set ------------------------------------------------------------


def _two_dimensions(env: Env) -> tuple[Any, Any]:
    l1, l2, _ = env.lines()
    env.run(
        h.sketch_dimension,
        dimensions=[
            {"type": "distance", "line": t(l1), "value": 100, "parameter_name": "plate_w"},
            {"type": "distance", "line": t(l2), "value": "plate_w * 0.6"},
        ],
    )
    dims = env.sketch.sketchDimensions
    return dims.item(0), dims.item(1)


def test_dimension_set_changes_the_value(env: Env) -> None:
    _, second = _two_dimensions(env)
    out = env.run(
        h.sketch_dimension_set, updates=[{"dimension": t(second), "value": "plate_w + 10 mm"}]
    )
    (dim,) = out["dimensions"]
    assert dim["expression"] == "plate_w + 10 mm"
    assert dim["value"] == pytest.approx(110.0)
    assert second.parameter.expression == "plate_w + 10 mm"
    assert "status" in out and "profiles" in out


def test_dimension_set_rolls_back_when_the_second_update_fails(env: Env) -> None:
    first, second = _two_dimensions(env)
    env.params.fail_on.add(second.parameter.name)
    error = env.error(
        h.sketch_dimension_set,
        updates=[
            {"dimension": t(first), "value": 120, "parameter_name": "width"},
            {"dimension": t(second), "value": 30},
        ],
    )
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert (error.details["phase"], error.details["index"], error.details["id"]) == (
        "updates",
        1,
        t(second),
    )
    assert first.parameter.expression == "100 mm"
    assert first.parameter.name == "plate_w"
    assert second.parameter.expression == "plate_w * 0.6"


def test_dimension_set_invalid_update_changes_nothing(env: Env) -> None:
    first, second = _two_dimensions(env)
    error = env.error(
        h.sketch_dimension_set,
        updates=[
            {"dimension": t(first), "value": 120},
            {"dimension": t(second), "value": "nope * 2"},
        ],
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["reason"] == "invalid_expression"
    assert (error.details["phase"], error.details["index"]) == ("updates", 1)
    assert first.parameter.expression == "100 mm"

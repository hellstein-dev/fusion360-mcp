"""``fusion_addin/sketch/resolve.py``: ``BatchScope`` local ids and spec resolution (c05 D4,
task 3.3)."""

from typing import Any

import pytest

from fusion_addin.core.context import Context
from fusion_addin.sketch.resolve import (
    CURVE_TYPES,
    LINE_TYPES,
    POINT_TYPES,
    BatchScope,
    entity_ref,
    same_entity,
)
from fusion_addin.sketch.specs import Xy
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import P
from tests.fakes.design import FakeApp
from tests.fakes.fusion_sketch import fake_sketch_design, make_sketch


def setup(plane: str = "XY", units: str | None = None) -> tuple[Any, Any, BatchScope]:
    design = fake_sketch_design()
    sketch = make_sketch(design, plane=plane)
    ctx = Context(FakeApp(design), {"units": units} if units else {})
    return ctx, sketch, BatchScope(ctx, sketch)


def add_line(sketch: Any, start: Any = None, end: Any = None) -> Any:
    lines = sketch.sketchCurves.sketchLines
    return lines.addByTwoPoints(start or P(0, 0, 0), end or P(4, 0, 0))


def invalid(fn: Any, *args: Any, **kwargs: Any) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        fn(*args, **kwargs)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    return info.value


def test_origin_resolves_to_origin_point() -> None:
    _, sketch, scope = setup()
    assert scope.entity("origin") is sketch.originPoint
    assert scope.point("origin", "p") is sketch.originPoint
    error = invalid(scope.entity, "origin", LINE_TYPES, "line")
    assert error.details["reason"] == "wrong_type"
    assert error.details["expected"] == ["SketchLine"]
    assert error.details["got"] == "SketchPoint"


def test_registered_local_paths() -> None:
    _, sketch, scope = setup()
    line = add_line(sketch)
    scope.register("a", line, {"start": line.startSketchPoint, "end": line.endSketchPoint})
    assert scope.entity("a", LINE_TYPES) is line
    assert scope.point("a.end") is line.endSketchPoint
    assert scope.has("a.start") and not scope.has("b")
    assert scope.get("a.start") is line.startSketchPoint
    assert scope.ids() == ["a"]
    error = invalid(scope.point, "a", "items[1].start")
    assert error.details["field"] == "items[1].start"
    assert error.details["expected"] == list(POINT_TYPES)


def test_refs_is_a_flat_map_in_item_order() -> None:
    _, sketch, scope = setup()
    a = add_line(sketch)
    b = add_line(sketch, a.endSketchPoint, P(4, 3, 0))
    scope.register("a", a, {"start": a.startSketchPoint, "end": a.endSketchPoint})
    scope.register("b", b, {"start": b.startSketchPoint, "end": b.endSketchPoint})
    scope.register(None, add_line(sketch))
    refs = scope.refs()
    assert list(refs) == ["a", "a.start", "a.end", "b", "b.start", "b.end"]
    assert refs["a"] == {"ref": a.entityToken, "type": "SketchLine"}
    assert refs["b.start"] == refs["a.end"]
    assert entity_ref(b) == refs["b"]


def test_unknown_local_id_is_invalid_params() -> None:
    _, sketch, scope = setup()
    line = add_line(sketch)
    scope.register("a", line, {"end": line.endSketchPoint})
    error = invalid(scope.point, "zz.end", "items[2].start")
    assert error.details["reason"] == "unknown_local_id"
    assert error.details["known_ids"] == ["a"]
    error = invalid(scope.entity, "a.middle")
    assert error.details["reason"] == "unknown_sub"
    assert error.details["available"] == ["end"]


def test_token_of_this_sketch() -> None:
    _, sketch, scope = setup()
    line = add_line(sketch)
    assert scope.entity(line.entityToken, CURVE_TYPES) is line
    assert scope.point(line.startSketchPoint.entityToken) is line.startSketchPoint
    assert scope.entity({"ref": line.entityToken}["ref"]) is line


def test_token_of_another_sketch_is_foreign() -> None:
    design = fake_sketch_design()
    sketch = make_sketch(design)
    other = make_sketch(design, plane="XZ")
    ctx = Context(FakeApp(design))
    scope = BatchScope(ctx, sketch)
    foreign = add_line(other)
    error = invalid(scope.entity, foreign.entityToken, None, "curve")
    assert error.details["reason"] == "foreign_sketch_entity"
    assert error.details["field"] == "curve"
    assert scope.entity(foreign.entityToken, own=False) is foreign


def test_non_sketch_entity_is_foreign() -> None:
    ctx, _, scope = setup()
    plane = ctx.root.xYConstructionPlane
    error = invalid(scope.entity, plane.entityToken)
    assert error.details["reason"] == "foreign_sketch_entity"
    assert error.details["got"] == "ConstructionPlane"


def test_stale_token_is_entity_not_found() -> None:
    _, sketch, scope = setup()
    line = add_line(sketch)
    token = line.entityToken
    line.deleteMe()
    with pytest.raises(BridgeError) as info:
        scope.entity(token, field="curve")
    assert info.value.code is ErrorCode.ENTITY_NOT_FOUND
    assert info.value.details["field"] == "curve"


def test_invalid_but_findable_entity_is_not_found() -> None:
    ctx, sketch, scope = setup()
    line = add_line(sketch)
    ctx.design.by_token[line.entityToken] = line
    line._valid = False
    with pytest.raises(BridgeError) as info:
        scope.entity(line.entityToken)
    assert info.value.code is ErrorCode.ENTITY_NOT_FOUND


def test_point_coordinates_use_units_and_collect_warnings() -> None:
    _, sketch, scope = setup(units="cm")
    point = scope.point([1, 2])
    assert (point.x, point.y, point.z) == pytest.approx((1.0, 2.0, 0.0))
    point = scope.point({"model": [1, 2, 3]}, "items[0].at")
    assert (point.x, point.y, point.z) == pytest.approx((1.0, 2.0, 0.0))
    assert [w["field"] for w in scope.warnings] == ["items[0].at"]
    explicit = BatchScope(scope.ctx, sketch, units="mm")
    assert explicit.point(Xy(10, 0)).x == pytest.approx(1.0)


def test_position_of_sketch_point_and_coordinates() -> None:
    _, sketch, scope = setup()
    line = add_line(sketch, P(1, 2, 0), P(3, 2, 0))
    scope.register("a", line, {"end": line.endSketchPoint})
    end = scope.position("a.end")
    assert (end.x, end.y) == pytest.approx((3.0, 2.0))
    xy = scope.position([10, 20])
    assert (xy.x, xy.y) == pytest.approx((1.0, 2.0))


def test_string_coordinate_and_bad_spec() -> None:
    _, _, scope = setup()
    error = invalid(scope.point, ["1", 2], "p")
    assert error.details["reason"] == "string_coordinate"
    error = invalid(scope.entity, 42, None, "e")
    assert error.details["reason"] == "invalid_entity_spec"


def test_register_rejects_bad_and_duplicate_ids() -> None:
    _, sketch, scope = setup()
    line = add_line(sketch)
    scope.register("a", line)
    assert invalid(scope.register, "a", line).details["reason"] == "duplicate_id"
    assert invalid(scope.register, "origin", line).details["reason"] == "reserved_id"
    assert invalid(scope.register, "1x", line).details["reason"] == "invalid_local_id"


def test_declare_then_check_before_creation() -> None:
    _, sketch, scope = setup()
    scope.declare("a", "SketchLine", {"start": "SketchPoint", "end": "SketchPoint"})
    scope.check("a", LINE_TYPES, "items[1].line")
    scope.check_point("a.end", "items[1].start")
    scope.check_point([1, 2], "items[1].end")
    scope.check_point("origin")
    error = invalid(scope.check_point, "a", "items[1].start")
    assert error.details["reason"] == "wrong_type"
    error = invalid(scope.check, "a.mid")
    assert error.details["reason"] == "unknown_sub"
    assert error.details["available"] == ["start", "end"]
    error = invalid(scope.check, "b")
    assert error.details["reason"] == "unknown_local_id"
    assert invalid(scope.declare, "a").details["reason"] == "duplicate_id"
    # Declared but not created yet: entity() refuses a forward reference.
    assert invalid(scope.entity, "a.end").details["reason"] == "forward_reference"
    line = add_line(sketch)
    scope.register("a", line, {"start": line.startSketchPoint, "end": line.endSketchPoint})
    assert scope.entity("a.end") is line.endSketchPoint
    assert invalid(scope.register, "a", line).details["reason"] == "duplicate_id"
    assert list(scope.refs()) == ["a", "a.start", "a.end"]


def test_declare_names_only_and_untyped_check() -> None:
    _, _, scope = setup()
    scope.declare("r", None, ["bottom", "center"])
    scope.check("r.bottom", LINE_TYPES)
    scope.check_point("r.center")
    scope.declare(None)
    assert scope.refs() == {}


def test_check_resolves_tokens_fully() -> None:
    _, sketch, scope = setup()
    line = add_line(sketch)
    scope.check(line.entityToken, LINE_TYPES)
    error = invalid(scope.check_point, line.entityToken, "p")
    assert error.details["reason"] == "wrong_type"


def test_same_entity_by_token() -> None:
    _, sketch, _ = setup()
    line = add_line(sketch)

    class Wrapper:
        entityToken = line.entityToken

    assert same_entity(line, Wrapper())
    assert same_entity(line, line)
    assert not same_entity(line, add_line(sketch))
    assert not same_entity(object(), object())

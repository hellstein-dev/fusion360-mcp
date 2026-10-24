"""``sketch.*`` editing handlers (c05 D11; tasks 8.1-8.11): set_construction, delete,
transform, offset, trim, extend, fillet, mirror, pattern, project."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import sketch as h
from fusion_addin.sketch import edit
from fusion_addin.sketch.journal import SketchJournal
from fusion_addin.sketch.resolve import BatchScope
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import FakeConstructionPlane, fake_box_body
from tests.fakes.design import FakeApp
from tests.fakes.fusion_sketch import (
    P,
    auto_profiles,
    circle_curve,
    fake_sketch_design,
    make_sketch,
    polygon_curves,
    remove_optional_api,
)

adsk = fusion_addin.adsk
V = adsk.core.Vector3D.create
PLATE = [(0, 0), (10, 0), (10, 6), (0, 6)]  # cm: 100 x 60 mm

EDIT_METHODS = (
    "sketch.set_construction",
    "sketch.delete",
    "sketch.transform",
    "sketch.offset",
    "sketch.trim",
    "sketch.extend",
    "sketch.fillet",
    "sketch.mirror",
    "sketch.pattern",
    "sketch.project",
)


# --- helpers ---------------------------------------------------------------------------------


def run(fn: Any, design: Any, params: dict[str, Any]) -> Any:
    return fn(Context(FakeApp(design), params), params)


def error(fn: Any, design: Any, params: dict[str, Any]) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        run(fn, design, params)
    return info.value


def tok(entity: Any) -> str:
    return str(entity.entityToken)


def xy(point: Any) -> tuple[float, float]:
    g = point.geometry if hasattr(point, "geometry") else point
    return (round(g.x, 9) + 0.0, round(g.y, 9) + 0.0)


def ends(line: Any) -> tuple[tuple[float, float], tuple[float, float]]:
    return xy(line.startSketchPoint), xy(line.endSketchPoint)


def line(sketch: Any, a: tuple[float, float], b: tuple[float, float]) -> Any:
    return polygon_curves(sketch, [a, b], closed=False)[0]


def setup(design: Any = None) -> tuple[Any, Any]:
    design = design if design is not None else fake_sketch_design()
    return design, make_sketch(design)


def base(sketch: Any, **params: Any) -> dict[str, Any]:
    return {"sketch": tok(sketch), **params}


def resolved(design: Any, ref: dict[str, Any]) -> Any:
    return design.by_token[ref["ref"]]


def scope_of(design: Any, sketch: Any) -> BatchScope:
    return BatchScope(Context(FakeApp(design), {}), sketch)


def test_registration() -> None:
    for name in EDIT_METHODS:
        spec = REGISTRY.get(name)
        assert spec is not None, name
        assert spec.mutating is True, name


# --- 8.1 set_construction --------------------------------------------------------------------


def test_set_construction_merges_profiles() -> None:
    design, sketch = setup()
    outer = polygon_curves(sketch, PLATE)
    divider = line(sketch, (5, 0), (5, 6))
    sketch.profile_builder = auto_profiles
    # The divider shares no points with the outline: the fake sees one outer profile.
    hole = circle_curve(sketch, (2, 3), 0.5)
    assert len(list(sketch.profiles)) == 2
    out = run(h.sketch_set_construction, design, base(sketch, curves=[tok(hole), tok(divider)]))
    assert hole.isConstruction and divider.isConstruction
    assert not any(c.isConstruction for c in outer)
    assert [c["construction"] for c in out["curves"]] == [True, True]
    assert [c["ref"]["ref"] for c in out["curves"]] == [tok(hole), tok(divider)]
    assert len(out["profiles"]) == 1
    assert "status" in out
    back = run(
        h.sketch_set_construction,
        design,
        base(sketch, curves=[tok(hole)], construction=False, return_profiles=False),
    )
    assert not hole.isConstruction and "profiles" not in back


def test_set_construction_restores_old_values_on_failure() -> None:
    design, sketch = setup()
    a = line(sketch, (0, 0), (1, 0))
    b = line(sketch, (0, 1), (1, 1))
    b.isConstruction = True
    scope = scope_of(design, sketch)
    with pytest.raises(BridgeError), SketchJournal(sketch) as journal:
        edit.set_construction(scope, journal, [tok(a), tok(b)], True)
        assert a.isConstruction
        raise RuntimeError("later failure")
    assert not a.isConstruction and b.isConstruction


def test_set_construction_rejects_points_and_unknown_refs() -> None:
    design, sketch = setup()
    a = line(sketch, (0, 0), (1, 0))
    err = error(
        h.sketch_set_construction, design, base(sketch, curves=[tok(a), tok(a.startSketchPoint)])
    )
    assert err.code is ErrorCode.INVALID_PARAMS
    assert err.details["reason"] == "wrong_type"
    assert not a.isConstruction
    err = error(h.sketch_set_construction, design, base(sketch, curves=[]))
    assert err.code is ErrorCode.INVALID_PARAMS


# --- 8.2 delete ------------------------------------------------------------------------------


def test_delete_line_and_its_constraint_reports_cascade() -> None:
    design, sketch = setup()
    a = line(sketch, (0, 0), (2, 0))
    keep = line(sketch, (0, 1), (2, 1))
    horizontal = sketch.geometricConstraints.addHorizontal(a)
    other = sketch.geometricConstraints.addHorizontal(keep)
    sketch.reset_calls()
    out = run(h.sketch_delete, design, base(sketch, entities=[tok(horizontal), tok(a)]))
    assert [r["ref"] for r in out["deleted"]] == [tok(a)]
    assert [r["ref"] for r in out["cascaded"]] == [tok(horizontal)]
    assert out["cascaded"][0]["type"] == "HorizontalConstraint"
    assert not a.isValid and not horizontal.isValid
    assert keep.isValid and other.isValid
    assert "status" in out and "profiles" in out


def test_delete_order_dimensions_constraints_texts_curves_points() -> None:
    design, sketch = setup()
    a = line(sketch, (0, 0), (2, 0))
    b = line(sketch, (0, 1), (2, 1))
    lone = sketch.sketchPoints.add(P(5, 5, 0))
    dim = sketch.sketchDimensions.addDistanceDimension(
        b.startSketchPoint, b.endSketchPoint, 0, P(1, 2, 0)
    )
    vertical = sketch.geometricConstraints.addHorizontal(b)
    sketch.reset_calls()
    specs = [tok(lone), tok(a), tok(vertical), tok(dim)]
    out = run(h.sketch_delete, design, base(sketch, entities=specs))
    order = [args[0] for name, args in sketch.calls if name == "deleteMe"]
    assert order == [dim, vertical, a, lone]
    assert {r["ref"] for r in out["deleted"]} == set(specs)
    assert out["cascaded"] == []


def test_delete_stale_ref_deletes_nothing() -> None:
    design, sketch = setup()
    a = line(sketch, (0, 0), (2, 0))
    gone = line(sketch, (0, 1), (2, 1))
    stale = tok(gone)
    gone.deleteMe()
    sketch.reset_calls()
    err = error(h.sketch_delete, design, base(sketch, entities=[tok(a), stale]))
    assert err.code is ErrorCode.ENTITY_NOT_FOUND
    assert a.isValid
    assert "deleteMe" not in sketch.call_names()


def test_delete_rejects_origin_and_reports_partial_failure() -> None:
    design, sketch = setup()
    err = error(h.sketch_delete, design, base(sketch, entities=["origin"]))
    assert err.code is ErrorCode.INVALID_PARAMS
    a = line(sketch, (0, 0), (2, 0))
    b = line(sketch, (0, 1), (2, 1))
    b.fail_delete = "locked"
    err = error(h.sketch_delete, design, base(sketch, entities=[tok(a), tok(b)]))
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert [r["ref"] for r in err.details["deleted"]] == [tok(a)]
    assert err.details["failed"]["ref"] == tok(b)
    assert not a.isValid and b.isValid


# --- 8.3 transform ---------------------------------------------------------------------------


def test_transform_copy_with_offset() -> None:
    design, sketch = setup()
    circle = circle_curve(sketch, (1, 1), 0.5)
    out = run(
        h.sketch_transform,
        design,
        base(sketch, entities=[tok(circle)], translate=[30, 0], copy=True),
    )
    assert len(out["created"]) == 1
    created = out["created"][0]
    assert created["type"] == "circle"
    assert created["geometry"]["center"] == [40.0, 10.0]
    assert xy(circle.centerSketchPoint) == (1.0, 1.0)
    assert "Sketch.copy" in sketch.call_names()
    assert "entities" not in out


def test_transform_move_rotates_then_translates() -> None:
    design, sketch = setup()
    a = line(sketch, (2, 0), (3, 0))
    out = run(
        h.sketch_transform,
        design,
        base(sketch, entities=[tok(a)], rotate=90, about=[10, 0], translate=[0, 5]),
    )
    start, end = ends(a)
    assert start == pytest.approx((1.0, 1.5)) and end == pytest.approx((1.0, 2.5))
    geometry = out["entities"][0]["geometry"]
    assert geometry["start"] == pytest.approx([10.0, 15.0])
    assert "Sketch.move" in sketch.call_names()
    # Expression angle and a sketch point as pivot.
    run(
        h.sketch_transform,
        design,
        base(sketch, entities=[tok(a)], rotate="90 deg", about=tok(a.startSketchPoint)),
    )
    start, end = ends(a)
    assert start == pytest.approx((1.0, 1.5)) and end == pytest.approx((0.0, 1.5))


def test_transform_move_is_undone_by_the_inverse_matrix() -> None:
    design, sketch = setup()
    a = line(sketch, (2, 0), (3, 0))
    scope = scope_of(design, sketch)
    with pytest.raises(BridgeError), SketchJournal(sketch) as journal:
        edit.transform(scope, journal, [tok(a)], (1.0, 2.0), math.pi / 3, P(0, 0, 0), False)
        assert ends(a) != ((2.0, 0.0), (3.0, 0.0))
        raise RuntimeError("later failure")
    start, end = ends(a)
    assert start == pytest.approx((2.0, 0.0)) and end == pytest.approx((3.0, 0.0))
    assert sketch.call_names().count("Sketch.move") == 2


def test_transform_rejects_origin_and_bad_translate() -> None:
    design, sketch = setup()
    a = line(sketch, (2, 0), (3, 0))
    err = error(h.sketch_transform, design, base(sketch, entities=["origin"], translate=[1, 0]))
    assert err.code is ErrorCode.INVALID_PARAMS
    err = error(h.sketch_transform, design, base(sketch, entities=[tok(a)], translate=["1", 0]))
    assert err.code is ErrorCode.INVALID_PARAMS
    assert "Sketch.move" not in sketch.call_names()


# --- 8.4 offset ------------------------------------------------------------------------------


def test_parametric_offset_of_a_rectangle_chain() -> None:
    design, sketch = setup()
    design.parameter_set.add_user("wall", "2 mm", "mm", "")
    rect = polygon_curves(sketch, PLATE)
    out = run(
        h.sketch_offset,
        design,
        base(sketch, curves=[tok(rect[0])], distance="wall", side_point=[50, 30]),
    )
    assert out["parametric"] is True
    assert len(out["created"]) == 4
    assert out["dimension"]["expression"] == "wall"
    created = [resolved(design, r) for r in out["created"]]
    assert sorted(xy(c.startSketchPoint) for c in created)[0] == pytest.approx((0.2, 0.2))
    names = sketch.call_names()
    assert "Sketch.findConnectedCurves" in names
    assert "geometricConstraints.addOffset2" in names
    assert "Sketch.offset" not in names


def test_fallback_offset_uses_evaluated_distance_in_cm() -> None:
    design, sketch = setup()
    remove_optional_api(sketch)
    rect = polygon_curves(sketch, PLATE)
    out = run(
        h.sketch_offset,
        design,
        base(sketch, curves=[tok(rect[0])], distance=2, side_point=[50, -30], chain=False),
    )
    assert out["parametric"] is False and "dimension" not in out
    assert len(out["created"]) == 1
    _, (curves, _side, distance) = next(c for c in sketch.calls if c[0] == "Sketch.offset")
    assert distance == pytest.approx(0.2)
    assert list(curves) == [rect[0]]
    assert "Sketch.findConnectedCurves" not in sketch.call_names()
    piece = resolved(design, out["created"][0])
    assert ends(piece) == ((0.0, -0.2), (10.0, -0.2))


def test_offset_invalid_expression_creates_nothing() -> None:
    design, sketch = setup()
    rect = polygon_curves(sketch, PLATE)
    sketch.reset_calls()
    err = error(
        h.sketch_offset,
        design,
        base(sketch, curves=[tok(rect[0])], distance="nope * 2", side_point=[50, 30]),
    )
    assert err.code is ErrorCode.INVALID_PARAMS
    assert err.details["reason"] == "invalid_expression"
    assert sketch.call_names() == []


def test_offset_failure_after_creation_rolls_back_created_curves() -> None:
    design, sketch = setup()
    rect = polygon_curves(sketch, PLATE)
    count = sketch.sketchCurves.count

    def broken_profiles(_sketch: Any) -> list[Any]:
        raise RuntimeError("profile computation failed")

    # The answer's profiles are computed inside the journal: their failure undoes the edit.
    sketch.profile_builder = broken_profiles
    err = error(
        h.sketch_offset, design, base(sketch, curves=[tok(rect[0])], distance=1, side_point=[1, 1])
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert "geometricConstraints.addOffset2" in sketch.call_names()
    assert sketch.sketchCurves.count == count
    assert sketch.geometricConstraints.count == 0


# --- 8.5 trim / 8.6 extend -------------------------------------------------------------------


def test_trim_line_inside_circle() -> None:
    design, sketch = setup()
    crossing = line(sketch, (-2, 0), (2, 0))
    circle_curve(sketch, (0, 0), 1)
    out = run(h.sketch_trim, design, base(sketch, curve=tok(crossing), point=[0, 0]))
    assert [r["ref"] for r in out["deleted"]] == [tok(crossing)]
    pieces = [resolved(design, r) for r in out["created"]]
    assert sorted(ends(p) for p in pieces) == [
        ((-2.0, 0.0), (-1.0, 0.0)),
        ((1.0, 0.0), (2.0, 0.0)),
    ]
    name, (curve, point, constraints) = next(c for c in sketch.calls if c[0].endswith("trim"))
    assert (name, curve, constraints) == ("SketchLine.trim", crossing, True)
    assert xy(point) == (0.0, 0.0)


def test_trim_point_too_far_leaves_sketch_unchanged() -> None:
    design, sketch = setup()
    crossing = line(sketch, (-2, 0), (2, 0))
    circle_curve(sketch, (0, 0), 1)
    err = error(h.sketch_trim, design, base(sketch, curve=tok(crossing), point=[0, 20]))
    assert err.code is ErrorCode.INVALID_PARAMS
    assert err.details["reason"] == "point_too_far"
    assert err.details["distance_mm"] == pytest.approx(20.0)
    assert crossing.isValid
    assert not any(n.endswith("trim") for n in sketch.call_names())


def test_extend_line_to_wall() -> None:
    design, sketch = setup()
    short = line(sketch, (10, 0), (11, 0))
    line(sketch, (13, -1), (13, 1))
    out = run(h.sketch_extend, design, base(sketch, curve=tok(short), point=[109, 1]))
    assert [r["ref"] for r in out["deleted"]] == [tok(short)]
    (longer,) = [resolved(design, r) for r in out["created"]]
    assert ends(longer) == ((10.0, 0.0), (13.0, 0.0))
    assert "SketchLine.extend" in sketch.call_names()


def test_extend_without_boundary_and_closed_curve() -> None:
    design, sketch = setup()
    short = line(sketch, (10, 0), (11, 0))
    err = error(h.sketch_extend, design, base(sketch, curve=tok(short), point=[110, 0]))
    assert err.code is ErrorCode.INVALID_PARAMS and err.details["reason"] == "no_boundary"
    assert short.isValid
    circle = circle_curve(sketch, (0, 0), 1)
    err = error(h.sketch_extend, design, base(sketch, curve=tok(circle), point=[10, 0]))
    assert err.code is ErrorCode.INVALID_PARAMS and err.details["reason"] == "wrong_type"


# --- 8.7 fillet ------------------------------------------------------------------------------


def corner(sketch: Any) -> tuple[Any, Any]:
    one, two = polygon_curves(sketch, [(2, 0), (0, 0), (0, 2)], closed=False)
    return one, two


def test_fillet_with_parameter_radius() -> None:
    design, sketch = setup()
    design.parameter_set.add_user("r_corner", "5 mm", "mm", "")
    one, two = corner(sketch)
    out = run(
        h.sketch_fillet,
        design,
        base(sketch, curve1=tok(one), curve2=tok(two), radius="r_corner"),
    )
    arc = resolved(design, out["arc"])
    assert arc.radius == pytest.approx(0.5)
    assert xy(arc.centerSketchPoint) == pytest.approx((0.5, 0.5))
    assert out["dimension"]["expression"] == "r_corner"
    assert out["dimension"]["type"] == "radial"
    _, (c1, p1, c2, p2, radius) = next(c for c in sketch.calls if c[0].endswith("addFillet"))
    assert (c1, c2, radius) == (one, two, pytest.approx(0.5))
    # Default pick points: on each line, near the shared corner.
    assert xy(p1) == pytest.approx((0.2, 0.0)) and xy(p2) == pytest.approx((0.0, 0.2))
    assert "sketchDimensions.addRadialDimension" in sketch.call_names()


def test_fillet_nearest_ends_explicit_points_and_no_dimension() -> None:
    design, sketch = setup()
    one = line(sketch, (2, 0), (0.1, 0))
    two = line(sketch, (0, 0.1), (0, 2))
    out = run(
        h.sketch_fillet,
        design,
        base(sketch, curve1=tok(one), curve2=tok(two), radius=3, dimension=False),
    )
    assert "dimension" not in out
    assert resolved(design, out["arc"]).radius == pytest.approx(0.3)
    assert "sketchDimensions.addRadialDimension" not in sketch.call_names()

    _, sketch2 = setup(design)
    a, b = corner(sketch2)
    run(
        h.sketch_fillet,
        design,
        base(sketch2, curve1=tok(a), curve2=tok(b), radius=2, point1=[10, 0], point2=[0, 10]),
    )
    _, (_, p1, _, p2, _) = next(c for c in sketch2.calls if c[0].endswith("addFillet"))
    assert xy(p1) == (1.0, 0.0) and xy(p2) == (0.0, 1.0)


def test_fillet_dimension_failure_keeps_fillet_with_warning() -> None:
    design, sketch = setup()
    one, two = corner(sketch)
    sketch.fail_on("addRadialDimension")
    out = run(h.sketch_fillet, design, base(sketch, curve1=tok(one), curve2=tok(two), radius=5))
    assert resolved(design, out["arc"]).isValid
    assert "dimension" not in out
    assert out["warnings"][0]["code"] == "fillet_dimension_failed"


def test_fillet_validation_happens_before_the_fillet() -> None:
    design, sketch = setup()
    one, two = corner(sketch)
    circle = circle_curve(sketch, (5, 5), 1)
    for params, reason in (
        ({"curve1": tok(one), "curve2": tok(one), "radius": 5}, "same_entity"),
        ({"curve1": tok(one), "curve2": tok(two), "radius": "nope"}, "invalid_expression"),
        ({"curve1": tok(one), "curve2": tok(two), "radius": -1}, "not_positive"),
        ({"curve1": tok(one), "curve2": tok(circle), "radius": 1}, "wrong_type"),
    ):
        err = error(h.sketch_fillet, design, base(sketch, **params))
        assert err.code is ErrorCode.INVALID_PARAMS
        assert err.details["reason"] == reason
    assert not any(n.endswith("addFillet") for n in sketch.call_names())


# --- 8.8 mirror ------------------------------------------------------------------------------


def test_mirror_l_shaped_polyline() -> None:
    design, sketch = setup()
    axis = line(sketch, (0, -1), (0, 3))
    axis.isConstruction = True
    l1, l2 = polygon_curves(sketch, [(1, 0), (1, 2), (2, 2)], closed=False)
    out = run(h.sketch_mirror, design, base(sketch, entities=[tok(l1), tok(l2)], axis=tok(axis)))
    m1 = resolved(design, out["mapping"][tok(l1)])
    m2 = resolved(design, out["mapping"][tok(l2)])
    assert ends(m1) == ((-1.0, 0.0), (-1.0, 2.0))
    assert ends(m2) == ((-1.0, 2.0), (-2.0, 2.0))
    assert m1.endSketchPoint is m2.startSketchPoint  # the shared point stays shared
    symmetry = [c for c in out["constraints"] if c["type"] == "symmetry"]
    assert len(symmetry) == 3
    pairs = {
        (xy(c.entityOne), xy(c.entityTwo)) for c in (resolved(design, s["ref"]) for s in symmetry)
    }
    assert pairs == {
        ((1.0, 0.0), (-1.0, 0.0)),
        ((1.0, 2.0), (-1.0, 2.0)),
        ((2.0, 2.0), (-2.0, 2.0)),
    }
    assert all(resolved(design, s["ref"]).symmetryLine is axis for s in symmetry)


def test_mirror_reuses_points_on_the_axis_and_other_kinds() -> None:
    design, sketch = setup()
    axis = line(sketch, (0, -5), (0, 5))
    touching = line(sketch, (0, 1), (1, 1))
    circle = circle_curve(sketch, (2, 0), 0.5)
    circle.isConstruction = True
    arc = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(P(2, 3, 0), P(3, 3, 0), math.pi / 2)
    spline = sketch.sketchCurves.sketchFittedSplines.add(
        adsk.core.ObjectCollection.createWithArray([P(1, -1, 0), P(2, -2, 0), P(3, -1, 0)])
    )
    dot = sketch.sketchPoints.add(P(4, 4, 0))
    specs = [tok(touching), tok(circle), tok(arc), tok(spline), tok(dot)]
    out = run(h.sketch_mirror, design, base(sketch, entities=specs, axis=tok(axis)))
    mirrored = {k: resolved(design, v) for k, v in out["mapping"].items()}
    assert mirrored[tok(touching)].startSketchPoint is touching.startSketchPoint
    assert xy(mirrored[tok(touching)].endSketchPoint) == (-1.0, 1.0)
    new_circle = mirrored[tok(circle)]
    assert xy(new_circle.centerSketchPoint) == (-2.0, 0.0)
    assert new_circle.radius == pytest.approx(0.5) and new_circle.isConstruction
    new_arc = mirrored[tok(arc)]
    assert xy(new_arc.centerSketchPoint) == pytest.approx((-2.0, 3.0))
    assert {xy(new_arc.startSketchPoint), xy(new_arc.endSketchPoint)} == {(-3.0, 3.0), (-2.0, 4.0)}
    new_spline = mirrored[tok(spline)]
    assert [xy(p) for p in new_spline.fitPoints] == [(-1.0, -1.0), (-2.0, -2.0), (-3.0, -1.0)]
    assert xy(mirrored[tok(dot)]) == (-4.0, 4.0)
    types = [c["type"] for c in out["constraints"]]
    # touching: 1 (its axis end is reused), circle centre 1, arc ends 2, spline 3, point 1.
    assert types.count("symmetry") == 8
    assert types.count("equal") == 2


def test_mirror_without_constraints_and_unsupported() -> None:
    design, sketch = setup()
    axis = line(sketch, (0, -5), (0, 5))
    a = line(sketch, (1, 0), (2, 0))
    out = run(
        h.sketch_mirror,
        design,
        base(sketch, entities=[tok(a)], axis=tok(axis), constrain=False),
    )
    assert out["constraints"] == []
    count = sketch.sketchCurves.count
    ellipse = sketch.sketchCurves.sketchEllipses.add(P(5, 0, 0), P(7, 0, 0), P(5, 1, 0))
    err = error(
        h.sketch_mirror, design, base(sketch, entities=[tok(a), tok(ellipse)], axis=tok(axis))
    )
    assert err.code is ErrorCode.INVALID_PARAMS and err.details["reason"] == "unsupported"
    assert sketch.sketchCurves.count == count + 1  # nothing mirrored


def test_mirror_failure_rolls_back() -> None:
    design, sketch = setup()
    axis = line(sketch, (0, -5), (0, 5))
    a = line(sketch, (1, 0), (2, 0))
    count = sketch.sketchCurves.count
    sketch.fail_on("addSymmetry", 2)
    err = error(h.sketch_mirror, design, base(sketch, entities=[tok(a)], axis=tok(axis)))
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert sketch.sketchCurves.count == count
    assert sketch.geometricConstraints.count == 0


# --- 8.9 rectangular pattern -----------------------------------------------------------------


def centers(design: Any, instances: list[list[dict[str, Any]]]) -> list[tuple[float, float]]:
    return [xy(resolved(design, group[0]).centerSketchPoint) for group in instances]


def flat(points: list[tuple[float, float]]) -> list[float]:
    return [c for p in points for c in p]


def test_rectangular_pattern_parametric() -> None:
    design, sketch = setup()
    hole = circle_curve(sketch, (0, 0), 0.2)
    out = run(
        h.sketch_pattern,
        design,
        base(
            sketch,
            entities=[tok(hole)],
            kind="rectangular",
            count_x=3,
            spacing_x=10,
            count_y=2,
            spacing_y="5 mm",
        ),
    )
    assert out["parametric"] is True
    assert out["constraint"]["type"] == "RectangularPatternConstraint"
    assert len(out["instances"]) == 6
    assert out["instances"][0][0]["ref"] == tok(hole)
    assert sorted(centers(design, out["instances"])) == [
        (0.0, 0.0),
        (0.0, 0.5),
        (1.0, 0.0),
        (1.0, 0.5),
        (2.0, 0.0),
        (2.0, 0.5),
    ]
    constraint = resolved(design, out["constraint"])
    assert constraint.distanceOne.expression == "10 mm"
    assert "Sketch.copy" not in sketch.call_names()


def test_rectangular_pattern_with_direction_line() -> None:
    design, sketch = setup()
    hole = circle_curve(sketch, (0, 0), 0.2)
    diagonal = line(sketch, (0, 0), (3, 4))
    out = run(
        h.sketch_pattern,
        design,
        base(
            sketch,
            entities=[tok(hole)],
            kind="rectangular",
            count_x=2,
            spacing_x=10,
            direction=tok(diagonal),
        ),
    )
    assert out["parametric"] is True
    assert centers(design, out["instances"])[1] == pytest.approx((0.6, 0.8))


def test_rectangular_pattern_copy_fallback() -> None:
    design, sketch = setup()
    remove_optional_api(sketch)
    hole = circle_curve(sketch, (0, 0), 0.2)
    out = run(
        h.sketch_pattern,
        design,
        base(sketch, entities=[tok(hole)], kind="rectangular", count_x=3, spacing_x=10),
    )
    assert out["parametric"] is False and "constraint" not in out
    assert centers(design, out["instances"]) == [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
    copies = [args for name, args in sketch.calls if name == "Sketch.copy"]
    assert len(copies) == 2
    assert copies[1][1].translation.x == pytest.approx(2.0)


def test_rectangular_pattern_angle_without_direction_copies_with_warning() -> None:
    design, sketch = setup()
    hole = circle_curve(sketch, (0, 0), 0.2)
    out = run(
        h.sketch_pattern,
        design,
        base(sketch, entities=[tok(hole)], kind="rectangular", count_x=2, spacing_x=10, angle=90),
    )
    assert out["parametric"] is False
    assert out["warnings"][0]["code"] == "pattern_not_parametric"
    assert centers(design, out["instances"])[1] == pytest.approx((0.0, 1.0))


def test_rectangular_pattern_validation() -> None:
    design, sketch = setup()
    hole = circle_curve(sketch, (0, 0), 0.2)
    diagonal = line(sketch, (0, 0), (3, 4))
    cases: list[dict[str, Any]] = [
        {"count_x": 1, "spacing_x": 10},
        {"count_x": 2, "spacing_x": 10, "angle": 30, "direction": tok(diagonal)},
        {"count_x": 2, "spacing_x": "nope"},
        {"count_x": 2, "spacing_x": 10, "count": 3},
    ]
    for case in cases:
        params = base(sketch, entities=[tok(hole)], kind="rectangular", **case)
        assert error(h.sketch_pattern, design, params).code is ErrorCode.INVALID_PARAMS
    assert sketch.geometricConstraints.count == 0


# --- 8.10 circular pattern -------------------------------------------------------------------


def test_circular_hole_pattern_parametric_about_origin() -> None:
    design, sketch = setup()
    hole = circle_curve(sketch, (1, 0), 0.1)
    out = run(
        h.sketch_pattern,
        design,
        base(sketch, entities=[tok(hole)], kind="circular", center="origin", count=6),
    )
    assert out["parametric"] is True
    assert len(out["instances"]) == 6
    constraint = resolved(design, out["constraint"])
    assert constraint.centerPoint is sketch.originPoint
    assert constraint.totalAngle.expression == "360 deg"
    assert centers(design, out["instances"])[1] == pytest.approx((0.5, math.sqrt(3) / 2))


def test_circular_pattern_coordinate_center_becomes_a_point() -> None:
    design, sketch = setup()
    hole = circle_curve(sketch, (1, 0), 0.1)
    out = run(
        h.sketch_pattern,
        design,
        base(sketch, entities=[tok(hole)], kind="circular", center=[20, 0], count=2),
    )
    constraint = resolved(design, out["constraint"])
    assert xy(constraint.centerPoint) == (2.0, 0.0)
    assert "sketchPoints.add" in sketch.call_names()
    assert centers(design, out["instances"])[1] == pytest.approx((3.0, 0.0))


def test_circular_pattern_copy_fallback_with_rotation() -> None:
    design, sketch = setup()
    remove_optional_api(sketch)
    hole = circle_curve(sketch, (1, 0), 0.1)
    out = run(
        h.sketch_pattern,
        design,
        base(sketch, entities=[tok(hole)], kind="circular", center=[0, 0], count=4),
    )
    assert out["parametric"] is False
    assert flat(centers(design, out["instances"])) == pytest.approx(
        flat([(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)])
    )
    assert "sketchPoints.add" not in sketch.call_names()
    partial = run(
        h.sketch_pattern,
        design,
        base(sketch, entities=[tok(hole)], kind="circular", center="origin", count=3, angle=90),
    )
    assert flat(centers(design, partial["instances"])) == pytest.approx(
        flat([(1.0, 0.0), (math.sqrt(0.5), math.sqrt(0.5)), (0.0, 1.0)])
    )


def test_circular_pattern_failure_rolls_back_copies() -> None:
    design, sketch = setup()
    remove_optional_api(sketch)
    hole = circle_curve(sketch, (1, 0), 0.1)
    count = sketch.sketchCurves.count
    sketch.fail_on("Sketch.copy", 3)
    err = error(
        h.sketch_pattern,
        design,
        base(sketch, entities=[tok(hole)], kind="circular", center="origin", count=6),
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert sketch.sketchCurves.count == count


# --- 8.11 project ----------------------------------------------------------------------------


def box_design() -> tuple[Any, Any]:
    body = fake_box_body(2.0, 1.0, 3.0, token="b")
    return fake_sketch_design(body), body


def test_project_a_face() -> None:
    design, _body = box_design()
    sketch = make_sketch(design)
    face = design.by_token["b-face+z"]
    out = run(h.sketch_project, design, base(sketch, entities=["b-face+z"]))
    refs = out["created"]["b-face+z"]
    assert len(refs) == 4
    curves = [resolved(design, r) for r in refs]
    assert all(c.isReference and c.referencedEntity is face for c in curves)
    assert "status" in out and "profiles" in out
    assert sketch.call_names() == ["Sketch.project"]


def test_project_body_falls_back_to_its_edges() -> None:
    design, body = box_design()
    sketch = make_sketch(design)
    out = run(h.sketch_project, design, base(sketch, entities=[tok(body)]))
    assert len(out["created"][tok(body)]) == 12
    names = sketch.call_names()
    assert names[0] == "Sketch.project" and names.count("Sketch.project") == 13
    sketch2 = make_sketch(design)
    sketch2.allow_project_body = True
    out = run(h.sketch_project, design, base(sketch2, entities=[tok(body)]))
    assert len(out["created"][tok(body)]) == 12
    assert sketch2.call_names() == ["Sketch.project"]


def test_include_intersect_and_cut_edges() -> None:
    design, body = box_design()
    sketch = make_sketch(design)
    out = run(h.sketch_project, design, base(sketch, entities=["b-e000100"], mode="include"))
    (included,) = [resolved(design, r) for r in out["created"]["b-e000100"]]
    assert included.isLinked
    plane = FakeConstructionPlane("mid", P(0, 0, 1.5), V(0, 0, 1))
    mid = design.rootComponent.sketches.addWithoutEdges(plane)
    out = run(h.sketch_project, design, base(mid, entities=[tok(body)], mode="intersect"))
    assert len(out["created"][tok(body)]) == 4
    name, (collection,) = mid.calls[-1]
    assert name == "Sketch.intersectWithSketchPlane" and collection.asArray() == [body]
    out = run(h.sketch_project, design, base(mid, entities=[tok(body)], mode="cut_edges"))
    assert len(out["created"][tok(body)]) == 4
    err = error(h.sketch_project, design, base(mid, entities=["b-face+z"], mode="cut_edges"))
    assert err.code is ErrorCode.INVALID_PARAMS and err.details["reason"] == "not_a_body"


def test_project_validation_and_rollback() -> None:
    design, _body = box_design()
    sketch = make_sketch(design)
    own = line(sketch, (0, 0), (1, 0))
    err = error(h.sketch_project, design, base(sketch, entities=[tok(own)]))
    assert err.code is ErrorCode.INVALID_PARAMS and err.details["reason"] == "same_sketch"
    err = error(h.sketch_project, design, base(sketch, entities=["b-face+z"], mode="flatten"))
    assert err.code is ErrorCode.INVALID_PARAMS
    count = sketch.sketchCurves.count
    sketch.fail_on("Sketch.project", 2)
    err = error(h.sketch_project, design, base(sketch, entities=["b-face+z", "b-face-z"]))
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert err.details["field"] == "entities[1]"
    assert sketch.sketchCurves.count == count

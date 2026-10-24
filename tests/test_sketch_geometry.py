"""``fusion_addin/sketch/geometry.py``: draw-item builders (c05 D7, tasks 5.1-5.9)."""

import math
from typing import Any

import pytest

from fusion_addin.core.context import Context
from fusion_addin.sketch.geometry import (
    GROUP_TYPE,
    DrawnItem,
    build_items,
    default_text_size,
    validate_items,
)
from fusion_addin.sketch.journal import SketchJournal
from fusion_addin.sketch.resolve import BatchScope
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.design import FakeApp
from tests.fakes.fusion_sketch import fake_sketch_design, make_sketch

adsk = __import__("fusion_addin").adsk


def setup(units: str | None = None) -> tuple[Any, BatchScope]:
    design = fake_sketch_design()
    sketch = make_sketch(design)
    ctx = Context(FakeApp(design), {"units": units} if units else {})
    return sketch, BatchScope(ctx, sketch)


def draw(sketch: Any, scope: BatchScope, *items: dict[str, Any]) -> list[DrawnItem]:
    specs = validate_items(scope, list(items))
    with SketchJournal(sketch) as journal:
        journal.defer()
        return build_items(scope, journal, specs)


def mm(point: Any) -> tuple[float, float]:
    p = point.geometry if hasattr(point, "geometry") else point
    return (round(p.x * 10, 6) + 0.0, round(p.y * 10, 6) + 0.0)


def calls(sketch: Any, name: str) -> list[tuple[Any, ...]]:
    return [args for n, args in sketch.calls if n == name]


def constraint_kinds(sketch: Any) -> list[str]:
    return [c.type_name for c in sketch.geometricConstraints]


def fails(sketch: Any, scope: BatchScope, *items: dict[str, Any]) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        draw(sketch, scope, *items)
    return info.value


# --- 5.1 point, line, polyline, construction ---------------------------------------------


def test_point_item() -> None:
    sketch, scope = setup()
    (drawn,) = draw(sketch, scope, {"kind": "point", "id": "p", "at": [10, 20]})
    assert mm(drawn.entity) == (10.0, 20.0)
    assert calls(sketch, "sketchPoints.add")
    assert scope.refs()["p"]["type"] == "SketchPoint"


def test_point_on_existing_point_is_that_point() -> None:
    sketch, scope = setup()
    (drawn,) = draw(sketch, scope, {"kind": "point", "id": "p", "at": "origin"})
    assert drawn.entity is sketch.originPoint
    assert sketch.calls == []


def test_lines_share_endpoint_by_local_id() -> None:
    sketch, scope = setup()
    a, b = draw(
        sketch,
        scope,
        {"kind": "line", "id": "a", "start": [0, 0], "end": [40, 0]},
        {"kind": "line", "id": "b", "start": "a.end", "end": [40, 30]},
    )
    assert b.entity.startSketchPoint is a.entity.endSketchPoint
    assert list(scope.refs()) == ["a", "a.start", "a.end", "b", "b.start", "b.end"]
    assert mm(a.entity.endSketchPoint) == (40.0, 0.0)
    assert sketch.sketchPoints.count == 1 + 3


def test_construction_flag_and_units() -> None:
    sketch, scope = setup(units="cm")
    (drawn,) = draw(
        sketch, scope, {"kind": "line", "start": [0, 0], "end": [4, 0], "construction": True}
    )
    assert drawn.entity.isConstruction is True
    assert drawn.entity.length == pytest.approx(4.0)  # 4 cm


def test_open_polyline() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch, scope, {"kind": "polyline", "id": "p", "points": [[0, 0], [10, 0], [10, 10]]}
    )
    assert drawn.entity is None
    assert set(drawn.subs) == {"e0", "e1", "v0", "v1", "v2"}
    assert drawn.subs["e1"].startSketchPoint is drawn.subs["e0"].endSketchPoint
    assert drawn.subs["v1"] is drawn.subs["e0"].endSketchPoint
    assert "p" not in scope.refs() and "p.e0" in scope.refs()


def test_closed_polyline_reuses_first_point() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch,
        scope,
        {
            "kind": "polyline",
            "id": "p",
            "closed": True,
            "points": [[0, 0], [10, 0], [10, 10], [0, 10]],
        },
    )
    edges = [drawn.subs[f"e{k}"] for k in range(4)]
    assert sketch.sketchCurves.sketchLines.count == 4
    assert edges[3].endSketchPoint is edges[0].startSketchPoint
    assert [mm(drawn.subs[f"v{k}"]) for k in range(4)] == [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert sketch.sketchPoints.count == 1 + 4


def test_polyline_from_existing_point() -> None:
    sketch, scope = setup()
    (drawn,) = draw(sketch, scope, {"kind": "polyline", "points": ["origin", [10, 0], [10, 5]]})
    assert drawn.subs["v0"] is sketch.originPoint


# --- 5.2 rectangle two_point / three_point -----------------------------------------------


def test_two_point_rectangle_names_by_geometry() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch, scope, {"kind": "rectangle", "id": "r", "corner1": [0, 30], "corner2": [50, 0]}
    )
    s = drawn.subs
    assert {mm(s["bottom"].startSketchPoint)[1], mm(s["bottom"].endSketchPoint)[1]} == {0.0}
    assert {mm(s["top"].startSketchPoint)[1], mm(s["top"].endSketchPoint)[1]} == {30.0}
    assert {mm(s["left"].startSketchPoint)[0], mm(s["left"].endSketchPoint)[0]} == {0.0}
    assert {mm(s["right"].startSketchPoint)[0], mm(s["right"].endSketchPoint)[0]} == {50.0}
    assert [mm(s[c]) for c in ("bl", "br", "tr", "tl")] == [(0, 0), (50, 0), (50, 30), (0, 30)]
    # Fusion's own horizontal/vertical constraints are kept, nothing added.
    assert not calls(sketch, "geometricConstraints.addHorizontal")
    assert list(scope.refs())[:5] == ["r.bottom", "r.right", "r.top", "r.left", "r.bl"]


def test_two_point_rectangle_adds_missing_axis_constraints() -> None:
    sketch, scope = setup()
    sketch.sketchCurves.sketchLines.rectangle_constraints = False
    (drawn,) = draw(sketch, scope, {"kind": "rectangle", "corner1": [0, 0], "corner2": [50, 30]})
    horizontal = [args[0] for args in calls(sketch, "geometricConstraints.addHorizontal")]
    vertical = [args[0] for args in calls(sketch, "geometricConstraints.addVertical")]
    assert horizontal == [drawn.subs["bottom"], drawn.subs["top"]]
    assert vertical == [drawn.subs["right"], drawn.subs["left"]]
    assert len(drawn.constraints) == 4


def test_three_point_rectangle() -> None:
    sketch, scope = setup()
    draw(sketch, scope, {"kind": "point", "id": "q", "at": [25, 30]})
    (drawn,) = draw(
        sketch,
        scope,
        {
            "kind": "rectangle",
            "mode": "three_point",
            "id": "r",
            "p1": [0, 0],
            "p2": [40, 0],
            "p3": "q",
        },
    )
    s = drawn.subs
    assert [mm(s[f"v{k}"]) for k in range(4)] == [(0, 0), (40, 0), (40, 30), (0, 30)]
    for k in range(4):
        edge = s[f"e{k}"]
        ends = {mm(edge.startSketchPoint), mm(edge.endSketchPoint)}
        assert ends == {mm(s[f"v{k}"]), mm(s[f"v{(k + 1) % 4}"])}
    (coincident,) = calls(sketch, "geometricConstraints.addCoincident")
    assert coincident == (scope.get("q"), s["e2"])


# --- 5.3 rectangle center ----------------------------------------------------------------


def test_center_rectangle_with_diagonal_and_midpoint() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch,
        scope,
        {"kind": "rectangle", "mode": "center", "id": "r", "center": [0, 0], "corner": [50, 30]},
    )
    s = drawn.subs
    assert sketch.sketchCurves.sketchLines.count == 5
    diagonal = s["diagonal"]
    assert diagonal.isConstruction is True
    assert {mm(diagonal.startSketchPoint), mm(diagonal.endSketchPoint)} == {(-50, -30), (50, 30)}
    assert mm(s["center"]) == (0.0, 0.0) and s["center"] is not sketch.originPoint
    assert calls(sketch, "geometricConstraints.addMidPoint") == [(s["center"], diagonal)]
    assert {"r.center", "r.diagonal", "r.bottom", "r.left"} <= set(scope.refs())


def test_center_rectangle_on_origin_uses_origin_point() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch,
        scope,
        {"kind": "rectangle", "mode": "center", "center": "origin", "corner": [50, 30]},
    )
    assert drawn.subs["center"] is sketch.originPoint
    assert not calls(sketch, "sketchPoints.add")
    assert calls(sketch, "geometricConstraints.addMidPoint")[0][0] is sketch.originPoint


def test_center_rectangle_reuses_fusion_diagonals() -> None:
    sketch, scope = setup()
    sketch.sketchCurves.sketchLines.center_rectangle_diagonals = True
    (drawn,) = draw(
        sketch,
        scope,
        {"kind": "rectangle", "mode": "center", "center": "origin", "corner": [50, 30]},
    )
    assert sketch.sketchCurves.sketchLines.count == 6  # 4 sides + Fusion's 2 diagonals
    assert not calls(sketch, "sketchLines.addByTwoPoints")
    assert not calls(sketch, "geometricConstraints.addMidPoint")
    (coincident,) = calls(sketch, "geometricConstraints.addCoincident")
    assert coincident[1] is sketch.originPoint
    assert drawn.subs["center"] is sketch.originPoint
    assert drawn.subs["diagonal"].isConstruction


# --- 5.4 circle, arc three_point / center_start_sweep ------------------------------------


def test_circle_center_radius() -> None:
    sketch, scope = setup()
    (drawn,) = draw(sketch, scope, {"kind": "circle", "id": "c", "center": [10, 0], "radius": 5})
    assert drawn.entity.radius == pytest.approx(0.5)
    assert mm(drawn.subs["center"]) == (10.0, 0.0)
    assert list(scope.refs()) == ["c", "c.center"]


def test_circle_two_point_ties_existing_points() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch, scope, {"kind": "circle", "mode": "two_point", "p1": "origin", "p2": [20, 0]}
    )
    assert drawn.entity.radius == pytest.approx(1.0)
    assert mm(drawn.subs["center"]) == (10.0, 0.0)
    args = calls(sketch, "sketchCircles.addByTwoPoints")[0]
    assert all(type(a).__name__ == "Point3D" for a in args)
    assert calls(sketch, "geometricConstraints.addCoincident") == [
        (sketch.originPoint, drawn.entity)
    ]


def test_circle_three_point() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch,
        scope,
        {"kind": "circle", "mode": "three_point", "p1": [10, 0], "p2": [0, 10], "p3": [-10, 0]},
    )
    assert drawn.entity.radius == pytest.approx(1.0)
    assert mm(drawn.subs["center"]) == (0.0, 0.0)


def test_arc_three_point_start_follows_input() -> None:
    sketch, scope = setup()
    # Clockwise: Fusion stores it counter-clockwise with swapped ends.
    (drawn,) = draw(
        sketch, scope, {"kind": "arc", "id": "a", "start": [-10, 0], "mid": [0, 10], "end": [10, 0]}
    )
    assert mm(drawn.subs["start"]) == (-10.0, 0.0)
    assert mm(drawn.subs["end"]) == (10.0, 0.0)
    assert mm(drawn.subs["center"]) == (0.0, 0.0)
    assert list(scope.refs()) == ["a", "a.start", "a.end", "a.center"]


def test_arc_center_start_sweep_degrees_to_radians() -> None:
    sketch, scope = setup()
    a, b = draw(
        sketch,
        scope,
        {
            "kind": "arc",
            "mode": "center_start_sweep",
            "center": [0, 0],
            "start": [10, 0],
            "sweep": 90,
        },
        {
            "kind": "arc",
            "mode": "center_start_sweep",
            "center": [0, 0],
            "start": [10, 0],
            "sweep": -90,
        },
    )
    sweeps = [args[2] for args in calls(sketch, "sketchArcs.addByCenterStartSweep")]
    assert sweeps == [pytest.approx(math.pi / 2), pytest.approx(-math.pi / 2)]
    assert mm(a.subs["end"]) == (0.0, 10.0)
    assert mm(b.subs["start"]) == (10.0, 0.0) and mm(b.subs["end"]) == (0.0, -10.0)


# --- 5.5 tangent arc ---------------------------------------------------------------------


def test_tangent_arc_from_line_end() -> None:
    sketch, scope = setup()
    line, arc = draw(
        sketch,
        scope,
        {"kind": "line", "id": "l", "start": [0, 0], "end": [40, 0]},
        {"kind": "arc", "mode": "tangent", "id": "t", "from": "l.end", "to": [60, 20]},
    )
    assert arc.subs["start"] is line.entity.endSketchPoint
    assert mm(arc.subs["end"]) == (60.0, 20.0)
    assert mm(arc.subs["center"]) == pytest.approx((40.0, 20.0))
    assert calls(sketch, "geometricConstraints.addTangent") == [(line.entity, arc.entity)]


def test_tangent_arc_from_line_start_and_arc_end() -> None:
    sketch, scope = setup()
    _, back, _, onward = draw(
        sketch,
        scope,
        {"kind": "line", "id": "l", "start": [0, 0], "end": [40, 0]},
        {"kind": "arc", "mode": "tangent", "id": "b", "from": "l.start", "to": [-20, 20]},
        {
            "kind": "arc",
            "mode": "center_start_sweep",
            "id": "a",
            "center": [100, 0],
            "start": [110, 0],
            "sweep": 90,
        },
        {"kind": "arc", "mode": "tangent", "id": "t", "from": "a.end", "to": [80, 20]},
    )
    # From the line start the arc continues towards -X: centre straight above/below the start.
    assert mm(back.subs["center"])[0] == pytest.approx(0.0)
    # From an arc end the tangent is perpendicular to the radius: centre on the radial line.
    assert mm(onward.subs["center"])[0] == pytest.approx(100.0)
    assert onward.subs["start"] is scope.get("a.end")


def test_tangent_arc_from_polyline_vertex_prefers_curve_ending_there() -> None:
    sketch, scope = setup()
    poly, arc = draw(
        sketch,
        scope,
        {"kind": "polyline", "id": "p", "points": [[0, 0], [10, 0], [10, 10]]},
        {"kind": "arc", "mode": "tangent", "from": "p.v1", "to": [20, 10]},
    )
    assert calls(sketch, "geometricConstraints.addTangent") == [(poly.subs["e0"], arc.entity)]


def test_tangent_arc_errors() -> None:
    sketch, scope = setup()
    error = fails(
        sketch,
        scope,
        {"kind": "circle", "id": "c", "center": [0, 0], "radius": 5},
        {"kind": "arc", "mode": "tangent", "from": "c.center", "to": [20, 10]},
    )
    assert error.details["reason"] == "no_tangent_curve"
    assert error.details["index"] == 1
    assert sketch.sketchCurves.count == 0  # rolled back

    sketch, scope = setup()
    error = fails(
        sketch,
        scope,
        {"kind": "line", "id": "l", "start": [0, 0], "end": [40, 0]},
        {"kind": "arc", "mode": "tangent", "from": "l.end", "to": [60, 0]},
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["reason"] == "degenerate"

    sketch, scope = setup()
    error = fails(sketch, scope, {"kind": "arc", "mode": "tangent", "from": [0, 0], "to": [1, 1]})
    assert error.details["reason"] == "invalid_entity_spec"


# --- 5.6 ellipse, spline -----------------------------------------------------------------


def test_ellipse() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch,
        scope,
        {
            "kind": "ellipse",
            "id": "e",
            "center": [10, 0],
            "major_radius": 20,
            "minor_radius": 10,
            "angle": 90,
        },
    )
    e = drawn.entity
    assert (e.majorAxisRadius, e.minorAxisRadius) == (pytest.approx(2.0), pytest.approx(1.0))
    assert (e.majorAxis.x, e.majorAxis.y) == (pytest.approx(0.0), pytest.approx(1.0))
    assert mm(drawn.subs["center"]) == (10.0, 0.0)


def test_ellipse_minor_greater_than_major_is_refused() -> None:
    sketch, scope = setup()
    error = fails(
        sketch, scope, {"kind": "ellipse", "center": [0, 0], "major_radius": 5, "minor_radius": 10}
    )
    assert error.details["reason"] == "degenerate"
    assert sketch.calls == []


def test_spline_open_with_shared_point() -> None:
    sketch, scope = setup()
    line, spline = draw(
        sketch,
        scope,
        {"kind": "line", "id": "l", "start": [0, 0], "end": [10, 0]},
        {"kind": "spline", "id": "s", "points": ["l.end", [20, 10], [30, 0]]},
    )
    (fit,) = calls(sketch, "sketchFittedSplines.add")
    assert fit[0].item(0) is line.entity.endSketchPoint
    assert spline.subs["start"] is line.entity.endSketchPoint
    assert mm(spline.subs["fp1"]) == (20.0, 10.0)
    assert spline.entity.isClosed is False
    assert {"s", "s.start", "s.end", "s.fp0", "s.fp1", "s.fp2"} <= set(scope.refs())


def test_closed_spline() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch, scope, {"kind": "spline", "closed": True, "points": [[0, 0], [10, 0], [5, 8]]}
    )
    assert drawn.entity.isClosed is True
    error = fails(*setup(), {"kind": "spline", "closed": True, "points": [[0, 0], [10, 0]]})
    assert error.details["reason"] == "too_few_points"


# --- 5.7 polygon -------------------------------------------------------------------------


def test_inscribed_hexagon() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch, scope, {"kind": "polygon", "id": "h", "center": [0, 0], "radius": 20, "sides": 6}
    )
    circle = drawn.subs["circle"]
    assert circle.isConstruction is True and circle.radius == pytest.approx(2.0)
    assert sketch.sketchCurves.sketchLines.count == 6
    edges = [drawn.subs[f"e{k}"] for k in range(6)]
    assert edges[5].endSketchPoint is edges[0].startSketchPoint
    assert all(not e.isConstruction for e in edges)
    kinds = constraint_kinds(sketch)
    assert kinds.count("EqualConstraint") == 5
    assert kinds.count("CoincidentConstraint") == 6
    for k in range(6):
        assert math.hypot(*mm(drawn.subs[f"v{k}"])) == pytest.approx(20.0)
    assert mm(drawn.subs["v0"]) == pytest.approx((20.0, 0.0))
    assert drawn.subs["center"] is circle.centerSketchPoint


def test_circumscribed_square_and_unconstrained() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch,
        scope,
        {"kind": "polygon", "mode": "circumscribed", "center": "origin", "radius": 10, "sides": 4},
    )
    kinds = constraint_kinds(sketch)
    assert kinds.count("TangentConstraint") == 4 and kinds.count("EqualConstraint") == 3
    assert drawn.subs["center"] is sketch.originPoint
    assert mm(drawn.subs["v0"]) == pytest.approx((10.0, -10.0))

    sketch, scope = setup()
    draw(
        sketch,
        scope,
        {"kind": "polygon", "center": [0, 0], "radius": 10, "sides": 5, "constrain": False},
    )
    assert constraint_kinds(sketch) == []


def test_polygon_sides_out_of_range() -> None:
    error = fails(*setup(), {"kind": "polygon", "center": [0, 0], "radius": 10, "sides": 2})
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["field"] == "items[0].sides"


# --- 5.8 slot ----------------------------------------------------------------------------


def test_slot_center_to_center() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch, scope, {"kind": "slot", "id": "s", "p1": [0, 0], "p2": [40, 0], "width": 10}
    )
    s = drawn.subs
    for end, center in (("end0", (0.0, 0.0)), ("end1", (40.0, 0.0))):
        assert s[end].radius == pytest.approx(0.5)
        assert mm(s[end].centerSketchPoint) == center
    assert s["c0"] is s["end0"].centerSketchPoint
    assert s["axis"].isConstruction is True
    assert {mm(s["axis"].startSketchPoint), mm(s["axis"].endSketchPoint)} == {(0, 0), (40, 0)}
    # Sides on the arcs' end points: one closed loop.
    assert s["side0"].startSketchPoint in (s["end0"].startSketchPoint, s["end0"].endSketchPoint)
    assert s["side0"].endSketchPoint in (s["end1"].startSketchPoint, s["end1"].endSketchPoint)
    assert s["side1"].endSketchPoint in (s["end0"].startSketchPoint, s["end0"].endSketchPoint)
    assert {mm(s["side0"].startSketchPoint)[1], mm(s["side1"].startSketchPoint)[1]} == {-5.0, 5.0}
    sweeps = [args[2] for args in calls(sketch, "sketchArcs.addByCenterStartSweep")]
    assert sweeps == [pytest.approx(math.pi)] * 2
    kinds = constraint_kinds(sketch)
    assert kinds.count("TangentConstraint") == 4 and kinds.count("EqualConstraint") == 1
    assert sketch.sketchCurves.count == 5
    assert set(scope.refs()) == {
        f"s.{k}" for k in ("side0", "side1", "end0", "end1", "axis", "c0", "c1")
    }


def test_slot_overall_and_shared_centre() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch, scope, {"kind": "slot", "mode": "overall", "p1": [0, 0], "p2": [40, 0], "width": 10}
    )
    assert mm(drawn.subs["c0"]) == (5.0, 0.0) and mm(drawn.subs["c1"]) == (35.0, 0.0)

    sketch, scope = setup()
    (drawn,) = draw(sketch, scope, {"kind": "slot", "p1": "origin", "p2": [40, 0], "width": 10})
    assert drawn.subs["c0"] is sketch.originPoint

    error = fails(
        *setup(), {"kind": "slot", "mode": "overall", "p1": [0, 0], "p2": [8, 0], "width": 10}
    )
    assert error.details["reason"] == "degenerate"


def test_construction_slot_keeps_all_curves_construction() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch,
        scope,
        {"kind": "slot", "p1": [0, 0], "p2": [40, 0], "width": 10, "construction": True},
    )
    assert all(c.isConstruction for c in drawn.curves)


# --- 5.9 text ----------------------------------------------------------------------------


def test_text_default_box_alignment_and_font() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch,
        scope,
        {
            "kind": "text",
            "id": "t",
            "text": "AB\nC",
            "height": 10,
            "corner": [5, 5],
            "font": "Consolas",
            "h_align": "center",
            "v_align": "middle",
        },
    )
    assert calls(sketch, "sketchTexts.createInput2") == [("AB\nC", pytest.approx(1.0))]
    text = drawn.entity
    w, h = default_text_size("AB\nC", 1.0)
    assert (w, h) == pytest.approx((1.6, 3.0))
    corner, diagonal = text.definition.cornerPoint, text.definition.diagonalPoint
    assert mm(corner) == (5.0, 5.0)
    assert mm(diagonal) == pytest.approx((5.0 + 16.0, 5.0 + 30.0))
    assert text.horizontalAlignment == adsk.core.HorizontalAlignments.CenterHorizontalAlignment
    assert text.verticalAlignment == adsk.core.VerticalAlignments.MiddleVerticalAlignment
    assert text.fontName == "Consolas"
    assert scope.refs()["t"]["type"] == "SketchText"


def test_text_explicit_size_and_fallback_create_input() -> None:
    sketch, scope = setup()
    (drawn,) = draw(
        sketch,
        scope,
        {"kind": "text", "text": "X", "height": 5, "corner": [0, 0], "size": [40, 10]},
    )
    assert mm(drawn.entity.definition.diagonalPoint) == (40.0, 10.0)
    assert (
        drawn.entity.horizontalAlignment == adsk.core.HorizontalAlignments.LeftHorizontalAlignment
    )

    sketch, scope = setup()
    sketch.sketchTexts.hide("createInput2")
    (drawn,) = draw(sketch, scope, {"kind": "text", "text": "X", "height": 5, "corner": [1, 2]})
    assert calls(sketch, "sketchTexts.createInput")
    assert mm(drawn.entity.position) == (1.0, 2.0)


# --- validation and rollback -------------------------------------------------------------


@pytest.mark.parametrize(
    ("item", "field", "reason"),
    [
        ({"kind": "blob"}, "items[0].kind", None),
        ({"kind": "circle", "mode": "four_point"}, "items[0].mode", None),
        ({"kind": "line", "start": [0, 0]}, "items[0].end", "missing_field"),
        ({"kind": "circle", "center": [0, 0], "radius": "r/2"}, "items[0].radius", "string_size"),
        ({"kind": "line", "start": [0, 0], "end": ["w", 0]}, "items[0].end", "string_coordinate"),
        ({"kind": "line", "start": [1, 1], "end": [1, 1]}, "items[0].end", "degenerate"),
        ({"kind": "line", "start": "zz.end", "end": [1, 1]}, "items[0].start", "unknown_local_id"),
        (
            {"kind": "rectangle", "corner1": [0, 0], "corner2": [0, 5]},
            "items[0].corner2",
            "degenerate",
        ),
        (
            {"kind": "circle", "mode": "three_point", "p1": [0, 0], "p2": [1, 1], "p3": [2, 2]},
            "items[0].p3",
            "degenerate",
        ),
        (
            {
                "kind": "arc",
                "mode": "center_start_sweep",
                "center": [0, 0],
                "start": [1, 0],
                "sweep": 0,
            },
            "items[0].sweep",
            None,
        ),
        ({"kind": "text", "text": "", "height": 1, "corner": [0, 0]}, "items[0].text", None),
        (
            {"kind": "text", "text": "a", "height": 1, "corner": [0, 0], "h_align": "justify"},
            "items[0].h_align",
            None,
        ),
        (
            {"kind": "polyline", "closed": True, "points": [[0, 0], [1, 0]]},
            "items[0].points",
            "too_few_points",
        ),
    ],
)
def test_invalid_items_fail_before_mutation(
    item: dict[str, Any], field: str, reason: str | None
) -> None:
    sketch, scope = setup()
    error = fails(sketch, scope, {"kind": "point", "at": [0, 0]}, item)
    assert error.code is ErrorCode.INVALID_PARAMS
    # The item under test is the second one of the batch.
    assert error.details["field"] == field.replace("items[0]", "items[1]")
    assert (error.details["phase"], error.details["index"]) == ("geometry", 1)
    if reason is not None:
        assert error.details["reason"] == reason
    assert sketch.calls == []


def test_ids_are_declared_with_types() -> None:
    sketch, scope = setup()
    error = fails(
        sketch,
        scope,
        {"kind": "rectangle", "id": "r", "corner1": [0, 0], "corner2": [5, 5]},
        {"kind": "line", "id": "l", "start": "r", "end": [1, 1]},
    )
    assert error.details["reason"] == "wrong_type" and error.details["got"] == GROUP_TYPE
    assert error.details["id"] == "l"

    sketch, scope = setup()
    error = fails(
        sketch,
        scope,
        {"kind": "circle", "id": "c", "center": [0, 0], "radius": 1},
        {"kind": "line", "start": "c.start", "end": [1, 1]},
    )
    assert error.details["reason"] == "unknown_sub"

    error = fails(
        *setup(),
        {"kind": "point", "id": "p", "at": [0, 0]},
        {"kind": "point", "id": "p", "at": [1, 0]},
    )
    assert error.details["reason"] == "duplicate_id"


def test_failure_rolls_back_the_batch() -> None:
    sketch, scope = setup()
    sketch.fail_on("addByTwoPoints", 3)
    with pytest.raises(BridgeError) as info:
        draw(
            sketch,
            scope,
            {"kind": "line", "start": [0, 0], "end": [10, 0]},
            {"kind": "polygon", "id": "h", "center": [0, 0], "radius": 20, "sides": 6},
        )
    error = info.value
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert (error.details["phase"], error.details["index"], error.details["id"]) == (
        "geometry",
        1,
        "h",
    )
    assert sketch.sketchCurves.count == 0
    assert sketch.sketchPoints.count == 1
    assert list(sketch.geometricConstraints) == []
    assert sketch.isComputeDeferred is False
    assert "rollback_error" not in error.details


def test_center_rectangle_rollback_removes_helpers() -> None:
    sketch, scope = setup()
    sketch.sketchCurves.sketchLines.rectangle_constraints = False
    sketch.fail_on("addMidPoint", 1)
    with pytest.raises(BridgeError):
        draw(
            sketch,
            scope,
            {"kind": "rectangle", "mode": "center", "center": [0, 0], "corner": [5, 3]},
        )
    assert sketch.sketchCurves.count == 0
    assert sketch.sketchPoints.count == 1
    assert list(sketch.geometricConstraints) == []


def test_model_point_projection_warning() -> None:
    sketch, scope = setup()
    (drawn,) = draw(sketch, scope, {"kind": "point", "at": {"model": [10, 20, 5]}})
    assert mm(drawn.entity) == (10.0, 20.0)
    assert scope.warnings and scope.warnings[0]["field"] == "items[0].at"

"""Behaviour of the sketch fakes (c05 2.2-2.4, D13): entities, ``FakeSketches``, fault
injection, constraints/dimensions, profiles and the editing API, units-manager expressions."""

import math
from typing import Any

import pytest

import fusion_addin
from tests.fakes.brep import FakeConstructionPlane, FakeSketch, fake_box_body, fake_cylinder_face
from tests.fakes.fusion_sketch import (
    FakeSketches,
    add_profile,
    auto_profiles,
    circle_curve,
    fake_sketch_design,
    make_sketch,
    polygon_curves,
    remove_optional_api,
    simple_solver,
)

adsk = fusion_addin.adsk
P = adsk.core.Point3D.create
V = adsk.core.Vector3D.create
OC = adsk.core.ObjectCollection
VI = adsk.core.ValueInput
DO = adsk.fusion.DimensionOrientations


def xy(p: Any) -> tuple[float, float]:
    g = p.geometry if hasattr(p, "geometry") else p
    return (round(g.x, 9) + 0.0, round(g.y, 9) + 0.0)


def xyz(p: Any) -> tuple[float, float, float]:
    return (round(p.x, 9) + 0.0, round(p.y, 9) + 0.0, round(p.z, 9) + 0.0)


def line(sketch: Any, a: tuple[float, float], b: tuple[float, float]) -> Any:
    return sketch.sketchCurves.sketchLines.addByTwoPoints(P(*a, 0), P(*b, 0))


# --- sketches collection, identity, coordinates -----------------------------------------------


def test_sketches_add_without_edges_registers_sketch_and_timeline() -> None:
    design = fake_sketch_design()
    root = design.rootComponent
    sketch = root.sketches.addWithoutEdges(root.xYConstructionPlane)
    assert isinstance(sketch, FakeSketch)
    assert isinstance(root.sketches, FakeSketches)
    assert root.sketches.calls == [("sketches.addWithoutEdges", (root.xYConstructionPlane,))]
    assert sketch.created_with == "addWithoutEdges"
    assert sketch.name == "Sketch1"
    assert sketch.parentComponent is root
    assert sketch.referencePlane is root.xYConstructionPlane
    assert design.findEntityByToken(sketch.entityToken) == [sketch]
    assert design.timeline.names() == ["Sketch1"]
    assert list(root.sketches) == [sketch]
    # Fusion's origin point is the first sketch point.
    assert sketch.sketchPoints.count == 1
    assert sketch.sketchPoints.item(0) is sketch.originPoint
    assert sketch.originPoint.isReference and sketch.originPoint.isFixed

    second = root.sketches.add(root.xZConstructionPlane)
    assert second.name == "Sketch2" and second.created_with == "add"

    sketch.deleteMe()
    assert not sketch.isValid
    assert list(root.sketches) == [second]
    assert design.timeline.names() == ["Sketch2"]
    assert design.findEntityByToken(sketch.entityToken) == []


def test_sketches_rejects_non_planar_and_injects_faults() -> None:
    design = fake_sketch_design()
    sketches = design.rootComponent.sketches
    with pytest.raises(RuntimeError, match="not a plane"):
        sketches.add(fake_cylinder_face(1.0, 2.0))
    sketches.fail_on("addWithoutEdges")
    with pytest.raises(RuntimeError, match="injected"):
        sketches.addWithoutEdges(design.rootComponent.xYConstructionPlane)
    assert sketches.count == 0
    assert sketches.addWithoutEdges(design.rootComponent.xYConstructionPlane).isValid


def test_sketches_add_on_face_projects_its_edges() -> None:
    body = fake_box_body(2.0, 1.0, 3.0, token="b")
    design = fake_sketch_design(body)
    top = design.by_token["b-face+z"]
    sketch = design.rootComponent.sketches.add(top)
    lines = list(sketch.sketchCurves.sketchLines)
    assert len(lines) == 4
    assert all(c.isReference and not c.isLinked for c in lines)
    # Sketch origin = face origin (its centroid), normal +Z.
    origin, x_axis, y_axis, normal = sketch.transform.getAsCoordinateSystem()
    assert xyz(origin) == (1.0, 0.5, 3.0)
    assert (x_axis.asArray(), y_axis.asArray(), normal.asArray()) == (
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, 1],
    )
    corners = sorted({xy(p) for c in lines for p in (c.startSketchPoint, c.endSketchPoint)})
    assert corners == [(-1.0, -0.5), (-1.0, 0.5), (1.0, -0.5), (1.0, 0.5)]
    assert design.rootComponent.sketches.addWithoutEdges(top).sketchCurves.count == 0


def test_origin_plane_transforms_and_space_conversion() -> None:
    design = fake_sketch_design()
    xy_sketch = make_sketch(design, "XY")
    assert xy_sketch.transform.isEqualTo(adsk.core.Matrix3D.create())

    xz = make_sketch(design, "XZ")
    _, x_axis, y_axis, normal = xz.transform.getAsCoordinateSystem()
    # Fusion's XZ sketch: sketch Y is model -Z.
    assert (x_axis.asArray(), y_axis.asArray(), normal.asArray()) == (
        [1, 0, 0],
        [0, 0, -1],
        [0, 1, 0],
    )
    assert xyz(xz.sketchToModelSpace(P(1, 2, 0))) == (1.0, 0.0, -2.0)
    assert xyz(xz.modelToSketchSpace(P(1, 5, -2))) == (1.0, 2.0, 5.0)
    point = xz.sketchPoints.add(P(1, 2, 0))
    assert xyz(point.worldGeometry) == (1.0, 0.0, -2.0)
    assert xyz(point.geometry) == (1.0, 2.0, 0.0)

    yz = make_sketch(design, "YZ")
    _, x_axis, y_axis, _ = yz.transform.getAsCoordinateSystem()
    assert (x_axis.asArray(), y_axis.asArray()) == ([0, 0, -1], [0, 1, 0])
    # make_sketch clears the creation call.
    assert design.rootComponent.sketches.calls == []
    assert yz.calls == []


def test_compute_deferred_flag_is_logged() -> None:
    sketch = make_sketch()
    assert sketch.isComputeDeferred is False
    sketch.isComputeDeferred = True
    assert sketch.isComputeDeferred is True
    sketch.isComputeDeferred = False
    assert sketch.defer_log == [True, False]


# --- entities --------------------------------------------------------------------------------


def test_line_entities_share_points_and_carry_identity() -> None:
    sketch = make_sketch()
    one = line(sketch, (0, 0), (2, 0))
    two = sketch.sketchCurves.sketchLines.addByTwoPoints(one.endSketchPoint, P(2, 1, 0))
    assert two.startSketchPoint is one.endSketchPoint
    assert sketch.sketchPoints.count == 1 + 3
    assert sketch.sketchCurves.sketchLines.count == 2
    assert sketch.sketchCurves.count == 2
    for entity in (one, two, one.startSketchPoint):
        assert entity.isValid
        assert entity.parentSketch is sketch
        assert entity.entityToken.startswith(sketch.entityToken + "/")
        assert entity.isFixed is False and entity.isFullyConstrained is False
    assert one.objectType == "adsk::fusion::SketchLine"
    assert one.startSketchPoint.objectType == "adsk::fusion::SketchPoint"
    assert one.isConstruction is False
    one.isConstruction = True
    assert one.isConstruction
    tokens = [e.entityToken for e in (*sketch.sketchPoints, *sketch.sketchCurves)]
    assert len(tokens) == len(set(tokens))
    assert one.length == 2.0
    assert one.geometry.objectType == "adsk::core::Line3D"
    assert xyz(one.geometry.endPoint) == (2.0, 0.0, 0.0)
    assert sketch.call_names() == ["sketchLines.addByTwoPoints"] * 2
    with pytest.raises(RuntimeError, match="distinct"):
        line(sketch, (1, 1), (1, 1))


def test_delete_cascades_like_fusion() -> None:
    sketch = make_sketch()
    one = line(sketch, (0, 0), (2, 0))
    two = sketch.sketchCurves.sketchLines.addByTwoPoints(one.endSketchPoint, P(2, 1, 0))
    horizontal = sketch.geometricConstraints.addHorizontal(one)
    a, b, c = one.startSketchPoint, one.endSketchPoint, two.endSketchPoint
    assert one.deleteMe()
    assert not one.isValid and not horizontal.isValid
    assert not a.isValid  # only used by the deleted line
    assert b.isValid and two.isValid  # still used by the second line
    assert ("deleteMe", (one,)) in sketch.calls
    with pytest.raises(RuntimeError, match="no longer valid"):
        one.deleteMe()
    c.deleteMe()
    assert not two.isValid
    assert sketch.sketchCurves.count == 0
    # A standalone point survives deleting the curves built on it.
    lone = sketch.sketchPoints.add(P(5, 5, 0))
    three = sketch.sketchCurves.sketchLines.addByTwoPoints(lone, P(6, 5, 0))
    three.deleteMe()
    assert lone.isValid
    lone.fail_delete = "locked"
    with pytest.raises(RuntimeError, match="locked"):
        lone.deleteMe()
    assert lone.isValid


def test_circles_arcs_ellipses_splines() -> None:
    sketch = make_sketch()
    circles = sketch.sketchCurves.sketchCircles
    c1 = circles.addByCenterRadius(P(1, 1, 0), 0.5)
    assert (xy(c1.centerSketchPoint), c1.radius) == ((1.0, 1.0), 0.5)
    assert c1.length == pytest.approx(math.pi)
    assert c1.geometry.radius == 0.5
    p1, p2 = sketch.sketchPoints.add(P(0, 0, 0)), sketch.sketchPoints.add(P(2, 0, 0))
    c2 = circles.addByTwoPoints(p1, p2)
    assert (xy(c2.centerSketchPoint), c2.radius) == ((1.0, 0.0), 1.0)
    ties = [g for g in sketch.geometricConstraints if g.entity is c2]
    assert [g.point for g in ties] == [p1, p2]
    c3 = circles.addByThreePoints(P(1, 0, 0), P(0, 1, 0), P(-1, 0, 0))
    assert xy(c3.centerSketchPoint) == (0.0, 0.0) and c3.radius == pytest.approx(1.0)
    with pytest.raises(RuntimeError, match="positive"):
        circles.addByCenterRadius(P(0, 0, 0), 0)

    arcs = sketch.sketchCurves.sketchArcs
    ccw = arcs.addByThreePoints(P(1, 0, 0), P(0, 1, 0), P(-1, 0, 0))
    assert xy(ccw.startSketchPoint) == (1.0, 0.0) and xy(ccw.endSketchPoint) == (-1.0, 0.0)
    assert ccw.geometry.startAngle == pytest.approx(0.0)
    assert ccw.geometry.endAngle == pytest.approx(math.pi)
    assert ccw.length == pytest.approx(math.pi)
    cw = arcs.addByThreePoints(P(-1, 0, 0), P(0, 1, 0), P(1, 0, 0))
    assert xy(cw.startSketchPoint) == (1.0, 0.0)  # stored counter-clockwise
    swept = arcs.addByCenterStartSweep(P(0, 0, 0), P(1, 0, 0), -math.pi / 2)
    assert xy(swept.startSketchPoint) == (0.0, -1.0) and xy(swept.endSketchPoint) == (1.0, 0.0)
    with pytest.raises(RuntimeError, match="collinear"):
        arcs.addByThreePoints(P(0, 0, 0), P(1, 0, 0), P(2, 0, 0))

    ellipse = sketch.sketchCurves.sketchEllipses.add(P(0, 0, 0), P(2, 0, 0), P(0, 1, 0))
    assert (ellipse.majorAxisRadius, ellipse.minorAxisRadius) == (2.0, 1.0)
    assert ellipse.majorAxis.asArray() == [1, 0, 0]
    assert ellipse.objectType == "adsk::fusion::SketchEllipse"

    fit = OC.create()
    shared = sketch.sketchPoints.add(P(0, 3, 0))
    for item in (shared, P(1, 4, 0), P(2, 3, 0)):
        fit.add(item)
    spline = sketch.sketchCurves.sketchFittedSplines.add(fit)
    assert spline.fitPoints.item(0) is shared
    assert spline.length == pytest.approx(2 * math.sqrt(2))
    spline.isClosed = True
    assert spline.isClosed
    assert spline.length == pytest.approx(2 * math.sqrt(2) + 2)
    assert sketch.sketchCurves.sketchFittedSplines.count == 1
    assert sketch.sketchCurves.count == 3 + 3 + 1 + 1


def test_rectangles() -> None:
    sketch = make_sketch()
    lines = sketch.sketchCurves.sketchLines
    rect = lines.addTwoPointRectangle(P(0, 0, 0), P(2, 1, 0))
    assert rect.count == 4
    corners = [xy(rect.item(i).startSketchPoint) for i in range(4)]
    assert corners == [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)]
    for i in range(4):
        assert rect.item(i).endSketchPoint is rect.item((i + 1) % 4).startSketchPoint
    kinds = [g.objectType.rsplit("::", 1)[1] for g in sketch.geometricConstraints]
    assert kinds == ["HorizontalConstraint", "VerticalConstraint"] * 2

    three = lines.addThreePointRectangle(P(0, 0, 0), P(2, 0, 0), P(1, 1, 0))
    assert [xy(three.item(i).startSketchPoint) for i in (2, 3)] == [(2.0, 1.0), (0.0, 1.0)]
    kinds = [g.objectType.rsplit("::", 1)[1] for g in sketch.geometricConstraints][4:]
    assert kinds == ["PerpendicularConstraint", "ParallelConstraint", "ParallelConstraint"]

    lines.rectangle_constraints = False
    lines.center_rectangle_diagonals = True
    before = sketch.sketchCurves.count
    centred = lines.addCenterPointRectangle(P(0, 5, 0), P(1, 6, 0))
    assert sorted(xy(centred.item(i).startSketchPoint) for i in range(4)) == [
        (-1.0, 4.0),
        (-1.0, 6.0),
        (1.0, 4.0),
        (1.0, 6.0),
    ]
    assert sketch.sketchCurves.count == before + 6
    assert sum(c.isConstruction for c in sketch.sketchCurves) == 2
    assert sketch.geometricConstraints.item(7).objectType.endswith("MidPointConstraint")
    with pytest.raises(RuntimeError, match="degenerate"):
        lines.addTwoPointRectangle(P(0, 0, 0), P(2, 0, 0))


def test_texts() -> None:
    sketch = make_sketch()
    texts = sketch.sketchTexts
    data = texts.createInput2("ABC", 0.5)
    left = adsk.core.HorizontalAlignments.LeftHorizontalAlignment
    bottom = adsk.core.VerticalAlignments.BottomVerticalAlignment
    with pytest.raises(RuntimeError, match="setAsMultiLine"):
        texts.add(data)
    assert data.setAsMultiLine(P(0, 0, 0), P(3, 1, 0), left, bottom, 0.0)
    data.fontName = "Consolas"
    text = texts.add(data)
    assert texts.count == 1 and texts.item(0) is text
    assert (text.text, text.height, text.fontName) == ("ABC", 0.5, "Consolas")
    assert text.objectType == "adsk::fusion::SketchText"
    assert xyz(text.boundingBox.maxPoint) == (3.0, 1.0, 0.0)
    assert text.definition.horizontalAlignment == left
    # The failed add is logged too.
    assert sketch.call_names() == ["sketchTexts.createInput2", "sketchTexts.add", "sketchTexts.add"]
    text.deleteMe()
    assert texts.count == 0


# --- fault injection, optional API -----------------------------------------------------------


def test_fault_on_nth_call_creates_nothing() -> None:
    sketch = make_sketch()
    sketch.fail_on("addByTwoPoints", n=2)
    line(sketch, (0, 0), (1, 0))
    with pytest.raises(RuntimeError, match=r"injected failure: sketchLines\.addByTwoPoints"):
        line(sketch, (0, 1), (1, 1))
    assert sketch.sketchCurves.count == 1
    assert sketch.sketchPoints.count == 1 + 2
    line(sketch, (0, 2), (1, 2))  # the fault fired once
    assert sketch.sketchCurves.count == 2
    # Full names, "*" and custom exceptions; calls are logged even when they fail.
    sketch.fail_on("geometricConstraints.addHorizontal", exc=ValueError("solver"))
    first = sketch.sketchCurves.item(0)
    with pytest.raises(ValueError, match="solver"):
        sketch.geometricConstraints.addHorizontal(first)
    assert sketch.geometricConstraints.count == 0
    assert sketch.calls[-1] == ("geometricConstraints.addHorizontal", (first,))
    sketch.fail_on("*", n=1)
    with pytest.raises(RuntimeError):
        sketch.sketchPoints.add(P(9, 9, 0))
    sketch.reset_calls()
    assert sketch.calls == []
    with pytest.raises(ValueError):
        sketch.fail_on("x", n=0)


def test_hidden_optional_api() -> None:
    sketch = make_sketch()
    assert hasattr(sketch, "isFullyConstrained")
    sketch.hide("isFullyConstrained")
    assert not hasattr(sketch, "isFullyConstrained")
    sketch.unhide("isFullyConstrained")
    assert hasattr(sketch, "isFullyConstrained")
    constraints = sketch.geometricConstraints
    assert hasattr(constraints, "addOffset2")
    remove_optional_api(sketch)
    for name in ("createOffsetInput", "addOffset2", "createCircularPatternInput"):
        assert not hasattr(constraints, name)
    assert hasattr(constraints, "addCoincident")


def test_sketch_is_fully_constrained_from_entities() -> None:
    sketch = make_sketch()
    assert sketch.isFullyConstrained  # only the reference origin point
    one = line(sketch, (0, 0), (1, 0))
    assert not sketch.isFullyConstrained
    for entity in (one, one.startSketchPoint, one.endSketchPoint):
        entity.isFullyConstrained = True
    assert sketch.isFullyConstrained


# --- constraints -----------------------------------------------------------------------------


def test_geometric_constraints_record_roles() -> None:
    sketch = make_sketch()
    gc = sketch.geometricConstraints
    l1 = line(sketch, (0, 0), (2, 0))
    l2 = line(sketch, (0, 1), (2, 1))
    circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(P(5, 0, 0), 1)
    arc = sketch.sketchCurves.sketchArcs.addByThreePoints(P(6, 0, 0), P(5, 1, 0), P(4, 0, 0))
    fit = OC.createWithArray([P(0, 5, 0), P(1, 6, 0), P(2, 5, 0)])
    spline = sketch.sketchCurves.sketchFittedSplines.add(fit)
    ellipse = sketch.sketchCurves.sketchEllipses.add(P(5, 0, 0), P(7, 0, 0), P(5, 1, 0))
    point = sketch.sketchPoints.add(P(1, 0, 0))
    a, b = l1.startSketchPoint, l2.startSketchPoint

    made = {
        "CoincidentConstraint": (gc.addCoincident(point, l1), {"point": point, "entity": l1}),
        "HorizontalConstraint": (gc.addHorizontal(l1), {"line": l1}),
        "VerticalPointsConstraint": (
            gc.addVerticalPoints(a, b),
            {"pointOne": a, "pointTwo": b},
        ),
        "HorizontalPointsConstraint": (
            gc.addHorizontalPoints(a, point),
            {"pointOne": a, "pointTwo": point},
        ),
        "ParallelConstraint": (gc.addParallel(l1, l2), {"lineOne": l1, "lineTwo": l2}),
        "CollinearConstraint": (gc.addCollinear(l1, l2), {"lineOne": l1, "lineTwo": l2}),
        "TangentConstraint": (gc.addTangent(l1, circle), {"curveOne": l1, "curveTwo": circle}),
        "EqualConstraint": (gc.addEqual(circle, arc), {"curveOne": circle, "curveTwo": arc}),
        "ConcentricConstraint": (
            gc.addConcentric(circle, ellipse),
            {"entityOne": circle, "entityTwo": ellipse},
        ),
        "MidPointConstraint": (
            gc.addMidPoint(point, l1),
            {"point": point, "midPointCurve": l1},
        ),
        "SymmetryConstraint": (
            gc.addSymmetry(a, b, l1),
            {"entityOne": a, "entityTwo": b, "symmetryLine": l1},
        ),
        "SmoothConstraint": (gc.addSmooth(spline, arc), {"curveOne": spline, "curveTwo": arc}),
    }
    for kind, (constraint, roles) in made.items():
        assert constraint.objectType == f"adsk::fusion::{kind}"
        assert constraint.parentSketch is sketch and constraint.isValid
        for role, entity in roles.items():
            assert getattr(constraint, role) is entity
    gc.addVertical(line(sketch, (9, 0), (9, 1)))
    gc.addPerpendicular(l1, sketch.sketchCurves.item(sketch.sketchCurves.count - 1))
    names = [n.split(".")[1] for n in sketch.call_names() if n.startswith("geometricC")]
    assert names[:3] == ["addCoincident", "addHorizontal", "addVerticalPoints"]
    assert names[-2:] == ["addVertical", "addPerpendicular"]
    assert gc.count == len(made) + 2


def test_constraint_type_checks() -> None:
    sketch = make_sketch()
    other = make_sketch(sketch.design)
    gc = sketch.geometricConstraints
    l1, l2 = line(sketch, (0, 0), (2, 0)), line(sketch, (0, 1), (2, 1))
    circle = circle_curve(sketch, (5, 0), 1)
    foreign = line(other, (0, 0), (1, 0))
    with pytest.raises(RuntimeError, match="expected SketchLine"):
        gc.addHorizontal(circle)
    with pytest.raises(RuntimeError, match="of this sketch"):
        gc.addParallel(l1, foreign)
    with pytest.raises(RuntimeError, match="not both lines"):
        gc.addTangent(l1, l2)
    with pytest.raises(RuntimeError, match="two lines or two arcs"):
        gc.addEqual(l1, circle)
    with pytest.raises(RuntimeError, match="spline"):
        gc.addSmooth(l1, circle)
    with pytest.raises(RuntimeError, match="must differ"):
        gc.addParallel(l1, l1)
    assert gc.count == 0


# --- dimensions ------------------------------------------------------------------------------


def test_distance_dimension_parameter_and_solver() -> None:
    design = fake_sketch_design()
    sketch = make_sketch(design)
    dims = sketch.sketchDimensions
    l1 = line(sketch, (0, 0), (2, 1))
    a, b = l1.startSketchPoint, l1.endSketchPoint
    aligned = dims.addDistanceDimension(a, b, DO.AlignedDimensionOrientation, P(1, 2, 0))
    horizontal = dims.addDistanceDimension(a, b, DO.HorizontalDimensionOrientation, P(1, -1, 0))
    vertical = dims.addDistanceDimension(a, b, DO.VerticalDimensionOrientation, P(3, 0, 0), False)
    assert aligned.objectType == "adsk::fusion::SketchLinearDimension"
    assert (aligned.entityOne, aligned.entityTwo) == (a, b)
    assert aligned.orientation == DO.AlignedDimensionOrientation
    assert aligned.parameter.value == pytest.approx(math.sqrt(5))
    assert (horizontal.parameter.expression, horizontal.parameter.name) == ("20 mm", "d2")
    assert vertical.parameter.expression == "10 mm" and vertical.isDriving is False
    assert xyz(aligned.textPosition) == (1.0, 2.0, 0.0)
    assert design.allParameters.itemByName("d1") is aligned.parameter
    name, args = sketch.calls[-1]
    assert name == "sketchDimensions.addDistanceDimension"
    assert args[:3] == (a, b, DO.VerticalDimensionOrientation)
    assert (xyz(args[3]), args[4]) == ((3.0, 0.0, 0.0), False)

    # Driving: expressions can name user parameters; the solver moves the free point.
    design.parameter_set.add_user("width", "15 mm", "mm", "")
    sketch.solver = simple_solver
    horizontal.parameter.expression = "width * 2"
    assert horizontal.parameter.value == pytest.approx(3.0)
    assert xy(b) == (3.0, 1.0)
    horizontal.parameter.name = "len"
    assert design.allParameters.itemByName("len") is horizontal.parameter
    # Driven parameters cannot be set.
    with pytest.raises(RuntimeError, match="driven"):
        vertical.parameter.expression = "5 mm"
    with pytest.raises(RuntimeError, match="invalid expression"):
        aligned.parameter.expression = "nope * 2"
    aligned.isDriving = False
    assert aligned.isDriving is False
    assert ("SketchDimension.isDriving", (aligned, False)) in sketch.calls

    # Deleting a dimension removes its parameter; deleting geometry cascades.
    vertical.deleteMe()
    assert design.allParameters.itemByName(vertical.parameter.name) is None
    l1.deleteMe()
    assert dims.count == 0
    with pytest.raises(RuntimeError, match="textPoint"):
        dims.addDistanceDimension(
            *(line(sketch, (0, 0), (1, 0))._points()), DO.AlignedDimensionOrientation, None
        )


def test_offset_radial_angular_ellipse_dimensions() -> None:
    sketch = make_sketch()
    dims = sketch.sketchDimensions
    base = line(sketch, (0, 0), (4, 0))
    parallel = line(sketch, (0, 1.5), (4, 1.5))
    point = sketch.sketchPoints.add(P(2, -0.5, 0))
    offset = dims.addOffsetDimension(base, parallel, P(5, 1, 0))
    assert offset.objectType == "adsk::fusion::SketchOffsetDimension"
    assert (offset.line, offset.entity, offset.parameter.expression) == (base, parallel, "15 mm")
    assert dims.addOffsetDimension(base, point, P(5, 0, 0)).parameter.expression == "5 mm"
    with pytest.raises(RuntimeError, match="not parallel"):
        dims.addOffsetDimension(base, line(sketch, (0, 0), (1, 1)), P(5, 0, 0))
    dims.offset_accepts_point = False
    with pytest.raises(RuntimeError, match="expected SketchLine"):
        dims.addOffsetDimension(base, point, P(5, 0, 0))

    circle = circle_curve(sketch, (8, 0), 1.25)
    radial = dims.addRadialDimension(circle, P(9, 1, 0))
    diameter = dims.addDiameterDimension(circle, P(9, -1, 0))
    assert radial.objectType == "adsk::fusion::SketchRadialDimension"
    assert (radial.entity, radial.parameter.expression) == (circle, "12.5 mm")
    assert diameter.parameter.expression == "25 mm"
    with pytest.raises(RuntimeError, match="expected SketchCircle"):
        dims.addRadialDimension(base, P(0, 0, 0))

    diagonal = line(sketch, (0, 0), (1, 1))
    inner = dims.addAngularDimension(base, diagonal, P(1, 0.4, 0))
    outer = dims.addAngularDimension(base, diagonal, P(-1, 0.4, 0))
    assert inner.objectType == "adsk::fusion::SketchAngularDimension"
    assert (inner.lineOne, inner.lineTwo) == (base, diagonal)
    assert inner.parameter.expression == "45 deg"
    assert inner.parameter.value == pytest.approx(math.pi / 4)
    assert outer.parameter.expression == "135 deg"

    ellipse = sketch.sketchCurves.sketchEllipses.add(P(0, 8, 0), P(3, 8, 0), P(0, 9, 0))
    major = dims.addEllipseMajorRadiusDimension(ellipse, P(0, 10, 0))
    minor = dims.addEllipseMinorRadiusDimension(ellipse, P(1, 10, 0))
    assert major.ellipse is ellipse
    assert (major.parameter.expression, minor.parameter.expression) == ("30 mm", "10 mm")
    sketch.solver = simple_solver
    radial.parameter.expression = "2 cm"
    major.parameter.expression = "40 mm"
    assert circle.radius == pytest.approx(2.0)
    assert ellipse.majorAxisRadius == pytest.approx(4.0)
    names = [n.split(".")[1] for n in sketch.call_names() if n.startswith("sketchDim")]
    assert "addEllipseMinorRadiusDimension" in names and "addDiameterDimension" in names


# --- profiles --------------------------------------------------------------------------------


def test_explicit_profile_with_hole() -> None:
    design = fake_sketch_design()
    sketch = make_sketch(design, "XZ")
    outer = polygon_curves(sketch, [(0, 0), (4, 0), (4, 2), (0, 2)])
    hole = circle_curve(sketch, (1, 1), 0.5)
    profile = add_profile(sketch, outer, [hole])
    assert sketch.profiles.count == 1 and sketch.profiles.item(0) is profile
    assert profile.parentSketch is sketch and profile.isValid
    loops = list(profile.profileLoops)
    assert [loop.isOuter for loop in loops] == [True, False]
    assert [pc.sketchEntity for pc in loops[0].profileCurves] == outer
    assert loops[1].profileCurves.item(0).sketchEntity is hole
    ok, strokes = loops[0].profileCurves.item(0).geometry.evaluator.getStrokes(0, 1, 0.01)
    assert ok and [xy(p) for p in strokes] == [(0.0, 0.0), (4.0, 0.0)]
    _, circle_strokes = hole.geometry.evaluator.getStrokes(0, 1, 0.01)
    assert len(circle_strokes) == 97
    assert all(math.dist(xy(p), (1, 1)) == pytest.approx(0.5) for p in circle_strokes)

    props = profile.areaProperties(adsk.fusion.CalculationAccuracy.HighCalculationAccuracy)
    hole_area = math.pi * 0.25
    assert props.area == pytest.approx(8 - hole_area, rel=1e-3)
    cx = (8 * 2 - hole_area * 1) / (8 - hole_area)
    # Centroid in model space: XZ sketch (x, y) -> model (x, 0, -y).
    assert (props.centroid.x, props.centroid.y, props.centroid.z) == (
        pytest.approx(cx, rel=1e-3),
        pytest.approx(0.0),
        pytest.approx(-1.0),
    )
    assert props.perimeter == pytest.approx(12 + math.pi)
    assert profile.accuracy_calls == [adsk.fusion.CalculationAccuracy.HighCalculationAccuracy]
    box = profile.boundingBox
    assert (xyz(box.minPoint), xyz(box.maxPoint)) == ((0, 0, 0), (4.0, 2.0, 0))
    sketch_space = add_profile(sketch, outer, centroid_space="sketch")
    assert xyz(sketch_space.areaProperties().centroid) == (2.0, 1.0, 0.0)


def test_auto_profiles_l_shape_nested_circle_and_invalidation() -> None:
    sketch = make_sketch()
    sketch.profile_builder = auto_profiles
    l_shape = polygon_curves(sketch, [(0, 0), (2, 0), (2, 1), (1, 1), (1, 2), (0, 2)])
    circle_curve(sketch, (0.5, 0.5), 0.2)
    circle_curve(sketch, (5, 5), 0.5).isConstruction = True  # ignored
    polygon_curves(sketch, [(8, 0), (9, 0), (9, 1)], closed=False)  # open: no profile
    profiles = list(sketch.profiles)
    assert len(profiles) == 2
    by_area = sorted(profiles, key=lambda p: p.areaProperties().area)
    small, big = by_area
    assert small.areaProperties().area == pytest.approx(math.pi * 0.04, rel=1e-2)
    assert big.areaProperties().area == pytest.approx(3 - math.pi * 0.04, rel=1e-2)
    assert big.profileLoops.count == 2
    assert {pc.sketchEntity for pc in big.profileLoops.item(0).profileCurves} == set(l_shape)

    # Cached while unchanged; recomputed (old ones invalid) after a change, unless deferred.
    assert list(sketch.profiles) == profiles
    sketch.isComputeDeferred = True
    circle_curve(sketch, (10, 10), 1)
    assert list(sketch.profiles) == profiles
    sketch.isComputeDeferred = False
    fresh = list(sketch.profiles)
    assert len(fresh) == 3
    assert not any(p.isValid for p in profiles)


# --- editing API -----------------------------------------------------------------------------


def test_find_connected_curves() -> None:
    sketch = make_sketch()
    chain = polygon_curves(sketch, [(0, 0), (1, 0), (1, 1), (0, 1)], closed=False)
    lone = line(sketch, (5, 5), (6, 5))
    found = sketch.findConnectedCurves(chain[1])
    assert isinstance(found, OC)
    assert set(found) == set(chain)
    assert list(sketch.findConnectedCurves(lone)) == [lone]
    assert sketch.calls[-2] == ("Sketch.findConnectedCurves", (chain[1],))


def test_project_and_include() -> None:
    body = fake_box_body(2.0, 1.0, 3.0, token="b")
    design = fake_sketch_design(body)
    sketch = make_sketch(design, "XY")
    created = sketch.project(design.by_token["b-face+z"])
    assert isinstance(created, OC) and created.count == 4
    assert all(c.isReference and not c.isLinked and c.isFullyConstrained for c in created)
    ends = {xy(p) for c in created for p in (c.startSketchPoint, c.endSketchPoint)}
    assert ends == {(0, 0), (2.0, 0), (0, 1.0), (2.0, 1.0)}
    # Corners are shared between the projected lines.
    assert sketch.sketchPoints.count == 1 + 4
    assert created.item(0).referencedEntity is design.by_token["b-face+z"]

    included = sketch.include(design.by_token["b-e000100"])
    assert included.count == 1 and included.item(0).isLinked
    assert sketch.project(design.by_token["b-v111"]).item(0).objectType.endswith("SketchPoint")
    axis = sketch.project(design.rootComponent.xConstructionAxis).item(0)
    assert axis.objectType == "adsk::fusion::SketchLine"

    other = make_sketch(design, "XZ")
    foreign = line(other, (0, 0), (1, 0))
    projected = sketch.project(foreign).item(0)
    assert projected.objectType == "adsk::fusion::SketchLine"
    assert (xy(projected.startSketchPoint), xy(projected.endSketchPoint)) == ((0, 0), (1.0, 0))
    with pytest.raises(RuntimeError, match="same sketch"):
        sketch.project(projected)

    with pytest.raises(RuntimeError, match="body"):
        sketch.project(body)
    sketch.allow_project_body = True
    assert sketch.project(body).count == 12
    names = sketch.call_names()
    assert names[:2] == ["Sketch.project", "Sketch.include"]


def test_intersect_and_cut_edges() -> None:
    body = fake_box_body(2.0, 1.0, 3.0, token="b")
    design = fake_sketch_design(body)
    plane = FakeConstructionPlane("mid", P(0, 0, 1.5), V(0, 0, 1))
    sketch = design.rootComponent.sketches.addWithoutEdges(plane)
    created = sketch.intersectWithSketchPlane(OC.createWithArray([body]))
    assert created.count == 4  # one line per side face
    assert all(c.isReference for c in created)
    # Four crossing points, each shared by two lines.
    assert sketch.sketchPoints.count == 1 + 4
    assert {xy(p) for p in list(sketch.sketchPoints)[1:]} == {
        (0, 0),
        (2.0, 0),
        (0, 1.0),
        (2.0, 1.0),
    }
    edge = sketch.intersectWithSketchPlane(OC.createWithArray([design.by_token["b-e000001"]]))
    assert edge.count == 1 and edge.item(0).objectType.endswith("SketchPoint")
    cut = make_sketch(design, plane).projectCutEdges(body)
    assert cut.count == 4
    with pytest.raises(RuntimeError, match="cannot intersect"):
        sketch.intersectWithSketchPlane([design.rootComponent.xConstructionAxis])


def test_sketch_offset_goes_to_one_side_of_a_chain() -> None:
    sketch = make_sketch()
    rect = polygon_curves(sketch, [(0, 0), (2, 0), (2, 1), (0, 1)])
    outward = sketch.offset(OC.createWithArray(rect), P(3, 0.5, 0), 0.1)
    corners = sorted(xy(c.startSketchPoint) for c in outward)
    assert corners == [(-0.1, -0.1), (-0.1, 1.1), (2.1, -0.1), (2.1, 1.1)]
    inward = sketch.offset(OC.createWithArray(rect), P(1, 0.5, 0), 0.1)
    assert sorted(xy(c.startSketchPoint) for c in inward) == [
        (0.1, 0.1),
        (0.1, 0.9),
        (1.9, 0.1),
        (1.9, 0.9),
    ]
    # Shared corners stay shared in the result.
    assert outward.item(0).endSketchPoint is outward.item(1).startSketchPoint
    single = sketch.offset(OC.createWithArray([rect[0]]), P(1, -1, 0), 0.25)
    assert (xy(single.item(0).startSketchPoint), xy(single.item(0).endSketchPoint)) == (
        (0, -0.25),
        (2.0, -0.25),
    )
    circle = circle_curve(sketch, (5, 5), 1)
    assert sketch.offset([circle], P(5, 5, 0), 0.25).item(0).radius == pytest.approx(0.75)
    assert sketch.offset([circle], P(9, 9, 0), 0.25).item(0).radius == pytest.approx(1.25)
    assert sketch.offset([circle], P(9, 9, 0), -0.25).item(0).radius == pytest.approx(0.75)
    with pytest.raises(RuntimeError, match="zero"):
        sketch.offset([circle], P(9, 9, 0), 0.0)
    assert sketch.call_names().count("Sketch.offset") == 7


def test_parametric_offset_constraint() -> None:
    design = fake_sketch_design()
    sketch = make_sketch(design)
    gc = sketch.geometricConstraints
    rect = polygon_curves(sketch, [(0, 0), (2, 0), (2, 1), (0, 1)])
    data = gc.createOffsetInput(OC.createWithArray(rect), VI.createByString("1 mm"))
    data.dirPoint = P(3, 0.5, 0)
    constraint = gc.addOffset2(data)
    assert constraint.objectType == "adsk::fusion::OffsetConstraint"
    assert constraint.parentCurves == rect
    children = list(constraint.childCurves)
    assert len(children) == 4
    assert sorted(xy(c.startSketchPoint) for c in children)[0] == (-0.1, -0.1)
    dimension = constraint.dimension
    assert dimension.objectType == "adsk::fusion::SketchOffsetCurvesDimension"
    assert dimension.parameter.expression == "1 mm"
    assert design.allParameters.itemByName(dimension.parameter.name) is dimension.parameter
    assert sketch.call_names() == [
        "geometricConstraints.createOffsetInput",
        "geometricConstraints.addOffset2",
    ]


def test_move_and_copy() -> None:
    sketch = make_sketch()
    one = line(sketch, (0, 0), (1, 0))
    shift = adsk.core.Matrix3D.create()
    shift.translation = V(1, 2, 0)
    assert sketch.move(OC.createWithArray([one]), shift)
    assert (xy(one.startSketchPoint), xy(one.endSketchPoint)) == ((1.0, 2.0), (2.0, 2.0))
    assert sketch.originPoint.geometry.x == 0.0

    turn = adsk.core.Matrix3D.create()
    turn.setToRotation(math.pi / 2, V(0, 0, 1), P(1, 2, 0))
    copies = sketch.copy(OC.createWithArray([one]), turn)
    assert copies.count == 1
    new = copies.item(0)
    assert new is not one and new.entityToken != one.entityToken
    assert (xy(new.startSketchPoint), xy(new.endSketchPoint)) == ((1.0, 2.0), (1.0, 3.0))
    assert xy(one.endSketchPoint) == (2.0, 2.0)  # the original is untouched
    assert sketch.call_names() == ["sketchLines.addByTwoPoints", "Sketch.move", "Sketch.copy"]


def test_rectangular_and_circular_patterns() -> None:
    design = fake_sketch_design()
    sketch = make_sketch(design)
    gc = sketch.geometricConstraints
    circle = circle_curve(sketch, (0, 0), 0.2)
    axis = line(sketch, (0, -1), (1, -1))
    spacing = adsk.fusion.PatternDistanceType.SpacingPatternDistanceType
    data = gc.createRectangularPatternInput(OC.createWithArray([circle]), spacing)
    data.setDirectionOne(axis, VI.createByReal(3), VI.createByString("10 mm"))
    data.setDirectionTwo(None, VI.createByReal(1), VI.createByReal(0))
    rect = gc.addRectangularPattern(data)
    assert rect.objectType == "adsk::fusion::RectangularPatternConstraint"
    centres = [xy(c.centerSketchPoint) for c in rect.createdEntities]
    assert centres == [(1.0, 0), (2.0, 0)]
    assert rect.distanceOne.expression == "10 mm"
    assert rect.quantityOne.expression == "3"

    center = sketch.originPoint
    dot = circle_curve(sketch, (1, 0), 0.1)
    data2 = gc.createCircularPatternInput(OC.createWithArray([dot]), center)
    data2.quantity = VI.createByReal(4)
    data2.totalAngle = VI.createByString("360 deg")
    circular = gc.addCircularPattern(data2)
    assert [xy(c.centerSketchPoint) for c in circular.createdEntities] == [
        (0, 1.0),
        (-1.0, 0),
        (0, -1.0),
    ]
    assert circular.centerPoint is center
    assert circular.totalAngle.expression == "360 deg"
    data3 = gc.createRectangularPatternInput([dot], spacing)
    with pytest.raises(RuntimeError, match="two instances"):
        gc.addRectangularPattern(data3)


def test_trim_and_extend() -> None:
    sketch = make_sketch()
    horizontal = line(sketch, (0, 0), (4, 0))
    cutter = line(sketch, (2, -1), (2, 1))
    kept = sketch.sketchCurves.sketchLines.item(0).trim(P(3, 0, 0), True)
    assert isinstance(kept, OC) and kept.count == 1
    piece = kept.item(0)
    assert not horizontal.isValid
    assert (xy(piece.startSketchPoint), xy(piece.endSketchPoint)) == ((0, 0), (2.0, 0))
    assert piece.startSketchPoint is horizontal.startSketchPoint
    tie = sketch.geometricConstraints.item(sketch.geometricConstraints.count - 1)
    assert (tie.point, tie.entity) == (piece.endSketchPoint, cutter)
    name, (curve, point, constraints) = sketch.calls[-1]
    assert (name, curve, constraints) == ("SketchLine.trim", horizontal, True)
    assert xyz(point) == (3.0, 0.0, 0.0)
    # The other side of the cut.
    other_side = line(sketch, (0, 0.5), (4, 0.5)).trim(P(1, 0.5, 0)).item(0)
    assert (xy(other_side.startSketchPoint), xy(other_side.endSketchPoint)) == ((2, 0.5), (4, 0.5))
    # No cutter: the whole curve goes, like in Fusion.
    lonely = line(sketch, (0, 9), (4, 9))
    assert lonely.trim(P(1, 9, 0)).count == 0 and not lonely.isValid

    circle = circle_curve(sketch, (0, 5), 1)
    line(sketch, (0, 3), (0, 7))
    count = sketch.geometricConstraints.count
    arc = circle.trim(P(1, 5, 0), False).item(0)
    assert arc.objectType == "adsk::fusion::SketchArc"
    assert (xy(arc.startSketchPoint), xy(arc.endSketchPoint)) == ((0, 6.0), (0, 4.0))
    assert not circle.isValid
    assert sketch.geometricConstraints.count == count  # createConstraints=False

    sketch = make_sketch()
    short = line(sketch, (10, 0), (11, 0))
    wall = line(sketch, (13, -1), (13, 1))
    longer = short.extend(P(11, 0, 0), True).item(0)
    assert (xy(longer.startSketchPoint), xy(longer.endSketchPoint)) == ((10.0, 0), (13.0, 0))
    assert not short.isValid
    tie = sketch.geometricConstraints.item(sketch.geometricConstraints.count - 1)
    assert tie.entity is wall
    # Nothing to reach: no change.
    assert longer.extend(P(10, 0, 0), True).count == 0
    assert longer.isValid


def test_add_fillet_between_lines() -> None:
    sketch = make_sketch()
    one = line(sketch, (0, 0), (2, 0))
    two = sketch.sketchCurves.sketchLines.addByTwoPoints(one.startSketchPoint, P(0, 2, 0))
    corner = one.startSketchPoint
    arcs = sketch.sketchCurves.sketchArcs
    arc = arcs.addFillet(one, P(1, 0, 0), two, P(0, 1, 0), 0.5)
    assert arc.objectType == "adsk::fusion::SketchArc"
    assert arc.radius == pytest.approx(0.5)
    assert xy(arc.centerSketchPoint) == (0.5, 0.5)
    assert {xy(arc.startSketchPoint), xy(arc.endSketchPoint)} == {(0.5, 0), (0, 0.5)}
    # The lines keep their tokens and now end at the tangent points.
    assert one.isValid and two.isValid
    assert xy(one.startSketchPoint) == (0.5, 0) and xy(two.startSketchPoint) == (0, 0.5)
    assert not corner.isValid
    tangents = list(sketch.geometricConstraints)
    assert [g.objectType.rsplit("::", 1)[1] for g in tangents] == ["TangentConstraint"] * 2
    assert [(g.curveOne, g.curveTwo) for g in tangents] == [(one, arc), (two, arc)]
    assert sketch.calls[-1][0] == "sketchArcs.addFillet"
    with pytest.raises(RuntimeError, match="too large"):
        arcs.addFillet(one, P(1, 0, 0), two, P(0, 1, 0), 5.0)


# --- units manager expressions (c04 FakeUnitsManager, c05 additions) -------------------------


def test_units_manager_expressions() -> None:
    design = fake_sketch_design()
    units = design.unitsManager
    design.parameter_set.add_user("width", "40 mm", "mm", "")
    design.parameter_set.add_user("tilt", "30 deg", "deg", "")
    assert units.isValidExpression("25 mm", "mm")
    assert units.isValidExpression("width / 2", "mm")
    assert units.isValidExpression("30 deg", "deg")
    assert units.isValidExpression("12", "mm")
    assert not units.isValidExpression("30 deg", "mm")
    assert not units.isValidExpression("width", "deg")
    assert not units.isValidExpression("tilt + 1 mm", "mm")
    assert not units.isValidExpression("unknown * 2", "mm")
    assert not units.isValidExpression("", "mm")
    assert not units.isValidExpression("2 +", "mm")
    assert units.evaluateExpression("25 mm", "mm") == pytest.approx(2.5)
    assert units.evaluateExpression("12", "mm") == pytest.approx(1.2)
    assert units.evaluateExpression("12", "cm") == pytest.approx(12.0)
    assert units.evaluateExpression("width / 2", "cm") == pytest.approx(2.0)
    assert units.evaluateExpression("90 deg", "deg") == pytest.approx(math.pi / 2)
    assert units.evaluateExpression("1 in", "DefaultDistance") == pytest.approx(2.54)
    assert units.evaluateExpression("10", "DefaultDistance") == pytest.approx(1.0)
    with pytest.raises(RuntimeError):
        units.evaluateExpression("30 deg", "mm")
    with pytest.raises(RuntimeError):
        units.evaluateExpression("nope", "mm")
    assert units.expression_calls[0] == ("validate", "25 mm", "mm")
    assert units.expression_calls[-1] == ("evaluate", "nope", "mm")
    assert units.convert(25, "mm", "cm") == pytest.approx(2.5)

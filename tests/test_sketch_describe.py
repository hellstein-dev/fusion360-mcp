"""``fusion_addin.sketch.describe``: points, curves, constraints, dimensions, texts and the
sketch status (c05 D5, tasks 3.5-3.6)."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.sketch import describe as d
from tests.fakes.fusion_sketch import (
    P,
    V,
    circle_curve,
    make_sketch,
    mark_fully_constrained,
    polygon_curves,
)

adsk_dims = fusion_addin.adsk.fusion.DimensionOrientations


def _ref(entity: Any) -> dict[str, Any]:
    return {"ref": entity.entityToken, "type": entity.objectType.rsplit("::", 1)[-1]}


# --- points and curves -------------------------------------------------------------------


def test_point() -> None:
    sketch = make_sketch()
    point = sketch.sketchPoints.add(P(1.5, -2.0, 0))
    point.isFixed = True
    assert d.describe_point(point) == {
        "ref": _ref(point),
        "xy": [15.0, -20.0],
        "fixed": True,
        "reference": False,
        "fully_constrained": False,
    }
    origin = d.describe_point(sketch.originPoint)
    assert origin["reference"] is True
    assert origin["fully_constrained"] is True


def test_line() -> None:
    sketch = make_sketch()
    line = sketch.sketchCurves.sketchLines.addByTwoPoints(P(0, 0, 0), P(3, 4, 0))
    line.isConstruction = True
    data = d.describe_curve(line)
    assert data == {
        "ref": _ref(line),
        "type": "line",
        "construction": True,
        "fixed": False,
        "reference": False,
        "fully_constrained": False,
        "length_mm": 50.0,
        "start": _ref(line.startSketchPoint),
        "end": _ref(line.endSketchPoint),
        "geometry": {"start": [0.0, 0.0], "end": [30.0, 40.0]},
    }


def test_circle() -> None:
    sketch = make_sketch()
    circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(P(1, 2, 0), 0.5)
    data = d.describe_curve(circle)
    assert data["type"] == "circle"
    assert data["start"] is None
    assert data["end"] is None
    assert data["center"] == _ref(circle.centerSketchPoint)
    assert data["length_mm"] == pytest.approx(10 * math.pi)
    assert data["geometry"] == {"center": [10.0, 20.0], "radius": 5.0}


def test_arc_angles_in_degrees_counter_clockwise() -> None:
    sketch = make_sketch()
    arc = sketch.sketchCurves.sketchArcs.addByCenterStartSweep(P(0, 0, 0), P(1, 0, 0), math.pi / 2)
    geometry = d.describe_curve(arc)["geometry"]
    assert geometry["center"] == [0.0, 0.0]
    assert geometry["radius"] == pytest.approx(10.0)
    assert geometry["start"] == [10.0, 0.0]
    assert geometry["end"] == pytest.approx([0.0, 10.0], abs=1e-6)
    assert geometry["start_angle"] == pytest.approx(0.0)
    assert geometry["sweep"] == pytest.approx(90.0)


def test_arc_with_downward_normal_has_negative_sweep() -> None:
    class Arc:
        objectType = "adsk::fusion::SketchArc"
        entityToken = "arc-1"
        radius = 1.0

        def __init__(self) -> None:
            self.centerSketchPoint = _Pt(0, 0)
            self.startSketchPoint = _Pt(1, 0)
            self.endSketchPoint = _Pt(0, 1)
            self.geometry = type("G", (), {"normal": V(0, 0, -1)})()

    geometry = d.curve_geometry(Arc(), "arc")
    assert geometry["start_angle"] == pytest.approx(0.0)
    assert geometry["sweep"] == pytest.approx(-270.0)


class _Pt:
    objectType = "adsk::fusion::SketchPoint"

    def __init__(self, x: float, y: float) -> None:
        self.geometry = P(x, y, 0)
        self.entityToken = f"pt-{x}-{y}"


def test_ellipse() -> None:
    sketch = make_sketch()
    ellipse = sketch.sketchCurves.sketchEllipses.add(P(0, 0, 0), P(0, 2, 0), P(-1, 0, 0))
    data = d.describe_curve(ellipse)
    assert data["type"] == "ellipse"
    assert data["start"] is None
    assert data["geometry"] == {
        "center": [0.0, 0.0],
        "major_axis": [0.0, 1.0],
        "major_radius": 20.0,
        "minor_radius": 10.0,
    }


def test_fitted_spline_open_and_closed() -> None:
    sketch = make_sketch()
    pts = [P(0, 0, 0), P(1, 1, 0), P(2, 0, 0)]
    spline = sketch.sketchCurves.sketchFittedSplines.add(pts)
    data = d.describe_curve(spline)
    assert data["type"] == "fitted_spline"
    assert data["geometry"] == {
        "fit_points": [[0.0, 0.0], [10.0, 10.0], [20.0, 0.0]],
        "closed": False,
    }
    assert data["start"] == _ref(spline.startSketchPoint)
    spline.isClosed = True
    closed = d.describe_curve(spline)
    assert closed["geometry"]["closed"] is True
    assert closed["start"] is None


def test_fixed_spline_and_unknown_curve() -> None:
    class Fixed:
        objectType = "adsk::fusion::SketchFixedSpline"
        entityToken = "fixed-1"
        length = 1.0
        geometry = type("G", (), {"controlPointCount": 7, "isClosed": True})()

    data = d.describe_curve(Fixed())
    assert data["type"] == "fixed_spline"
    assert data["geometry"] == {"control_point_count": 7, "closed": True}

    class Odd:
        objectType = "adsk::fusion::SketchWeirdCurve"
        entityToken = "odd-1"

    assert d.curve_type(Odd()) == "weird_curve"
    assert d.curve_geometry(Odd(), "weird_curve") == {}


def test_property_that_raises_is_treated_as_missing() -> None:
    class Line:
        objectType = "adsk::fusion::SketchLine"
        entityToken = "l-1"
        startSketchPoint = _Pt(0, 0)
        endSketchPoint = _Pt(1, 0)
        length = 1.0

        @property
        def isFixed(self) -> bool:
            raise RuntimeError("not applicable")

    data = d.describe_curve(Line())
    assert data["fixed"] is False
    assert data["construction"] is False


# --- status --------------------------------------------------------------------------------


def test_status_with_api_flag() -> None:
    sketch = make_sketch()
    lines = polygon_curves(sketch, [(0, 0), (1, 0), (1, 1)])
    status = d.sketch_status(sketch)
    assert status["fully_constrained"] is False
    # 3 lines + 3 points; the origin point is a reference and does not count.
    assert status["under_constrained_count"] == 6
    assert _ref(lines[0]) in status["under_constrained"]
    mark_fully_constrained(sketch)
    assert d.sketch_status(sketch) == {
        "fully_constrained": True,
        "under_constrained_count": 0,
        "under_constrained": [],
    }


def test_status_fallback_without_sketch_flag() -> None:
    sketch = make_sketch()
    polygon_curves(sketch, [(0, 0), (1, 0), (1, 1)])
    sketch.hide("isFullyConstrained")
    assert d.sketch_status(sketch)["fully_constrained"] is False
    mark_fully_constrained(sketch)
    assert d.sketch_status(sketch)["fully_constrained"] is True


def test_status_reference_curves_are_ignored_and_refs_limited() -> None:
    sketch = make_sketch()
    curves = polygon_curves(sketch, [(k, 0) for k in range(30)], closed=False)
    status = d.sketch_status(sketch, limit=5)
    assert status["under_constrained_count"] == 29 + 30
    assert len(status["under_constrained"]) == 5
    mark_fully_constrained(sketch)
    curves[0].isReference = True
    curves[0].isFullyConstrained = False
    assert d.sketch_status(sketch)["under_constrained_count"] == 0


# --- constraints -----------------------------------------------------------------------------


def test_constraints_by_class() -> None:
    sketch = make_sketch()
    lines = polygon_curves(sketch, [(0, 0), (2, 0), (2, 1), (0, 1)])
    circle = circle_curve(sketch, (1, 0.5), 0.2)
    gc = sketch.geometricConstraints
    horizontal = gc.addHorizontal(lines[0])
    parallel = gc.addParallel(lines[0], lines[2])
    coincident = gc.addCoincident(circle.centerSketchPoint, lines[0])
    midpoint = gc.addMidPoint(circle.centerSketchPoint, lines[2])
    points = gc.addHorizontalPoints(lines[0].startSketchPoint, lines[2].endSketchPoint)
    symmetry = gc.addSymmetry(lines[1], lines[3], lines[0])
    assert d.describe_constraint(horizontal) == {
        "ref": _ref(horizontal),
        "type": "horizontal",
        "entities": [_ref(lines[0])],
    }
    assert d.describe_constraint(parallel)["entities"] == [_ref(lines[0]), _ref(lines[2])]
    assert d.describe_constraint(coincident) == {
        "ref": _ref(coincident),
        "type": "coincident",
        "entities": [_ref(circle.centerSketchPoint), _ref(lines[0])],
    }
    assert d.describe_constraint(midpoint)["type"] == "midpoint"
    assert d.describe_constraint(points)["type"] == "horizontal_points"
    sym = d.describe_constraint(symmetry)
    assert sym["type"] == "symmetry"
    assert sym["entities"] == [_ref(lines[1]), _ref(lines[3]), _ref(lines[0])]


def test_unknown_constraint_class_has_no_entities() -> None:
    class Weird:
        objectType = "adsk::fusion::WeirdThingConstraint"
        entityToken = "w-1"
        line = object()

    assert d.describe_constraint(Weird()) == {
        "ref": {"ref": "w-1", "type": "WeirdThingConstraint"},
        "type": "weird_thing",
        "entities": [],
    }


# --- dimensions --------------------------------------------------------------------------------


def test_distance_dimension() -> None:
    sketch = make_sketch()
    line = sketch.sketchCurves.sketchLines.addByTwoPoints(P(0, 0, 0), P(2.5, 0, 0))
    dim = sketch.sketchDimensions.addDistanceDimension(
        line.startSketchPoint,
        line.endSketchPoint,
        adsk_dims.AlignedDimensionOrientation,
        P(1, 1, 0),
    )
    dim.parameter.name = "plate_w"
    assert d.describe_dimension(dim) == {
        "ref": _ref(dim),
        "type": "distance",
        "entities": [_ref(line.startSketchPoint), _ref(line.endSketchPoint)],
        "value": 25.0,
        "unit": "mm",
        "expression": "25 mm",
        "parameter": "plate_w",
        "driving": True,
    }


def test_angular_radial_ellipse_and_driven_dimensions() -> None:
    sketch = make_sketch()
    lines = polygon_curves(sketch, [(0, 0), (1, 0), (0, 1)], closed=False)
    angular = sketch.sketchDimensions.addAngularDimension(lines[0], lines[1], P(0.9, 0.05, 0))
    data = d.describe_dimension(angular)
    assert data["type"] == "angular"
    assert data["unit"] == "deg"
    assert data["value"] == pytest.approx(45.0)
    assert data["entities"] == [_ref(lines[0]), _ref(lines[1])]

    circle = circle_curve(sketch, (3, 3), 0.5)
    diameter = sketch.sketchDimensions.addDiameterDimension(circle, P(4, 4, 0), False)
    data = d.describe_dimension(diameter)
    assert data["type"] == "diameter"
    assert data["value"] == pytest.approx(10.0)
    assert data["driving"] is False
    assert data["entities"] == [_ref(circle)]

    ellipse = sketch.sketchCurves.sketchEllipses.add(P(0, 5, 0), P(2, 5, 0), P(0, 6, 0))
    major = sketch.sketchDimensions.addEllipseMajorRadiusDimension(ellipse, P(0, 8, 0))
    data = d.describe_dimension(major)
    assert data["type"] == "ellipse_major"
    assert data["value"] == pytest.approx(20.0)


def test_dimension_with_foreign_unit_is_marked() -> None:
    class Param:
        value = 3.0
        unit = "kg"
        expression = "3 kg"
        name = "d9"

    class Dim:
        objectType = "adsk::fusion::SketchStrangeDimension"
        entityToken = "dim-1"
        parameter = Param()
        isDriving = True

    data = d.describe_dimension(Dim())
    assert data["type"] == "strange"
    assert data["unit"] == "fusion_internal"
    assert data["entities"] == []


# --- texts and dispatch -------------------------------------------------------------------------


def test_text() -> None:
    sketch = make_sketch()
    texts = sketch.sketchTexts
    data = texts.createInput2("HELLO", 0.5)
    data.setAsMultiLine(P(1, 2, 0), P(4, 3, 0), 0, 0, 0.0)
    text = texts.add(data)
    assert d.describe_text(text) == {
        "ref": _ref(text),
        "text": "HELLO",
        "height": 5.0,
        "position": [10.0, 20.0],
    }


def test_describe_entity_dispatch() -> None:
    sketch = make_sketch()
    line = sketch.sketchCurves.sketchLines.addByTwoPoints(P(0, 0, 0), P(1, 0, 0))
    horizontal = sketch.geometricConstraints.addHorizontal(line)
    dim = sketch.sketchDimensions.addDistanceDimension(
        line.startSketchPoint, line.endSketchPoint, 0, P(0.5, 0.5, 0)
    )
    assert d.describe_entity(line)["type"] == "line"
    assert "xy" in d.describe_entity(line.startSketchPoint)
    assert d.describe_entity(horizontal)["type"] == "horizontal"
    assert d.describe_entity(dim)["type"] == "distance"
    assert d.describe_entity(sketch) == {
        "ref": {"ref": sketch.entityToken, "type": "Sketch", "name": sketch.name}
    }


def test_collections_of_a_sketch() -> None:
    sketch = make_sketch()
    polygon_curves(sketch, [(0, 0), (1, 0), (1, 1)])
    assert len(d.sketch_curves(sketch)) == 3
    assert len(d.sketch_points(sketch)) == 4
    assert d.sketch_constraints(sketch) == []
    assert d.sketch_dimensions(sketch) == []
    assert d.sketch_texts(sketch) == []

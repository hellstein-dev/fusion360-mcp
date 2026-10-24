"""Construction fakes behave like Fusion's collections and inputs (c06 D6, task 3.1)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core import health
from fusion_addin.core.describe import describe
from fusion_addin.core.dispatcher import rollback_timeline
from tests.fakes.brep import FakeComponent, FakeEdge, FakeOccurrence, circle_curve, fake_box_body
from tests.fakes.construction import (
    AXIS_SETTERS,
    PLANE_SETTERS,
    POINT_SETTERS,
    FakeConstructionAxes,
    FakeConstructionPlanes,
    FakeConstructionPoints,
    fake_construction_design,
    install_construction,
)

adsk = fusion_addin.adsk
P = adsk.core.Point3D.create
V = adsk.core.Vector3D.create
VI = adsk.core.ValueInput
HEALTH = adsk.fusion.FeatureHealthStates


def test_design_has_collections_on_the_active_root() -> None:
    design = fake_construction_design()
    root = design.rootComponent
    assert design.activeComponent is root
    assert isinstance(root.constructionPlanes, FakeConstructionPlanes)
    assert isinstance(root.constructionAxes, FakeConstructionAxes)
    assert isinstance(root.constructionPoints, FakeConstructionPoints)
    assert root.constructionPlanes.component is root


@pytest.mark.parametrize(
    ("attr", "setters", "object_type"),
    [
        ("constructionPlanes", PLANE_SETTERS, "ConstructionPlaneInput"),
        ("constructionAxes", AXIS_SETTERS, "ConstructionAxisInput"),
        ("constructionPoints", POINT_SETTERS, "ConstructionPointInput"),
    ],
)
def test_inputs_have_exactly_the_fusion_setters(
    attr: str, setters: tuple[str, ...], object_type: str
) -> None:
    collection = getattr(fake_construction_design().rootComponent, attr)
    inp = collection.createInput()
    assert inp.objectType == f"adsk::fusion::{object_type}"
    assert inp.occurrence is None
    for name in setters:
        assert getattr(inp, name)("a", "b") is True
    assert [name for name, _ in inp.calls] == list(setters)
    assert inp.call == (setters[-1], ("a", "b"))
    assert not hasattr(inp, "setByPlane")


def test_create_input_records_the_occurrence() -> None:
    component = install_construction(FakeComponent("Part"))
    occurrence = FakeOccurrence(component)
    inp = component.constructionAxes.createInput(occurrence)
    assert inp.occurrence is occurrence
    assert component.constructionAxes.inputs == [inp]


def test_add_offset_plane_computes_geometry_and_joins_timeline() -> None:
    design = fake_construction_design()
    root = design.rootComponent
    planes = root.constructionPlanes
    inp = planes.createInput()
    inp.setByOffset(root.xYConstructionPlane, VI.createByReal(1.5))
    plane = planes.add(inp)
    assert plane.name == "Plane1"
    assert plane.parentComponent is root
    assert plane.definition_call == ("setByOffset", inp.call[1])
    assert planes.count == 1 and planes.item(0) is plane
    assert design.timeline.names() == ["Plane1"]
    assert design.findEntityByToken(plane.entityToken) == [plane]
    data = describe(plane)
    assert data["origin_mm"] == [0.0, 0.0, 15.0]
    assert data["normal"] == [0.0, 0.0, 1.0]
    assert health.entity_health(plane) == {"state": "ok", "message": ""}


def test_axis_of_two_planes_and_center_point() -> None:
    root = fake_construction_design().rootComponent
    axes = root.constructionAxes
    inp = axes.createInput()
    inp.setByTwoPlanes(root.xZConstructionPlane, root.yZConstructionPlane)
    axis = axes.add(inp)
    assert [abs(c) for c in describe(axis)["direction"]] == [0.0, 0.0, 1.0]

    edge = FakeEdge(circle_curve(P(1, 2, 3), V(0, 0, 1), 0.5), 3.14, P(1.5, 2, 3))
    points = root.constructionPoints
    inp = points.createInput()
    inp.setByCenter(edge)
    assert describe(points.add(inp))["point_mm"] == [10.0, 20.0, 30.0]


def test_vertex_point_and_default_geometry() -> None:
    box = fake_box_body(1, 2, 3)
    root = fake_construction_design(box).rootComponent
    points = root.constructionPoints
    inp = points.createInput()
    inp.setByPoint(box.vertices.item(7))
    assert describe(points.add(inp))["point_mm"] == [10.0, 20.0, 30.0]
    planes = root.constructionPlanes
    inp = planes.createInput()
    inp.setByThreePoints("a", "b", "c")
    plane = planes.add(inp)
    assert describe(plane)["origin_mm"] == [0.0, 0.0, 0.0]


def test_geometry_override() -> None:
    root = fake_construction_design().rootComponent
    planes = root.constructionPlanes
    planes.geometry = (P(0, 0, 2), V(0, 1, 0))
    inp = planes.createInput()
    inp.setByTwoPlanes("a", "b")
    assert describe(planes.add(inp))["origin_mm"] == [0.0, 0.0, 20.0]


def test_setter_rejects_or_raises() -> None:
    planes = fake_construction_design().rootComponent.constructionPlanes
    planes.set_result = False
    assert planes.createInput().setByTwoPlanes("a", "b") is False
    planes.set_error = "bad inputs"
    inp = planes.createInput()
    with pytest.raises(RuntimeError, match="bad inputs"):
        inp.setByTwoPlanes("a", "b")
    assert inp.calls == [("setByTwoPlanes", ("a", "b"))]


def test_add_error_leaves_nothing_behind() -> None:
    design = fake_construction_design()
    axes = design.rootComponent.constructionAxes
    axes.add_error = "planes are parallel"
    inp = axes.createInput()
    inp.setByTwoPlanes("a", "b")
    with pytest.raises(RuntimeError, match="parallel"):
        axes.add(inp)
    assert axes.count == 0 and design.timeline.count == 0


def test_add_without_definition_raises() -> None:
    points = fake_construction_design().rootComponent.constructionPoints
    with pytest.raises(RuntimeError, match="setBy"):
        points.add(points.createInput())


def test_health_state_of_the_next_element() -> None:
    planes = fake_construction_design().rootComponent.constructionPlanes
    planes.health = HEALTH.ErrorFeatureHealthState
    planes.message = "Cannot compute the plane"
    inp = planes.createInput()
    inp.setByTwoPlanes("a", "b")
    plane = planes.add(inp)
    assert health.entity_health(plane) == {"state": "error", "message": "Cannot compute the plane"}


def test_rollback_through_timeline_removes_the_element() -> None:
    design = fake_construction_design()
    planes = design.rootComponent.constructionPlanes
    inp = planes.createInput()
    inp.setByTwoPlanes("a", "b")
    plane = planes.add(inp)
    rollback_timeline(design.timeline, 0, 0)
    assert planes.count == 0
    assert design.timeline.count == 0
    assert design.findEntityByToken(plane.entityToken) == []


def test_direct_design_has_no_timeline_item() -> None:
    design = fake_construction_design(parametric=False)
    points = design.rootComponent.constructionPoints
    inp: Any = points.createInput()
    inp.setByThreePlanes("a", "b", "c")
    point = points.add(inp)
    assert point.deleteMe() is True
    assert points.count == 0

"""``core/entity_kinds.py``: kind predicates and ``require_kind`` on c03 fakes (c06 D2, 2.1)."""

from __future__ import annotations

from typing import Any

import pytest

from fusion_addin.core.entity_kinds import (
    DESCRIPTIONS,
    KINDS,
    describe_kind,
    is_kind,
    require_kind,
)
from fusion_mcp.protocol.construction import METHODS
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import (
    FakeComponent,
    FakeConstructionAxis,
    FakeConstructionPlane,
    FakeConstructionPoint,
    FakeEdge,
    FakeFace,
    P,
    V,
    arc_curve,
    circle_curve,
    cone_surface,
    ellipse_curve,
    fake_box_body,
    fake_cylinder_face,
    nurbs_curve,
    nurbs_surface,
    sphere_surface,
    torus_surface,
)
from tests.fakes.design import FakeEntity


def sketch(kind: str) -> FakeEntity:
    return FakeEntity(f"adsk::fusion::{kind}", token=f"{kind}-1")


def entities() -> dict[str, Any]:
    box = fake_box_body(1, 2, 3)
    origin = FakeComponent("root")
    o = P(0, 0, 0)
    return {
        "planar_face": box.faces.item(0),
        "cylinder_face": fake_cylinder_face(0.5, 3),
        "cone_face": FakeFace(cone_surface(o, V(0, 0, 1), 1, 0.3), 1, o),
        "sphere_face": FakeFace(sphere_surface(o, 1), 1, o),
        "torus_face": FakeFace(torus_surface(o, V(0, 0, 1), 2, 0.5), 1, o),
        "nurbs_face": FakeFace(nurbs_surface(), 1, o),
        "line_edge": box.edges.item(0),
        "circle_edge": FakeEdge(circle_curve(o, V(0, 0, 1), 0.5), 3.14, P(0.5, 0, 0)),
        "arc_edge": FakeEdge(arc_curve(o, V(0, 0, 1), 0.5), 1.57, P(0.5, 0, 0)),
        "ellipse_edge": FakeEdge(ellipse_curve(o, V(0, 0, 1), 2, 1), 9, P(2, 0, 0)),
        "nurbs_edge": FakeEdge(nurbs_curve(), 5, o),
        "vertex": box.vertices.item(0),
        "construction_plane": FakeConstructionPlane("Plane1", o, V(0, 0, 1)),
        "construction_axis": FakeConstructionAxis("Axis1", o, V(1, 0, 0)),
        "construction_point": FakeConstructionPoint("Point1", o),
        "origin_XY": origin.xYConstructionPlane,
        "origin_Z": origin.zConstructionAxis,
        "origin_O": origin.originConstructionPoint,
        "sketch_point": sketch("SketchPoint"),
        "sketch_line": sketch("SketchLine"),
        "sketch_circle": sketch("SketchCircle"),
        "sketch_arc": sketch("SketchArc"),
        "sketch_ellipse": sketch("SketchEllipse"),
        "sketch_fitted_spline": sketch("SketchFittedSpline"),
        "sketch_fixed_spline": sketch("SketchFixedSpline"),
        "profile": sketch("Profile"),
        "body": box,
    }


# Which fake entities belong to each kind; everything else in ``entities()`` must not.
EXPECTED = {
    "planar": {"planar_face", "construction_plane", "origin_XY"},
    "linear": {"line_edge", "construction_axis", "origin_Z", "sketch_line"},
    "point": {"vertex", "construction_point", "origin_O", "sketch_point"},
    "path": {
        "line_edge",
        "circle_edge",
        "arc_edge",
        "ellipse_edge",
        "nurbs_edge",
        "sketch_line",
        "sketch_arc",
        "sketch_circle",
        "sketch_ellipse",
        "sketch_fitted_spline",
        "sketch_fixed_spline",
    },
    "circular_face": {"cylinder_face", "cone_face", "torus_face"},
    "tangent_face": {"cylinder_face", "cone_face"},
    "center_source": {
        "circle_edge",
        "arc_edge",
        "sphere_face",
        "torus_face",
        "sketch_circle",
        "sketch_arc",
    },
    "edge": {
        "line_edge",
        "circle_edge",
        "arc_edge",
        "ellipse_edge",
        "nurbs_edge",
        "construction_axis",
        "origin_Z",
        "sketch_line",
    },
    "face": {
        "planar_face",
        "cylinder_face",
        "cone_face",
        "sphere_face",
        "torus_face",
        "nurbs_face",
    },
}


def test_every_kind_used_by_the_method_table_has_a_predicate() -> None:
    used = {
        field.entity_kind
        for methods in METHODS.values()
        for spec in methods.values()
        for field in spec.fields
        if field.entity_kind is not None
    }
    assert used <= set(KINDS)
    assert set(KINDS) == set(EXPECTED) == set(DESCRIPTIONS)


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_kind_predicate(kind: str) -> None:
    members = {name for name, entity in entities().items() if is_kind(entity, kind)}
    assert members == EXPECTED[kind]


def test_require_kind_returns_the_entity() -> None:
    face = entities()["planar_face"]
    assert require_kind(face, "planar", "plane") is face


def test_require_kind_error_details() -> None:
    with pytest.raises(BridgeError) as info:
        require_kind(entities()["cylinder_face"], "planar", "plane")
    error = info.value
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details == {"param": "plane", "expected": "planar", "got": "BRepFace (cylinder)"}
    assert "planar face or a construction plane" in error.message


def test_require_kind_names_sketch_and_construction_types() -> None:
    with pytest.raises(BridgeError) as info:
        require_kind(entities()["sketch_circle"], "linear", "edge")
    assert info.value.details["got"] == "SketchCircle"
    with pytest.raises(BridgeError) as info:
        require_kind(entities()["origin_Z"], "point", "point")
    assert info.value.details["got"] == "ConstructionAxis"


def test_describe_kind_of_unclassified_geometry() -> None:
    all_entities = entities()
    assert describe_kind(all_entities["nurbs_face"]) == "BRepFace (other surface)"
    assert describe_kind(all_entities["ellipse_edge"]) == "BRepEdge (other curve)"
    assert describe_kind(all_entities["line_edge"]) == "BRepEdge (line)"
    assert describe_kind(all_entities["body"]) == "BRepBody"


def test_geometry_that_raises_is_not_classified() -> None:
    class Broken:
        objectType = "adsk::fusion::BRepFace"

        @property
        def geometry(self) -> Any:
            raise RuntimeError("geometry unavailable")

    assert not is_kind(Broken(), "planar")
    assert is_kind(Broken(), "face")
    assert describe_kind(Broken()) == "BRepFace (other surface)"


def test_unknown_kind_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="kind"):
        is_kind(entities()["vertex"], "solid")

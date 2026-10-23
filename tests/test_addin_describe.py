"""``fusion_addin.core.describe`` (c03 2.2-2.5, 2.7, D4)."""

import math

import pytest

from fusion_addin.core import refs
from fusion_addin.core.describe import describe
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import (
    CT,
    FakeBody,
    FakeComponent,
    FakeConstructionAxis,
    FakeConstructionPlane,
    FakeConstructionPoint,
    FakeCurve,
    FakeEdge,
    FakeFace,
    FakeFeature,
    FakeMatrix,
    FakeOccurrence,
    FakeSketch,
    FakeVertex,
    P,
    V,
    arc_curve,
    circle_curve,
    cone_surface,
    cylinder_surface,
    ellipse_curve,
    fake_box_body,
    nurbs_curve,
    nurbs_surface,
    plane_surface,
    sphere_surface,
    torus_surface,
)
from tests.fakes.design import FakeEntity


def faces_by_token(body: FakeBody) -> dict[str, FakeFace]:
    return {f.entityToken: f for f in body.faces}


# --- 2.2 dispatcher, fallback, detail, vertex --------------------------------------------


def test_unknown_type_falls_back_to_ref_type_name() -> None:
    entity = FakeEntity("adsk::fusion::Profile", "P1", token="tok")
    assert describe(entity) == {"ref": "tok", "type": "Profile", "name": "P1"}
    assert describe(entity, detail="full") == {"ref": "tok", "type": "Profile", "name": "P1"}


@pytest.mark.parametrize("detail", ["all", "", None, 1])
def test_bad_detail(detail: object) -> None:
    with pytest.raises(BridgeError) as info:
        describe(FakeEntity("adsk::fusion::Profile", token="t"), None, detail)  # type: ignore[arg-type]
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["param"] == "detail"


def test_vertex() -> None:
    body = fake_box_body(1, 2, 3, token="b")
    vertex = next(v for v in body.vertices if v.entityToken == "b-v111")
    assert describe(vertex) == {"ref": "b-v111", "type": "BRepVertex", "point_mm": [10, 20, 30]}
    full = describe(vertex, detail="full")
    assert full["edge_count"] == 3
    assert full["body"] == "b"


def test_refs_describe_delegates_to_brief() -> None:
    body = fake_box_body(1, 2, 3, token="b")
    vertex = body.vertices.item(0)
    assert refs.describe(vertex) == describe(vertex, None, "brief")


# --- 2.3 faces -------------------------------------------------------------------------------


def test_planar_face_brief_and_full() -> None:
    body = fake_box_body(1, 2, 3, token="b")
    top = faces_by_token(body)["b-face+z"]
    assert describe(top) == {
        "ref": "b-face+z",
        "type": "BRepFace",
        "surface_type": "plane",
        "area_mm2": 200.0,
        "centroid_mm": [5.0, 10.0, 30.0],
        "normal": [0.0, 0.0, 1.0],
        "body": "b",
    }
    assert top.evaluator.calls == [top.pointOnFace]
    full = describe(top, detail="full")
    assert full["bbox"] == {
        "min_mm": [0.0, 0.0, 30.0],
        "max_mm": [10.0, 20.0, 30.0],
        "size_mm": [10.0, 20.0, 0.0],
    }
    assert full["point_on_face_mm"] == [5.0, 10.0, 30.0]
    assert full["edge_count"] == 4
    assert full["geometry"] == {"origin_mm": [5.0, 10.0, 30.0], "normal": [0.0, 0.0, 1.0]}


def test_planar_normal_comes_from_evaluator() -> None:
    # Reversed face: the surface normal says +Z, the evaluator (face orientation) says -Z.
    face = FakeFace(plane_surface(P(0, 0, 0), V(0, 0, 1)), 1.0, P(0, 0, 0), normal=V(0, 0, -1))
    assert describe(face)["normal"] == [0.0, 0.0, -1.0]
    # No evaluator answer: the plane's own normal.
    face_without = FakeFace(plane_surface(P(0, 0, 0), V(0, 0, 2)), 1.0, P(0, 0, 0))
    assert describe(face_without)["normal"] == [0.0, 0.0, 1.0]


def full_geometry(face: FakeFace) -> dict:
    return describe(face, detail="full")["geometry"]


def test_cylinder_face() -> None:
    face = FakeFace(cylinder_surface(P(1, 0, 0), V(0, 0, 2), 0.5), 3.0, P(1, 0, 1))
    brief = describe(face)
    assert brief["surface_type"] == "cylinder"
    assert brief["normal"] is None
    assert brief["area_mm2"] == 300.0
    assert full_geometry(face) == {
        "origin_mm": [10.0, 0.0, 0.0],
        "axis": [0.0, 0.0, 1.0],
        "radius_mm": 5.0,
    }


def test_cone_face() -> None:
    face = FakeFace(cone_surface(P(0, 0, 0), V(0, 1, 0), 1.0, math.pi / 6), 1.0, P(0, 0, 0))
    assert describe(face)["surface_type"] == "cone"
    assert full_geometry(face) == {
        "origin_mm": [0.0, 0.0, 0.0],
        "axis": [0.0, 1.0, 0.0],
        "radius_mm": 10.0,
        "half_angle_deg": 30.0,
    }


def test_sphere_face() -> None:
    face = FakeFace(sphere_surface(P(0, 0, 1), 2.0), 1.0, P(0, 0, 1))
    assert describe(face)["surface_type"] == "sphere"
    assert full_geometry(face) == {"origin_mm": [0.0, 0.0, 10.0], "radius_mm": 20.0}


def test_torus_face() -> None:
    face = FakeFace(torus_surface(P(0, 0, 0), V(0, 0, 1), 3.0, 0.5), 1.0, P(0, 0, 0))
    assert describe(face)["surface_type"] == "torus"
    assert full_geometry(face) == {
        "origin_mm": [0.0, 0.0, 0.0],
        "axis": [0.0, 0.0, 1.0],
        "major_radius_mm": 30.0,
        "minor_radius_mm": 5.0,
    }


def test_nurbs_and_unknown_faces() -> None:
    face = FakeFace(nurbs_surface(), 1.0, P(0, 0, 0))
    assert describe(face)["surface_type"] == "nurbs"
    assert full_geometry(face) == {}
    face.geometry.surfaceType = 99
    assert describe(face)["surface_type"] == "other"


# --- 2.4 edges -------------------------------------------------------------------------------


def test_line_edge() -> None:
    body = fake_box_body(1, 2, 3, token="b")
    edge = next(e for e in body.edges if e.entityToken == "b-e000001")
    assert describe(edge) == {
        "ref": "b-e000001",
        "type": "BRepEdge",
        "curve_type": "line",
        "length_mm": 30.0,
        "mid_mm": [0.0, 0.0, 15.0],
        "body": "b",
    }
    full = describe(edge, detail="full")
    assert full["start_mm"] == [0.0, 0.0, 0.0]
    assert full["end_mm"] == [0.0, 0.0, 30.0]
    assert sorted(full["faces"]) == ["b-face-x", "b-face-y"]
    assert full["geometry"] == {"direction": [0.0, 0.0, 1.0]}
    assert full["bbox"]["size_mm"] == [0.0, 0.0, 30.0]


def test_closed_circle_edge_has_no_ends() -> None:
    seam = FakeVertex(P(1, 0, 0), token="seam")
    seam_again = FakeVertex(P(1, 0, 0), token="seam")  # Fusion hands out a new wrapper
    edge = FakeEdge(
        circle_curve(P(0, 0, 0), V(0, 0, 1), 1.0), 2 * math.pi, P(-1, 0, 0), seam, seam_again
    )
    full = describe(edge, detail="full")
    assert full["curve_type"] == "circle"
    assert full["start_mm"] is None
    assert full["end_mm"] is None
    assert full["geometry"] == {
        "center_mm": [0.0, 0.0, 0.0],
        "normal": [0.0, 0.0, 1.0],
        "radius_mm": 10.0,
    }
    no_vertices = FakeEdge(circle_curve(P(0, 0, 0), V(0, 0, 1), 1.0), 1.0, P(1, 0, 0))
    assert describe(no_vertices, detail="full")["start_mm"] is None


def test_arc_ellipse_and_other_edges() -> None:
    a, b = FakeVertex(P(1, 0, 0)), FakeVertex(P(0, 1, 0))
    arc = FakeEdge(arc_curve(P(0, 0, 0), V(0, 0, 1), 1.0), math.pi / 2, P(0.7, 0.7, 0), a, b)
    full = describe(arc, detail="full")
    assert full["curve_type"] == "arc"
    assert full["start_mm"] == [10.0, 0.0, 0.0]
    assert full["geometry"]["radius_mm"] == 10.0
    ellipse = FakeEdge(ellipse_curve(P(0, 0, 0), V(0, 0, 1), 2.0, 1.0), 9.7, P(2, 0, 0))
    assert describe(ellipse, detail="full")["geometry"] == {
        "center_mm": [0.0, 0.0, 0.0],
        "normal": [0.0, 0.0, 1.0],
        "major_radius_mm": 20.0,
        "minor_radius_mm": 10.0,
    }
    spline = FakeEdge(nurbs_curve(), 1.0, P(0, 0, 0), a, b)
    assert describe(spline)["curve_type"] == "nurbs"
    assert describe(spline, detail="full")["geometry"] == {}
    infinite = FakeEdge(FakeCurve(CT.InfiniteLine3DCurveType, "InfiniteLine3D"), 1.0, P(0, 0, 0))
    assert describe(infinite)["curve_type"] == "other"


# --- 2.5 bodies, components, occurrences, sketches ----------------------------------------


def test_body() -> None:
    component = FakeComponent("Part")
    body = fake_box_body(1, 2, 3, token="b")
    component.add_body(body)
    assert describe(body) == {
        "ref": "b",
        "type": "BRepBody",
        "name": "Box",
        "is_solid": True,
        "is_visible": True,
        "volume_mm3": 6000.0,
        "area_mm2": 2200.0,
        "component": component.entityToken,
    }
    full = describe(body, detail="full")
    assert (full["face_count"], full["edge_count"], full["vertex_count"]) == (6, 12, 8)
    assert full["bbox"]["size_mm"] == [10.0, 20.0, 30.0]
    assert full["material"] is None
    assert describe(FakeBody(material="Steel"), detail="full")["material"] == "Steel"


def test_component() -> None:
    component = FakeComponent("Part", token="c")
    component.add_body(fake_box_body(1, 1, 1))
    component.sketches.add(FakeSketch())
    assert describe(component) == {
        "ref": "c",
        "type": "Component",
        "name": "Part",
        "body_count": 1,
        "sketch_count": 1,
        "occurrence_count": 0,
    }
    assert "bbox" in describe(component, detail="full")


def test_occurrence_transform() -> None:
    component = FakeComponent("Part", token="c")
    turn = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    occurrence = FakeOccurrence(
        component, "Part:1", "Asm:1+Part:1", FakeMatrix(turn, (1, 2, 3)), True, token="o"
    )
    brief = describe(occurrence)
    assert brief == {
        "ref": "o",
        "type": "Occurrence",
        "name": "Part:1",
        "component": "c",
        "full_path_name": "Asm:1+Part:1",
        "is_grounded": True,
        "is_visible": True,
    }
    assert describe(occurrence, detail="full")["transform"] == {
        "translation_mm": [10.0, 20.0, 30.0],
        "rotation": turn,
    }


def test_sketch() -> None:
    component = FakeComponent("root")
    sketch = FakeSketch("S", 2, component.xYConstructionPlane, token="s")
    assert describe(sketch) == {
        "ref": "s",
        "type": "Sketch",
        "name": "S",
        "profile_count": 2,
        "is_visible": True,
        "plane": "origin:XY",
    }
    assert describe(FakeSketch(token="s2"))["plane"] is None


# --- 2.7 construction geometry and features ------------------------------------------------


def test_construction_geometry() -> None:
    plane = FakeConstructionPlane("Offset", P(0, 0, 1), V(0, 0, 3), token="pl")
    assert describe(plane) == {
        "ref": "pl",
        "type": "ConstructionPlane",
        "name": "Offset",
        "origin_mm": [0.0, 0.0, 10.0],
        "normal": [0.0, 0.0, 1.0],
    }
    axis = FakeConstructionAxis("A", P(1, 0, 0), V(0, 2, 0), token="ax")
    assert describe(axis)["direction"] == [0.0, 1.0, 0.0]
    assert describe(axis)["origin_mm"] == [10.0, 0.0, 0.0]
    cpoint = FakeConstructionPoint("Pt", P(0.5, 0, 0), token="pt")
    assert describe(cpoint)["point_mm"] == [5.0, 0.0, 0.0]


def test_feature() -> None:
    feature = FakeFeature("FilletFeature", "Fillet1", 1, "edge too short", 4, token="f")
    assert describe(feature) == {
        "ref": "f",
        "type": "FilletFeature",
        "name": "Fillet1",
        "health": "warning",
        "error_message": "edge too short",
        "timeline_index": 4,
        "is_suppressed": False,
    }
    plain = FakeFeature("ExtrudeFeature", health=None, is_suppressed=True, token="g")
    out = describe(plain)
    assert out["health"] == "unknown"
    assert out["timeline_index"] is None
    assert out["is_suppressed"] is True

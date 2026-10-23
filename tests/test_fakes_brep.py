"""Structure of the B-Rep fakes (c03 1.5, D13)."""

from tests.fakes.brep import (
    FakeComponent,
    FakeFeature,
    FakeOccurrence,
    FakeSketch,
    add_occurrence,
    fake_box_body,
    fake_cylinder_face,
    fake_part_design,
)
from tests.fakes.timeline import FakeTimeline


def test_box_topology() -> None:
    body = fake_box_body(1.0, 2.0, 3.0)
    assert (body.faces.count, body.edges.count, body.vertices.count) == (6, 12, 8)
    assert body.volume == 6.0
    assert body.area == 22.0
    assert body.objectType == "adsk::fusion::BRepBody"
    tokens = [e.entityToken for c in (body.faces, body.edges, body.vertices) for e in c]
    assert len(tokens) == len(set(tokens))
    for face in body.faces:
        assert face.edges.count == 4
        assert face.vertices.count == 4
        assert face.body is body
    for edge in body.edges:
        assert edge.faces.count == 2
    lengths = sorted(e.length for e in body.edges)
    assert lengths == [1.0] * 4 + [2.0] * 4 + [3.0] * 4


def test_box_faces_point_outwards() -> None:
    body = fake_box_body(1.0, 2.0, 3.0, token="b")
    by_token = {f.entityToken: f for f in body.faces}
    top = by_token["b-face+z"]
    ok, normal = top.evaluator.getNormalAtPoint(top.pointOnFace)
    assert ok
    assert normal.asArray() == [0.0, 0.0, 1.0]
    assert (top.centroid.x, top.centroid.y, top.centroid.z) == (0.5, 1.0, 3.0)
    assert top.area == 2.0
    bottom = by_token["b-face-z"]
    assert bottom.evaluator.getNormalAtPoint(bottom.pointOnFace)[1].z == -1.0
    assert by_token["b-face+x"].area == 6.0
    for face in body.faces:
        _, n = face.evaluator.getNormalAtPoint(face.pointOnFace)
        assert face.geometry.normal.asArray() == n.asArray()


def test_cylinder_face() -> None:
    face = fake_cylinder_face(0.5, 3.0)
    assert face.geometry.radius == 0.5
    assert face.geometry.axis.asArray() == [0.0, 0.0, 1.0]
    assert face.centroid.z == 1.5


def test_component_origin_and_design() -> None:
    body = fake_box_body(1, 1, 1)
    design = fake_part_design(body)
    root = design.rootComponent
    assert isinstance(root, FakeComponent)
    assert root.bRepBodies.item(0) is body
    assert body.parentComponent is root
    assert design.findEntityByToken(root.xYConstructionPlane.entityToken) == [
        root.xYConstructionPlane
    ]
    assert root.xYConstructionPlane.geometry.normal.asArray() == [0.0, 0.0, 1.0]
    assert root.zConstructionAxis.geometry.direction.asArray() == [0.0, 0.0, 1.0]
    assert len(root.origin_elements()) == 7
    assert design.findEntityByToken(body.faces.item(0).entityToken) == [body.faces.item(0)]


def test_occurrences_sketches_features_timeline() -> None:
    root = FakeComponent("root")
    part = FakeComponent("Part")
    occ = add_occurrence(root, FakeOccurrence(part, "Part:1"), root)
    child = add_occurrence(occ, FakeOccurrence(part, "Part:2", "Part:1+Part:2"), root)
    assert root.occurrences.count == 1
    assert occ.childOccurrences.item(0) is child
    assert root.allOccurrences.count == 2
    sketch = FakeSketch(profile_count=2, reference_plane=root.xYConstructionPlane)
    assert sketch.profiles.count == 2
    feature = FakeFeature("FilletFeature", "Fillet1", health=2, message="bad", timeline_index=3)
    assert feature.objectType == "adsk::fusion::FilletFeature"
    assert feature.timelineObject is not None
    assert feature.timelineObject.index == 3
    timeline = FakeTimeline(["Sketch1", "Extrude1"])
    assert timeline.count == 2

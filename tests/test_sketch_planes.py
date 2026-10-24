"""``fusion_addin/sketch/planes.py``: sketch plane resolution and creation (c05 D3, 3.4)."""

from typing import Any

import pytest

from fusion_addin.core.context import Context
from fusion_addin.sketch import planes
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import (
    FakeComponent,
    FakeConstructionPlane,
    FakeOccurrence,
    P,
    V,
    fake_box_body,
    fake_cylinder_face,
)
from tests.fakes.design import FakeApp
from tests.fakes.fusion_sketch import fake_sketch_design


def setup(*bodies: Any) -> tuple[Any, Context]:
    design = fake_sketch_design(*bodies)
    return design, Context(FakeApp(design))


def invalid(ctx: Context, plane: Any, component: Any = None) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        planes.resolve_sketch_plane(ctx, plane, component)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    return info.value


@pytest.mark.parametrize(
    ("ref", "attr"),
    [
        ("origin:XY", "xYConstructionPlane"),
        ("origin:XZ", "xZConstructionPlane"),
        ("origin:yz", "yZConstructionPlane"),
    ],
)
def test_origin_planes_of_root(ref: str, attr: str) -> None:
    design, ctx = setup()
    plane = planes.resolve_sketch_plane(ctx, ref)
    assert plane.entity is getattr(design.rootComponent, attr)
    assert plane.component is design.rootComponent
    assert plane.kind == planes.KIND_ORIGIN


@pytest.mark.parametrize(
    ("ref", "kind"),
    [
        ("origin:X", "ConstructionAxis"),
        ("origin:Z", "ConstructionAxis"),
        ("origin:O", "ConstructionPoint"),
    ],
)
def test_origin_axes_and_point_are_not_planes(ref: str, kind: str) -> None:
    _, ctx = setup()
    error = invalid(ctx, ref)
    assert error.details["reason"] == "not_a_plane"
    assert error.details["type"] == kind


def test_unknown_origin_name() -> None:
    _, ctx = setup()
    invalid(ctx, "origin:XW")


def test_origin_plane_of_given_component_entity_ref_and_occurrence() -> None:
    design, ctx = setup()
    part = FakeComponent("Part")
    occurrence = FakeOccurrence(part)
    design.add_entity(part)
    design.add_entity(occurrence)
    for component in (part, part.entityToken, {"ref": part.entityToken}, occurrence.entityToken):
        plane = planes.resolve_sketch_plane(ctx, {"ref": "origin:XZ"}, component)
        assert plane.entity is part.xZConstructionPlane
        assert plane.component is part


def test_component_must_be_a_component() -> None:
    design, ctx = setup()
    error = invalid(ctx, "origin:XY", design.rootComponent.xYConstructionPlane.entityToken)
    assert error.details["reason"] == "not_a_component"
    assert error.details["type"] == "ConstructionPlane"


def test_construction_plane_as_is() -> None:
    design, ctx = setup()
    offset = FakeConstructionPlane("Offset", P(0, 0, 1), V(0, 0, 1))
    design.add_entity(offset)
    plane = planes.resolve_sketch_plane(ctx, offset.entityToken)
    assert plane.entity is offset
    assert plane.kind == planes.KIND_CONSTRUCTION
    assert plane.component is design.rootComponent


def test_old_token_of_origin_plane_is_a_construction_plane() -> None:
    design, ctx = setup()
    xy = design.rootComponent.xYConstructionPlane
    assert planes.resolve_sketch_plane(ctx, xy.entityToken).entity is xy


def test_planar_face() -> None:
    body = fake_box_body(2, 3, 4)
    _, ctx = setup(body)
    top = next(f for f in body.faces if f.entityToken.endswith("face+z"))
    plane = planes.resolve_sketch_plane(ctx, top.entityToken)
    assert plane.entity is top
    assert plane.kind == planes.KIND_FACE


def test_cylindrical_face_is_not_planar() -> None:
    design, ctx = setup()
    face = fake_cylinder_face(1.0, 2.0)
    design.add_entity(face)
    error = invalid(ctx, face.entityToken)
    assert error.details["reason"] == "face_not_planar"
    assert error.details["surface_type"] == "Cylinder"


def test_edge_is_not_a_plane() -> None:
    body = fake_box_body(1, 1, 1)
    _, ctx = setup(body)
    edge = body.edges.item(0)
    error = invalid(ctx, edge.entityToken)
    assert error.details == {
        "param": "plane",
        "ref": edge.entityToken,
        "reason": "not_a_plane",
        "type": "BRepEdge",
    }


def test_unknown_token_is_not_found() -> None:
    _, ctx = setup()
    with pytest.raises(BridgeError) as info:
        planes.resolve_sketch_plane(ctx, "no-such-token")
    assert info.value.code is ErrorCode.ENTITY_NOT_FOUND


@pytest.mark.parametrize("value", [None, "", 5, {"ref": None}])
def test_bad_plane_value(value: Any) -> None:
    _, ctx = setup()
    error = invalid(ctx, value)
    assert error.details["param"] == "plane"


def test_create_on_origin_plane_uses_add() -> None:
    design, ctx = setup()
    for project_edges in (False, True):
        plane = planes.resolve_sketch_plane(ctx, "origin:XZ")
        sketch = planes.create_sketch(plane, project_edges)
        assert sketch.created_with == "add"
        assert sketch.referencePlane is design.rootComponent.xZConstructionPlane


def test_create_on_face_without_and_with_edges() -> None:
    body = fake_box_body(2, 3, 4)
    design, ctx = setup(body)
    top = next(f for f in body.faces if f.entityToken.endswith("face+z"))
    plane = planes.resolve_sketch_plane(ctx, top.entityToken)
    clean = planes.create_sketch(plane)
    assert clean.created_with == "addWithoutEdges"
    assert clean.sketchCurves.count == 0
    projected = planes.create_sketch(plane, project_edges=True)
    assert projected.created_with == "add"
    assert projected.sketchCurves.count == 4
    assert [name for name, _ in design.rootComponent.sketches.calls] == [
        "sketches.addWithoutEdges",
        "sketches.add",
    ]


def test_create_in_given_component() -> None:
    _, ctx = setup()
    part = FakeComponent("Part")
    plane = planes.resolve_sketch_plane(ctx, "origin:XY", part)
    sketch = planes.create_sketch(plane)
    assert part.sketches.item(0) is sketch


def test_create_returning_none_is_fusion_api_error() -> None:
    class NoSketches:
        def add(self, entity: Any) -> None:
            return None

    class Owner:
        sketches = NoSketches()

    plane = planes.SketchPlane(object(), Owner(), planes.KIND_CONSTRUCTION)
    with pytest.raises(BridgeError) as info:
        planes.create_sketch(plane)
    assert info.value.code is ErrorCode.FUSION_API_ERROR

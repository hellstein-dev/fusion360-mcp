"""``construction.*`` handlers on construction fakes (c06 D4, tasks 3.2-3.6)."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import construction as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.brep import (
    FakeComponent,
    FakeEdge,
    FakeOccurrence,
    P,
    V,
    add_occurrence,
    circle_curve,
    fake_box_body,
    fake_cylinder_face,
)
from tests.fakes.construction import fake_construction_design, install_construction
from tests.fakes.design import FakeApp, FakeEntity

adsk = fusion_addin.adsk
HEALTH = adsk.fusion.FeatureHealthStates
TOKEN = "t" * 32


def run(fn: Any, design: Any, params: dict[str, Any]) -> Any:
    return fn(Context(FakeApp(design), params), params)


def error(fn: Any, design: Any, params: dict[str, Any]) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        run(fn, design, params)
    return info.value


def dispatch(design: Any, method: str, params: dict[str, Any]) -> Any:
    dispatcher = Dispatcher(TOKEN, context_factory=lambda p: Context(FakeApp(design), p))
    return dispatcher.dispatch(Request("1", TOKEN, method, params))


def setter_call(collection: Any) -> tuple[str, tuple[Any, ...]]:
    """The ``setBy…`` call of the last input created on ``collection``."""
    return collection.inputs[-1].call


def box_design() -> tuple[Any, Any]:
    box = fake_box_body(1, 2, 3)  # cm: 10 x 20 x 30 mm
    design = fake_construction_design(box)
    return design, box


def face(box: Any, side: str) -> Any:
    return next(f for f in box.faces if f.entityToken.endswith(f"face{side}"))


# --- 3.2 shared flow, offset and angle -----------------------------------------------------------


def test_plane_handler_is_registered_as_mutating() -> None:
    spec = REGISTRY.get("construction.plane")
    assert spec is not None and spec.mutating is True


def test_offset_by_number() -> None:
    design = fake_construction_design()
    root = design.rootComponent
    out = run(
        h.construction_plane, design, {"method": "offset", "plane": "origin:XY", "distance": 15}
    )
    name, (plane, value) = setter_call(root.constructionPlanes)
    assert name == "setByOffset"
    assert plane is root.xYConstructionPlane
    assert (value.kind, value.value) == ("real", pytest.approx(1.5))
    created = root.constructionPlanes.item(0)
    assert out == {
        "ref": created.entityToken,
        "type": "ConstructionPlane",
        "name": "Plane1",
        "origin_mm": [0.0, 0.0, 15.0],
        "normal": [0.0, 0.0, 1.0],
        "health": "ok",
    }
    assert design.timeline.names() == ["Plane1"]


def test_offset_by_expression_and_request_units() -> None:
    design = fake_construction_design()
    planes = design.rootComponent.constructionPlanes
    run(
        h.construction_plane,
        design,
        {"method": "offset", "plane": "origin:XY", "distance": "height / 2"},
    )
    value = setter_call(planes)[1][1]
    assert (value.kind, value.value) == ("string", "height / 2")
    run(
        h.construction_plane,
        design,
        {"method": "offset", "plane": "origin:YZ", "distance": -2, "units": "in"},
    )
    value = setter_call(planes)[1][1]
    assert (value.kind, value.value) == ("real", pytest.approx(-5.08))


def test_offset_from_planar_face_by_token() -> None:
    design, box = box_design()
    top = face(box, "+z")
    run(h.construction_plane, design, {"method": "offset", "plane": top.entityToken, "distance": 5})
    assert setter_call(design.rootComponent.constructionPlanes)[1][0] is top


def test_angle_by_number_and_expression() -> None:
    design, box = box_design()
    root = design.rootComponent
    edge = box.edges.item(0)
    params = {"method": "angle", "edge": edge.entityToken, "angle": 30, "plane": "origin:XY"}
    run(h.construction_plane, design, params)
    name, (got_edge, value, plane) = setter_call(root.constructionPlanes)
    assert name == "setByAngle"
    assert got_edge is edge and plane is root.xYConstructionPlane
    assert (value.kind, value.value) == ("real", pytest.approx(math.radians(30)))
    run(h.construction_plane, design, {**params, "angle": "tilt * 2"})
    value = setter_call(root.constructionPlanes)[1][1]
    assert (value.kind, value.value) == ("string", "tilt * 2")


def test_angle_accepts_origin_axis_as_edge() -> None:
    design = fake_construction_design()
    root = design.rootComponent
    run(
        h.construction_plane,
        design,
        {"method": "angle", "edge": "origin:X", "angle": 45, "plane": "origin:XY"},
    )
    assert setter_call(root.constructionPlanes)[1][0] is root.xConstructionAxis


def test_wrong_entity_kind_fails_before_any_change() -> None:
    cylinder = fake_cylinder_face(0.5, 3, token="cyl")
    design = fake_construction_design()
    design.add_entity(cylinder)
    params = {"method": "offset", "plane": "cyl", "distance": 5}
    err = error(h.construction_plane, design, params)
    assert err.code is ErrorCode.INVALID_PARAMS
    assert err.details == {"param": "plane", "expected": "planar", "got": "BRepFace (cylinder)"}
    assert design.rootComponent.constructionPlanes.inputs == []
    response = dispatch(design, "construction.plane", params)
    assert response.ok is False and response.error.code is ErrorCode.INVALID_PARAMS
    assert design.timeline.count == 0


def test_origin_axis_is_not_a_plane() -> None:
    err = error(
        h.construction_plane,
        fake_construction_design(),
        {"method": "offset", "plane": "origin:Z", "distance": 1},
    )
    assert err.details == {"param": "plane", "expected": "planar", "got": "ConstructionAxis"}


def test_validation_errors_reach_the_agent() -> None:
    design = fake_construction_design()
    err = error(h.construction_plane, design, {"method": "offset", "plane": "origin:XY"})
    assert err.code is ErrorCode.INVALID_PARAMS and err.details["missing"] == ["distance"]
    err = error(h.construction_plane, design, {"method": "spin"})
    assert err.details["param"] == "method"
    err = error(
        h.construction_plane,
        design,
        {"method": "offset", "plane": "origin:XY", "distance": 1, "name": " "},
    )
    assert err.details == {"param": "name"}


def test_unknown_ref_is_entity_not_found() -> None:
    err = error(
        h.construction_plane,
        fake_construction_design(),
        {"method": "offset", "plane": "gone", "distance": 1},
    )
    assert err.code is ErrorCode.ENTITY_NOT_FOUND


def test_name_is_applied() -> None:
    design = fake_construction_design()
    out = run(
        h.construction_plane,
        design,
        {"method": "offset", "plane": "origin:XY", "distance": 15, "name": "Lid plane"},
    )
    assert out["name"] == "Lid plane"
    assert design.rootComponent.constructionPlanes.item(0).name == "Lid plane"


def test_health_error_rolls_back() -> None:
    design = fake_construction_design()
    planes = design.rootComponent.constructionPlanes
    planes.health = HEALTH.ErrorFeatureHealthState
    planes.message = "Reference is lost"
    response = dispatch(
        design, "construction.plane", {"method": "offset", "plane": "origin:XY", "distance": 1}
    )
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert "Reference is lost" in response.error.message
    assert response.error.details["health"] == {"state": "error", "message": "Reference is lost"}
    assert design.timeline.count == 0 and planes.count == 0


def test_health_warning_is_reported() -> None:
    design = fake_construction_design()
    planes = design.rootComponent.constructionPlanes
    planes.health = HEALTH.WarningFeatureHealthState
    planes.message = "Plane is outside the body"
    out = run(
        h.construction_plane, design, {"method": "offset", "plane": "origin:XY", "distance": 1}
    )
    assert out["health"] == "warning"
    assert out["warning"] == "Plane is outside the body"
    assert planes.count == 1


def test_rejected_inputs_are_fusion_api_error() -> None:
    design = fake_construction_design()
    planes = design.rootComponent.constructionPlanes
    planes.set_result = False
    response = dispatch(
        design, "construction.plane", {"method": "offset", "plane": "origin:XY", "distance": 1}
    )
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert response.error.message == "Fusion rejected inputs for offset"
    assert planes.added == [] and design.timeline.count == 0


def test_fusion_exception_in_add_rolls_back_with_its_message() -> None:
    design = fake_construction_design()
    design.timeline.add("Extrude1")
    planes = design.rootComponent.constructionPlanes
    planes.add_error = "InternalValidationError : bad geometry"
    response = dispatch(
        design, "construction.plane", {"method": "offset", "plane": "origin:XY", "distance": 1}
    )
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert "bad geometry" in response.error.message
    assert design.timeline.names() == ["Extrude1"]


def test_direct_design_takes_a_failed_element_back() -> None:
    design = fake_construction_design(parametric=False)
    planes = design.rootComponent.constructionPlanes
    planes.health = HEALTH.ErrorFeatureHealthState
    response = dispatch(
        design, "construction.plane", {"method": "offset", "plane": "origin:XY", "distance": 1}
    )
    assert response.ok is False
    assert response.error.details["rollback"] == "unavailable_direct_design"
    assert planes.count == 0


def test_default_target_is_the_active_component() -> None:
    design = fake_construction_design()
    part = install_construction(FakeComponent("Part"), design)
    design.activeComponent = part
    run(h.construction_plane, design, {"method": "offset", "plane": "origin:XY", "distance": 1})
    assert part.constructionPlanes.count == 1
    assert setter_call(part.constructionPlanes)[1][0] is part.xYConstructionPlane
    assert design.rootComponent.constructionPlanes.count == 0


def test_component_ref_targets_that_component() -> None:
    design = fake_construction_design()
    part = install_construction(FakeComponent("Part"), design)
    design.allComponents.add(part)
    run(
        h.construction_plane,
        design,
        {"method": "offset", "plane": "origin:XZ", "distance": 1, "component": "component:Part"},
    )
    assert part.constructionPlanes.inputs[-1].occurrence is None
    assert setter_call(part.constructionPlanes)[1][0] is part.xZConstructionPlane


def test_occurrence_ref_passes_the_occurrence_to_create_input() -> None:
    design = fake_construction_design()
    root = design.rootComponent
    part = install_construction(FakeComponent("Part"), design)
    occurrence = add_occurrence(root, FakeOccurrence(part, "Part:1"), root)
    run(
        h.construction_plane,
        design,
        {"method": "offset", "plane": "origin:XY", "distance": 1, "component": "occurrence:Part:1"},
    )
    assert part.constructionPlanes.inputs[-1].occurrence is occurrence


def test_component_ref_must_be_a_component() -> None:
    design, box = box_design()
    err = error(
        h.construction_plane,
        design,
        {"method": "offset", "plane": "origin:XY", "distance": 1, "component": box.entityToken},
    )
    assert err.code is ErrorCode.INVALID_PARAMS
    assert err.details == {"param": "component", "expected": "component", "got": "BRepBody"}


# --- 3.3 midplane, two_edges, three_points ---------------------------------------------------


def test_midplane_between_box_faces() -> None:
    design, box = box_design()
    top, bottom = face(box, "+z"), face(box, "-z")
    params = {"method": "midplane", "plane": top.entityToken, "plane2": bottom.entityToken}
    run(h.construction_plane, design, params)
    name, args = setter_call(design.rootComponent.constructionPlanes)
    assert name == "setByTwoPlanes"
    assert args == (top, bottom)


def test_midplane_needs_planar_second_plane() -> None:
    design, box = box_design()
    err = error(
        h.construction_plane,
        design,
        {"method": "midplane", "plane": "origin:XY", "plane2": box.edges.item(0).entityToken},
    )
    assert err.details == {"param": "plane2", "expected": "planar", "got": "BRepEdge (line)"}


def test_two_edges() -> None:
    design, box = box_design()
    one, two = box.edges.item(0), box.edges.item(1)
    run(
        h.construction_plane,
        design,
        {"method": "two_edges", "edge": one.entityToken, "edge2": two.entityToken},
    )
    assert setter_call(design.rootComponent.constructionPlanes) == ("setByTwoEdges", (one, two))


def test_two_edges_accepts_origin_axes() -> None:
    design = fake_construction_design()
    root = design.rootComponent
    params = {"method": "two_edges", "edge": "origin:X", "edge2": "origin:Y"}
    run(h.construction_plane, design, params)
    args = setter_call(root.constructionPlanes)[1]
    assert args == (root.xConstructionAxis, root.yConstructionAxis)


def test_three_points_mixes_vertices_and_origin_point() -> None:
    design, box = box_design()
    root = design.rootComponent
    a, b = box.vertices.item(1), box.vertices.item(2)
    params = {"method": "three_points", "points": [a.entityToken, b.entityToken, "origin:O"]}
    run(h.construction_plane, design, params)
    name, args = setter_call(root.constructionPlanes)
    assert name == "setByThreePoints"
    assert args == (a, b, root.originConstructionPoint)


def test_three_points_kind_error_names_the_index() -> None:
    design, box = box_design()
    params = {
        "method": "three_points",
        "points": [box.vertices.item(1).entityToken, "origin:Z", "origin:O"],
    }
    err = error(h.construction_plane, design, params)
    assert err.details == {"param": "points[1]", "expected": "point", "got": "ConstructionAxis"}
    assert design.rootComponent.constructionPlanes.inputs == []


# --- 3.4 tangent, tangent_at_point, along_path -----------------------------------------------


def cylinder_design() -> tuple[Any, Any]:
    design = fake_construction_design()
    cylinder = design.add_entity(fake_cylinder_face(0.5, 3, token="cyl"))
    return design, cylinder


def test_tangent_with_angle() -> None:
    design, cylinder = cylinder_design()
    root = design.rootComponent
    run(
        h.construction_plane,
        design,
        {"method": "tangent", "face": "cyl", "angle": 90, "plane": "origin:XZ"},
    )
    name, (got_face, value, plane) = setter_call(root.constructionPlanes)
    assert name == "setByTangent"
    assert got_face is cylinder and plane is root.xZConstructionPlane
    assert (value.kind, value.value) == ("real", pytest.approx(math.pi / 2))


def test_tangent_angle_defaults_to_zero() -> None:
    design, _ = cylinder_design()
    run(h.construction_plane, design, {"method": "tangent", "face": "cyl", "plane": "origin:XZ"})
    value = setter_call(design.rootComponent.constructionPlanes)[1][1]
    assert (value.kind, value.value) == ("real", 0.0)


def test_tangent_needs_cylindrical_or_conical_face() -> None:
    design, box = box_design()
    err = error(
        h.construction_plane,
        design,
        {"method": "tangent", "face": face(box, "+z").entityToken, "plane": "origin:XY"},
    )
    assert err.details == {"param": "face", "expected": "tangent_face", "got": "BRepFace (plane)"}


def test_tangent_at_point() -> None:
    design, cylinder = cylinder_design()
    root = design.rootComponent
    run(
        h.construction_plane,
        design,
        {"method": "tangent_at_point", "face": "cyl", "point": "origin:O"},
    )
    name, args = setter_call(root.constructionPlanes)
    assert name == "setByTangentAtPoint"
    assert args == (cylinder, root.originConstructionPoint)


def test_along_path_passes_the_proportion_as_a_real() -> None:
    design, box = box_design()
    edge = box.edges.item(0)
    run(
        h.construction_plane,
        design,
        {"method": "along_path", "path": edge.entityToken, "proportion": 0.5},
    )
    name, (path, value) = setter_call(design.rootComponent.constructionPlanes)
    assert name == "setByDistanceOnPath"
    assert path is edge
    assert (value.kind, value.value) == ("real", 0.5)


def test_along_path_accepts_a_sketch_curve_and_rejects_a_face() -> None:
    design, box = box_design()
    line = design.add_entity(FakeEntity("adsk::fusion::SketchLine", token="line-1"))
    run(h.construction_plane, design, {"method": "along_path", "path": "line-1", "proportion": 1})
    assert setter_call(design.rootComponent.constructionPlanes)[1][0] is line
    err = error(
        h.construction_plane,
        design,
        {"method": "along_path", "path": face(box, "+z").entityToken, "proportion": 0.5},
    )
    assert err.details["param"] == "path" and err.details["expected"] == "path"
    err = error(
        h.construction_plane, design, {"method": "along_path", "path": "line-1", "proportion": 2}
    )
    assert err.code is ErrorCode.INVALID_PARAMS and err.details["param"] == "proportion"


# --- 3.5 construction.axis -------------------------------------------------------------------


def test_axis_handler_is_registered_as_mutating() -> None:
    spec = REGISTRY.get("construction.axis")
    assert spec is not None and spec.mutating is True


def test_axis_two_points() -> None:
    design, box = box_design()
    a = box.vertices.item(0)
    params = {"method": "two_points", "points": [a.entityToken, "origin:O"]}
    run(h.construction_axis, design, params)
    root = design.rootComponent
    name, args = setter_call(root.constructionAxes)
    assert name == "setByTwoPoints"
    assert args == (a, root.originConstructionPoint)


def test_axis_two_points_needs_exactly_two() -> None:
    design, box = box_design()
    params = {"method": "two_points", "points": [box.vertices.item(0).entityToken]}
    err = error(h.construction_axis, design, params)
    assert err.code is ErrorCode.INVALID_PARAMS and err.details["param"] == "points"
    assert design.rootComponent.constructionAxes.inputs == []


def test_axis_edge() -> None:
    design, box = box_design()
    edge = box.edges.item(3)
    run(h.construction_axis, design, {"method": "edge", "edge": edge.entityToken})
    assert setter_call(design.rootComponent.constructionAxes) == ("setByEdge", (edge,))


def test_axis_edge_rejects_a_circular_edge() -> None:
    design = fake_construction_design()
    arc = design.add_entity(
        FakeEdge(circle_curve(P(0, 0, 0), V(0, 0, 1), 0.5), 3.14, P(0.5, 0, 0), token="circle")
    )
    err = error(h.construction_axis, design, {"method": "edge", "edge": arc.entityToken})
    assert err.details == {"param": "edge", "expected": "linear", "got": "BRepEdge (circle)"}


def test_axis_cylinder_returns_the_axis_description() -> None:
    design, cylinder = cylinder_design()
    root = design.rootComponent
    root.constructionAxes.geometry = (P(1, 2, 0), V(0, 0, 1))
    out = run(h.construction_axis, design, {"method": "cylinder", "face": "cyl"})
    assert setter_call(root.constructionAxes) == ("setByCircularFace", (cylinder,))
    assert out["type"] == "ConstructionAxis"
    assert out["origin_mm"] == [10.0, 20.0, 0.0]
    assert out["direction"] == [0.0, 0.0, 1.0]
    assert out["health"] == "ok"


def test_axis_cylinder_rejects_a_planar_face() -> None:
    design, box = box_design()
    err = error(
        h.construction_axis, design, {"method": "cylinder", "face": face(box, "+x").entityToken}
    )
    assert err.details == {"param": "face", "expected": "circular_face", "got": "BRepFace (plane)"}


def test_axis_perpendicular_at_point() -> None:
    design, box = box_design()
    top, corner = face(box, "+z"), box.vertices.item(7)
    params = {
        "method": "perpendicular_at_point",
        "face": top.entityToken,
        "point": corner.entityToken,
    }
    run(h.construction_axis, design, params)
    name, args = setter_call(design.rootComponent.constructionAxes)
    assert name == "setByPerpendicularAtPoint"
    assert args == (top, corner)


def test_axis_two_planes_of_origin() -> None:
    design = fake_construction_design()
    root = design.rootComponent
    out = run(
        h.construction_axis,
        design,
        {"method": "two_planes", "plane": "origin:XZ", "plane2": "origin:YZ"},
    )
    name, args = setter_call(root.constructionAxes)
    assert name == "setByTwoPlanes"
    assert args == (root.xZConstructionPlane, root.yZConstructionPlane)
    assert [abs(c) for c in out["direction"]] == [0.0, 0.0, 1.0]


def test_axis_two_parallel_planes_fails_without_a_trace() -> None:
    design = fake_construction_design()
    axes = design.rootComponent.constructionAxes
    axes.add_error = "The planes are parallel"
    response = dispatch(
        design,
        "construction.axis",
        {"method": "two_planes", "plane": "origin:XY", "plane2": "origin:XY"},
    )
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert "parallel" in response.error.message
    assert axes.count == 0 and design.timeline.count == 0

"""``sketch.*`` management handlers: create, list, get, profiles (c05 D3, D5, D8; tasks 4.1-4.4)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import sketch as h
from fusion_addin.sketch import space
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.sketch_refs import parse_profile_ref
from fusion_mcp.protocol.wire import Request
from tests.fakes.brep import FakeComponent, fake_box_body, fake_cylinder_face
from tests.fakes.design import FakeApp
from tests.fakes.fusion_sketch import (
    P,
    auto_profiles,
    circle_curve,
    fake_sketch_design,
    make_sketch,
    mark_fully_constrained,
    polygon_curves,
)

adsk = fusion_addin.adsk
TOKEN = "t" * 32
PLATE = [(0, 0), (10, 0), (10, 6), (0, 6)]  # cm: 100 x 60 mm
HOLES = [(1.5, 1.5), (8.5, 1.5), (1.5, 4.5), (8.5, 4.5)]


def run(fn: Any, design: Any, params: dict[str, Any] | None = None) -> Any:
    params = params or {}
    return fn(Context(FakeApp(design), params), params)


def error(fn: Any, design: Any, params: dict[str, Any] | None = None) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        run(fn, design, params)
    return info.value


def dispatch(design: Any, method: str, params: dict[str, Any]) -> Any:
    dispatcher = Dispatcher(TOKEN, context_factory=lambda p: Context(FakeApp(design), p))
    return dispatcher.dispatch(Request("1", TOKEN, method, params))


def ref(entity: Any) -> dict[str, Any]:
    data = {"ref": entity.entityToken, "type": entity.objectType.rsplit("::", 1)[-1]}
    name = getattr(entity, "name", None)
    if isinstance(name, str):
        data["name"] = name
    return data


def plate(design: Any = None) -> tuple[Any, list[Any], list[Any]]:
    sketch = make_sketch(design)
    lines = polygon_curves(sketch, PLATE)
    holes = [circle_curve(sketch, c, 0.5) for c in HOLES]
    sketch.profile_builder = auto_profiles
    return sketch, lines, holes


def test_registration() -> None:
    expected = {
        "sketch.create": True,
        "sketch.list": False,
        "sketch.get": False,
        "sketch.profiles": False,
    }
    for name, mutating in expected.items():
        spec = REGISTRY.get(name)
        assert spec is not None, name
        assert spec.mutating is mutating, name


# --- 4.1 sketch.create -----------------------------------------------------------------------


def test_create_on_origin_xy_with_name() -> None:
    design = fake_sketch_design()
    root = design.rootComponent
    out = run(h.sketch_create, design, {"plane": "origin:XY", "name": "Base"})
    sketch = root.sketches.item(0)
    assert sketch.name == "Base"
    assert out == {
        "sketch": {"ref": sketch.entityToken, "type": "Sketch", "name": "Base"},
        "name": "Base",
        "plane": {"ref": "origin:XY", "type": "ConstructionPlane", "name": "XY"},
        "component": ref(root),
        "transform": {
            "origin": [0.0, 0.0, 0.0],
            "x_axis": [1.0, 0.0, 0.0],
            "y_axis": [0.0, 1.0, 0.0],
            "normal": [0.0, 0.0, 1.0],
        },
    }
    assert root.sketches.calls == [("sketches.add", (root.xYConstructionPlane,))]


def test_create_on_xz_reports_axis_mapping() -> None:
    design = fake_sketch_design()
    out = run(h.sketch_create, design, {"plane": "origin:XZ"})
    assert out["name"] == "Sketch1"
    assert out["transform"]["x_axis"] == pytest.approx([1, 0, 0])
    assert out["transform"]["y_axis"] == pytest.approx([0, 0, -1])
    assert out["transform"]["normal"] == pytest.approx([0, 1, 0])


def test_create_on_planar_face_without_and_with_edges() -> None:
    body = fake_box_body(2, 3, 4)
    design = fake_sketch_design(body)
    top = next(f for f in body.faces if f.entityToken.endswith("face+z"))
    run(h.sketch_create, design, {"plane": top.entityToken})
    clean = design.rootComponent.sketches.item(0)
    assert clean.created_with == "addWithoutEdges"
    assert clean.sketchCurves.count == 0
    out = run(h.sketch_create, design, {"plane": top.entityToken, "project_edges": True})
    projected = design.rootComponent.sketches.item(1)
    assert projected.created_with == "add"
    assert projected.sketchCurves.count == 4
    assert out["plane"]["ref"] == top.entityToken
    assert out["transform"]["normal"] == pytest.approx([0, 0, 1])


def test_create_on_cylindrical_face_is_rejected_and_creates_nothing() -> None:
    design = fake_sketch_design()
    face = fake_cylinder_face(1.0, 2.0)
    design.add_entity(face)
    response = dispatch(design, "sketch.create", {"plane": face.entityToken})
    assert response.ok is False
    assert response.error.code is ErrorCode.INVALID_PARAMS
    assert response.error.details["reason"] == "face_not_planar"
    assert design.rootComponent.sketches.count == 0
    assert design.timeline.count == 0


def test_create_in_given_component() -> None:
    design = fake_sketch_design()
    part = FakeComponent("Part")
    design.add_entity(part)
    out = run(h.sketch_create, design, {"plane": "origin:YZ", "component": part.entityToken})
    assert part.sketches.count == 1
    assert design.rootComponent.sketches.count == 0
    assert out["component"] == ref(part)
    assert out["plane"]["ref"] == "origin:YZ"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"plane": 5},
        {"plane": "origin:XY", "name": "  "},
        {"plane": "origin:XY", "name": 3},
        {"plane": "origin:XY", "project_edges": "yes"},
        {"plane": "origin:X"},
    ],
)
def test_create_bad_params(params: dict[str, Any]) -> None:
    design = fake_sketch_design()
    assert error(h.sketch_create, design, params).code is ErrorCode.INVALID_PARAMS
    assert design.rootComponent.sketches.count == 0


def test_create_failure_after_add_is_rolled_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(sketch: Any) -> Any:
        raise RuntimeError("transform unavailable")

    monkeypatch.setattr(space, "describe_transform", boom)
    for parametric in (True, False):
        design = fake_sketch_design(parametric=parametric)
        response = dispatch(design, "sketch.create", {"plane": "origin:XY", "name": "Base"})
        assert response.ok is False
        assert design.rootComponent.sketches.count == 0
        if parametric:
            assert design.timeline.count == 0


# --- 4.2 sketch.list -------------------------------------------------------------------------


def test_list_all_components() -> None:
    design = fake_sketch_design()
    first, _, _ = plate(design)
    first.name = "Plate"
    mark_fully_constrained(first)
    part = FakeComponent("Part")
    design.allComponents.add(part)
    design.add_entity(part)
    other = part.sketches.add(part.xZConstructionPlane)
    other.isVisible = False
    out = run(h.sketch_list, design)
    assert out == {
        "sketches": [
            {
                "sketch": ref(first),
                "name": "Plate",
                "component": ref(design.rootComponent),
                "plane": {"ref": "origin:XY", "type": "ConstructionPlane", "name": "XY"},
                "fully_constrained": True,
                "curve_count": 8,
                "profile_count": 5,
                "visible": True,
            },
            {
                "sketch": ref(other),
                "name": "Sketch1",
                "component": ref(part),
                "plane": {"ref": "origin:XZ", "type": "ConstructionPlane", "name": "XZ"},
                "fully_constrained": True,
                "curve_count": 0,
                "profile_count": 0,
                "visible": False,
            },
        ]
    }


def test_list_one_component_and_empty() -> None:
    design = fake_sketch_design()
    assert run(h.sketch_list, design) == {"sketches": []}
    make_sketch(design)
    part = FakeComponent("Part")
    design.allComponents.add(part)
    design.add_entity(part)
    part.sketches.add(part.xYConstructionPlane)
    design.add_entity(design.rootComponent)
    root_only = run(h.sketch_list, design, {"component": design.rootComponent.entityToken})
    assert [s["component"]["ref"] for s in root_only["sketches"]] == [
        design.rootComponent.entityToken
    ]
    part_only = run(h.sketch_list, design, {"component": part.entityToken})
    assert len(part_only["sketches"]) == 1
    assert part_only["sketches"][0]["fully_constrained"] is True


def test_list_component_must_be_a_component() -> None:
    design = fake_sketch_design()
    sketch = make_sketch(design)
    err = error(h.sketch_list, design, {"component": sketch.entityToken})
    assert err.code is ErrorCode.INVALID_PARAMS
    assert err.details["reason"] == "not_a_component"


# --- 4.3 sketch.get --------------------------------------------------------------------------


def test_get_full_state() -> None:
    design = fake_sketch_design()
    sketch = make_sketch(design)
    line = sketch.sketchCurves.sketchLines.addByTwoPoints(P(0, 0, 0), P(5, 0, 0))
    horizontal = sketch.geometricConstraints.addHorizontal(line)
    dim = sketch.sketchDimensions.addDistanceDimension(
        line.startSketchPoint,
        line.endSketchPoint,
        adsk.fusion.DimensionOrientations.AlignedDimensionOrientation,
        P(2.5, 1, 0),
    )
    design.userParameters.add("plate_w", adsk.core.ValueInput.createByString("100 mm"), "mm", "")
    dim.parameter.expression = "plate_w / 2"
    out = run(h.sketch_get, design, {"sketch": sketch.entityToken})
    assert set(out) == {
        "sketch",
        "name",
        "component",
        "plane",
        "points",
        "curves",
        "constraints",
        "dimensions",
        "texts",
        "profiles",
        "transform",
        "status",
    }
    assert out["sketch"] == ref(sketch)
    curves = out["curves"]
    assert curves["total"] == 1 and curves["truncated"] is False
    entry = curves["items"][0]
    assert entry["type"] == "line"
    assert entry["geometry"] == {"start": [0.0, 0.0], "end": [50.0, 0.0]}
    assert entry["length_mm"] == 50.0
    # Origin point + two line ends.
    assert out["points"]["total"] == 3
    assert out["constraints"]["items"] == [
        {"ref": ref(horizontal), "type": "horizontal", "entities": [ref(line)]}
    ]
    dimension = out["dimensions"]["items"][0]
    assert dimension["expression"] == "plate_w / 2"
    assert dimension["parameter"] == dim.parameter.name
    assert dimension["driving"] is True
    assert dimension["value"] == pytest.approx(50.0)
    assert dimension["unit"] == "mm"
    assert out["texts"] == {"items": [], "total": 0, "truncated": False}
    assert out["profiles"] == {"items": [], "total": 0, "truncated": False}
    assert out["transform"]["normal"] == [0.0, 0.0, 1.0]
    status = out["status"]
    assert status["fully_constrained"] is False
    assert ref(line) in status["under_constrained"]


def test_get_section_filter_and_limit() -> None:
    design = fake_sketch_design()
    sketch = make_sketch(design)
    polygon_curves(sketch, [(k, 0) for k in range(26)], closed=False)
    out = run(
        h.sketch_get, design, {"sketch": sketch.entityToken, "include": ["curves"], "limit": 10}
    )
    assert "points" not in out and "profiles" not in out
    assert len(out["curves"]["items"]) == 10
    assert out["curves"]["total"] == 25
    assert out["curves"]["truncated"] is True
    assert "transform" in out and "status" in out


def test_get_fully_constrained_and_profiles_section() -> None:
    design = fake_sketch_design()
    sketch, _, _ = plate(design)
    mark_fully_constrained(sketch)
    out = run(
        h.sketch_get, design, {"sketch": sketch.entityToken, "include": ["profiles"], "limit": 2}
    )
    assert out["status"] == {
        "fully_constrained": True,
        "under_constrained_count": 0,
        "under_constrained": [],
    }
    assert out["profiles"]["total"] == 5
    assert len(out["profiles"]["items"]) == 2
    assert out["profiles"]["truncated"] is True


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({}, ErrorCode.INVALID_PARAMS),
        ({"include": ["curves", "bogus"]}, ErrorCode.INVALID_PARAMS),
        ({"include": []}, ErrorCode.INVALID_PARAMS),
        ({"include": "curves"}, ErrorCode.INVALID_PARAMS),
        ({"limit": 0}, ErrorCode.INVALID_PARAMS),
        ({"limit": 5001}, ErrorCode.INVALID_PARAMS),
        ({"limit": True}, ErrorCode.INVALID_PARAMS),
    ],
)
def test_get_bad_params(params: dict[str, Any], code: ErrorCode) -> None:
    design = fake_sketch_design()
    sketch = make_sketch(design)
    if params:
        params = {"sketch": sketch.entityToken, **params}
    assert error(h.sketch_get, design, params).code is code


def test_get_rejects_non_sketch_and_deleted_sketch() -> None:
    body = fake_box_body(1, 1, 1)
    design = fake_sketch_design(body)
    err = error(h.sketch_get, design, {"sketch": body.entityToken})
    assert err.code is ErrorCode.INVALID_PARAMS
    assert err.details["reason"] == "not_a_sketch"
    sketch = make_sketch(design)
    token = sketch.entityToken
    sketch.deleteMe()
    assert error(h.sketch_get, design, {"sketch": token}).code is ErrorCode.ENTITY_NOT_FOUND


# --- 4.4 sketch.profiles ---------------------------------------------------------------------


def test_profiles_plate_with_four_holes() -> None:
    design = fake_sketch_design()
    sketch, lines, _ = plate(design)
    out = run(h.sketch_profiles, design, {"sketch": sketch.entityToken})
    assert len(out["profiles"]) == 5
    body = next(p for p in out["profiles"] if p["inner_loops"])
    assert len(body["inner_loops"]) == 4
    assert body["outer_loop"]["curves"] == [ref(line) for line in lines]
    token, _, _ = parse_profile_ref(body["ref"]["ref"])
    assert token == sketch.entityToken


def test_profiles_contains_point_in_a_hole() -> None:
    design = fake_sketch_design()
    sketch, _, holes = plate(design)
    out = run(h.sketch_profiles, design, {"sketch": sketch.entityToken, "contains": [15, 15]})
    assert len(out["profiles"]) == 1
    hole = out["profiles"][0]
    assert hole["outer_loop"]["curves"] == [ref(holes[0])]
    assert hole["ref"]["name"] == "P1"


def test_profiles_contains_point_in_the_plate_and_outside() -> None:
    design = fake_sketch_design()
    sketch, _, _ = plate(design)
    inside = run(h.sketch_profiles, design, {"sketch": sketch.entityToken, "contains": [50, 30]})
    assert [len(p["inner_loops"]) for p in inside["profiles"]] == [4]
    outside = run(h.sketch_profiles, design, {"sketch": sketch.entityToken, "contains": [500, 30]})
    assert outside == {"profiles": []}
    # On the plate edge: no region strictly contains it.
    edge = run(h.sketch_profiles, design, {"sketch": sketch.entityToken, "contains": [0, 30]})
    assert edge == {"profiles": []}


def test_profiles_contains_in_request_units() -> None:
    design = fake_sketch_design()
    sketch, _, holes = plate(design)
    out = run(
        h.sketch_profiles,
        design,
        {"sketch": sketch.entityToken, "contains": [1.5, 1.5], "units": "cm"},
    )
    assert [p["outer_loop"]["curves"] for p in out["profiles"]] == [[ref(holes[0])]]


@pytest.mark.parametrize(
    "contains", [[1], [1, 2, 3], ["x", 0], [True, 0], "1,2", [float("nan"), 0]]
)
def test_profiles_bad_contains(contains: Any) -> None:
    design = fake_sketch_design()
    sketch, _, _ = plate(design)
    err = error(h.sketch_profiles, design, {"sketch": sketch.entityToken, "contains": contains})
    assert err.code is ErrorCode.INVALID_PARAMS
    assert err.details["param"] == "contains"


# --- shared helpers --------------------------------------------------------------------------


def test_sketch_state_with_and_without_profiles() -> None:
    design = fake_sketch_design()
    sketch, _, _ = plate(design)
    full = h.sketch_state(sketch)
    assert set(full) == {"status", "profiles"} and len(full["profiles"]) == 5
    assert set(h.sketch_state(sketch, return_profiles=False)) == {"status"}

"""``inspect.*`` handlers on fake designs (c03 3.1-3.5, D5-D9)."""

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import inspect
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import (
    FakeBody,
    FakeBoundingBox,
    FakeComponent,
    FakeConstructionPlane,
    FakeFeature,
    FakeMatrix,
    FakeMeasureManager,
    FakeOccurrence,
    FakePhysicalProperties,
    FakeSketch,
    P,
    V,
    add_face,
    add_occurrence,
    fake_box_body,
    fake_cylinder_face,
    fake_part_design,
    register_body,
)
from tests.fakes.design import FakeApp, FakeDesign
from tests.fakes.timeline import FakeTimeline

adsk = fusion_addin.adsk


def run(fn: Any, design: Any, params: dict[str, Any], app: FakeApp | None = None) -> Any:
    app = app or FakeApp(design)
    if not hasattr(app, "measureManager"):
        app.measureManager = FakeMeasureManager()  # type: ignore[attr-defined]
    return fn(Context(app, params), params)


def invalid(fn: Any, design: Any, params: dict[str, Any]) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        run(fn, design, params)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    return info.value


def test_handlers_registered_read_only() -> None:
    for name in (
        "inspect.describe",
        "inspect.tree",
        "inspect.bounding_box",
        "inspect.physical_properties",
        "inspect.query",
    ):
        spec = REGISTRY.get(name)
        assert spec is not None, name
        assert spec.mutating is False


# --- 3.1 inspect.describe --------------------------------------------------------------------


def test_describe_valid_stale_and_origin() -> None:
    box = fake_box_body(1, 2, 3, token="b")
    design = fake_part_design(box)
    out = run(
        inspect.inspect_describe,
        design,
        {"refs": ["b-face+z", "gone", "origin:xy", "bad-token"], "detail": "brief"},
    )
    assert [i["ref"] for i in out["items"]] == ["b-face+z", "origin:XY"]
    assert out["items"][1]["type"] == "ConstructionPlane"
    assert out["items"][1]["normal"] == [0.0, 0.0, 1.0]
    assert "bbox" not in out["items"][0]
    assert [(e["ref"], e["code"]) for e in out["errors"]] == [
        ("gone", "ENTITY_NOT_FOUND"),
        ("bad-token", "ENTITY_NOT_FOUND"),
    ]


def test_describe_default_detail_is_full() -> None:
    design = fake_part_design(fake_box_body(1, 2, 3, token="b"))
    out = run(inspect.inspect_describe, design, {"refs": ["b-face+z"]})
    assert "geometry" in out["items"][0]


def test_describe_only_errors_is_still_ok() -> None:
    out = run(inspect.inspect_describe, fake_part_design(), {"refs": ["origin:XW", 5]})
    assert out["items"] == []
    assert [e["code"] for e in out["errors"]] == ["INVALID_PARAMS", "INVALID_PARAMS"]


@pytest.mark.parametrize(
    "params",
    [{}, {"refs": []}, {"refs": "b"}, {"refs": ["x"] * 201}, {"refs": ["x"], "detail": "x"}],
)
def test_describe_invalid(params: dict[str, Any]) -> None:
    invalid(inspect.inspect_describe, fake_part_design(), params)


# --- 3.2 inspect.tree ------------------------------------------------------------------------


def part_with_features() -> tuple[FakeDesign, FakeComponent]:
    box = fake_box_body(1, 2, 3, token="b")
    design = fake_part_design(box)
    root = design.rootComponent
    sketch = FakeSketch("Sketch1", 1, root.xYConstructionPlane, token="s")
    root.sketches.add(sketch)
    offset = FakeConstructionPlane("Offset", P(0, 0, 1), V(0, 0, 1), token="pl")
    root.constructionPlanes.add(offset)
    root.constructionPlanes.add(root.xYConstructionPlane)  # origin: never listed
    timeline = FakeTimeline(["Sketch1", "Plane1", "Extrude1", "Group1", "Fillet1"])
    timeline.item(0).entity = sketch
    timeline.item(1).entity = offset
    timeline.item(2).entity = FakeFeature("ExtrudeFeature", "Extrude1", 0, "", 2, component=root)
    timeline.item(3).isGroup = True
    timeline.item(3).entity = None
    timeline.item(4).entity = FakeFeature("FilletFeature", "Fillet1", 2, "bad", 4, token="f")
    design._timeline = timeline
    return design, root


def test_tree_simple_part() -> None:
    design, _ = part_with_features()
    out = run(inspect.inspect_tree, design, {})
    assert out["design_type"] == "parametric"
    assert out["truncated"] is False
    node = out["root"]
    assert node["type"] == "Component"
    assert [b["ref"] for b in node["bodies"]] == ["b"]
    assert node["bodies"][0]["volume_mm3"] == 6000.0
    assert [s["ref"] for s in node["sketches"]] == ["s"]
    assert node["sketches"][0]["plane"] == "origin:XY"
    assert [c["ref"] for c in node["construction"]] == ["pl"]
    # Sketches, construction and groups are not features; a feature without a parent
    # component lands in the root node.
    assert [f["name"] for f in node["features"]] == ["Extrude1", "Fillet1"]
    assert node["features"][1]["health"] == "error"
    assert node["occurrences"] == []
    assert out["item_count"] == 6


def test_tree_nested_occurrences_and_depth() -> None:
    design = fake_part_design()
    root = design.rootComponent
    part = FakeComponent("Part")
    lid = add_occurrence(root, FakeOccurrence(part, "Lid:1", token="o1"), root)
    pin = add_occurrence(lid, FakeOccurrence(part, "Pin:1", "Lid:1+Pin:1", token="o2"), root)
    proxy = FakeBody("Pin body", token="pb")
    pin.bRepBodies.add(proxy)
    out = run(inspect.inspect_tree, design, {"include": ["occurrences", "bodies"]})
    lid_node = out["root"]["occurrences"][0]
    assert lid_node["ref"] == "o1"
    assert lid_node["full_path_name"] == "Lid:1"
    pin_node = lid_node["occurrences"][0]
    assert pin_node["full_path_name"] == "Lid:1+Pin:1"
    assert [b["ref"] for b in pin_node["bodies"]] == ["pb"]
    assert "sketches" not in out["root"]
    shallow = run(inspect.inspect_tree, design, {"depth": 1})
    assert shallow["root"]["occurrences"][0]["occurrences"] == []
    only_root = run(inspect.inspect_tree, design, {"depth": 0})
    assert only_root["root"]["occurrences"] == []


def test_tree_truncated() -> None:
    design = fake_part_design(*(fake_box_body(1, 1, 1) for _ in range(5)))
    out = run(inspect.inspect_tree, design, {"max_items": 3})
    assert out["truncated"] is True
    assert out["item_count"] == 3
    assert len(out["root"]["bodies"]) == 2


def test_tree_direct_design() -> None:
    design = fake_part_design(fake_box_body(1, 1, 1), parametric=False)
    out = run(inspect.inspect_tree, design, {})
    assert out["design_type"] == "direct"
    assert out["root"]["features"] == []


def test_tree_of_a_component_ref() -> None:
    design = fake_part_design()
    part = FakeComponent("Part", token="cp")
    part.add_body(fake_box_body(1, 1, 1, token="pbody"))
    design.add_entity(part)
    out = run(inspect.inspect_tree, design, {"component": "cp"})
    assert out["root"]["ref"] == "cp"
    assert [b["ref"] for b in out["root"]["bodies"]] == ["pbody"]


@pytest.mark.parametrize(
    "params",
    [
        {"depth": -1},
        {"depth": "1"},
        {"include": ["meshes"]},
        {"include": "bodies"},
        {"max_items": 0},
        {"component": "b"},
    ],
)
def test_tree_invalid(params: dict[str, Any]) -> None:
    invalid(inspect.inspect_tree, fake_part_design(fake_box_body(1, 1, 1, token="b")), params)


# --- 3.3 inspect.bounding_box ----------------------------------------------------------------


def test_bounding_box_default_all_visible_bodies() -> None:
    a = fake_box_body(1, 2, 3, token="a")
    hidden = fake_box_body(9, 9, 9, token="h")
    hidden.isVisible = False
    design = fake_part_design(a, hidden)
    part = FakeComponent("Part")
    occ = add_occurrence(design.rootComponent, FakeOccurrence(part, "P:1"), design.rootComponent)
    proxy = FakeBody("proxy", token="p", bounding_box=FakeBoundingBox((-1, 0, 0), (0, 1, 1)))
    occ.bRepBodies.add(proxy)
    manager = FakeMeasureManager()
    app = FakeApp(design)
    app.measureManager = manager  # type: ignore[attr-defined]
    out = run(inspect.inspect_bounding_box, design, {}, app)
    assert [i["ref"] for i in out["items"]] == ["a", "p"]
    assert out["items"][0] == {
        "ref": "a",
        "min_mm": [0.0, 0.0, 0.0],
        "max_mm": [10.0, 20.0, 30.0],
        "size_mm": [10.0, 20.0, 30.0],
        "tight": True,
    }
    assert out["union"]["min_mm"] == [-10.0, 0.0, 0.0]
    assert out["union"]["size_mm"] == [20.0, 20.0, 30.0]
    assert manager.calls[0][2:] == ([1.0, 0.0, 0.0], [0.0, 1.0, 0.0])


def test_bounding_box_tight_vs_fallback_and_errors() -> None:
    body = fake_box_body(1, 1, 1, token="b")
    body.boundingBox = FakeBoundingBox((-1, -1, -1), (2, 2, 2))  # loose
    body.tight_box = FakeBoundingBox((0, 0, 0), (1, 1, 1))  # type: ignore[attr-defined]
    design = fake_part_design(body)
    manager = FakeMeasureManager()
    manager.unsupported.add("BRepFace")
    app = FakeApp(design)
    app.measureManager = manager  # type: ignore[attr-defined]
    out = run(inspect.inspect_bounding_box, design, {"refs": ["b", "b-face+z", "nope"]}, app)
    by_ref = {i["ref"]: i for i in out["items"]}
    assert by_ref["b"]["size_mm"] == [10.0, 10.0, 10.0]
    assert by_ref["b"]["tight"] is True
    assert by_ref["b-face+z"]["tight"] is False
    assert [e["ref"] for e in out["errors"]] == ["nope"]


def test_bounding_box_component_is_union_of_bodies() -> None:
    design = fake_part_design(fake_box_body(1, 1, 1, token="a"))
    second = fake_box_body(1, 1, 1, token="c")
    second.boundingBox = FakeBoundingBox((2, 0, 0), (3, 1, 1))
    design.rootComponent.add_body(second)
    design.add_entity(design.rootComponent)
    out = run(inspect.inspect_bounding_box, design, {"refs": [design.rootComponent.entityToken]})
    assert out["items"][0]["size_mm"] == [30.0, 10.0, 10.0]
    assert out["items"][0]["tight"] is True


def test_bounding_box_empty_list_invalid() -> None:
    invalid(inspect.inspect_bounding_box, fake_part_design(), {"refs": []})


# --- 3.4 inspect.physical_properties ---------------------------------------------------------


def steel_props() -> FakePhysicalProperties:
    return FakePhysicalProperties(
        mass=0.0471,
        volume=6.0,
        area=22.0,
        density=0.00785,
        center=P(0.5, 1.0, 1.5),
        moments=(0.01, 0.02, 0.03, 0.0, 0.0, 0.0),
        principal=(0.01, 0.02, 0.03),
    )


def test_physical_properties_body() -> None:
    body = fake_box_body(1, 2, 3, token="b")
    body.physical = steel_props()
    body.material = type("M", (), {"name": "Steel"})()
    design = fake_part_design(body)
    out = run(inspect.inspect_physical_properties, design, {"refs": ["b"], "accuracy": "very_high"})
    item = out["items"][0]
    assert item["ref"] == "b"
    assert item["mass_kg"] == pytest.approx(0.0471)
    assert item["volume_mm3"] == pytest.approx(6000.0)
    assert item["area_mm2"] == pytest.approx(2200.0)
    assert item["density_kg_m3"] == pytest.approx(7850.0)
    assert item["center_of_mass_mm"] == [5.0, 10.0, 15.0]
    assert item["inertia_kg_mm2"]["xx"] == pytest.approx(1.0)
    assert item["principal_moments_kg_mm2"] == pytest.approx([1.0, 2.0, 3.0])
    assert item["material"] == "Steel"
    assert item["accuracy"] == "very_high"
    assert body.accuracy_calls == [adsk.fusion.CalculationAccuracy.VeryHighCalculationAccuracy]


def test_physical_properties_default_root_high_accuracy_and_failed_moments() -> None:
    design = fake_part_design()
    root = design.rootComponent
    root.physical = FakePhysicalProperties(mass=1.0, moments=None, principal=None)
    out = run(inspect.inspect_physical_properties, design, {})
    item = out["items"][0]
    assert item["ref"] == root.entityToken
    assert item["inertia_kg_mm2"] is None
    assert item["principal_moments_kg_mm2"] is None
    assert item["accuracy"] == "high"
    assert root.accuracy_calls == [adsk.fusion.CalculationAccuracy.HighCalculationAccuracy]


def test_physical_properties_occurrence_moves_center_of_mass() -> None:
    design = fake_part_design()
    part = FakeComponent("Part")
    part.physical = steel_props()
    occ = FakeOccurrence(part, "Part:1", transform=FakeMatrix(translation=(10, 0, 0)), token="o")
    add_occurrence(design.rootComponent, occ, design.rootComponent)
    design.add_entity(occ)
    out = run(inspect.inspect_physical_properties, design, {"refs": ["o", "origin:XY", "gone"]})
    assert out["items"][0]["center_of_mass_mm"] == [105.0, 10.0, 15.0]
    assert part.physical.centerOfMass.x == 0.5  # Fusion's point is not modified
    assert [e["ref"] for e in out["errors"]] == ["origin:XY", "gone"]
    assert out["errors"][0]["code"] == "INVALID_PARAMS"


def test_physical_properties_bad_accuracy() -> None:
    invalid(inspect.inspect_physical_properties, fake_part_design(), {"accuracy": "max"})


# --- 3.5 inspect.query -----------------------------------------------------------------------


def holed_body() -> FakeBody:
    body = fake_box_body(4, 4, 1, token="hb")
    add_face(body, fake_cylinder_face(0.5, 1.0, (2, 2, 0), token="hole"))
    add_face(body, fake_cylinder_face(1.0, 1.0, (0, 0, 1), (0, 0, -1), token="boss"))
    return body


def query(design: Any, **params: Any) -> dict[str, Any]:
    out: dict[str, Any] = run(inspect.inspect_query, design, params)
    return out


def test_query_top_face() -> None:
    design = fake_part_design(fake_box_body(1, 2, 3, token="b"))
    out = query(design, kind="face", surface_type="plane", normal=[0, 0, 1])
    assert [i["ref"] for i in out["items"]] == ["b-face+z"]
    assert out["total"] == 1
    assert out["truncated"] is False


def test_query_largest_face() -> None:
    design = fake_part_design(fake_box_body(1, 2, 3, token="b"))
    out = query(design, kind="face", sort_by="area", limit=1)
    assert out["total"] == 6
    assert out["truncated"] is True
    assert out["items"][0]["area_mm2"] == 600.0


def test_query_long_edges_in_body_scope() -> None:
    design = fake_part_design(fake_box_body(1, 2, 3, token="b"), fake_box_body(5, 5, 5, token="x"))
    out = query(design, kind="edge", scope=["b"], length_min_mm=25)
    assert out["total"] == 4
    assert all(i["length_mm"] == 30.0 for i in out["items"])
    assert all(i["direction"] == [0.0, 0.0, 1.0] for i in out["items"])


def test_query_cylinder_by_radius_and_axis() -> None:
    body = holed_body()
    design = fake_part_design(body)
    register_body(design, body)
    out = query(design, kind="face", surface_type="cylinder", radius_min_mm=4.9, radius_max_mm=5.1)
    assert [i["ref"] for i in out["items"]] == ["hole"]
    assert out["items"][0]["radius_mm"] == 5.0
    assert out["items"][0]["axis"] == [0.0, 0.0, 1.0]
    both = query(design, kind="face", axis=[0, 0, 1])
    assert sorted(i["ref"] for i in both["items"]) == ["boss", "hole"]


def test_query_scope_face_and_dedup() -> None:
    design = fake_part_design(fake_box_body(1, 2, 3, token="b"))
    out = query(design, kind="edge", scope=["b-face+z", "b-face+x"])
    assert out["total"] == 7  # 4 + 4 edges, one shared
    vertices = query(design, kind="vertex", scope=["b-face+z"])
    assert vertices["total"] == 4
    bodies = query(design, kind="body", scope=["b-face+z", "b"], sort_by="volume")
    assert [i["ref"] for i in bodies["items"]] == ["b"]
    assert bodies["items"][0]["bbox"]["size_mm"] == [10.0, 20.0, 30.0]


def test_query_validation_happens_first() -> None:
    error = invalid(
        inspect.inspect_query, fake_part_design(), {"kind": "vertex", "surface_type": "plane"}
    )
    assert error.details["param"] == "surface_type"
    invalid(
        inspect.inspect_query,
        fake_part_design(),
        {"kind": "face", "area_min_mm2": 100, "area_max_mm2": 10},
    )
    invalid(inspect.inspect_query, fake_part_design(), {"kind": "face", "scope": ["origin:XY"]})


def test_query_component_without_bodies_is_an_empty_scope() -> None:
    design = fake_part_design()
    design.add_entity(design.rootComponent)
    out = query(design, kind="face", scope=[design.rootComponent.entityToken])
    assert out == {"items": [], "total": 0, "truncated": False}


def test_query_stale_scope_is_not_found() -> None:
    with pytest.raises(BridgeError) as info:
        query(fake_part_design(), kind="face", scope=["gone"])
    assert info.value.code is ErrorCode.ENTITY_NOT_FOUND

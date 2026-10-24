"""``assembly.*`` rigid groups, joint listing and interference on fakes (c10 tasks 6.1, 6.3,
6.5)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.handlers import assembly
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.assembly import (
    FakeInterference,
    FakeInterferenceResult,
    add_component,
    fake_assembly_design,
)
from tests.fakes.brep import FakeBody, FakeBoundingBox, fake_box_body
from tests.fakes.design import FakeApp

adsk = fusion_addin.adsk
HEALTH = adsk.fusion.FeatureHealthStates
TOKEN = "t" * 32

BOX_EDGE = "bx-e011111"
LID_EDGE = "ld-e010110"


@pytest.fixture
def asm() -> Any:
    design = fake_assembly_design()
    design.box_component, design.box = add_component(
        design, "Box", fake_box_body(10, 6, 4, token="bx")
    )
    _, design.lid = add_component(design, "Lid", fake_box_body(10, 6, 0.5, token="ld"))
    return design


def make_ctx(design: Any) -> Context:
    return Context(FakeApp(design))


def run(fn: Any, design: Any, params: dict[str, Any]) -> Any:
    return fn(make_ctx(design), params)


def error_of(fn: Any, design: Any, params: dict[str, Any]) -> BridgeError:
    with pytest.raises(BridgeError) as exc:
        run(fn, design, params)
    return exc.value


def dispatch(design: Any, method: str, params: dict[str, Any]) -> Any:
    dispatcher = Dispatcher(TOKEN, context_factory=lambda p: Context(FakeApp(design), p))
    return dispatcher.dispatch(Request("1", TOKEN, method, params))


def box_copy(design: Any) -> Any:
    matrix = adsk.core.Matrix3D.create()
    return design.rootComponent.occurrences.addExistingComponent(design.box_component, matrix)


# --- 6.1 rigid groups ----------------------------------------------------------------------------


def test_rigid_group(asm: Any) -> None:
    out = run(
        assembly.assembly_create_rigid_group,
        asm,
        {
            "occurrences": ["occurrence:Box:1", "occurrence:Lid:1"],
            "include_children": False,
            "name": "Shell",
        },
    )["rigid_group"]
    groups = asm.rootComponent.rigidGroups
    group = groups.item(0)
    assert out == {
        "ref": group.entityToken,
        "name": "Shell",
        "occurrences": ["occurrence:Box:1", "occurrence:Lid:1"],
        "include_children": False,
        "is_suppressed": False,
    }
    assert groups.include_children is False
    assert [o.fullPathName for o in group.occurrences] == ["Box:1", "Lid:1"]


def test_rigid_group_of_one_occurrence(asm: Any) -> None:
    fn = assembly.assembly_create_rigid_group
    for params in (
        {"occurrences": ["occurrence:Box:1"]},
        {"occurrences": ["occurrence:Box:1", "occurrence:Box:1"]},
        {"occurrences": ["occurrence:Box:1", BOX_EDGE + "@Box:1"]},
        {"occurrences": ["occurrence:Box:1", "occurrence:Lid:1"], "name": " "},
        {"occurrences": "occurrence:Box:1"},
    ):
        assert error_of(fn, asm, params).code is ErrorCode.INVALID_PARAMS, params
    assert asm.rootComponent.rigidGroups.count == 0


def test_rigid_group_error_health_is_undone(asm: Any) -> None:
    groups = asm.rootComponent.rigidGroups
    groups.next_health = HEALTH.ErrorFeatureHealthState
    params = {"occurrences": ["occurrence:Box:1", "occurrence:Lid:1"]}
    err = error_of(assembly.assembly_create_rigid_group, asm, params)
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert groups.count == 0 and asm.timeline.count == 0


def test_rigid_group_add_failure_leaves_nothing(asm: Any) -> None:
    groups = asm.rootComponent.rigidGroups
    groups.fail_add = "rigid group failed"
    before = (asm.timeline.count, asm.timeline.markerPosition)
    response = dispatch(
        asm,
        "assembly.create_rigid_group",
        {"occurrences": ["occurrence:Box:1", "occurrence:Lid:1"]},
    )
    assert response.error is not None
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert groups.count == 0
    assert (asm.timeline.count, asm.timeline.markerPosition) == before


# --- 6.3 list_joints -----------------------------------------------------------------------------


def test_list_joints(asm: Any) -> None:
    hinge = {
        "type": "revolute",
        "geometry_one": {"kind": "curve", "entity": f"{LID_EDGE}@Lid:1"},
        "geometry_two": {"kind": "curve", "entity": f"{BOX_EDGE}@Box:1"},
        "motion": {"axis": f"{BOX_EDGE}@Box:1"},
        "name": "Hinge",
    }
    run(assembly.assembly_create_joint, asm, hinge)
    run(
        assembly.assembly_create_as_built_joint,
        asm,
        {
            "type": "rigid",
            "occurrence_one": "occurrence:Lid:1",
            "occurrence_two": "occurrence:Box:1",
        },
    )
    run(
        assembly.assembly_create_joint_origin,
        asm,
        {"geometry": {"kind": "planar_face", "entity": "ld-face+z@Lid:1"}, "name": "Top"},
    )
    run(
        assembly.assembly_create_rigid_group,
        asm,
        {"occurrences": ["occurrence:Box:1", "occurrence:Lid:1"]},
    )
    asm.rootComponent.joints.item(0).jointMotion.rotationValue = 3.141592653589793 / 2

    out = run(assembly.assembly_list_joints, asm, {})
    (joint,) = out["joints"]
    assert joint["name"] == "Hinge" and joint["type"] == "revolute"
    assert joint["values"] == {"rotation_deg": 90.0}
    assert joint["occurrence_one"] == "occurrence:Lid:1"
    (built,) = out["as_built_joints"]
    assert built["type"] == "rigid" and built["occurrence_two"] == "occurrence:Box:1"
    (origin,) = out["joint_origins"]
    native = asm.lid.component.jointOrigins.item(0).entityToken
    assert origin == {
        "ref": f"{native}@Lid:1",
        "native_ref": native,
        "name": "Top",
        "component": "component:Lid",
    }
    (group,) = out["rigid_groups"]
    assert group["occurrences"] == ["occurrence:Box:1", "occurrence:Lid:1"]


def test_list_joints_empty(asm: Any) -> None:
    out = run(assembly.assembly_list_joints, asm, {})
    assert out == {"joints": [], "as_built_joints": [], "joint_origins": [], "rigid_groups": []}


# --- 6.5 interference ----------------------------------------------------------------------------


def overlap(lo: tuple[float, float, float], hi: tuple[float, float, float]) -> FakeBody:
    volume = (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])
    return FakeBody("Interference", volume=volume, bounding_box=FakeBoundingBox(lo, hi))


def test_lid_resting_on_box(asm: Any) -> None:
    touch = FakeInterferenceResult(asm.box, asm.lid, overlap((0, 0, 4), (10, 6, 4)), True)
    interference = FakeInterference(asm, [touch])
    params = {"items": ["occurrence:Box:1", "occurrence:Lid:1"]}
    out = run(assembly.assembly_analyze_interference, asm, params)
    assert out == {"checked": 2, "count": 0, "interferences": []}
    assert interference.inputs[-1].areCoincidentFacesIncluded is False

    out = run(
        assembly.assembly_analyze_interference, asm, {**params, "include_coincident_faces": True}
    )
    assert out["count"] == 1
    assert interference.inputs[-1].areCoincidentFacesIncluded is True


def test_overlapping_copies(asm: Any) -> None:
    copy = box_copy(asm)
    found = FakeInterferenceResult(asm.box, copy, overlap((0, 0, 0), (10, 6, 4)))
    interference = FakeInterference(asm, [found])
    out = run(
        assembly.assembly_analyze_interference,
        asm,
        {"items": ["occurrence:Box:1", "occurrence:Box:2"]},
    )
    assert out["checked"] == 2 and out["count"] == 1
    (item,) = out["interferences"]
    assert item == {
        "entity_one": "occurrence:Box:1",
        "entity_two": "occurrence:Box:2",
        "volume_mm3": pytest.approx(240000.0),
        "bbox": {
            "min_mm": [0.0, 0.0, 0.0],
            "max_mm": [100.0, 60.0, 40.0],
            "size_mm": [100.0, 60.0, 40.0],
        },
    }
    assert interference.analyzed[-1].bodies_created is False
    assert asm.timeline.count == 0


def test_interference_default_items(asm: Any) -> None:
    root_body = fake_box_body(1, 1, 1, token="rb")
    asm.rootComponent.add_body(root_body)
    interference = FakeInterference(asm, [])
    out = run(assembly.assembly_analyze_interference, asm, {})
    assert out == {"checked": 3, "count": 0, "interferences": []}
    entities = interference.inputs[-1].entities
    passed = [entities.item(i) for i in range(entities.count)]
    assert passed == [asm.box, asm.lid, root_body]


def test_interference_body_refs_are_coerced(asm: Any) -> None:
    lid_body = asm.lid.component.bRepBodies.item(0)
    interference = FakeInterference(asm, [])
    run(assembly.assembly_analyze_interference, asm, {"items": ["bx", "ld@Lid:1"]})
    entities = interference.inputs[-1].entities
    first, second = (entities.item(i) for i in range(2))
    assert first.nativeObject.entityToken == "bx" and first.assemblyContext is asm.box
    assert second.nativeObject is lid_body and second.assemblyContext.fullPathName == "Lid:1"


def test_interference_errors(asm: Any) -> None:
    FakeInterference(asm, [])
    fn = assembly.assembly_analyze_interference
    for params in (
        {"items": ["occurrence:Box:1"]},
        {"items": ["occurrence:Box:1", "occurrence:Box:1"]},
        {"items": ["occurrence:Box:1", f"{BOX_EDGE}@Box:1"]},
        {"items": ["occurrence:Box:1", "component:Lid"]},
        {"items": ["occurrence:Box:1"], "include_coincident_faces": "yes"},
    ):
        assert error_of(fn, asm, params).code is ErrorCode.INVALID_PARAMS, params


def test_interference_needs_two_items_by_default() -> None:
    design = fake_assembly_design()
    add_component(design, "Box", fake_box_body(10, 6, 4, token="bx"))
    FakeInterference(design, [])
    err = error_of(assembly.assembly_analyze_interference, design, {})
    assert err.code is ErrorCode.INVALID_PARAMS and err.details["count"] == 1


def test_interference_ambiguous_native_body(asm: Any) -> None:
    box_copy(asm)
    FakeInterference(asm, [])
    err = error_of(
        assembly.assembly_analyze_interference, asm, {"items": ["bx", "occurrence:Lid:1"]}
    )
    assert err.code is ErrorCode.INVALID_PARAMS
    assert err.details["candidates"] == ["bx@Box:1", "bx@Box:2"]


def test_interference_not_available(asm: Any) -> None:
    err = error_of(
        assembly.assembly_analyze_interference,
        asm,
        {"items": ["occurrence:Box:1", "occurrence:Lid:1"]},
    )
    assert err.code is ErrorCode.FUSION_API_ERROR

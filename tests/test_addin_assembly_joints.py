"""``assembly.*`` joint handlers on fakes (c10 tasks 4.6, 5.1, 5.3, 5.5, 5.7, 5.9)."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.handlers import assembly
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.assembly import FakeJointMotion, add_component, fake_assembly_design
from tests.fakes.brep import fake_box_body
from tests.fakes.design import FakeApp, FakeEntity

adsk = fusion_addin.adsk
DIR = adsk.fusion.JointDirections
HEALTH = adsk.fusion.FeatureHealthStates
TOKEN = "t" * 32

BOX_EDGE = "bx-e011111"
LID_EDGE = "ld-e010110"


@pytest.fixture
def asm() -> Any:
    design = fake_assembly_design()
    _, design.box = add_component(design, "Box", fake_box_body(10, 6, 4, token="bx"))
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


def curve(ref: str) -> dict[str, str]:
    return {"kind": "curve", "entity": ref}


HINGE = {
    "type": "revolute",
    "geometry_one": curve(f"{LID_EDGE}@Lid:1"),
    "geometry_two": curve(f"{BOX_EDGE}@Box:1"),
    "motion": {"axis": f"{BOX_EDGE}@Box:1"},
}


def hinge(design: Any, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = run(assembly.assembly_create_joint, design, {**HINGE, **extra})
    return out["joint"]


# --- 4.6 _joint --------------------------------------------------------------------------------


def joint_with(motion: FakeJointMotion, **attrs: Any) -> Any:
    return FakeEntity("adsk::fusion::Joint", "J", token="j-1", jointMotion=motion, **attrs)


def test_joint_form_cylindrical(asm: Any) -> None:
    motion = FakeJointMotion("cylindrical")
    motion.rotationValue = math.pi / 2
    motion.slideValue = 1.5
    motion.slideLimits.isMaximumValueEnabled = True
    motion.slideLimits.maximumValue = 2.0
    motion.rotationLimits.isRestValueEnabled = True
    motion.rotationLimits.restValue = math.pi
    joint = joint_with(
        motion,
        occurrenceOne=asm.lid,
        occurrenceTwo=None,
        isSuppressed=True,
        healthState=HEALTH.WarningFeatureHealthState,
        errorOrWarningMessage="check",
    )
    out = assembly._joint(make_ctx(asm), joint)
    assert out == {
        "ref": "j-1",
        "name": "J",
        "type": "cylindrical",
        "occurrence_one": "occurrence:Lid:1",
        "occurrence_two": None,
        "values": {"rotation_deg": 90.0, "slide_mm": 15.0},
        "limits": {
            "rotation": {"min": None, "max": None, "rest": 180.0},
            "slide": {"min": None, "max": 20.0, "rest": None},
        },
        "is_suppressed": True,
        "health": "warning",
        "message": "check",
    }


def test_joint_form_each_type(asm: Any) -> None:
    keys = {
        "rigid": [],
        "revolute": ["rotation_deg"],
        "slider": ["slide_mm"],
        "pin_slot": ["rotation_deg", "slide_mm"],
        "planar": ["primary_slide_mm", "secondary_slide_mm", "rotation_deg"],
        "ball": ["pitch_deg", "yaw_deg", "roll_deg"],
    }
    for joint_type, expected in keys.items():
        out = assembly._joint(make_ctx(asm), joint_with(FakeJointMotion(joint_type)))
        assert out["type"] == joint_type and list(out["values"]) == expected
        assert out["health"] == "unknown"


# --- 5.1 joint origins -------------------------------------------------------------------------


def test_joint_origin_on_proxy_face(asm: Any) -> None:
    out = run(
        assembly.assembly_create_joint_origin,
        asm,
        {
            "geometry": {"kind": "planar_face", "entity": "ld-face+z@Lid:1"},
            "name": "Top",
            "offset_mm": [0, 0, 5],
            "angle": 90,
            "flip": True,
        },
    )["joint_origin"]
    origins = asm.lid.component.jointOrigins
    origin = origins.item(0)
    assert out == {
        "ref": f"{origin.entityToken}@Lid:1",
        "native_ref": origin.entityToken,
        "name": "Top",
        "component": "component:Lid",
    }
    geometry = origin.geometry
    assert geometry.method == "createByPlanarFace"
    assert geometry.args[0].entityToken == "ld-face+z"  # native face, not the proxy
    origin_input = origins.inputs[-1]
    assert [
        v.value for v in (origin_input.offsetX, origin_input.offsetY, origin_input.offsetZ)
    ] == [
        0.0,
        0.0,
        0.5,
    ]
    assert origin_input.angle.value == pytest.approx(math.pi / 2) and origin_input.isFlipped


def test_joint_origin_in_root(asm: Any) -> None:
    out = run(
        assembly.assembly_create_joint_origin,
        asm,
        {"geometry": {"kind": "point", "entity": "origin:O"}},
    )["joint_origin"]
    assert out["component"] == "component:root" and out["ref"] == out["native_ref"]
    assert asm.rootComponent.jointOrigins.count == 1


def test_joint_origin_errors(asm: Any) -> None:
    fn = assembly.assembly_create_joint_origin
    for params in (
        {"geometry": {"kind": "joint_origin", "entity": "x"}},
        {"geometry": {"kind": "sphere", "entity": "x"}},
        {"geometry": {"kind": "planar_face", "entity": "ld-face+z", "edge": BOX_EDGE}},
        {"geometry": {"kind": "point", "entity": "ld-v000"}, "offset_mm": [1, 2]},
        {"geometry": {"kind": "point", "entity": "ld-v000"}, "name": " "},
    ):
        assert error_of(fn, asm, params).code is ErrorCode.INVALID_PARAMS, params
    assert asm.lid.component.jointOrigins.count == 0


def test_joint_origin_error_health_is_undone(asm: Any) -> None:
    origins = asm.lid.component.jointOrigins
    origins.next_health = HEALTH.ErrorFeatureHealthState
    err = error_of(
        assembly.assembly_create_joint_origin,
        asm,
        {"geometry": {"kind": "point", "entity": "ld-v000"}},
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert origins.count == 0 and asm.timeline.count == 0


# --- 5.3 joints --------------------------------------------------------------------------------


def test_revolute_hinge(asm: Any) -> None:
    out = hinge(asm, name="Hinge", offset=2, angle="45 deg", flip=True)
    assert out["type"] == "revolute" and out["name"] == "Hinge"
    assert out["values"] == {"rotation_deg": 0.0}
    assert out["occurrence_one"] == "occurrence:Lid:1"
    assert out["occurrence_two"] == "occurrence:Box:1"
    joint_input = asm.rootComponent.joints.inputs[-1]
    name, (direction, custom) = joint_input.motion_call
    assert name == "setAsRevoluteJointMotion" and direction == DIR.CustomJointDirection
    assert custom.nativeObject.entityToken == BOX_EDGE
    assert joint_input.offset.value == pytest.approx(0.2)
    assert joint_input.angle.value == "45 deg" and joint_input.isFlipped is True
    assert asm.rootComponent.joints.count == 1


def test_joint_on_joint_origin(asm: Any) -> None:
    component = asm.lid.component
    origin = component.jointOrigins.add(component.jointOrigins.createInput(None))
    out = run(
        assembly.assembly_create_joint,
        asm,
        {
            "type": "rigid",
            "geometry_one": {"kind": "joint_origin", "entity": origin.entityToken},
            "geometry_two": curve(BOX_EDGE),
        },
    )["joint"]
    joint_input = asm.rootComponent.joints.inputs[-1]
    assert joint_input.args[0].nativeObject is origin
    assert (
        out["type"] == "rigid"
        and out["values"] == {}
        and out["occurrence_one"] == "occurrence:Lid:1"
    )


def test_motion_key_not_valid_for_type_adds_nothing(asm: Any) -> None:
    err = error_of(assembly.assembly_create_joint, asm, {**HINGE, "type": "slider"})
    assert err.code is ErrorCode.INVALID_PARAMS
    assert asm.rootComponent.joints.count == 0 and asm.rootComponent.joints.inputs == []


def test_joint_validation(asm: Any) -> None:
    fn = assembly.assembly_create_joint
    for params in (
        {**HINGE, "type": "hinge"},
        {**HINGE, "geometry_one": {"kind": "sphere", "entity": "x"}},
        {k: v for k, v in HINGE.items() if k != "geometry_two"},
        {**HINGE, "offset": True},
        {**HINGE, "flip": "yes"},
    ):
        assert error_of(fn, asm, params).code is ErrorCode.INVALID_PARAMS, params
    assert asm.rootComponent.joints.count == 0


def test_joints_add_failure_leaves_nothing(asm: Any) -> None:
    asm.timeline.add("Extrude1")
    count, marker = asm.timeline.count, asm.timeline.markerPosition
    asm.rootComponent.joints.fail_add = "Joint failed to compute"
    response = dispatch(asm, "assembly.create_joint", HINGE)
    assert response.ok is False and response.error.code is ErrorCode.FUSION_API_ERROR
    assert "Joint failed to compute" in response.error.message
    assert asm.rootComponent.joints.count == 0
    assert (asm.timeline.count, asm.timeline.markerPosition) == (count, marker)


def test_joint_in_error_state_is_removed(asm: Any) -> None:
    asm.rootComponent.joints.next_health = HEALTH.ErrorFeatureHealthState
    response = dispatch(asm, "assembly.create_joint", HINGE)
    assert response.ok is False and response.error.code is ErrorCode.FUSION_API_ERROR
    assert asm.rootComponent.joints.count == 0 and asm.timeline.count == 0


# --- 5.5 as-built joints -----------------------------------------------------------------------


def test_as_built_revolute(asm: Any) -> None:
    out = run(
        assembly.assembly_create_as_built_joint,
        asm,
        {
            "type": "revolute",
            "occurrence_one": "occurrence:Lid:1",
            "occurrence_two": "occurrence:Box:1",
            "geometry": curve(BOX_EDGE),
            "motion": {"axis": "x"},
            "name": "AB",
        },
    )["joint"]
    built = asm.rootComponent.asBuiltJoints
    one, two, geometry = built.inputs[-1].args
    assert one is asm.lid and two is asm.box
    assert geometry.method == "createByCurve" and geometry.args[0].assemblyContext is asm.box
    assert built.inputs[-1].motion_call == (
        "setAsRevoluteJointMotion",
        (DIR.XAxisJointDirection, None),
    )
    assert out["type"] == "revolute" and out["name"] == "AB"
    assert out["occurrence_one"] == "occurrence:Lid:1"


def test_as_built_rigid_without_geometry(asm: Any) -> None:
    out = run(
        assembly.assembly_create_as_built_joint,
        asm,
        {
            "type": "rigid",
            "occurrence_one": "occurrence:Lid:1",
            "occurrence_two": "occurrence:Box:1",
        },
    )["joint"]
    assert out["type"] == "rigid" and asm.rootComponent.asBuiltJoints.inputs[-1].args[2] is None


def test_as_built_errors(asm: Any) -> None:
    fn = assembly.assembly_create_as_built_joint
    base = {"occurrence_one": "occurrence:Lid:1", "occurrence_two": "occurrence:Box:1"}
    for params in (
        {**base, "type": "revolute"},
        {**base, "type": "rigid", "occurrence_two": "occurrence:Lid:1"},
        {**base, "type": "rigid", "occurrence_one": BOX_EDGE},
        {**base, "type": "slider", "geometry": {"kind": "joint_origin", "entity": "x"}},
        {**base, "type": "slider", "geometry": curve(BOX_EDGE), "motion": {"axis": "x"}},
    ):
        assert error_of(fn, asm, params).code is ErrorCode.INVALID_PARAMS, params
    assert asm.rootComponent.asBuiltJoints.count == 0


# --- 5.7 limits --------------------------------------------------------------------------------


def limits_of(asm: Any) -> Any:
    return asm.rootComponent.joints.item(0).jointMotion.rotationLimits


def test_set_limits(asm: Any) -> None:
    ref = hinge(asm)["ref"]
    out = run(
        assembly.assembly_set_joint_limits,
        asm,
        {"joint": ref, "dof": "rotation", "min": 0, "max": 110, "rest": 10},
    )["joint"]
    assert out["limits"]["rotation"] == {"min": 0.0, "max": 110.0, "rest": 10.0}
    limits = limits_of(asm)
    assert limits.maximumValue == pytest.approx(math.radians(110))
    out = run(
        assembly.assembly_set_joint_limits, asm, {"joint": ref, "dof": "rotation", "min": "off"}
    )
    assert out["joint"]["limits"]["rotation"] == {"min": None, "max": 110.0, "rest": 10.0}


def test_slide_limits_in_mm(asm: Any) -> None:
    joint = hinge(asm, type="slider", motion={"direction": "z"})
    out = run(
        assembly.assembly_set_joint_limits, asm, {"joint": joint["ref"], "dof": "slide", "max": 25}
    )
    assert out["joint"]["limits"]["slide"]["max"] == 25.0
    assert asm.rootComponent.joints.item(0).jointMotion.slideLimits.maximumValue == 2.5


def test_limit_errors(asm: Any) -> None:
    ref = hinge(asm)["ref"]
    fn = assembly.assembly_set_joint_limits
    run(fn, asm, {"joint": ref, "dof": "rotation", "max": 30})
    for params in (
        {"joint": ref, "dof": "rotation", "min": 90, "max": 10},
        {"joint": ref, "dof": "rotation", "min": 45},  # above the existing max 30
        {"joint": ref, "dof": "slide", "max": 1},
        {"joint": ref, "dof": "rotation"},
        {"joint": ref, "dof": "rotation", "max": "on"},
        {"joint": ref, "dof": "rotation", "max": True},
        {"joint": "occurrence:Box:1", "dof": "rotation", "max": 1},
    ):
        assert error_of(fn, asm, params).code is ErrorCode.INVALID_PARAMS, params
    assert limits_of(asm).maximumValue == pytest.approx(math.radians(30))


def test_limits_restored_on_failure(asm: Any) -> None:
    ref = hinge(asm)["ref"]
    limits = limits_of(asm)

    class Exploding:
        """Fails when the maximum is enabled, after the minimum was already changed."""

        def __init__(self, inner: Any) -> None:
            object.__setattr__(self, "inner", inner)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

        def __setattr__(self, name: str, value: Any) -> None:
            if name == "isMaximumValueEnabled" and value is True:
                raise RuntimeError("limit rejected")
            setattr(self.inner, name, value)

    asm.rootComponent.joints.item(0).jointMotion.__dict__["rotationLimits"] = Exploding(limits)
    err = error_of(
        assembly.assembly_set_joint_limits,
        asm,
        {"joint": ref, "dof": "rotation", "min": 5, "max": 50},
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert limits.isMinimumValueEnabled is False and limits.minimumValue == 0.0
    assert limits.isMaximumValueEnabled is False and limits.maximumValue == 0.0


# --- 5.9 drive ---------------------------------------------------------------------------------


def test_drive_to_90(asm: Any) -> None:
    ref = hinge(asm)["ref"]
    asm.snapshots.hasPendingSnapshot = True
    out = run(assembly.assembly_drive_joint, asm, {"joint": ref, "values": {"rotation": 90}})
    assert out["joint"]["values"] == {"rotation_deg": 90.0}
    assert out["requested"] == {"rotation": 90.0} and out["clamped"] == []
    assert out["captured"] is True and asm.snapshots.added == 1
    motion = asm.rootComponent.joints.item(0).jointMotion
    assert motion.rotationValue == pytest.approx(math.pi / 2)


def test_drive_without_capture(asm: Any) -> None:
    ref = hinge(asm)["ref"]
    asm.snapshots.hasPendingSnapshot = True
    out = run(
        assembly.assembly_drive_joint,
        asm,
        {"joint": ref, "values": {"rotation": 10}, "capture_position": False},
    )
    assert out["captured"] is False and asm.snapshots.added == 0


def test_drive_clamped_by_limit(asm: Any) -> None:
    ref = hinge(asm)["ref"]
    run(assembly.assembly_set_joint_limits, asm, {"joint": ref, "dof": "rotation", "max": 110})
    out = run(assembly.assembly_drive_joint, asm, {"joint": ref, "values": {"rotation": 150}})
    assert out["clamped"] == ["rotation"]
    assert out["joint"]["values"]["rotation_deg"] == pytest.approx(110.0)


def test_drive_failure_restores_values(asm: Any) -> None:
    joint = hinge(asm, type="cylindrical", motion={"axis": "z"})
    motion = asm.rootComponent.joints.item(0).jointMotion
    motion.rotationValue = 0.5
    motion.slideValue = 1.0
    motion.fail_on["slide"] = "solver failed"
    err = error_of(
        assembly.assembly_drive_joint,
        asm,
        {"joint": joint["ref"], "values": {"rotation": 90, "slide": 30}},
    )
    assert err.code is ErrorCode.FUSION_API_ERROR and "solver failed" in err.message
    assert motion.rotationValue == 0.5 and motion.slideValue == 1.0


def test_drive_errors(asm: Any) -> None:
    ref = hinge(asm)["ref"]
    fn = assembly.assembly_drive_joint
    for params in (
        {"joint": ref, "values": {}},
        {"joint": ref, "values": {"slide": 1}},
        {"joint": ref, "values": {"rotation": "90"}},
        {"joint": ref, "values": {"rotation": float("nan")}},
        {"joint": "occurrence:Box:1", "values": {"rotation": 1}},
    ):
        assert error_of(fn, asm, params).code is ErrorCode.INVALID_PARAMS, params
    assert asm.rootComponent.joints.item(0).jointMotion.rotationValue == 0.0

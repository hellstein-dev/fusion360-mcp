"""``core/jointgeo.py``: joint geometry and motion on fakes (c10 tasks 4.4, 4.5)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core import jointgeo
from fusion_addin.core.context import Context
from fusion_mcp.protocol.assembly import parse_joint_geometry, parse_motion
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.assembly import FakeJointInput, add_component, fake_assembly_design
from tests.fakes.brep import fake_box_body
from tests.fakes.design import FakeApp, FakeEntity

adsk = fusion_addin.adsk
KP = adsk.fusion.JointKeyPointTypes
DIR = adsk.fusion.JointDirections

BOX_EDGE = "bx-e011111"  # back top edge of the box (y = 6, z = 4), along X
LID_EDGE = "ld-e010110"  # back bottom edge of the lid


@pytest.fixture
def asm() -> Any:
    design = fake_assembly_design()
    _, design.box = add_component(design, "Box", fake_box_body(10, 6, 4, token="bx"))
    _, design.lid = add_component(design, "Lid", fake_box_body(10, 6, 0.5, token="ld"))
    return design


def make_ctx(design: Any) -> Context:
    return Context(FakeApp(design))


def build(design: Any, spec: dict[str, Any], native: bool = False) -> Any:
    return jointgeo.build(make_ctx(design), parse_joint_geometry(spec), native=native)


def error_of(fn: Any, *args: Any, **kwargs: Any) -> BridgeError:
    with pytest.raises(BridgeError) as exc:
        fn(*args, **kwargs)
    return exc.value


# --- 4.4 build ---------------------------------------------------------------------------------


def test_curve_in_occurrence_context(asm: Any) -> None:
    geometry = build(asm, {"kind": "curve", "entity": f"{LID_EDGE}@Lid:1"})
    assert geometry.method == "createByCurve"
    edge, key = geometry.args
    assert (
        edge.assemblyContext.fullPathName == "Lid:1" and edge.nativeObject.entityToken == LID_EDGE
    )
    assert key == KP.MiddleKeyPoint


def test_native_ref_is_coerced_to_the_only_occurrence(asm: Any) -> None:
    geometry = build(asm, {"kind": "curve", "entity": BOX_EDGE, "key_point": "start"})
    edge, key = geometry.args
    assert edge.assemblyContext is asm.box and key == KP.StartKeyPoint


def test_native_flag_keeps_native_entity(asm: Any) -> None:
    geometry = build(asm, {"kind": "curve", "entity": f"{BOX_EDGE}@Box:1"}, native=True)
    assert geometry.args[0].entityToken == BOX_EDGE
    assert getattr(geometry.args[0], "assemblyContext", None) is None


def test_point_planar_and_nonplanar_faces(asm: Any) -> None:
    point = build(asm, {"kind": "point", "entity": "bx-v111"})
    assert point.method == "createByPoint" and len(point.args) == 1
    face = build(asm, {"kind": "planar_face", "entity": "bx-face+z", "edge": BOX_EDGE})
    assert face.method == "createByPlanarFace"
    assert face.args[1].nativeObject.entityToken == BOX_EDGE and face.args[2] == KP.CenterKeyPoint
    nonplanar = build(asm, {"kind": "nonplanar_face", "entity": "bx-face+x", "key_point": "end"})
    assert nonplanar.method == "createByNonPlanarFace" and nonplanar.args[1] == KP.EndKeyPoint


def test_profile_and_joint_origin(asm: Any) -> None:
    profile = FakeEntity("adsk::fusion::Profile", token="prof-1", parentComponent=asm.rootComponent)
    asm.add_entity(profile)
    root_edge = FakeEntity(
        "adsk::fusion::SketchLine", token="sl-1", parentComponent=asm.rootComponent
    )
    asm.add_entity(root_edge)
    geometry = build(asm, {"kind": "profile", "entity": "prof-1", "curve": "sl-1"})
    assert geometry.method == "createByProfile"
    assert geometry.args == (profile, root_edge, KP.CenterKeyPoint)
    component = asm.lid.component
    origin = component.jointOrigins.add(component.jointOrigins.createInput(None))
    found = build(asm, {"kind": "joint_origin", "entity": origin.entityToken})
    assert found.nativeObject is origin and found.assemblyContext is asm.lid


def test_wrong_entity_type(asm: Any) -> None:
    err = error_of(build, asm, {"kind": "curve", "entity": "bx-face+z"})
    assert err.code is ErrorCode.INVALID_PARAMS and err.details["param"] == "geometry.entity"
    err = error_of(build, asm, {"kind": "planar_face", "entity": "bx-face+z", "edge": "bx-v000"})
    assert err.details["param"] == "geometry.edge"


def test_ambiguous_context(asm: Any) -> None:
    asm.rootComponent.occurrences.addExistingComponent(
        asm.box.component, adsk.core.Matrix3D.create()
    )
    err = error_of(build, asm, {"kind": "curve", "entity": BOX_EDGE})
    assert err.code is ErrorCode.INVALID_PARAMS
    assert err.details["candidates"] == [f"{BOX_EDGE}@Box:1", f"{BOX_EDGE}@Box:2"]


def test_fusion_refusal_is_api_error(asm: Any) -> None:
    edge = asm.findEntityByToken(LID_EDGE)[0]
    edge.joint_geometry_error = "Invalid geometry"
    err = error_of(build, asm, {"kind": "curve", "entity": LID_EDGE})
    assert err.code is ErrorCode.FUSION_API_ERROR and "Invalid geometry" in err.message


def test_unknown_ref(asm: Any) -> None:
    err = error_of(build, asm, {"kind": "curve", "entity": "nope@Lid:1"})
    assert err.code is ErrorCode.ENTITY_NOT_FOUND


# --- 4.5 apply_motion --------------------------------------------------------------------------


def motion_call(asm: Any, joint_type: str, motion: Any = None) -> tuple[str, tuple[Any, ...]]:
    joint_input = FakeJointInput()
    jointgeo.apply_motion(joint_input, joint_type, parse_motion(joint_type, motion), make_ctx(asm))
    assert joint_input.motion_call is not None
    return joint_input.motion_call


def test_rigid(asm: Any) -> None:
    assert motion_call(asm, "rigid") == ("setAsRigidJointMotion", ())


def test_revolute_named_and_custom_axis(asm: Any) -> None:
    assert motion_call(asm, "revolute") == (
        "setAsRevoluteJointMotion",
        (DIR.ZAxisJointDirection, None),
    )
    name, (direction, custom) = motion_call(asm, "revolute", {"axis": BOX_EDGE})
    assert name == "setAsRevoluteJointMotion" and direction == DIR.CustomJointDirection
    assert custom.assemblyContext is asm.box and custom.nativeObject.entityToken == BOX_EDGE


def test_slider_and_cylindrical(asm: Any) -> None:
    assert motion_call(asm, "slider", {"direction": "x"}) == (
        "setAsSliderJointMotion",
        (DIR.XAxisJointDirection, None),
    )
    assert motion_call(asm, "cylindrical", {"axis": "y"}) == (
        "setAsCylindricalJointMotion",
        (DIR.YAxisJointDirection, None),
    )


def test_pin_slot(asm: Any) -> None:
    name, args = motion_call(asm, "pin_slot", {"slide_direction": "origin:X"})
    assert name == "setAsPinSlotJointMotion"
    assert args[:3] == (DIR.ZAxisJointDirection, DIR.CustomJointDirection, None)
    assert args[3] is asm.rootComponent.xConstructionAxis


def test_planar(asm: Any) -> None:
    assert motion_call(asm, "planar") == (
        "setAsPlanarJointMotion",
        (DIR.ZAxisJointDirection, None, None),
    )
    name, args = motion_call(asm, "planar", {"primary_slide_direction": f"{BOX_EDGE}@Box:1"})
    assert name == "setAsPlanarJointMotion"
    assert args[2].assemblyContext.fullPathName == "Box:1"


def test_ball(asm: Any) -> None:
    assert motion_call(asm, "ball", {"pitch_direction": "x", "yaw_direction": "y"}) == (
        "setAsBallJointMotion",
        (DIR.XAxisJointDirection, DIR.YAxisJointDirection, None, None),
    )


def test_bad_direction_entity_and_refusal(asm: Any) -> None:
    err = error_of(motion_call, asm, "revolute", {"axis": "bx-v000"})
    assert err.code is ErrorCode.INVALID_PARAMS and err.details["param"] == "motion.axis"
    joint_input = FakeJointInput()
    joint_input.motion_result = False
    err = error_of(
        jointgeo.apply_motion, joint_input, "rigid", parse_motion("rigid", None), make_ctx(asm)
    )
    assert err.code is ErrorCode.FUSION_API_ERROR


def test_joint_type_names() -> None:
    for name in ("rigid", "revolute", "ball"):
        assert jointgeo.joint_type_name(jointgeo.joint_type_enum(name)) == name
    assert jointgeo.joint_type_name(99) is None

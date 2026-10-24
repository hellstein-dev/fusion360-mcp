"""Joint fakes and stubs (c10 task 4.3)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from tests.fakes.assembly import (
    FakeJointGeometry,
    FakeJointMotion,
    add_component,
    fake_assembly_design,
)
from tests.fakes.brep import fake_box_body

adsk = fusion_addin.adsk


@pytest.fixture
def asm() -> Any:
    design = fake_assembly_design()
    _, design.box = add_component(design, "Box", fake_box_body(10, 6, 4, token="bx"))
    return design


def test_stub_enums_and_geometry_factories(asm: Any) -> None:
    edge = asm.box.component.bRepBodies.item(0).edges.item(0)
    geometry = adsk.fusion.JointGeometry.createByCurve(
        edge, adsk.fusion.JointKeyPointTypes.MiddleKeyPoint
    )
    assert isinstance(geometry, FakeJointGeometry)
    assert geometry.method == "createByCurve"
    assert geometry.args == (edge, adsk.fusion.JointKeyPointTypes.MiddleKeyPoint)
    assert adsk.fusion.JointTypes.RevoluteJointType != adsk.fusion.JointTypes.RigidJointType
    assert adsk.fusion.JointDirections.CustomJointDirection == 3
    edge.joint_geometry_error = "cannot snap"
    with pytest.raises(RuntimeError, match="cannot snap"):
        adsk.fusion.JointGeometry.createByPoint(edge)


def test_joint_add_records_motion_occurrences_and_timeline(asm: Any) -> None:
    root = asm.rootComponent
    edge = asm.box.component.bRepBodies.item(0).edges.item(0).createForAssemblyContext(asm.box)
    one = adsk.fusion.JointGeometry.createByCurve(edge, 1)
    two = adsk.fusion.JointGeometry.createByPoint(root.originConstructionPoint)
    joint_input = root.joints.createInput(one, two)
    assert joint_input.setAsRevoluteJointMotion(2, None) is True
    joint = root.joints.add(joint_input)
    assert joint_input.motion_call == ("setAsRevoluteJointMotion", (2, None))
    assert joint.jointMotion.jointType == adsk.fusion.JointTypes.RevoluteJointType
    assert joint.occurrenceOne is asm.box and joint.occurrenceTwo is None
    assert root.joints.count == 1 and asm.timeline.count == 1
    assert asm.findEntityByToken(joint.entityToken) == [joint]
    assert joint.deleteMe() is True
    assert root.joints.count == 0 and asm.timeline.count == 0


def test_add_failure_leaves_nothing(asm: Any) -> None:
    root = asm.rootComponent
    root.joints.fail_add = "joint failed"
    with pytest.raises(RuntimeError, match="joint failed"):
        root.joints.add(root.joints.createInput(None, None))
    assert root.joints.count == 0 and asm.timeline.count == 0


def test_motion_values_limits_and_clamping() -> None:
    motion = FakeJointMotion("cylindrical")
    assert motion.rotationValue == 0.0 and motion.slideValue == 0.0
    assert not hasattr(motion, "pitchValue")
    motion.rotationLimits.isMaximumValueEnabled = True
    motion.rotationLimits.maximumValue = 1.0
    motion.rotationValue = 2.0
    assert motion.rotationValue == 1.0
    motion.clamp = False
    motion.rotationValue = 2.0
    assert motion.rotationValue == 2.0
    motion.fail_on["slide"] = "solver failed"
    with pytest.raises(RuntimeError, match="solver failed"):
        motion.slideValue = 1.0
    motion.slideValue = 1.0
    assert motion.slideValue == 1.0


def test_as_built_joint_origin_and_rigid_group(asm: Any) -> None:
    root = asm.rootComponent
    built = root.asBuiltJoints.add(root.asBuiltJoints.createInput(asm.box, None, None))
    assert built.occurrenceOne is asm.box and built.objectType.endswith("AsBuiltJoint")
    component = asm.box.component
    origin_input = component.jointOrigins.createInput("geometry")
    origin = component.jointOrigins.add(origin_input)
    assert origin.parentComponent is component and origin.geometry == "geometry"
    proxy = origin.createForAssemblyContext(asm.box)
    assert proxy.nativeObject is origin and proxy.assemblyContext is asm.box
    group = root.rigidGroups.add([asm.box], False)
    assert group.include_children is False and group.occurrences.count == 1

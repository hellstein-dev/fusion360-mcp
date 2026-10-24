"""Assembly fakes (c10 D9, task 1.3): proxies, occurrences, snapshots, Matrix3D stub."""

from __future__ import annotations

import pytest

import fusion_addin
from tests.fakes.assembly import FakeProxy, add_component, fake_assembly_design
from tests.fakes.brep import FakeOccurrence, fake_box_body

adsk = fusion_addin.adsk


def test_proxy_round_trip_and_delegation() -> None:
    design = fake_assembly_design()
    body = fake_box_body(1, 2, 3, token="b")
    _, occ = add_component(design, "Part", body)
    face = design.by_token["b-face+z"]
    proxy = face.createForAssemblyContext(occ)
    assert isinstance(proxy, FakeProxy)
    assert proxy.nativeObject is face
    assert proxy.assemblyContext is occ
    assert proxy.objectType == face.objectType
    assert proxy.entityToken != face.entityToken
    assert proxy.area == face.area
    other = proxy.createForAssemblyContext(occ)
    assert other.nativeObject is face and other is not proxy


def test_occurrences_add_and_paths() -> None:
    design = fake_assembly_design()
    root = design.rootComponent
    box, box_occ = add_component(design, "Box")
    assert box_occ.fullPathName == "Box:1"
    assert design.allComponents.itemByName("Box") is box
    again = root.occurrences.addExistingComponent(box, adsk.core.Matrix3D.create())
    assert again.fullPathName == "Box:2"
    new = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    assert new.component.name.startswith("Component")
    assert [c[0] for c in root.occurrences.calls] == [
        "addExistingComponent",
        "addExistingComponent",
        "addNewComponent",
    ]
    assert root.allOccurrences.count == 3


def test_nested_occurrence_gets_root_context_copy() -> None:
    design = fake_assembly_design()
    box, box_occ = add_component(design, "Box")
    hinge, hinge_occ = add_component(design, "Hinge", parent=box_occ)
    assert hinge_occ.fullPathName == "Box:1+Hinge:1"
    assert hinge_occ.assemblyContext is box_occ
    native = box.occurrences.item(0)
    assert hinge_occ.nativeObject is native
    assert native.fullPathName == "Hinge:1"
    assert box_occ.childOccurrences.item(0) is hinge_occ
    # Renaming the component renames the occurrences (Fusion behaviour).
    hinge.name = "Pin"
    assert hinge_occ.fullPathName == "Box:1+Pin:1"


def test_independent_copy_and_unsupported() -> None:
    design = fake_assembly_design()
    box, _ = add_component(design, "Box", fake_box_body(1, 1, 1))
    occurrences = design.rootComponent.occurrences
    copy = occurrences.addNewComponentCopy(box, adsk.core.Matrix3D.create())
    assert copy.component is not box
    assert copy.component.bRepBodies.count == 1
    occurrences.copy_supported = False
    with pytest.raises(AttributeError):
        occurrences.addNewComponentCopy(box, adsk.core.Matrix3D.create())


def test_occurrence_state_and_explicit_names_unchanged() -> None:
    design = fake_assembly_design()
    part, _ = add_component(design, "Part")
    fixed = FakeOccurrence(part, "Lid:1", "Asm:1+Lid:1")
    assert (fixed.name, fixed.fullPathName) == ("Lid:1", "Asm:1+Lid:1")
    assert FakeOccurrence(part).fullPathName == "Part:1"
    assert fixed.isLightBulbOn is True and fixed.assemblyContext is None
    assert fixed.activate() and fixed.activations == 1
    assert fixed.deleteMe() and fixed.deleted


def test_snapshots_and_activate_root() -> None:
    design = fake_assembly_design()
    snapshots = design.snapshots
    snapshots.hasPendingSnapshot = True
    assert snapshots.add() and snapshots.added == 1
    assert snapshots.hasPendingSnapshot is False
    box, _ = add_component(design, "Box")
    design.activeComponent = box
    design.activateRootComponent()
    assert design.activeComponent is design.rootComponent


def test_matrix_stub_coordinate_system_and_transform_by() -> None:
    m = adsk.core.Matrix3D.create()
    P, V = adsk.core.Point3D.create, adsk.core.Vector3D.create
    m.setWithCoordinateSystem(P(1, 2, 3), V(0, 1, 0), V(-1, 0, 0), V(0, 0, 1))
    origin, x, y, z = m.getAsCoordinateSystem()
    assert origin.asArray() == [1, 2, 3] and x.asArray() == [0, 1, 0]
    assert y.asArray() == [-1, 0, 0] and z.asArray() == [0, 0, 1]
    shift = adsk.core.Matrix3D.create()
    shift.translation = V(10, 0, 0)
    m.transformBy(shift)
    assert m.translation.asArray() == [11, 2, 3]

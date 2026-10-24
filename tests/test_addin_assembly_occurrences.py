"""``assembly.*`` occurrence handlers on fakes (c10 tasks 3.2, 3.4, 3.6, 3.8)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.handlers import assembly
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.assembly import add_component, fake_assembly_design
from tests.fakes.brep import fake_box_body
from tests.fakes.design import FakeApp, FakeCollection, FakeEntity

adsk = fusion_addin.adsk


def make_ctx(design: Any) -> Context:
    return Context(FakeApp(design))


def error_of(fn: Any, ctx: Any, params: dict[str, Any]) -> BridgeError:
    with pytest.raises(BridgeError) as exc:
        fn(ctx, params)
    return exc.value


@pytest.fixture
def asm() -> Any:
    design = fake_assembly_design(fake_box_body(1, 1, 1, token="rt"))
    _, design.box = add_component(design, "Box", fake_box_body(10, 6, 4, token="bx"))
    _, design.lid = add_component(design, "Lid", fake_box_body(10, 6, 0.5, token="ld"))
    return design


def translation(out: dict[str, Any]) -> list[float]:
    return list(out["occurrence"]["transform"]["translation_mm"])


# --- 3.2 assembly.add_occurrence -------------------------------------------------------------


def test_add_referenced_occurrence(asm: Any) -> None:
    out = assembly.assembly_add_occurrence(
        make_ctx(asm), {"component": "component:Box", "translate_mm": [0, 100, 0]}
    )
    assert out["occurrence"]["ref"] == "occurrence:Box:2"
    assert out["occurrence"]["component"] == "component:Box"
    assert translation(out) == [0.0, 100.0, 0.0]
    assert asm.rootComponent.occurrences.calls[-1][0] == "addExistingComponent"


def test_add_independent_copy(asm: Any) -> None:
    out = assembly.assembly_add_occurrence(
        make_ctx(asm), {"component": "component:Box", "independent": True}
    )
    assert out["occurrence"]["component"] == "component:Box (1)"
    assert asm.rootComponent.occurrences.calls[-1][0] == "addNewComponentCopy"


def test_independent_copy_not_supported(asm: Any) -> None:
    asm.rootComponent.occurrences.copy_supported = False
    err = error_of(
        assembly.assembly_add_occurrence,
        make_ctx(asm),
        {"component": "component:Box", "independent": True},
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert "independent copy not supported by this Fusion version" in err.message


def test_add_occurrence_under_parent(asm: Any) -> None:
    out = assembly.assembly_add_occurrence(
        make_ctx(asm), {"component": "component:Lid", "parent": "occurrence:Box:1"}
    )
    assert out["occurrence"]["ref"] == "occurrence:Box:1+Lid:1"


def test_add_occurrence_validation(asm: Any) -> None:
    ctx = make_ctx(asm)
    fn = assembly.assembly_add_occurrence
    for params in (
        {"component": "component:root"},
        {"component": "component:Box", "parent": "occurrence:Box:1"},
        {"component": "bx"},
        {"component": "component:Box", "translate_mm": [0, "1", 0]},
    ):
        assert error_of(fn, ctx, params).code is ErrorCode.INVALID_PARAMS, params
    assert error_of(fn, ctx, {"component": "component:Nope"}).code is ErrorCode.ENTITY_NOT_FOUND


def test_add_occurrence_failure_deletes_it(asm: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(ctx: Any, occurrence: Any) -> dict[str, Any]:
        raise RuntimeError("describe failed")

    monkeypatch.setattr(assembly, "_occ", broken)
    err = error_of(assembly.assembly_add_occurrence, make_ctx(asm), {"component": "component:Box"})
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert asm.rootComponent.occurrences.item(2).deleted is True


# --- 3.4 assembly.transform_occurrence ----------------------------------------------------------


def test_translate_occurrence_captures_position(asm: Any) -> None:
    asm.snapshots.hasPendingSnapshot = True
    out = assembly.assembly_transform_occurrence(
        make_ctx(asm), {"occurrence": "occurrence:Lid:1", "translate_mm": [0, 0, 40]}
    )
    assert translation(out) == [0.0, 0.0, 40.0]
    assert out["captured"] is True
    assert out["constrained_by_joints"] is False
    assert asm.snapshots.added == 1


def test_no_pending_snapshot_or_disabled_is_not_captured(asm: Any) -> None:
    ctx = make_ctx(asm)
    params: dict[str, Any] = {"occurrence": "occurrence:Lid:1", "translate_mm": [1, 0, 0]}
    assert assembly.assembly_transform_occurrence(ctx, params)["captured"] is False
    asm.snapshots.hasPendingSnapshot = True
    params["capture_position"] = False
    assert assembly.assembly_transform_occurrence(ctx, params)["captured"] is False
    assert asm.snapshots.added == 0
    del asm.snapshots
    params["capture_position"] = True
    assert assembly.assembly_transform_occurrence(ctx, params)["captured"] is False


def test_direct_design_is_not_captured() -> None:
    design = fake_assembly_design(parametric=False)
    add_component(design, "Lid")
    design.snapshots.hasPendingSnapshot = True
    out = assembly.assembly_transform_occurrence(
        make_ctx(design), {"occurrence": "occurrence:Lid:1", "translate_mm": [1, 0, 0]}
    )
    assert out["captured"] is False


def test_rotate_then_translate(asm: Any) -> None:
    ctx = make_ctx(asm)
    assembly.assembly_transform_occurrence(
        ctx, {"occurrence": "occurrence:Lid:1", "translate_mm": [10, 0, 0]}
    )
    out = assembly.assembly_transform_occurrence(
        ctx,
        {
            "occurrence": "occurrence:Lid:1",
            "rotate": {"axis": [0, 0, 1], "angle_deg": 90},
            "translate_mm": [0, 0, 5],
        },
    )
    assert translation(out) == pytest.approx([0.0, 10.0, 5.0])
    rotation = out["occurrence"]["transform"]["rotation"]
    assert rotation == [[0, -1, 0], [1, 0, 0], [0, 0, 1]]


def test_absolute_placement(asm: Any) -> None:
    out = assembly.assembly_transform_occurrence(
        make_ctx(asm),
        {
            "occurrence": "occurrence:Lid:1",
            "absolute": {"origin_mm": [1, 2, 3], "x_axis": [0, 2, 0], "y_axis": [-1, 0, 0]},
        },
    )
    assert translation(out) == pytest.approx([1.0, 2.0, 3.0])
    assert out["occurrence"]["transform"]["rotation"] == [[0, -1, 0], [1, 0, 0], [0, 0, 1]]


def test_transform_validation(asm: Any) -> None:
    ctx = make_ctx(asm)
    fn = assembly.assembly_transform_occurrence
    placement = {"origin_mm": [0, 0, 0], "x_axis": [1, 0, 0], "y_axis": [0, 1, 0]}
    for params in (
        {"occurrence": "occurrence:Lid:1"},
        {"occurrence": "occurrence:Lid:1", "absolute": placement, "translate_mm": [1, 0, 0]},
        {"occurrence": "occurrence:Lid:1", "absolute": {**placement, "y_axis": [1, 1, 0]}},
        {"occurrence": "occurrence:Lid:1", "absolute": {**placement, "z_axis": [0, 0, 1]}},
        {"occurrence": "occurrence:Lid:1", "rotate": {"axis": [0, 0, 0], "angle_deg": 5}},
        {"occurrence": "component:Lid", "translate_mm": [1, 0, 0]},
    ):
        assert error_of(fn, ctx, params).code is ErrorCode.INVALID_PARAMS, params
    assert translation({"occurrence": assembly._occ(ctx, asm.lid)}) == [0.0, 0.0, 0.0]


def test_grounded_occurrence_is_rejected(asm: Any) -> None:
    asm.lid.isGrounded = True
    err = error_of(
        assembly.assembly_transform_occurrence,
        make_ctx(asm),
        {"occurrence": "occurrence:Lid:1", "translate_mm": [0, 0, 40]},
    )
    assert err.code is ErrorCode.INVALID_PARAMS
    assert "grounded" in err.message
    assert list(asm.lid.transform2.asArray())[11] == 0.0


def test_transform_failure_restores_matrix(asm: Any) -> None:
    asm.snapshots.hasPendingSnapshot = True

    def refuse() -> bool:
        raise RuntimeError("snapshot failed")

    asm.snapshots.add = refuse
    err = error_of(
        assembly.assembly_transform_occurrence,
        make_ctx(asm),
        {"occurrence": "occurrence:Lid:1", "translate_mm": [0, 0, 40]},
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert list(asm.lid.transform2.asArray())[11] == 0.0


def test_constrained_by_joints(asm: Any) -> None:
    asm.rootComponent.joints = FakeCollection(
        [FakeEntity("adsk::fusion::Joint", "J", occurrenceOne=asm.lid, occurrenceTwo=asm.box)]
    )
    out = assembly.assembly_transform_occurrence(
        make_ctx(asm), {"occurrence": "occurrence:Lid:1", "translate_mm": [0, 0, 1]}
    )
    assert out["constrained_by_joints"] is True


# --- 3.6 assembly.set_occurrence_state ----------------------------------------------------------


def test_set_state(asm: Any) -> None:
    out = assembly.assembly_set_occurrence_state(
        make_ctx(asm), {"occurrence": "occurrence:Box:1", "grounded": True, "light_bulb": False}
    )
    assert out["occurrence"]["is_grounded"] is True
    assert out["occurrence"]["is_light_bulb_on"] is False
    assert asm.box.isGrounded is True and asm.box.isLightBulbOn is False


def test_set_state_needs_a_change(asm: Any) -> None:
    err = error_of(
        assembly.assembly_set_occurrence_state, make_ctx(asm), {"occurrence": "occurrence:Box:1"}
    )
    assert err.code is ErrorCode.INVALID_PARAMS


def test_set_state_failure_restores_both(asm: Any) -> None:
    class Sticky:
        """An occurrence whose light bulb Fusion refuses to switch off."""

        def __init__(self, inner: Any) -> None:
            self.__dict__["inner"] = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self.__dict__["inner"], name)

        def __setattr__(self, name: str, value: Any) -> None:
            if name == "isLightBulbOn":
                return
            setattr(self.__dict__["inner"], name, value)

    sticky = Sticky(asm.box)
    asm.rootComponent.allOccurrences._items[0] = sticky
    err = error_of(
        assembly.assembly_set_occurrence_state,
        make_ctx(asm),
        {"occurrence": "occurrence:Box:1", "grounded": True, "light_bulb": False},
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert asm.box.isGrounded is False
    assert asm.box.isLightBulbOn is True


# --- 3.8 assembly.delete ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["Joint", "AsBuiltJoint", "JointOrigin", "RigidGroup"])
def test_delete_assembly_objects(asm: Any, kind: str) -> None:
    deleted: list[bool] = []

    def delete_me() -> bool:
        deleted.append(True)
        return True

    item = FakeEntity(f"adsk::fusion::{kind}", "Item1", token=f"tok-{kind}", deleteMe=delete_me)
    asm.add_entity(item)
    out = assembly.assembly_delete(make_ctx(asm), {"item": f"tok-{kind}"})
    assert out == {"deleted": {"name": "Item1", "type": kind}}
    assert deleted == [True]


def test_delete_occurrence(asm: Any) -> None:
    out = assembly.assembly_delete(make_ctx(asm), {"item": "occurrence:Lid:1"})
    assert out == {"deleted": {"name": "Lid:1", "type": "Occurrence"}}
    assert asm.lid.deleted is True


def test_delete_other_types_is_invalid(asm: Any) -> None:
    for ref in ("bx", "component:Box"):
        err = error_of(assembly.assembly_delete, make_ctx(asm), {"item": ref})
        assert err.code is ErrorCode.INVALID_PARAMS
        assert "RigidGroup" in err.message


def test_delete_refused(asm: Any) -> None:
    asm.lid.deleteMe = lambda: False
    err = error_of(assembly.assembly_delete, make_ctx(asm), {"item": "occurrence:Lid:1"})
    assert err.code is ErrorCode.FUSION_API_ERROR

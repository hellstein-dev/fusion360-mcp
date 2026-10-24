"""``handlers/assembly.py`` (c10): shared helpers (task 1.4)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.handlers import assembly
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.assembly import FakeProxy, add_component, fake_assembly_design
from tests.fakes.brep import fake_box_body
from tests.fakes.design import FakeApp, FakeCollection, FakeEntity


def make_ctx(design: Any) -> Context:
    return Context(FakeApp(design))


@pytest.fixture
def asm() -> Any:
    design = fake_assembly_design(fake_box_body(1, 1, 1, token="rt"))
    _, design.box = add_component(design, "Box", fake_box_body(10, 6, 4, token="bx"))
    return design


# --- 1.4 _in_root_context ------------------------------------------------------------------


def test_single_occurrence_gives_proxy(asm: Any) -> None:
    face = asm.by_token["bx-face+z"]
    proxy = assembly._in_root_context(make_ctx(asm), face)
    assert isinstance(proxy, FakeProxy)
    assert proxy.nativeObject is face
    assert proxy.assemblyContext is asm.box


def test_two_occurrences_are_ambiguous(asm: Any) -> None:
    box = asm.box.component
    asm.rootComponent.occurrences.addExistingComponent(
        box, fusion_addin.adsk.core.Matrix3D.create()
    )
    with pytest.raises(BridgeError) as exc:
        assembly._in_root_context(make_ctx(asm), asm.by_token["bx-face+z"])
    assert exc.value.code is ErrorCode.INVALID_PARAMS
    assert "ambiguous assembly context" in exc.value.message
    assert exc.value.details["candidates"] == ["bx-face+z@Box:1", "bx-face+z@Box:2"]


def test_no_occurrence_is_invalid(asm: Any) -> None:
    from tests.fakes.assembly import new_component

    orphan = new_component(asm, "Orphan")
    body = orphan.add_body(fake_box_body(1, 1, 1, token="or"))
    with pytest.raises(BridgeError) as exc:
        assembly._in_root_context(make_ctx(asm), body)
    assert exc.value.details["candidates"] == []


def test_proxy_root_entity_and_occurrence_as_is(asm: Any) -> None:
    ctx = make_ctx(asm)
    proxy = asm.by_token["bx-face+z"].createForAssemblyContext(asm.box)
    assert assembly._in_root_context(ctx, proxy) is proxy
    root_face = asm.by_token["rt-face+z"]
    assert assembly._in_root_context(ctx, root_face) is root_face
    assert assembly._in_root_context(ctx, asm.box) is asm.box


def test_owner_component_through_proxy(asm: Any) -> None:
    proxy = asm.by_token["bx-face+z"].createForAssemblyContext(asm.box)
    assert assembly._owner_component(proxy) is asm.box.component


# --- 1.4 _occ ------------------------------------------------------------------------------


def _joint(one: Any, two: Any) -> FakeEntity:
    return FakeEntity("adsk::fusion::Joint", "J", occurrenceOne=one, occurrenceTwo=two)


def test_occ_describes_with_light_bulb_and_joint_count(asm: Any) -> None:
    _, lid = add_component(asm, "Lid")
    root = asm.rootComponent
    root.joints = FakeCollection([_joint(lid, asm.box)])
    root.asBuiltJoints = FakeCollection([_joint(asm.box, None)])
    asm.box.isLightBulbOn = False
    out = assembly._occ(make_ctx(asm), asm.box)
    assert out["type"] == "Occurrence"
    assert out["full_path_name"] == "Box:1"
    assert out["component"] == asm.box.component.entityToken
    assert out["transform"]["translation_mm"] == [0.0, 0.0, 0.0]
    assert out["is_light_bulb_on"] is False
    assert out["joint_count"] == 2
    assert assembly._occ(make_ctx(asm), lid)["joint_count"] == 1


def test_module_is_registered() -> None:
    assert "assembly" in fusion_addin.handlers.__all__

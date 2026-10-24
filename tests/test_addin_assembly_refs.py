"""``core/refs.py`` in an assembly context (c10 D1, tasks 1.1-1.2)."""

from __future__ import annotations

from typing import Any

import pytest

from fusion_addin.core import refs
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.assembly import FakeProxy, add_component, fake_assembly_design
from tests.fakes.brep import FakeComponent, FakeOccurrence, fake_box_body


@pytest.fixture
def asm() -> Any:
    design = fake_assembly_design()
    _, box_occ = add_component(design, "Box", fake_box_body(10, 6, 4, token="bx"))
    _, lid_occ = add_component(design, "Lid", fake_box_body(10, 6, 0.5, token="ld"))
    _, hinge_occ = add_component(
        design, "Hinge", fake_box_body(1, 1, 1, token="hg"), parent=box_occ
    )
    design.parts = {"Box": box_occ, "Lid": lid_occ, "Hinge": hinge_occ}
    return design


def face(design: Any, token: str) -> Any:
    return design.by_token[token]


# --- 1.1 to_ref --------------------------------------------------------------------------


def test_proxy_ref_is_native_ref_at_path(asm: Any) -> None:
    proxy = face(asm, "ld-face+z").createForAssemblyContext(asm.parts["Lid"])
    assert isinstance(proxy, FakeProxy)
    assert refs.to_ref(proxy) == "ld-face+z@Lid:1"


def test_nested_proxy_and_origin_proxy(asm: Any) -> None:
    hinge = asm.parts["Hinge"]
    assert hinge.fullPathName == "Box:1+Hinge:1"
    edge = asm.by_token["hg-e000100"].createForAssemblyContext(hinge)
    assert refs.to_ref(edge) == "hg-e000100@Box:1+Hinge:1"
    axis = hinge.component.zConstructionAxis.createForAssemblyContext(hinge)
    assert refs.to_ref(axis) == "origin:Z@Box:1+Hinge:1"


class FakeEntityOccurrence:
    objectType = "adsk::fusion::Occurrence"
    entityToken = None
    fullPathName = "Box:1+Pin:1"
    assemblyContext = object()
    nativeObject = object()


def test_occurrence_ref_unchanged(asm: Any) -> None:
    hinge = asm.parts["Hinge"]
    assert hinge.assemblyContext is asm.parts["Box"]
    # Nested occurrences have an assembly context, but keep their own form.
    assert refs.to_ref(hinge) == hinge.entityToken
    named = FakeEntityOccurrence()
    assert refs.to_ref(named) == "occurrence:Box:1+Pin:1"


def test_native_entity_without_context_unchanged(asm: Any) -> None:
    assert refs.to_ref(face(asm, "bx-face-z")) == "bx-face-z"


# --- 1.2 resolve -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "path"),
    [("ld-face+z", "Lid:1"), ("bx-e000001", "Box:1"), ("hg-face-x", "Box:1+Hinge:1")],
)
def test_round_trip(asm: Any, token: str, path: str) -> None:
    ref = f"{token}@{path}"
    proxy = refs.resolve(asm, ref)
    assert proxy.nativeObject is asm.by_token[token]
    assert proxy.assemblyContext.fullPathName == path
    assert refs.to_ref(proxy) == ref


def test_origin_in_occurrence_context(asm: Any) -> None:
    proxy = refs.resolve(asm, "origin:Z@Lid:1")
    assert proxy.nativeObject is asm.parts["Lid"].component.zConstructionAxis
    assert proxy.assemblyContext is asm.parts["Lid"]
    assert refs.to_ref(proxy) == "origin:Z@Lid:1"


def test_proxy_token_found_by_token_is_normalized(asm: Any) -> None:
    native = face(asm, "ld-face+z")
    stale = native.createForAssemblyContext(asm.parts["Box"])
    asm.by_token["ld-proxy-tok"] = stale
    # findEntityByToken hands out a proxy: its nativeObject is put into the new context.
    proxy = refs.resolve(asm, "ld-proxy-tok@Lid:1")
    assert proxy.nativeObject is native
    assert proxy.assemblyContext is asm.parts["Lid"]


def test_plain_proxy_token_resolves_as_before(asm: Any) -> None:
    proxy = face(asm, "ld-face+z").createForAssemblyContext(asm.parts["Lid"])
    asm.add_entity(proxy)
    assert refs.resolve(asm, proxy.entityToken) is proxy


def test_entity_of_other_component_is_invalid(asm: Any) -> None:
    with pytest.raises(BridgeError) as exc:
        refs.resolve(asm, "bx-face+z@Lid:1")
    assert exc.value.code is ErrorCode.INVALID_PARAMS
    assert "entity is not in Lid" in exc.value.message
    assert exc.value.details["owner"] == "Box"


@pytest.mark.parametrize("ref", ["ld-face+z@Nope:1", "origin:Z@Lid:9", "nothing@Lid:1"])
def test_unknown_path_or_entity_not_found(asm: Any, ref: str) -> None:
    with pytest.raises(BridgeError) as exc:
        refs.resolve(asm, ref)
    assert exc.value.code is ErrorCode.ENTITY_NOT_FOUND


@pytest.mark.parametrize("ref", ["@Lid:1", "ld-face+z@", "origin:Q@Lid:1"])
def test_malformed_context_ref_invalid(asm: Any, ref: str) -> None:
    with pytest.raises(BridgeError) as exc:
        refs.resolve(asm, ref)
    assert exc.value.code is ErrorCode.INVALID_PARAMS


def test_name_refs_not_split(asm: Any) -> None:
    part = FakeComponent("Part@2")
    asm.allComponents.add(part)
    assert refs.resolve(asm, "component:Part@2") is part
    named = FakeOccurrence(part, "Part@2:1", token="np")
    asm.rootComponent.allOccurrences.add(named)
    assert refs.resolve(asm, "occurrence:Part@2:1") is named


def test_origin_without_context_uses_component(asm: Any) -> None:
    lid = asm.parts["Lid"].component
    assert refs.resolve(asm, "origin:XY") is asm.rootComponent.xYConstructionPlane
    assert refs.resolve(asm, "origin:XY", lid) is lid.xYConstructionPlane


def test_owner_component_paths(asm: Any) -> None:
    body = face(asm, "ld-face+z").body
    lid = asm.parts["Lid"].component
    assert refs.owner_component(body) is lid
    assert refs.owner_component(face(asm, "ld-face+z")) is lid
    assert refs.owner_component(lid.xConstructionAxis) is lid
    assert refs.owner_component(object()) is None

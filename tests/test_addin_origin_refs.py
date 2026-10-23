"""``origin:<NAME>`` in ``fusion_addin.core.refs`` (c03 2.8, D15)."""

import pytest

from fusion_addin.core import refs
from fusion_addin.core.context import Context
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import (
    ORIGIN_ATTRS,
    FakeComponent,
    FakeConstructionPlane,
    P,
    V,
    fake_part_design,
)
from tests.fakes.design import FakeApp


@pytest.mark.parametrize(("name", "attr"), list(ORIGIN_ATTRS.items()))
def test_resolve_every_origin_name_in_root(name: str, attr: str) -> None:
    design = fake_part_design()
    expected = getattr(design.rootComponent, attr)
    assert refs.resolve(design, f"origin:{name}") is expected
    assert refs.resolve(design, f"origin:{name.lower()}") is expected


def test_resolve_in_given_component() -> None:
    design = fake_part_design()
    part = FakeComponent("Part")
    assert refs.resolve(design, "origin:z", component=part) is part.zConstructionAxis


def test_resolve_unknown_origin_name() -> None:
    with pytest.raises(BridgeError) as info:
        refs.resolve(fake_part_design(), "origin:XW")
    assert info.value.code is ErrorCode.INVALID_PARAMS


def test_to_ref_is_canonical_and_round_trips() -> None:
    design = fake_part_design()
    for name, element in design.rootComponent.origin_elements().items():
        ref = refs.to_ref(element)
        assert ref == f"origin:{name}"
        assert refs.resolve(design, ref) is element


def test_to_ref_matches_by_token_for_a_new_wrapper() -> None:
    root = FakeComponent("root")
    wrapper = FakeConstructionPlane(
        "XY", P(0, 0, 0), V(0, 0, 1), root.xYConstructionPlane.entityToken, parentComponent=root
    )
    assert refs.to_ref(wrapper) == "origin:XY"


def test_old_token_of_origin_element_still_resolves() -> None:
    design = fake_part_design()
    plane = design.rootComponent.xZConstructionPlane
    assert refs.resolve(design, plane.entityToken) is plane


def test_user_construction_plane_keeps_its_token() -> None:
    root = FakeComponent("root")
    plane = FakeConstructionPlane("Offset", P(0, 0, 0), V(0, 0, 1), parentComponent=root)
    assert refs.to_ref(plane) == plane.entityToken


def test_ctx_refs_resolves_origin() -> None:
    design = fake_part_design()
    ctx = Context(FakeApp(design))
    assert ctx.refs.resolve("origin:o") is design.rootComponent.originConstructionPoint
    assert ctx.refs.describe(design.rootComponent.xYConstructionPlane) == {
        "ref": "origin:XY",
        "type": "ConstructionPlane",
        "name": "XY",
        "origin_mm": [0.0, 0.0, 0.0],
        "normal": [0.0, 0.0, 1.0],
    }

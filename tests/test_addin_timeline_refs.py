"""``timeline:<name>`` refs in ``core/refs.py`` (c09 D1, task 1.2)."""

import pytest

from fusion_addin.core import refs
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.design import FakeDesign
from tests.fakes.timeline import FakeTimeline


def design_with_group() -> tuple[FakeDesign, FakeTimeline]:
    timeline = FakeTimeline()
    timeline.add_feature("Sketch1", "adsk::fusion::Sketch", "sk-1")
    timeline.add_feature("Extrude1", "adsk::fusion::ExtrudeFeature", "ext-1")
    timeline.add_feature("Fillet1", "adsk::fusion::FilletFeature", "fil-1")
    group = timeline.timelineGroups.add(0, 1)
    assert group is not None
    group.name = "Base"
    return FakeDesign(timeline=timeline), timeline


def test_walk_puts_members_right_after_their_group() -> None:
    _, timeline = design_with_group()
    assert [i.name for i in refs.walk_timeline(timeline)] == [
        "Base",
        "Sketch1",
        "Extrude1",
        "Fillet1",
    ]


def test_resolve_top_level_group_and_member() -> None:
    design, timeline = design_with_group()
    assert refs.resolve(design, "timeline:Base") is timeline.item(0)
    assert refs.resolve(design, "timeline:Extrude1") is timeline.item(2)
    assert refs.resolve(design, "timeline:Fillet1") is timeline.item(3)


def test_resolve_unknown_name_is_not_found() -> None:
    design, _ = design_with_group()
    with pytest.raises(BridgeError) as info:
        refs.resolve(design, "timeline:Nope")
    assert info.value.code is ErrorCode.ENTITY_NOT_FOUND
    assert info.value.details["ref"] == "timeline:Nope"


def test_resolve_ambiguous_name_lists_candidates() -> None:
    design, timeline = design_with_group()
    timeline.item(3).name = "Extrude1"
    with pytest.raises(BridgeError) as info:
        refs.resolve(design, "timeline:Extrude1")
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["candidates"] == [
        {"index": 2, "type": "ExtrudeFeature"},
        {"index": 3, "type": "FilletFeature"},
    ]


def test_to_ref_of_a_group() -> None:
    _, timeline = design_with_group()
    assert refs.to_ref(timeline.item(0)) == "timeline:Base"
    assert refs.to_ref(timeline.item(1).entity) == "sk-1"

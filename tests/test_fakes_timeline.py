"""The c09 additions to ``tests/fakes/timeline.py`` and ``tests/fakes/parameters.py`` (2.1)."""

import pytest

import fusion_addin
from tests.fakes.design import FakeDesign
from tests.fakes.parameters import FakeAllParameters, FakeModelParameter, FakeParameterSet
from tests.fakes.timeline import FakeTimeline, FakeTimelineGroup

STATES = fusion_addin.adsk.fusion.FeatureHealthStates


def three() -> FakeTimeline:
    timeline = FakeTimeline()
    sketch = timeline.add_feature("Sketch1", "adsk::fusion::Sketch", "sk-1")
    extrude = timeline.add_feature("Extrude1", depends_on=[sketch])
    timeline.add_feature("Fillet1", "adsk::fusion::FilletFeature", depends_on=[extrude])
    return timeline


def test_insertion_at_the_marker_and_move_to_ends() -> None:
    timeline = three()
    timeline.markerPosition = 1
    timeline.add_feature("Plane1", "adsk::fusion::ConstructionPlane")
    assert timeline.names() == ["Sketch1", "Plane1", "Extrude1", "Fillet1"]
    assert timeline.markerPosition == 2
    timeline.moveToBeginning()
    assert timeline.markerPosition == 0
    timeline.moveToEnd()
    assert timeline.markerPosition == 4


def test_roll_to_and_rolled_back() -> None:
    timeline = three()
    extrude = timeline.item(1)
    assert extrude.rollTo(True) is True
    assert timeline.markerPosition == 1
    assert [i.isRolledBack for i in (timeline.item(n) for n in range(3))] == [False, True, True]
    extrude.rollTo(False)
    assert timeline.markerPosition == 2
    timeline.fail_roll = True
    assert extrude.rollTo(True) is False


def test_health_follows_dependencies() -> None:
    timeline = three()
    sketch, extrude, fillet = (timeline.item(i) for i in range(3))
    assert fillet.healthState == STATES.HealthyFeatureHealthState
    extrude.isSuppressed = True
    assert extrude.healthState == STATES.SuppressedFeatureHealthState
    assert fillet.healthState == STATES.ErrorFeatureHealthState
    assert fillet.errorOrWarningMessage == "lost reference to Extrude1"
    extrude.isSuppressed = False
    sketch.entity.deleteMe()
    assert extrude.healthState == STATES.ErrorFeatureHealthState
    assert fillet.healthState == STATES.ErrorFeatureHealthState  # error propagates


def test_suppression_failures() -> None:
    timeline = three()
    item = timeline.item(0)
    item.canSuppress = False
    with pytest.raises(RuntimeError):
        item.isSuppressed = True
    timeline.fail_suppress.add("Extrude1")
    with pytest.raises(RuntimeError):
        timeline.item(1).isSuppressed = True


def test_entity_name_token_and_timeline_object() -> None:
    timeline = three()
    design = FakeDesign(timeline=timeline)
    item = timeline.add_feature("Hole1", "adsk::fusion::HoleFeature", "hole-1", design=design)
    assert design.findEntityByToken("hole-1") == [item.entity]
    item.entity.name = "Bolt hole"
    assert item.name == "Bolt hole"
    assert item.entity.timelineObject is item
    item.entity.rename_error = "read only"
    with pytest.raises(RuntimeError):
        item.entity.name = "x"
    item.entity.deleteMe()
    assert item.entity.timelineObject is None


def test_groups_add_collapse_and_delete() -> None:
    timeline = three()
    group = timeline.timelineGroups.add(0, 1)
    assert isinstance(group, FakeTimelineGroup)
    assert timeline.names() == ["Group1", "Sketch1", "Extrude1", "Fillet1"]
    assert [group.item(j).name for j in range(group.count)] == ["Sketch1", "Extrude1"]
    assert timeline.item(1).parentGroup is group
    assert timeline.markerPosition == 4
    with pytest.raises(RuntimeError):
        timeline.timelineGroups.add(1, 3)  # nested
    group.deleteMe(False)
    assert timeline.names() == ["Sketch1", "Extrude1", "Fillet1"]
    assert timeline.item(0).parentGroup is None
    group = timeline.timelineGroups.add(1, 2)
    assert group is not None
    group.deleteMe(True)
    assert timeline.names() == ["Sketch1"]
    assert timeline.group_deletions == [("Group1", False), ("Group2", True)]


def test_compute_all_hook() -> None:
    design = FakeDesign()
    seen: list[int] = []
    design.on_compute = lambda d: seen.append(d.compute_calls)
    assert design.computeAll() is True
    assert seen == [1]


def test_model_parameter_role_created_by_and_all_parameters() -> None:
    params = FakeParameterSet()
    owner = object()
    d1 = params.add_model("d1", "20 mm", created_by=owner, role="AlongDistance")
    assert isinstance(d1, FakeModelParameter)
    assert (d1.createdBy, d1.role) == (owner, "AlongDistance")
    everything = FakeAllParameters(params)
    assert everything.itemByName("d1") is d1
    assert list(everything) == [d1]
    assert d1.value == pytest.approx(2.0)

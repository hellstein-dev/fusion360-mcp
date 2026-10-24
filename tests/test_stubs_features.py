"""``adsk`` stubs used by feature code (c07 2.1, D21): feature enums and the extent/start
definitions that record their ``create`` arguments."""

from __future__ import annotations

import fusion_addin

adsk = fusion_addin.adsk
F = adsk.fusion
VI = adsk.core.ValueInput


def test_feature_enums_import_with_distinct_values() -> None:
    expected = {
        F.FeatureOperations: (
            "JoinFeatureOperation",
            "CutFeatureOperation",
            "IntersectFeatureOperation",
            "NewBodyFeatureOperation",
            "NewComponentFeatureOperation",
        ),
        F.ExtentDirections: (
            "PositiveExtentDirection",
            "NegativeExtentDirection",
            "SymmetricExtentDirection",
        ),
        F.ThinExtrudeWallLocation: ("Side1", "Side2", "Center"),
        F.SweepOrientationTypes: ("ParallelOrientationType", "PerpendicularOrientationType"),
        F.SweepProfileScalingOptions: (
            "SweepProfileScaleOption",
            "SweepProfileStretchOption",
            "SweepProfileNoScalingOption",
        ),
        F.ThreadLocations: ("HighEndThreadLocation", "LowEndThreadLocation"),
        F.RuleFilletTopologyTypes: (
            "AllEdgesRuleFilletTopologyType",
            "BetweenFacesRuleFilletTopologyType",
        ),
    }
    for enum, names in expected.items():
        values = [getattr(enum, name) for name in names]
        assert all(isinstance(v, int) for v in values)
        assert len(set(values)) == len(values), enum


def test_earlier_stubs_are_kept() -> None:
    # c03/c05 names must not be redefined by c07.
    assert VI.createByReal(1.5).kind == "real"
    assert VI.createByString("wall/2").value == "wall/2"
    assert adsk.core.ObjectCollection.create().count == 0
    assert F.FeatureHealthStates.ErrorFeatureHealthState == 2
    assert adsk.core.SurfaceTypes.PlaneSurfaceType == 0


def test_distance_extent_records_value() -> None:
    value = VI.createByReal(1.5)
    definition = F.DistanceExtentDefinition.create(value)
    assert isinstance(definition, F.DistanceExtentDefinition)
    assert definition.args == (value,)
    assert definition.distance is value
    assert definition.objectType == "adsk::fusion::DistanceExtentDefinition"


def test_to_entity_extent_records_entity_chain_and_offset() -> None:
    face = object()
    offset = VI.createByString("wall/2")
    definition = F.ToEntityExtentDefinition.create(face, True, offset)
    assert definition.args == (face, True, offset)
    assert definition.entity is face
    assert definition.isChained is True
    assert definition.offset is offset
    bare = F.ToEntityExtentDefinition.create(face, False)
    assert bare.args == (face, False, None)
    assert bare.offset is None


def test_through_all_and_profile_plane_take_no_arguments() -> None:
    through = F.ThroughAllExtentDefinition.create()
    assert through.args == ()
    assert through.isFlipped is False
    assert isinstance(through, F.ThroughAllExtentDefinition)
    plane = F.ProfilePlaneStartDefinition.create()
    assert plane.args == ()
    assert plane.objectType == "adsk::fusion::ProfilePlaneStartDefinition"


def test_start_definitions_record_arguments() -> None:
    offset = VI.createByReal(0.5)
    start = F.OffsetStartDefinition.create(offset)
    assert start.args == (offset,)
    assert start.offset is offset
    plane = object()
    entity_start = F.EntityStartDefinition.create(plane, offset)
    assert entity_start.args == (plane, offset)
    assert entity_start.entity is plane
    assert entity_start.offset is offset
    assert F.EntityStartDefinition.create(plane).offset is None


def test_each_create_returns_a_new_object() -> None:
    a = F.ThroughAllExtentDefinition.create()
    b = F.ThroughAllExtentDefinition.create()
    assert a is not b
    one = F.DistanceExtentDefinition.create(VI.createByReal(1))
    two = F.DistanceExtentDefinition.create(VI.createByReal(2))
    assert one.distance.value == 1
    assert two.distance.value == 2

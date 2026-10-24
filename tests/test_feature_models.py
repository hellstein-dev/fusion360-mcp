"""``server/tools/_feature_models.py``: shared feature input models (c07 D20, tasks 1.2-1.3)."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from fusion_mcp.protocol import features as vocab
from fusion_mcp.protocol.units import CM_PER_UNIT
from fusion_mcp.server.tools import _feature_models as m


def adapter(tp: Any) -> TypeAdapter[Any]:
    return TypeAdapter(tp)


def parse(tp: Any, value: Any) -> Any:
    return adapter(tp).validate_python(value)


def rejects(tp: Any, value: Any) -> ValidationError:
    with pytest.raises(ValidationError) as info:
        parse(tp, value)
    return info.value


def dumped(tp: Any, value: Any) -> Any:
    return m.to_params(x=parse(tp, value))["x"]


# --- 1.2: vocabularies, scalars, extents, thin, to_params -----------------------------------


@pytest.mark.parametrize(
    ("literal", "values"),
    [
        (m.Operation, vocab.OPERATIONS),
        (m.ExtentType, vocab.EXTENT_TYPES),
        (m.ExtrudeMode, vocab.EXTRUDE_MODES),
        (m.Direction, vocab.DIRECTIONS),
        (m.StartType, vocab.START_TYPES),
        (m.ThinLocation, vocab.THIN_LOCATIONS),
        (m.HealthState, vocab.HEALTH_STATES),
    ],
)
def test_literals_match_protocol_tuples(literal: Any, values: tuple[str, ...]) -> None:
    assert get_args(literal) == values


def test_units_are_the_protocol_length_units() -> None:
    assert set(get_args(m.Units)) == set(CM_PER_UNIT)


def test_union_tags_match_protocol_tuples() -> None:
    def tags(union: Any) -> tuple[str, ...]:
        members = get_args(get_args(union)[0])
        return tuple(get_args(c.model_fields["type"].annotation)[0] for c in members)

    assert tags(m.Extent) == vocab.EXTENT_TYPES
    assert tags(m.StartExtent) == vocab.START_TYPES


@pytest.mark.parametrize("value", [15, -2.5, 0, "wall / 2", "25 mm"])
def test_length_and_angle_accept_numbers_and_expressions(value: Any) -> None:
    assert parse(m.Length, value) == value
    assert parse(m.Angle, value) == value


@pytest.mark.parametrize("value", [True, "", "   ", float("nan"), float("inf"), None, [1]])
def test_length_and_angle_reject(value: Any) -> None:
    rejects(m.Length, value)
    rejects(m.Angle, value)


def test_ref_must_be_non_empty() -> None:
    assert parse(m.Ref, "tok") == "tok"
    rejects(m.Ref, "")


@pytest.mark.parametrize(
    ("value", "cls"),
    [
        ({"type": "distance", "distance": 15}, m.DistanceExtent),
        ({"type": "to_entity", "entity": "face-1"}, m.ToEntityExtent),
        ({"type": "through_all"}, m.ThroughAllExtent),
    ],
)
def test_extent_union_picks_model_by_type(value: dict[str, Any], cls: type) -> None:
    assert isinstance(parse(m.Extent, value), cls)


def test_extent_defaults_and_dump() -> None:
    assert dumped(m.Extent, {"type": "to_entity", "entity": "f", "offset": "wall/2"}) == {
        "type": "to_entity",
        "entity": "f",
        "offset": "wall/2",
        "chained": True,
    }
    assert dumped(m.Extent, {"type": "distance", "distance": -3}) == {
        "type": "distance",
        "distance": -3,
    }
    assert dumped(m.Extent, {"type": "through_all"}) == {"type": "through_all"}


@pytest.mark.parametrize(
    "value",
    [
        {"type": "blind", "distance": 5},
        {"distance": 5},
        {"type": "distance"},
        {"type": "distance", "distance": 5, "entity": "f"},
        {"type": "to_entity"},
        {"type": "through_all", "distance": 5},
    ],
)
def test_extent_rejects(value: dict[str, Any]) -> None:
    rejects(m.Extent, value)


def test_start_extent() -> None:
    assert isinstance(parse(m.StartExtent, {"type": "profile_plane"}), m.ProfilePlaneStart)
    assert dumped(m.StartExtent, {"type": "offset", "offset": 5}) == {"type": "offset", "offset": 5}
    assert dumped(m.StartExtent, {"type": "entity", "entity": "origin:XY"}) == {
        "type": "entity",
        "entity": "origin:XY",
        "offset": 0.0,
    }
    rejects(m.StartExtent, {"type": "offset"})
    rejects(m.StartExtent, {"type": "entity"})
    rejects(m.StartExtent, {"type": "distance", "distance": 1})


def test_thin_options() -> None:
    assert dumped(m.ThinOptions, {"thickness": 2}) == {"thickness": 2, "location": "side1"}
    assert dumped(m.ThinOptions, {"thickness": 2, "location": "center", "thickness2": 1}) == {
        "thickness": 2,
        "location": "center",
        "thickness2": 1,
    }
    rejects(m.ThinOptions, {"thickness": 2, "location": "middle"})
    rejects(m.ThinOptions, {"location": "side1"})


def test_json_schema_has_discriminators() -> None:
    for union in (m.Extent, m.StartExtent, m.RevolveExtent, m.HolePlacement, m.HoleExtent):
        schema = adapter(union).json_schema()
        assert schema["discriminator"]["propertyName"] == "type"
        assert len(schema["oneOf"]) == len(schema["discriminator"]["mapping"])
    extent = adapter(m.Extent).json_schema()
    assert set(extent["discriminator"]["mapping"]) == set(vocab.EXTENT_TYPES)


def test_profile_spec_forms() -> None:
    assert parse(m.ProfileSpec, "profile:sk@1.0000,2.0000") == "profile:sk@1.0000,2.0000"
    assert isinstance(parse(m.ProfileSpec, {"sketch": "sk", "point": [1, 2]}), m.ProfileAtPoint)
    assert isinstance(parse(m.ProfileSpec, {"sketch": "sk", "curves": ["c1"]}), m.ProfileByCurves)
    assert isinstance(parse(m.ProfileSpec, {"sketch": "sk", "all": True}), m.AllProfiles)
    assert dumped(m.ProfileSpec, {"sketch": "sk", "point": [1, 2]}) == {
        "sketch": "sk",
        "point": [1.0, 2.0],
    }


@pytest.mark.parametrize(
    "value",
    [
        "",
        {"sketch": "sk"},
        {"sketch": "sk", "all": False},
        {"sketch": "sk", "curves": []},
        {"sketch": "sk", "point": [1]},
        {"sketch": "sk", "point": [1, 2], "all": True},
        {"point": [1, 2]},
    ],
)
def test_profile_spec_rejects(value: Any) -> None:
    rejects(m.ProfileSpec, value)


def test_to_params_drops_none_and_dumps_models() -> None:
    extent = parse(m.Extent, {"type": "distance", "distance": 10})
    thin = m.ThinOptions(thickness=2)
    params = m.to_params(
        profiles=["p1", parse(m.ProfileSpec, {"sketch": "s", "all": True})],
        side1=extent,
        side2=None,
        thin=thin,
        start=None,
        participant_bodies=None,
        taper1=0,
        symmetric_full_length=False,
        units="mm",
    )
    assert params == {
        "profiles": ["p1", {"sketch": "s", "all": True}],
        "side1": {"type": "distance", "distance": 10},
        "thin": {"thickness": 2, "location": "side1"},
        "taper1": 0,
        "symmetric_full_length": False,
        "units": "mm",
    }
    assert m.to_params() == {}
    assert m.to_params(a={"b": None, "c": thin}) == {
        "a": {"c": {"thickness": 2, "location": "side1"}}
    }


# --- 1.3: revolve, loft, hole, thread, fillet ------------------------------------------------


def test_revolve_extent() -> None:
    assert dumped(m.RevolveExtent, {"type": "full"}) == {"type": "full"}
    assert dumped(m.RevolveExtent, {"type": "angle"}) == {
        "type": "angle",
        "angle": 360.0,
        "symmetric": False,
    }
    assert dumped(m.RevolveExtent, {"type": "angle", "angle": "90 deg", "symmetric": True}) == {
        "type": "angle",
        "angle": "90 deg",
        "symmetric": True,
    }
    assert dumped(m.RevolveExtent, {"type": "two_angles", "angle1": 30, "angle2": 60}) == {
        "type": "two_angles",
        "angle1": 30,
        "angle2": 60,
    }
    assert dumped(m.RevolveExtent, {"type": "to_entity", "entity": "f"}) == {
        "type": "to_entity",
        "entity": "f",
        "direction": "positive",
    }
    for bad in (
        {"type": "two_angles", "angle1": 30},
        {"type": "to_entity"},
        {"type": "to_entity", "entity": "f", "direction": "up"},
        {"type": "full", "angle": 90},
        {"type": "distance", "distance": 5},
    ):
        rejects(m.RevolveExtent, bad)


def test_loft_section_defaults_and_dump() -> None:
    assert dumped(m.LoftSection, {"entity": "profile:s@0,0"}) == {
        "entity": "profile:s@0,0",
        "end_condition": "free",
        "angle": 0.0,
        "weight": 1.0,
    }
    section = parse(
        m.LoftSection,
        {"entity": {"sketch": "s", "all": True}, "end_condition": "direction", "angle": 10},
    )
    assert isinstance(section.entity, m.AllProfiles)
    assert m.to_params(s=section)["s"]["entity"] == {"sketch": "s", "all": True}
    for condition in ("tangent", "smooth", "direction", "point_tangent"):
        assert parse(m.LoftSection, {"entity": "e", "end_condition": condition, "weight": 2})


@pytest.mark.parametrize(
    "value",
    [
        {"entity": "e", "end_condition": "sharp"},
        {"entity": "e", "end_condition": "tangent", "angle": 5},
        {"entity": "e", "end_condition": "free", "weight": 2},
        {"entity": "e", "end_condition": "point_sharp", "weight": 0.5},
        {"entity": "e", "end_condition": "smooth", "weight": 0},
        {"entity": "e", "weight": True},
        {"end_condition": "free"},
    ],
)
def test_loft_section_rejects(value: dict[str, Any]) -> None:
    rejects(m.LoftSection, value)


def test_hole_placements() -> None:
    assert dumped(m.HolePlacement, {"type": "point", "face": "f", "point": [1, 2, 3]}) == {
        "type": "point",
        "face": "f",
        "point": [1.0, 2.0, 3.0],
    }
    assert dumped(m.HolePlacement, {"type": "sketch_points", "points": ["a", "b"]}) == {
        "type": "sketch_points",
        "points": ["a", "b"],
    }
    offsets = {
        "type": "face_offsets",
        "face": "f",
        "point": [0, 0, 0],
        "edge1": "e1",
        "offset1": 10,
        "edge2": "e2",
        "offset2": "15 mm",
    }
    assert dumped(m.HolePlacement, offsets) == {**offsets, "point": [0.0, 0.0, 0.0]}
    assert dumped(m.HolePlacement, {"type": "center", "face": "f", "edge": "e"}) == {
        "type": "center",
        "face": "f",
        "edge": "e",
    }


@pytest.mark.parametrize(
    "value",
    [
        {"type": "point", "face": "f", "point": [1, 2]},
        {"type": "point", "face": "f", "point": ["1", 2, 3]},
        {"type": "point", "point": [1, 2, 3]},
        {"type": "sketch_points", "points": []},
        {"type": "face_offsets", "face": "f", "point": [0, 0, 0], "edge1": "e1", "offset1": 1},
        {"type": "center", "face": "f"},
        {"type": "axis", "edge": "e"},
    ],
)
def test_hole_placement_rejects(value: dict[str, Any]) -> None:
    rejects(m.HolePlacement, value)


def test_hole_extent() -> None:
    assert dumped(m.HoleExtent, {"type": "distance", "depth": 12}) == {
        "type": "distance",
        "depth": 12,
    }
    assert dumped(m.HoleExtent, {"type": "through_all"}) == {"type": "through_all"}
    assert dumped(m.HoleExtent, {"type": "to_entity", "entity": "f"}) == {
        "type": "to_entity",
        "entity": "f",
    }
    rejects(m.HoleExtent, {"type": "distance"})
    rejects(m.HoleExtent, {"type": "distance", "distance": 5})
    rejects(m.HoleExtent, {"type": "to_entity"})


def test_thread_spec_defaults_and_dump() -> None:
    assert dumped(m.ThreadSpec, {}) == {
        "thread_type": "ISO Metric profile",
        "modeled": False,
        "full_length": True,
        "offset": 0.0,
        "location": "high",
    }
    spec = {
        "designation": "M6x1",
        "thread_class": "6H",
        "modeled": True,
        "full_length": False,
        "length": 8,
        "offset": "1 mm",
        "location": "low",
    }
    assert dumped(m.ThreadSpec, spec) == {"thread_type": "ISO Metric profile", **spec}


@pytest.mark.parametrize(
    "value",
    [
        {"length": 8},
        {"full_length": False},
        {"location": "middle"},
        {"designation": ""},
        {"thread_type": ""},
        {"pitch": 1},
    ],
)
def test_thread_spec_rejects(value: dict[str, Any]) -> None:
    rejects(m.ThreadSpec, value)


def test_fillet_mid() -> None:
    assert dumped(m.FilletMid, {"position": 0.5, "radius": 3}) == {"position": 0.5, "radius": 3}
    assert parse(m.FilletMid, {"position": 0, "radius": "r"}).position == 0
    assert parse(m.FilletMid, {"position": 1, "radius": 1}).position == 1
    for bad in (
        {"position": -0.1, "radius": 3},
        {"position": 1.1, "radius": 3},
        {"position": "0.5", "radius": 3},
        {"position": 0.5},
    ):
        rejects(m.FilletMid, bad)


def test_models_forbid_unknown_fields() -> None:
    rejects(m.ThinOptions, {"thickness": 1, "side": "x"})
    rejects(m.FilletMid, {"position": 0.5, "radius": 1, "extra": 1})

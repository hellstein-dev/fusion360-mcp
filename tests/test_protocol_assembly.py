"""``fusion_mcp.protocol.assembly``: joint geometry and motion parsing (c10 tasks 4.1, 4.2)."""

from __future__ import annotations

from typing import Any

import pytest

from fusion_mcp.protocol.assembly import (
    DOF_ATTRS,
    DOFS,
    DOFS_BY_TYPE,
    GEOMETRY_KINDS,
    JOINT_TYPE_ENUMS,
    JOINT_TYPES,
    MOTION_KEYS,
    ROTATIONAL_DOFS,
    check_dof,
    parse_joint_geometry,
    parse_motion,
    value_key,
)
from fusion_mcp.protocol.errors import BridgeError, ErrorCode


def invalid(fn: Any, *args: Any) -> BridgeError:
    with pytest.raises(BridgeError) as exc:
        fn(*args)
    assert exc.value.code is ErrorCode.INVALID_PARAMS
    return exc.value


# --- 4.1 vocabularies and geometry -------------------------------------------------------------


def test_tables_are_consistent() -> None:
    assert set(JOINT_TYPES) == set(DOFS_BY_TYPE) == set(JOINT_TYPE_ENUMS) == set(MOTION_KEYS)
    assert {d for dofs in DOFS_BY_TYPE.values() for d in dofs} == set(DOFS) == set(DOF_ATTRS)
    assert sorted(ROTATIONAL_DOFS) == ["pitch", "roll", "rotation", "yaw"]
    assert DOFS_BY_TYPE["rigid"] == ()
    assert DOFS_BY_TYPE["planar"] == ("primary_slide", "secondary_slide", "rotation")
    assert value_key("rotation") == "rotation_deg" and value_key("slide") == "slide_mm"


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({"kind": "point", "entity": "v"}, {"key_point": None}),
        ({"kind": "planar_face", "entity": "f"}, {"key_point": "center", "edge": None}),
        (
            {"kind": "planar_face", "entity": "f", "key_point": "middle", "edge": "e"},
            {"key_point": "middle", "edge": "e"},
        ),
        ({"kind": "nonplanar_face", "entity": "f"}, {"key_point": "center"}),
        ({"kind": "curve", "entity": "e"}, {"key_point": "middle"}),
        ({"kind": "curve", "entity": "e", "key_point": "start"}, {"key_point": "start"}),
        (
            {"kind": "profile", "entity": "profile:p@1,2", "curve": "c"},
            {"key_point": "center", "curve": "c"},
        ),
        ({"kind": "joint_origin", "entity": "jo"}, {"key_point": None}),
    ],
)
def test_parse_each_kind(spec: dict[str, Any], expected: dict[str, Any]) -> None:
    parsed = parse_joint_geometry(spec, "geometry_one")
    assert parsed["kind"] == spec["kind"] and parsed["entity"] == spec["entity"]
    for key, value in expected.items():
        assert parsed[key] == value
    assert set(parsed) == {"kind", "entity", "key_point", "edge", "curve"}


def test_invalid_kind_lists_allowed() -> None:
    err = invalid(parse_joint_geometry, {"kind": "sphere", "entity": "x"}, "geometry_one")
    assert err.details["allowed"] == list(GEOMETRY_KINDS)
    assert "planar_face" in err.message and err.details["param"] == "geometry_one.kind"


@pytest.mark.parametrize(
    "spec",
    [
        "face",
        {"kind": "curve"},
        {"kind": "curve", "entity": ""},
        {"kind": "curve", "entity": 5},
        {"kind": "curve", "entity": "e", "key_point": "top"},
        {"kind": "curve", "entity": "e", "edge": "e2"},
        {"kind": "point", "entity": "v", "key_point": "center"},
        {"kind": "nonplanar_face", "entity": "f", "curve": "c"},
        {"kind": "curve", "entity": "e", "radius": 1},
    ],
)
def test_geometry_errors(spec: Any) -> None:
    invalid(parse_joint_geometry, spec, "geometry")


# --- 4.2 motion --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("joint_type", "motion", "expected"),
    [
        ("rigid", None, {}),
        ("revolute", None, {"axis": "z"}),
        ("revolute", {"axis": "X"}, {"axis": "x"}),
        ("revolute", {"axis": "E=@Box:1"}, {"axis": "E=@Box:1"}),
        ("slider", {"direction": "y"}, {"direction": "y"}),
        ("cylindrical", {}, {"axis": "z"}),
        ("pin_slot", {"axis": "e1"}, {"axis": "e1", "slide_direction": "x"}),
        ("planar", None, {"normal": "z", "primary_slide_direction": None}),
        (
            "planar",
            {"normal": "x", "primary_slide_direction": "e2"},
            {"normal": "x", "primary_slide_direction": "e2"},
        ),
        ("ball", {"yaw_direction": "y"}, {"pitch_direction": "z", "yaw_direction": "y"}),
    ],
)
def test_parse_motion(joint_type: str, motion: Any, expected: dict[str, Any]) -> None:
    assert parse_motion(joint_type, motion) == expected


def test_motion_key_not_valid_for_type() -> None:
    err = invalid(parse_motion, "slider", {"axis": "x"})
    assert err.details["unknown"] == ["axis"] and err.details["allowed"] == ["direction"]
    invalid(parse_motion, "rigid", {"axis": "z"})


@pytest.mark.parametrize(
    ("joint_type", "motion"),
    [
        ("hinge", None),
        ("revolute", "z"),
        ("revolute", {"axis": ""}),
        ("revolute", {"axis": 1}),
        ("planar", {"primary_slide_direction": "x"}),
    ],
)
def test_motion_errors(joint_type: str, motion: Any) -> None:
    invalid(parse_motion, joint_type, motion)


def test_check_dof() -> None:
    assert check_dof("cylindrical", "slide") == "slide"
    err = invalid(check_dof, "revolute", "slide")
    assert err.details["allowed"] == ["rotation"]
    invalid(check_dof, "rigid", "rotation")

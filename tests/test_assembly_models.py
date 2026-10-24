"""``server/tools/assembly.py`` input models (c10 D8, task 1.5)."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge
from fusion_mcp.server.tools import TOOL_MODULES
from fusion_mcp.server.tools import assembly as m
from fusion_mcp.server.tools._feature_models import to_params


def parse(tp: Any, value: Any) -> Any:
    return TypeAdapter(tp).validate_python(value)


def rejects(tp: Any, value: Any) -> None:
    with pytest.raises(ValidationError):
        parse(tp, value)


def test_vocabularies() -> None:
    assert get_args(m.JointType) == (
        "rigid",
        "revolute",
        "slider",
        "cylindrical",
        "pin_slot",
        "planar",
        "ball",
    )
    assert set(get_args(m.Dof)) == {
        "rotation",
        "slide",
        "primary_slide",
        "secondary_slide",
        "pitch",
        "yaw",
        "roll",
    }
    rejects(m.JointType, "hinge")


@pytest.mark.parametrize(
    "spec",
    [
        {"kind": "point", "entity": "v1@Box:1"},
        {"kind": "planar_face", "entity": "f1", "key_point": "center", "edge": "e1"},
        {"kind": "nonplanar_face", "entity": "f2", "key_point": "middle"},
        {"kind": "curve", "entity": "e1@Lid:1", "key_point": "start"},
        {"kind": "profile", "entity": "p1", "curve": "c1", "key_point": "end"},
        {"kind": "joint_origin", "entity": "jo1"},
    ],
)
def test_joint_geometry_kinds(spec: dict[str, Any]) -> None:
    assert to_params(g=parse(m.JointGeometry, spec))["g"] == spec


@pytest.mark.parametrize(
    "spec",
    [
        {"kind": "sphere", "entity": "f1"},
        {"kind": "curve"},
        {"kind": "curve", "entity": ""},
        {"kind": "curve", "entity": "e1", "key_point": "top"},
        {"kind": "point", "entity": "v1", "key_point": "center"},
        {"kind": "curve", "entity": "e1", "edge": "e2"},
        {"kind": "planar_face", "entity": "f1", "curve": "c1"},
        {"kind": "curve", "entity": "e1", "extra": 1},
    ],
)
def test_joint_geometry_rejects(spec: dict[str, Any]) -> None:
    rejects(m.JointGeometry, spec)


def test_rotate_defaults_and_validation() -> None:
    assert to_params(r=parse(m.Rotate, {"axis": [0, 0, 1], "angle_deg": 90}))["r"] == {
        "axis": [0.0, 0.0, 1.0],
        "angle_deg": 90.0,
        "origin_mm": [0.0, 0.0, 0.0],
    }
    rejects(m.Rotate, {"axis": [0, 0, 0], "angle_deg": 90})
    rejects(m.Rotate, {"axis": [0, 0, 1]})
    rejects(m.Rotate, {"axis": [0, 1], "angle_deg": 90})
    rejects(m.Rotate, {"axis": [0, 0, 1], "angle_deg": "90 deg"})


def test_placement_validation() -> None:
    spec = {"origin_mm": [1, 2, 3], "x_axis": [1, 0, 0], "y_axis": [0, 1, 0]}
    assert parse(m.Placement, spec).origin_mm == [1.0, 2.0, 3.0]
    rejects(m.Placement, {**spec, "x_axis": [0, 0, 0]})
    rejects(m.Placement, {"origin_mm": [0, 0, 0], "x_axis": [1, 0, 0]})
    rejects(m.Placement, {**spec, "z_axis": [0, 0, 1]})


def test_module_registered_in_server() -> None:
    assert m in TOOL_MODULES
    create_server(FakeBridge())

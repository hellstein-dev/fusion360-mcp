"""``protocol/construction.py``: the method table and ``validate`` (c06 D3, task 1.1)."""

from __future__ import annotations

from typing import Any

import pytest

from fusion_mcp.protocol.construction import (
    ANGLE,
    COMMON_PARAMS,
    LENGTH,
    METHODS,
    PROPORTION,
    REF,
    REFS,
    method_spec,
    validate,
)
from fusion_mcp.protocol.errors import BridgeError, ErrorCode

# One complete valid call per method: (kind, method, params); validate returns params as is.
VALID: list[tuple[str, str, dict[str, Any]]] = [
    ("plane", "offset", {"plane": "origin:XY", "distance": 15}),
    ("plane", "angle", {"edge": "e", "angle": 30, "plane": "p"}),
    ("plane", "midplane", {"plane": "a", "plane2": "b"}),
    ("plane", "two_edges", {"edge": "a", "edge2": "b"}),
    ("plane", "three_points", {"points": ["a", "b", "c"]}),
    ("plane", "tangent", {"face": "f", "angle": 15, "plane": "p"}),
    ("plane", "tangent_at_point", {"face": "f", "point": "v"}),
    ("plane", "along_path", {"path": "e", "proportion": 0.5}),
    ("axis", "two_points", {"points": ["a", "b"]}),
    ("axis", "edge", {"edge": "e"}),
    ("axis", "cylinder", {"face": "f"}),
    ("axis", "perpendicular_at_point", {"face": "f", "point": "v"}),
    ("axis", "two_planes", {"plane": "origin:XZ", "plane2": "origin:YZ"}),
    ("point", "vertex", {"point": "v"}),
    ("point", "two_edges", {"edge": "a", "edge2": "b"}),
    ("point", "three_planes", {"plane": "a", "plane2": "b", "plane3": "c"}),
    ("point", "edge_plane", {"edge": "e", "plane": "p"}),
    ("point", "center", {"entity": "c"}),
]


def invalid(kind: str, method: Any, params: dict[str, Any]) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        validate(kind, method, params)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    return info.value


def test_table_covers_every_method_of_d3() -> None:
    assert {k: list(v) for k, v in METHODS.items()} == {
        "plane": [
            "offset",
            "angle",
            "midplane",
            "two_edges",
            "three_points",
            "tangent",
            "tangent_at_point",
            "along_path",
        ],
        "axis": ["two_points", "edge", "cylinder", "perpendicular_at_point", "two_planes"],
        "point": ["vertex", "two_edges", "three_planes", "edge_plane", "center"],
    }
    assert {(kind, method) for kind, method, _ in VALID} == {
        (kind, method) for kind, methods in METHODS.items() for method in methods
    }


def test_field_types_and_entity_kinds() -> None:
    def fields(kind: str, method: str) -> list[tuple[str, str, str | None, int | None]]:
        spec = method_spec(kind, method)
        return [(f.name, f.field_type, f.entity_kind, f.count) for f in spec.fields]

    assert fields("plane", "offset") == [
        ("plane", REF, "planar", None),
        ("distance", LENGTH, None, None),
    ]
    assert fields("plane", "angle") == [
        ("edge", REF, "linear", None),
        ("angle", ANGLE, None, None),
        ("plane", REF, "planar", None),
    ]
    assert fields("plane", "three_points") == [("points", REFS, "point", 3)]
    assert fields("plane", "tangent")[0] == ("face", REF, "tangent_face", None)
    assert fields("plane", "along_path") == [
        ("path", REF, "path", None),
        ("proportion", PROPORTION, None, None),
    ]
    assert fields("axis", "two_points") == [("points", REFS, "point", 2)]
    assert fields("axis", "cylinder") == [("face", REF, "circular_face", None)]
    assert fields("axis", "perpendicular_at_point") == [
        ("face", REF, "face", None),
        ("point", REF, "point", None),
    ]
    assert fields("point", "two_edges") == [
        ("edge", REF, "edge", None),
        ("edge2", REF, "edge", None),
    ]
    assert fields("point", "edge_plane") == [
        ("edge", REF, "edge", None),
        ("plane", REF, "planar", None),
    ]
    assert fields("point", "center") == [("entity", REF, "center_source", None)]
    assert method_spec("plane", "tangent").required == ("face", "plane")


@pytest.mark.parametrize(("kind", "method", "params"), VALID)
def test_valid_call_returns_fields(kind: str, method: str, params: dict[str, Any]) -> None:
    assert validate(kind, method, params) == params


def test_common_params_and_none_are_ignored() -> None:
    params = {
        "method": "offset",
        "plane": "origin:XY",
        "distance": "height / 2",
        "component": "component:root",
        "name": "Lid plane",
        "units": "mm",
        "edge": None,
    }
    assert validate("plane", "offset", params) == {"plane": "origin:XY", "distance": "height / 2"}
    assert {"method", "component", "name", "units"} == COMMON_PARAMS


def test_ref_may_be_a_ref_dict() -> None:
    out = validate("axis", "edge", {"edge": {"ref": "tok", "type": "BRepEdge"}})
    assert out == {"edge": {"ref": "tok", "type": "BRepEdge"}}


def test_tangent_angle_defaults_to_zero() -> None:
    assert validate("plane", "tangent", {"face": "f", "plane": "p"}) == {
        "face": "f",
        "angle": 0,
        "plane": "p",
    }


def test_unknown_method() -> None:
    error = invalid("plane", "mirror", {})
    assert error.details["param"] == "method"
    assert error.details["expected"] == list(METHODS["plane"])
    assert invalid("axis", None, {}).details["param"] == "method"
    assert invalid("point", 3, {}).details["value"] == "3"


def test_unknown_kind_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="kind"):
        validate("sketch", "offset", {})


def test_missing_required_field() -> None:
    error = invalid("plane", "offset", {"plane": "origin:XY"})
    assert error.details == {
        "param": "distance",
        "method": "offset",
        "missing": ["distance"],
        "required": ["plane", "distance"],
    }
    assert "distance" in error.message


def test_field_of_another_method_is_rejected() -> None:
    error = invalid("plane", "offset", {"plane": "origin:XY", "distance": 5, "edge": "e"})
    assert error.details["param"] == "edge"
    assert error.details["unexpected"] == ["edge"]
    assert error.details["allowed"] == ["plane", "distance"]


@pytest.mark.parametrize(
    ("kind", "method", "count"), [("axis", "two_points", 2), ("plane", "three_points", 3)]
)
def test_points_length(kind: str, method: str, count: int) -> None:
    for wrong in (["a"], ["a", "b", "c", "d"]):
        error = invalid(kind, method, {"points": wrong})
        assert error.details["param"] == "points"
        assert error.details["expected_count"] == count
        assert error.details["got_count"] == len(wrong)


def test_points_must_be_a_list_of_refs() -> None:
    assert invalid("axis", "two_points", {"points": "a,b"}).details["param"] == "points"
    assert invalid("axis", "two_points", {"points": ["a", ""]}).details["param"] == "points"
    assert invalid("axis", "two_points", {"points": ["a", 3]}).details["param"] == "points"


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), "0.5", True])
def test_proportion_out_of_range_or_not_a_number(value: Any) -> None:
    error = invalid("plane", "along_path", {"path": "e", "proportion": value})
    assert error.details["param"] == "proportion"


@pytest.mark.parametrize("value", [0, 1, 0.0, 1.0, 0.25])
def test_proportion_bounds_are_inclusive(value: float) -> None:
    assert (
        validate("plane", "along_path", {"path": "e", "proportion": value})["proportion"] == value
    )


@pytest.mark.parametrize("value", ["", "   ", True, float("inf"), [1]])
def test_length_and_angle_values(value: Any) -> None:
    assert (
        invalid("plane", "offset", {"plane": "p", "distance": value}).details["param"] == "distance"
    )
    error = invalid("plane", "angle", {"edge": "e", "angle": value, "plane": "p"})
    assert error.details["param"] == "angle"


def test_negative_distance_and_expressions_are_valid() -> None:
    assert validate("plane", "offset", {"plane": "p", "distance": -5})["distance"] == -5
    assert (
        validate("plane", "angle", {"edge": "e", "angle": "30 deg", "plane": "p"})["angle"]
        == "30 deg"
    )


@pytest.mark.parametrize("value", ["", 5, None.__class__, {"ref": ""}])
def test_ref_must_be_non_empty_string(value: Any) -> None:
    error = invalid("point", "center", {"entity": value})
    assert error.details == {"param": "entity", "method": "center"}

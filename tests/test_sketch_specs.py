"""``fusion_addin.sketch.specs``: local ids, PointSpec and EntitySpec (c05 1.2, D2, D4)."""

import pytest

from fusion_addin.sketch.specs import (
    ORIGIN_ID,
    LocalPath,
    ModelXyz,
    Origin,
    Token,
    Xy,
    is_local_id,
    parse_entity_spec,
    parse_point_spec,
    validate_local_id,
)
from fusion_mcp.protocol.errors import BridgeError, ErrorCode

BASE64_TOKEN = "/v4DAAAAZm9vYmFyYmF6cXV4AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA+=="


def _invalid(fn: object, *args: object) -> BridgeError:
    assert callable(fn)
    with pytest.raises(BridgeError) as info:
        fn(*args)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    return info.value


@pytest.mark.parametrize("value", ["a", "Line1", "r_2", "x" * 32, "A_b_C"])
def test_valid_local_ids(value: str) -> None:
    assert is_local_id(value)
    assert validate_local_id(value) == value


@pytest.mark.parametrize("value", ["", "1a", "_a", "a-b", "a.b", "x" * 33, "a b", None, 5])
def test_invalid_local_ids(value: object) -> None:
    assert not is_local_id(value)
    err = _invalid(validate_local_id, value)
    assert err.details["reason"] == "invalid_local_id"
    assert err.details["field"] == "id"


def test_origin_id_is_reserved() -> None:
    assert ORIGIN_ID == "origin"
    err = _invalid(validate_local_id, "origin")
    assert err.details["reason"] == "reserved_id"


def test_validate_local_id_field_name() -> None:
    err = _invalid(validate_local_id, "9", "items[3].id")
    assert err.details["field"] == "items[3].id"


def test_entity_spec_origin() -> None:
    assert parse_entity_spec("origin") == Origin()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("l1", LocalPath("l1")),
        ("l1.end", LocalPath("l1", "end")),
        ("r.bottom", LocalPath("r", "bottom")),
        ("p.e0", LocalPath("p", "e0")),
        ("Origin", LocalPath("Origin")),
    ],
)
def test_entity_spec_local_path(value: str, expected: LocalPath) -> None:
    parsed = parse_entity_spec(value)
    assert parsed == expected
    assert isinstance(parsed, LocalPath)
    assert parsed.path == value


@pytest.mark.parametrize(
    "value",
    [BASE64_TOKEN, "origin:XY", "a.b.c", "x" * 33, "l1.", "1abc", "tok@Lid:1", "a.1"],
)
def test_entity_spec_token(value: str) -> None:
    assert parse_entity_spec(value) == Token(value)


def test_origin_has_no_sub_entities() -> None:
    err = _invalid(parse_entity_spec, "origin.start")
    assert err.details["reason"] == "reserved_id"


@pytest.mark.parametrize("value", ["", None, 3, ["l1"], {"ref": "tok"}])
def test_entity_spec_invalid(value: object) -> None:
    err = _invalid(parse_entity_spec, value, "line")
    assert err.details["reason"] == "invalid_entity_spec"
    assert err.details["field"] == "line"


def test_point_spec_xy() -> None:
    assert parse_point_spec([1, 2.5]) == Xy(1.0, 2.5)
    assert parse_point_spec((-3, 0)) == Xy(-3.0, 0.0)
    xy = parse_point_spec([1, 2])
    assert isinstance(xy, Xy)
    assert isinstance(xy.x, float)


def test_point_spec_model() -> None:
    assert parse_point_spec({"model": [10, 0, 5]}) == ModelXyz(10.0, 0.0, 5.0)


def test_point_spec_strings_are_entity_specs() -> None:
    assert parse_point_spec("origin") == Origin()
    assert parse_point_spec("l1.end") == LocalPath("l1", "end")
    assert parse_point_spec(BASE64_TOKEN) == Token(BASE64_TOKEN)


@pytest.mark.parametrize("value", [["10 mm", 0], [0, "w/2"], {"model": [0, "h", 0]}, ("1", "2")])
def test_point_spec_string_coordinates_hint_dimension(value: object) -> None:
    err = _invalid(parse_point_spec, value, "corner1")
    assert err.details["reason"] == "string_coordinate"
    assert "sketch_dimension" in err.message
    assert "sketch_dimension" in err.details["hint"]
    assert err.details["field"] == "corner1"


@pytest.mark.parametrize(
    "value",
    [
        [],
        [1],
        [1, 2, 3],
        [True, 0],
        [None, 0],
        [float("nan"), 0],
        [float("inf"), 0],
        {"model": [1, 2]},
        {"model": [1, 2, 3], "x": 1},
        {"sketch": [1, 2]},
        {},
        3,
        None,
        "",
    ],
)
def test_point_spec_invalid(value: object) -> None:
    err = _invalid(parse_point_spec, value)
    assert err.details["field"] == "point"

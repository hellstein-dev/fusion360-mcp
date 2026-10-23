"""``fusion_mcp.protocol.refs``: origin refs and assembly-context grammar (c03 1.6, D15)."""

import pytest

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.refs import (
    NAME_PREFIXES,
    ORIGIN_NAMES,
    format_origin_ref,
    is_origin_ref,
    parse_origin_ref,
    split_context,
    with_context,
)


def test_origin_names() -> None:
    assert ORIGIN_NAMES == ("XY", "XZ", "YZ", "X", "Y", "Z", "O")


@pytest.mark.parametrize(
    ("ref", "name"),
    [("origin:xy", "XY"), ("origin:XY", "XY"), ("origin:yZ", "YZ"), ("origin:o", "O")],
)
def test_parse_origin_ref_any_case(ref: str, name: str) -> None:
    assert parse_origin_ref(ref) == name


@pytest.mark.parametrize("ref", ["origin:XW", "origin:", "origin:XYZ", "plane:XY"])
def test_parse_origin_ref_unknown(ref: str) -> None:
    with pytest.raises(BridgeError) as info:
        parse_origin_ref(ref)
    assert info.value.code is ErrorCode.INVALID_PARAMS


def test_format_origin_ref() -> None:
    assert format_origin_ref("xz") == "origin:XZ"
    assert all(parse_origin_ref(format_origin_ref(n)) == n for n in ORIGIN_NAMES)
    with pytest.raises(ValueError):
        format_origin_ref("W")


def test_is_origin_ref() -> None:
    assert is_origin_ref("origin:Z")
    assert not is_origin_ref("tok")
    assert not is_origin_ref(None)


def test_name_prefixes() -> None:
    assert NAME_PREFIXES == (
        "component:",
        "occurrence:",
        "param:",
        "timeline:",
        "document:",
        "profile:",
    )


def test_split_context() -> None:
    assert split_context("tokABC") == ("tokABC", None)
    assert split_context("tokABC@Lid:1") == ("tokABC", "Lid:1")
    assert split_context("tok@A:1+B:1") == ("tok", "A:1+B:1")
    assert split_context("origin:XY@Lid:1") == ("origin:XY", "Lid:1")


def test_name_refs_are_not_split() -> None:
    assert split_context("component:Part@2") == ("component:Part@2", None)
    assert split_context("profile:tok@1.5,2") == ("profile:tok@1.5,2", None)
    assert split_context("param:d@1") == ("param:d@1", None)


@pytest.mark.parametrize("ref", ["@Lid:1", "tok@", "@"])
def test_split_context_empty_part(ref: str) -> None:
    with pytest.raises(ValueError):
        split_context(ref)


def test_with_context_round_trip() -> None:
    assert with_context("tok", "Lid:1") == "tok@Lid:1"
    assert with_context("tok", None) == "tok"
    assert split_context(with_context("origin:XY", "A:1+B:1")) == ("origin:XY", "A:1+B:1")


@pytest.mark.parametrize(
    ("inner", "path"), [("tok@A:1", "B:1"), ("component:P", "A:1"), ("", "A:1"), ("tok", "")]
)
def test_with_context_rejects(inner: str, path: str) -> None:
    with pytest.raises(ValueError):
        with_context(inner, path)

"""``fusion_mcp.protocol.sketch_refs``: profile refs ``profile:<token>@<x>,<y>`` (c05 1.1, D8)."""

import math

import pytest

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.refs import NAME_PREFIXES, split_context
from fusion_mcp.protocol.sketch_refs import (
    PROFILE_PREFIX,
    format_profile_ref,
    is_profile_ref,
    parse_profile_ref,
)

BASE64_TOKEN = "/v4DAAAA+abc/Zm9v=="


def test_profile_prefix_is_a_name_prefix() -> None:
    assert PROFILE_PREFIX in NAME_PREFIXES


def test_format_four_decimals() -> None:
    assert format_profile_ref("tok", 1.5, -2) == "profile:tok@1.5000,-2.0000"
    assert format_profile_ref("tok", 12.345678, 0.00004) == "profile:tok@12.3457,0.0000"


def test_format_has_no_negative_zero() -> None:
    assert format_profile_ref("tok", -0.0, -0.00001) == "profile:tok@0.0000,0.0000"


@pytest.mark.parametrize(
    ("token", "x", "y"),
    [("tok", 0.0, 0.0), (BASE64_TOKEN, 25.0, -12.5), ("a+b/c=", -1234.5678, 99999.0001)],
)
def test_round_trip(token: str, x: float, y: float) -> None:
    ref = format_profile_ref(token, x, y)
    assert is_profile_ref(ref)
    parsed_token, px, py = parse_profile_ref(ref)
    assert parsed_token == token
    assert px == pytest.approx(x, abs=5e-5)
    assert py == pytest.approx(y, abs=5e-5)
    # Re-formatting the parsed value is stable.
    assert format_profile_ref(parsed_token, px, py) == ref


def test_token_with_base64_symbols() -> None:
    assert parse_profile_ref(f"profile:{BASE64_TOKEN}@1,2") == (BASE64_TOKEN, 1.0, 2.0)


def test_parse_splits_on_last_at() -> None:
    assert parse_profile_ref("profile:a@b@3.5,-4") == ("a@b", 3.5, -4.0)


def test_parse_accepts_plain_numbers() -> None:
    assert parse_profile_ref("profile:t@+1.,.5") == ("t", 1.0, 0.5)
    assert parse_profile_ref("profile:t@1e2,-2E-1") == ("t", 100.0, -0.2)


def test_assembly_context_does_not_split_profile_ref() -> None:
    ref = format_profile_ref(BASE64_TOKEN, 1, 2)
    assert split_context(ref) == (ref, None)


@pytest.mark.parametrize(
    "ref",
    [
        None,
        42,
        "",
        "tok@1,2",
        "Profile:tok@1,2",
        "profile:",
        "profile:tok",
        "profile:@1,2",
        "profile:tok@",
        "profile:tok@1",
        "profile:tok@1,2,3",
        "profile:tok@1,",
        "profile:tok@,2",
        "profile:tok@a,b",
        "profile:tok@1, 2",
        "profile:tok@nan,0",
        "profile:tok@inf,0",
        "profile:tok@1_0,0",
        "profile:tok@1e999,0",
        "profile:tok@1;2",
    ],
)
def test_parse_malformed(ref: object) -> None:
    with pytest.raises(BridgeError) as info:
        parse_profile_ref(ref)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["reason"] == "malformed_profile_ref"
    assert info.value.details["ref"] == ref


@pytest.mark.parametrize(("x", "y"), [(math.nan, 0.0), (0.0, math.inf), (-math.inf, 1.0)])
def test_format_rejects_non_finite(x: float, y: float) -> None:
    with pytest.raises(ValueError):
        format_profile_ref("tok", x, y)


def test_format_rejects_empty_token() -> None:
    with pytest.raises(ValueError):
        format_profile_ref("", 1, 2)


def test_is_profile_ref() -> None:
    assert is_profile_ref("profile:x@1,2")
    assert not is_profile_ref("tok")
    assert not is_profile_ref(None)

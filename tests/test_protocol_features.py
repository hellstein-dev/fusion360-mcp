"""``protocol/features.py``: feature vocabularies and ``check_choice`` (c07 D2, task 1.1)."""

from __future__ import annotations

from typing import Any

import pytest

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import (
    DIRECTIONS,
    EXTENT_TYPES,
    EXTRUDE_MODES,
    HEALTH_STATES,
    OPERATIONS,
    START_TYPES,
    THIN_LOCATIONS,
    check_choice,
)


def test_vocabularies_of_d2() -> None:
    assert OPERATIONS == ("new_body", "join", "cut", "intersect", "new_component")
    assert EXTENT_TYPES == ("distance", "to_entity", "through_all")
    assert EXTRUDE_MODES == ("one_side", "two_sides", "symmetric")
    assert DIRECTIONS == ("positive", "negative")
    assert START_TYPES == ("profile_plane", "offset", "entity")
    assert THIN_LOCATIONS == ("side1", "side2", "center")
    assert HEALTH_STATES == ("ok", "warning", "error", "unknown")


def test_health_states_match_the_c03_mapping() -> None:
    # core/health.py maps healthState onto these words plus "unknown" for anything else.
    from fusion_addin.core import health

    assert set(health._states().values()) | {"unknown"} == set(HEALTH_STATES)


@pytest.mark.parametrize("value", OPERATIONS)
def test_valid_choice_is_returned(value: str) -> None:
    assert check_choice("operation", value, OPERATIONS) == value


@pytest.mark.parametrize(
    ("value", "shown"),
    [("merge", "merge"), ("", ""), (None, None), (3, "3"), (["cut"], "['cut']"), ("CUT", "CUT")],
)
def test_invalid_choice(value: Any, shown: Any) -> None:
    with pytest.raises(BridgeError) as info:
        check_choice("operation", value, OPERATIONS)
    error = info.value
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details == {"param": "operation", "value": shown, "expected": list(OPERATIONS)}
    assert "'operation'" in error.message
    assert "new_body, join, cut, intersect, new_component" in error.message


def test_choices_may_be_any_sequence() -> None:
    assert check_choice("direction", "negative", list(DIRECTIONS)) == "negative"
    with pytest.raises(BridgeError) as info:
        check_choice("mode", "both", EXTRUDE_MODES)
    assert info.value.details["param"] == "mode"
    assert info.value.details["expected"] == ["one_side", "two_sides", "symmetric"]

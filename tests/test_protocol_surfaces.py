"""Surface vocabularies (c11 D2; tasks 1.1, 1.4)."""

from __future__ import annotations

from typing import get_args

import pytest

from fusion_mcp.protocol import surfaces as p
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import check_choice
from fusion_mcp.server.tools import _surface_models as m

VOCABULARIES = {
    "SURFACE_OPERATIONS": (p.SURFACE_OPERATIONS, m.SurfaceOperation),
    "CONTINUITY": (p.CONTINUITY, m.Continuity),
    "EXTEND_TYPES": (p.EXTEND_TYPES, m.ExtendType),
    "EXTEND_ALIGNMENTS": (p.EXTEND_ALIGNMENTS, m.ExtendAlignment),
    "UNTRIM_LOOP_TYPES": (p.UNTRIM_LOOP_TYPES, m.UntrimLoopType),
    "RULED_TYPES": (p.RULED_TYPES, m.RuledType),
    "RULED_CORNERS": (p.RULED_CORNERS, m.RuledCorner),
    "OFFSET_TYPES": (p.OFFSET_TYPES, m.OffsetType),
}


@pytest.mark.parametrize("name", sorted(VOCABULARIES))
def test_values_are_unique_strings(name: str) -> None:
    values, _ = VOCABULARIES[name]
    assert values and len(set(values)) == len(values)
    assert all(isinstance(v, str) and v for v in values)


@pytest.mark.parametrize("name", sorted(VOCABULARIES))
def test_check_choice_accepts_known_and_refuses_unknown(name: str) -> None:
    values, _ = VOCABULARIES[name]
    for value in values:
        assert check_choice("x", value, values) == value
    with pytest.raises(BridgeError) as info:
        check_choice("x", "bogus", values)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details == {"param": "x", "value": "bogus", "expected": list(values)}


def test_expected_values() -> None:
    assert p.SURFACE_OPERATIONS == ("new_body", "new_component")
    assert p.CONTINUITY == ("connected", "tangent", "curvature")
    assert p.EXTEND_TYPES == ("natural", "tangent", "perpendicular")
    assert p.EXTEND_ALIGNMENTS == ("free", "align")
    assert p.UNTRIM_LOOP_TYPES == ("all", "external", "internal")
    assert p.RULED_TYPES == ("normal", "tangent", "direction")
    assert p.RULED_CORNERS == ("rounded", "mitered")
    assert p.OFFSET_TYPES == ("sharp", "rounded")


@pytest.mark.parametrize("name", sorted(VOCABULARIES))
def test_server_literals_match_protocol(name: str) -> None:
    values, literal = VOCABULARIES[name]
    assert get_args(literal) == values

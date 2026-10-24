"""Sheet metal vocabularies and server models (c13 D2, D5; tasks 1.1, 1.2)."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from fusion_mcp.protocol import sheet_metal as p
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import check_choice
from fusion_mcp.server.tools import _sheet_metal_models as m

VOCABULARIES = {
    "RELIEF_SHAPES": (p.RELIEF_SHAPES, m.ReliefShape),
    "FLANGE_ORIENTATIONS": (p.FLANGE_ORIENTATIONS, m.FlangeOrientation),
    "FLANGE_DIRECTIONS": (p.FLANGE_DIRECTIONS, m.FlangeDirection),
    "HEIGHT_DATUMS": (p.HEIGHT_DATUMS, m.HeightDatum),
    "BEND_POSITIONS": (p.BEND_POSITIONS, m.BendPosition),
    "EDGE_WIDTH_TYPES": (p.EDGE_WIDTH_TYPES, m.EdgeWidthType),
    "RULE_SOURCES": (p.RULE_SOURCES, m.RuleSource),
    "FLAT_EXPORT_FORMATS": (p.FLAT_EXPORT_FORMATS, m.FlatExportFormat),
    "FLANGE_OPERATIONS": (p.FLANGE_OPERATIONS, m.FlangeOperation),
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
    assert p.RELIEF_SHAPES == ("straight", "tear", "round")
    assert p.FLANGE_ORIENTATIONS == ("side_one", "side_two", "centered")
    assert p.FLANGE_DIRECTIONS == ("one_side", "symmetric", "two_sides")
    assert p.HEIGHT_DATUMS == ("inner", "outer", "tangent")
    assert p.BEND_POSITIONS == ("start_edge", "inside", "outside", "tangent_to_side")
    assert p.EDGE_WIDTH_TYPES == ("full", "symmetric", "two_sided", "two_offsets")
    assert p.RULE_SOURCES == ("design", "library", "all")
    assert p.FLAT_EXPORT_FORMATS == ("dxf", "step")


@pytest.mark.parametrize("name", sorted(VOCABULARIES))
def test_server_literals_match_protocol(name: str) -> None:
    values, literal = VOCABULARIES[name]
    assert get_args(literal) == values


WIDTH: TypeAdapter[Any] = TypeAdapter(m.EdgeFlangeWidth)


@pytest.mark.parametrize(
    ("data", "cls"),
    [
        ({"type": "full"}, m.FullWidth),
        ({"type": "symmetric", "width": 30}, m.SymmetricWidth),
        ({"type": "two_sided", "width1": 10, "width2": "d1 * 2"}, m.TwoSidedWidth),
        ({"type": "two_offsets", "offset1": 5, "offset2": 5}, m.TwoOffsetsWidth),
    ],
)
def test_width_variants(data: dict[str, Any], cls: type[BaseModel]) -> None:
    width = WIDTH.validate_python(data)
    assert isinstance(width, cls)
    assert width.model_dump() == data


def test_width_type_tags_match_protocol() -> None:
    tags = tuple(
        get_args(c.model_fields["type"].annotation)[0]
        for c in get_args(get_args(m.EdgeFlangeWidth)[0])
    )
    assert tags == p.EDGE_WIDTH_TYPES


@pytest.mark.parametrize(
    "data",
    [
        {"type": "symmetric"},
        {"type": "two_sided", "width1": 10},
        {"type": "two_offsets", "offset1": 1, "offset2": True},
        {"type": "full", "width": 3},
        {"type": "diagonal"},
        {"type": "symmetric", "width": ""},
    ],
)
def test_width_refuses_bad_input(data: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WIDTH.validate_python(data)

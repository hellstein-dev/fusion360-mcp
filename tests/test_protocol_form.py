"""Form vocabularies and server models (c14 D1, D2, D5; tasks 1.1, 1.2)."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from fusion_mcp.protocol import form as p
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import check_choice
from fusion_mcp.server.tools import _form_models as m

VOCABULARIES = {
    "SHAPES": (p.SHAPES, m.Shape),
    "ELEMENT_KINDS": (p.ELEMENT_KINDS, m.ElementKind),
    "INSERTION_MODES": (p.INSERTION_MODES, m.InsertionMode),
    "MOVE_KINDS": (p.MOVE_KINDS, m.MoveKind),
    "INSPECT_PARTS": (p.INSPECT_PARTS, m.InspectPart),
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
    assert p.SHAPES == ("box", "plane", "cylinder", "sphere", "torus", "quadball")
    assert p.ELEMENT_KINDS == ("vertex", "edge", "face")
    assert p.INSERTION_MODES == ("simple", "exact")


def test_every_shape_has_its_own_sizes_and_face_counts() -> None:
    assert set(p.SHAPE_SIZES) == set(p.SHAPES) == set(p.SHAPE_FACES)
    assert p.SHAPE_SIZES["box"] == ("x_size", "y_size", "z_size")
    assert p.SHAPE_SIZES["plane"] == ("x_size", "y_size")
    assert p.SHAPE_SIZES["cylinder"] == ("radius", "height")
    assert p.SHAPE_SIZES["sphere"] == ("radius",)
    assert p.SHAPE_SIZES["torus"] == ("major_radius", "minor_radius")
    assert p.SHAPE_SIZES["quadball"] == ("radius",)
    signatures = {(p.SHAPE_SIZES[s], p.SHAPE_FACES[s]) for s in p.SHAPES}
    assert len(signatures) == len(p.SHAPES)
    for shape in p.SHAPES:
        assert p.SHAPE_SIZES[shape] and p.SHAPE_FACES[shape]
        assert not set(p.SHAPE_SIZES[shape]) & set(p.SHAPE_FACES[shape])
    assert set(p.CIRCULAR_SHAPES) <= set(p.SHAPES)
    assert set(p.ALL_SIZES) == {s for v in p.SHAPE_SIZES.values() for s in v}
    assert set(p.ALL_FACES) == {s for v in p.SHAPE_FACES.values() for s in v}


def test_limits() -> None:
    assert p.TSM_INPUT_MAX_BYTES == 8 * 1024 * 1024
    assert p.TSM_OUTPUT_MAX_BYTES == 1024 * 1024
    assert p.TSM_EXTENSIONS == (".tsm",)
    assert p.DEFAULT_TOLERANCE_MM == 0.5


@pytest.mark.parametrize("name", sorted(VOCABULARIES))
def test_server_literals_match_protocol(name: str) -> None:
    values, literal = VOCABULARIES[name]
    assert get_args(literal) == values


SYMMETRY: TypeAdapter[Any] = TypeAdapter(m.Symmetry)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("circular", "circular"),
        ({"x": True}, {"x": True, "y": False, "z": False}),
        ({}, {"x": False, "y": False, "z": False}),
        ({"x": True, "y": True, "z": True}, {"x": True, "y": True, "z": True}),
    ],
)
def test_symmetry_accepts(data: Any, expected: Any) -> None:
    value = SYMMETRY.validate_python(data)
    dumped = value if isinstance(value, str) else value.model_dump()
    assert dumped == expected


@pytest.mark.parametrize("data", ["radial", {"w": True}, {"x": "maybe"}, 3])
def test_symmetry_refuses(data: Any) -> None:
    with pytest.raises(ValidationError):
        SYMMETRY.validate_python(data)


def test_symmetry_axes_match_protocol() -> None:
    assert tuple(m.AxisSymmetry.model_fields) == p.SYMMETRY_AXES

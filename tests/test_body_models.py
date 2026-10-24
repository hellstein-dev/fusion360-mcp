"""``server/tools/_body_models.py``: pattern/body input models (c08 D14, tasks 1.2-1.3)."""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from fusion_mcp.protocol import features as vocab
from fusion_mcp.server.tools import _body_models as m
from fusion_mcp.server.tools._feature_models import to_params


def parse(tp: Any, value: Any) -> Any:
    return TypeAdapter(tp).validate_python(value)


def rejects(tp: Any, value: Any) -> ValidationError:
    with pytest.raises(ValidationError) as info:
        parse(tp, value)
    return info.value


def dumped(tp: Any, value: Any) -> Any:
    return to_params(x=parse(tp, value))["x"]


# --- 1.2 literals, Point3, ScaleFactors ----------------------------------------------------


@pytest.mark.parametrize(
    ("literal", "values"),
    [
        (m.PatternDistanceType, vocab.PATTERN_DISTANCE_TYPES),
        (m.ComputeOption, vocab.COMPUTE_OPTIONS),
        (m.CombineOperation, vocab.COMBINE_OPERATIONS),
        (m.MoveType, vocab.MOVE_TYPES),
        (m.SplitFaceType, vocab.SPLIT_FACE_TYPES),
        (m.RemoveMode, vocab.REMOVE_MODES),
    ],
)
def test_literals_match_protocol_tuples(literal: Any, values: tuple[str, ...]) -> None:
    assert get_args(literal) == values


def test_point3_takes_three_numbers() -> None:
    assert parse(m.Point3, [1, 2.5, -3]) == [1.0, 2.5, -3.0]
    for bad in ([1, 2], [1, 2, 3, 4], [1, "2", 3], [True, 0, 0], [float("nan"), 0, 0]):
        rejects(m.Point3, bad)


def test_scale_factors() -> None:
    assert dumped(m.ScaleFactors, {"x": 2, "y": 0.5, "z": "k"}) == {"x": 2, "y": 0.5, "z": "k"}
    for bad in (
        {"x": 0, "y": 1, "z": 1},
        {"x": -1, "y": 1, "z": 1},
        {"x": 1, "y": 1},
        {"x": 1, "y": 1, "z": 1, "w": 1},
        {"x": " ", "y": 1, "z": 1},
        {"x": True, "y": 1, "z": 1},
    ):
        rejects(m.ScaleFactors, bad)


# --- 1.3 Transform union -------------------------------------------------------------------


def test_translate_defaults_and_dump() -> None:
    assert dumped(m.Transform, {"type": "translate", "z": 50}) == {
        "type": "translate",
        "x": 0.0,
        "y": 0.0,
        "z": 50,
        "design_space": True,
    }
    assert dumped(m.Transform, {"type": "translate", "x": "d / 2", "design_space": False})["x"] == (
        "d / 2"
    )


def test_translate_along_and_rotate() -> None:
    assert dumped(m.Transform, {"type": "translate_along", "entity": "e1", "distance": 5}) == {
        "type": "translate_along",
        "entity": "e1",
        "distance": 5,
    }
    assert dumped(m.Transform, {"type": "rotate", "axis": "origin:Z", "angle": "30 deg"}) == {
        "type": "rotate",
        "axis": "origin:Z",
        "angle": "30 deg",
    }
    rejects(m.Transform, {"type": "rotate", "axis": "origin:Z"})
    rejects(m.Transform, {"type": "translate_along", "entity": "", "distance": 1})


def test_rotate_about_numbers_only() -> None:
    spec = {"type": "rotate_about", "point": [0, 0, 0], "direction": [0, 0, 1], "angle": 90}
    assert dumped(m.Transform, spec) == {
        "type": "rotate_about",
        "point": [0.0, 0.0, 0.0],
        "direction": [0.0, 0.0, 1.0],
        "angle": 90,
    }
    rejects(m.Transform, {**spec, "angle": "90 deg"})
    rejects(m.Transform, {**spec, "point": [0, 0]})


def test_align_uses_from_alias() -> None:
    spec = {"type": "align", "from": "v1", "to": "origin:O"}
    parsed = parse(m.Transform, spec)
    assert parsed.from_ == "v1"
    assert to_params(transform=parsed)["transform"] == spec
    rejects(m.Transform, {"type": "align", "to": "origin:O"})


def test_matrix_needs_sixteen_numbers() -> None:
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    assert dumped(m.Transform, {"type": "matrix", "matrix": identity})["matrix"] == [
        float(v) for v in identity
    ]
    rejects(m.Transform, {"type": "matrix", "matrix": identity[:15]})
    rejects(m.Transform, {"type": "matrix", "matrix": [*identity, 0]})
    rejects(m.Transform, {"type": "matrix", "matrix": ["1", *identity[1:]]})


def test_unknown_type_and_extra_fields_are_refused() -> None:
    rejects(m.Transform, {"type": "scale", "factor": 2})
    rejects(m.Transform, {"type": "translate", "x": 1, "w": 2})
    rejects(m.Transform, {"x": 1})

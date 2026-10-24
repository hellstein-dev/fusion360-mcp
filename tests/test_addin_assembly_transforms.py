"""Occurrence placements (c10 task 3.1): ``point_to_internal`` and ``core/transforms.py``
``placement_matrix`` / ``relative_matrix`` on the stub ``Matrix3D``."""

from __future__ import annotations

import math

import pytest

import fusion_addin
from fusion_addin.core import transforms
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.units import point_to_internal

adsk = fusion_addin.adsk


def cells(matrix: object) -> list[float]:
    return [round(v, 9) + 0.0 for v in matrix.asArray()]  # type: ignore[attr-defined]


def test_point_to_internal() -> None:
    assert point_to_internal([10, -20, 5]) == pytest.approx([1.0, -2.0, 0.5])
    assert point_to_internal([1, 1, 1], "in") == pytest.approx([2.54, 2.54, 2.54])
    with pytest.raises(ValueError):
        point_to_internal([1, 2])
    with pytest.raises(TypeError):
        point_to_internal([1, True, 2])
    with pytest.raises(ValueError):
        point_to_internal([1, math.inf, 2])


def test_placement_matrix_normalises_axes() -> None:
    m = transforms.placement_matrix([10, 20, 30], [0, 3, 0], [-2, 0, 0])
    assert cells(m) == [0, -1, 0, 1, 1, 0, 0, 2, 0, 0, 1, 3, 0, 0, 0, 1]


def test_placement_matrix_rejects_bad_axes() -> None:
    with pytest.raises(BridgeError) as exc:
        transforms.placement_matrix([0, 0, 0], [1, 0, 0], [1, 1e-3, 0])
    assert exc.value.code is ErrorCode.INVALID_PARAMS
    assert exc.value.details["reason"] == "not_orthogonal"
    with pytest.raises(BridgeError):
        transforms.placement_matrix([0, 0, 0], [0, 0, 0], [0, 1, 0])
    with pytest.raises(BridgeError):
        transforms.placement_matrix([0, 0], [1, 0, 0], [0, 1, 0])


def test_relative_matrix_rotates_then_translates_in_assembly_space() -> None:
    current = adsk.core.Matrix3D.create()
    current.translation = adsk.core.Vector3D.create(1, 0, 0)  # 10 mm along X
    m = transforms.relative_matrix(
        current, [0, 0, 50], {"axis": [0, 0, 1], "angle_deg": 90, "origin_mm": [0, 0, 0]}
    )
    assert cells(m) == [0, -1, 0, 0, 1, 0, 0, 1, 0, 0, 1, 5, 0, 0, 0, 1]
    assert cells(current)[3] == 1.0  # the input is not modified


def test_relative_matrix_rotation_about_offset_origin() -> None:
    m = transforms.relative_matrix(
        adsk.core.Matrix3D.create(),
        None,
        {"axis": [0, 0, 1], "angle_deg": 180, "origin_mm": [10, 0, 0]},
    )
    assert cells(m)[3] == pytest.approx(2.0)  # the origin maps to x = 20 mm


def test_rotation_validation() -> None:
    identity = adsk.core.Matrix3D.create()
    for rotate in (
        "z",
        {"axis": [0, 0, 1]},
        {"axis": [0, 0, 1], "angle_deg": "90"},
        {"axis": [0, 0, 1], "angle_deg": 90, "center": [0, 0, 0]},
        {"axis": [0, 0], "angle_deg": 90},
    ):
        with pytest.raises(BridgeError) as exc:
            transforms.relative_matrix(identity, None, rotate)
        assert exc.value.code is ErrorCode.INVALID_PARAMS, rotate

"""Inspection helpers of ``fusion_mcp.protocol.units`` (c03 1.1, D2)."""

import pytest

from fusion_mcp.protocol import units


def test_vec3_length_to_user_converts_cm_to_mm() -> None:
    assert units.vec3_length_to_user([1.0, -2.5, 0.0]) == [10.0, -25.0, 0.0]
    assert units.vec3_length_to_user((0.1, 0.2, 0.3)) == [1.0, 2.0, 3.0]


def test_vec3_length_to_user_rounds_to_micrometre_fraction() -> None:
    assert units.vec3_length_to_user([0.12345678912, 0.0, 1e-9]) == [1.234568, 0.0, 0.0]


def test_vec3_length_to_user_no_negative_zero() -> None:
    out = units.vec3_length_to_user([-1e-12, 0.0, 0.0])
    assert str(out[0]) == "0.0"


def test_vec3_length_to_user_rejects_wrong_size_and_non_numbers() -> None:
    with pytest.raises(ValueError):
        units.vec3_length_to_user([1.0, 2.0])
    with pytest.raises(TypeError):
        units.vec3_length_to_user(["1", 2.0, 3.0])  # type: ignore[list-item]


def test_inertia_to_user_kg_cm2_to_kg_mm2() -> None:
    assert units.inertia_to_user(1.0) == pytest.approx(100.0)
    assert units.inertia_to_user(0.025) == pytest.approx(2.5)


def test_density_to_user_kg_cm3_to_kg_m3() -> None:
    assert units.density_to_user(0.00785) == pytest.approx(7850.0)
    assert units.density_to_user(0.0027) == pytest.approx(2700.0)

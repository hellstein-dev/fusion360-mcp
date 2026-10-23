"""``fusion_mcp.protocol.units``: the only place that converts to/from Fusion's cm and radians."""

import math

import pytest

from fusion_mcp.protocol import units as u

LENGTH_CASES = [
    ("mm", 25, 2.5),
    ("cm", 3, 3.0),
    ("m", 1.5, 150.0),
    ("in", 1, 2.54),
    ("ft", 2, 60.96),
]


@pytest.mark.parametrize(("unit", "value", "cm"), LENGTH_CASES)
def test_length_round_trip(unit: str, value: float, cm: float) -> None:
    assert u.length_to_internal(value, unit) == pytest.approx(cm)
    assert u.length_from_internal(cm, unit) == pytest.approx(value)


def test_length_defaults_to_mm() -> None:
    assert u.length_to_internal(25) == pytest.approx(2.5)
    assert u.length_from_internal(2.5) == pytest.approx(25)


@pytest.mark.parametrize(("unit", "value", "cm"), LENGTH_CASES)
def test_area_and_volume(unit: str, value: float, cm: float) -> None:
    factor = cm / value
    assert u.area_to_internal(value, unit) == pytest.approx(value * factor**2)
    assert u.volume_to_internal(value, unit) == pytest.approx(value * factor**3)
    assert u.area_from_internal(value * factor**2, unit) == pytest.approx(value)
    assert u.volume_from_internal(value * factor**3, unit) == pytest.approx(value)


def test_area_volume_mm_outputs() -> None:
    assert u.area_from_internal(1.0) == pytest.approx(100.0)
    assert u.volume_from_internal(1.0) == pytest.approx(1000.0)


def test_angles() -> None:
    assert u.angle_to_internal(180) == pytest.approx(math.pi)
    assert u.angle_from_internal(math.pi / 2) == pytest.approx(90.0)


def test_density_and_mass() -> None:
    assert u.density_from_internal(0.00785) == pytest.approx(7850.0)
    assert u.mass_from_internal(1.25) == 1.25


@pytest.mark.parametrize("expr", ["width / 2", "10 mm", "45 deg", ""])
def test_expressions_pass_through_unchanged(expr: str) -> None:
    assert u.is_expression(expr)
    assert u.length_to_internal(expr) is expr
    assert u.length_to_internal(expr, "in") is expr
    assert u.angle_to_internal(expr) is expr


def test_numbers_are_not_expressions() -> None:
    assert not u.is_expression(25)


def test_unknown_unit_rejected() -> None:
    with pytest.raises(ValueError, match="unknown length unit"):
        u.length_to_internal(1, "yd")
    with pytest.raises(ValueError):
        u.Units("parsec")


@pytest.mark.parametrize("bad", [True, None, math.nan, math.inf])
def test_bad_numbers_rejected(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        u.length_to_internal(bad)  # type: ignore[arg-type]


def test_units_converter() -> None:
    conv = u.Units("in")
    assert conv.length(1) == pytest.approx(2.54)
    assert conv.length("a + b") == "a + b"
    assert conv.angle(90) == pytest.approx(math.pi / 2)
    assert conv.area(1) == pytest.approx(2.54**2)
    assert conv.volume(1) == pytest.approx(2.54**3)
    # Output is always agent-facing mm / degrees, independent of the input unit.
    assert conv.length_out(2.54) == pytest.approx(25.4)
    assert conv.area_out(1) == pytest.approx(100)
    assert conv.volume_out(1) == pytest.approx(1000)
    assert conv.angle_out(math.pi) == pytest.approx(180)

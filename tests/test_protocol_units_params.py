"""Parameter unit helpers in ``fusion_mcp.protocol.units`` (c04 D5, task 1.1)."""

import math

import pytest

from fusion_mcp.protocol import units as u


@pytest.mark.parametrize("unit", ["mm", "cm", "m", "in", "ft", "um", "yd", "mil"])
def test_classify_length(unit: str) -> None:
    assert u.classify_unit(unit) == "length"
    assert u.classify_unit(f"  {unit.upper()} ") == "length"


@pytest.mark.parametrize("unit", ["deg", "rad", "DEG", " Rad "])
def test_classify_angle(unit: str) -> None:
    assert u.classify_unit(unit) == "angle"


@pytest.mark.parametrize("unit", ["", "   "])
def test_classify_unitless(unit: str) -> None:
    assert u.classify_unit(unit) == "unitless"


@pytest.mark.parametrize("unit", ["inch", '"', "kg", "mm^2", "N", "deg/s"])
def test_classify_other(unit: str) -> None:
    assert u.classify_unit(unit) == "other"


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (12.5, "mm", "12.5 mm"),
        (40, "mm", "40 mm"),
        (40.0, "in", "40 in"),
        (-3.25, "deg", "-3.25 deg"),
        (7, "", "7"),
        (0.1, " cm ", "0.1 cm"),
        (123456789012345.0, "mm", "123456789012345 mm"),
        (1e20, "mm", "1e+20 mm"),
        (0.1 + 0.2, "mm", "0.30000000000000004 mm"),
    ],
)
def test_number_expression(value: float, unit: str, expected: str) -> None:
    assert u.number_expression(value, unit) == expected


def test_number_expression_keeps_precision() -> None:
    value = 1234567.8912345678
    number, unit = u.number_expression(value, "mm").split()
    assert float(number) == value
    assert unit == "mm"


@pytest.mark.parametrize("bad", [math.nan, math.inf, True, "5"])
def test_number_expression_rejects(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        u.number_expression(bad, "mm")  # type: ignore[arg-type]


def test_value_to_user_length_in_mm() -> None:
    assert u.parameter_value_to_user(4.0, "mm") == {"value": 40.0, "value_unit": "mm"}
    # Fusion keeps the value in cm whatever the parameter's own unit is.
    assert u.parameter_value_to_user(2.54, "in") == {
        "value": pytest.approx(25.4),
        "value_unit": "mm",
    }


def test_value_to_user_angle_in_degrees() -> None:
    out = u.parameter_value_to_user(math.pi / 2, "deg")
    assert out["value"] == pytest.approx(90.0)
    assert out["value_unit"] == "deg"


def test_value_to_user_unitless() -> None:
    assert u.parameter_value_to_user(3.0, "") == {"value": 3.0, "value_unit": ""}


def test_value_to_user_other_is_marked() -> None:
    assert u.parameter_value_to_user(2.5, "kg") == {"value": 2.5, "value_unit": "fusion_internal"}

"""The single unit-conversion module (INV-6, design D7).

Fusion works in centimetres and radians internally. Tool inputs are in millimetres and
degrees by default (or an explicit ``units`` of mm/cm/m/in/ft); tool outputs are in mm,
mm², mm³, degrees, kg and kg/m³. A string value is a Fusion expression ("10 mm",
"width / 2") and is passed to ``ValueInput.createByString`` unchanged.
"""

from __future__ import annotations

import math
from typing import Literal, TypeAlias

Length: TypeAlias = float | str
Angle: TypeAlias = float | str
LengthUnit = Literal["mm", "cm", "m", "in", "ft"]

DEFAULT_LENGTH_UNIT: LengthUnit = "mm"

# Centimetres per unit: Fusion's internal length unit is the centimetre.
CM_PER_UNIT: dict[str, float] = {
    "mm": 0.1,
    "cm": 1.0,
    "m": 100.0,
    "in": 2.54,
    "ft": 30.48,
}

MM_PER_CM = 10.0
# Fusion's internal density unit is kg/cm³.
KG_PER_M3_PER_KG_PER_CM3 = 1_000_000.0


def _cm_per(unit: str) -> float:
    try:
        return CM_PER_UNIT[unit]
    except KeyError:
        raise ValueError(
            f"unknown length unit {unit!r}; expected one of {', '.join(CM_PER_UNIT)}"
        ) from None


def _number(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"expected a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"expected a finite number, got {value!r}")
    return float(value)


def is_expression(value: object) -> bool:
    """True for a string value: a Fusion expression passed through unchanged."""
    return isinstance(value, str)


# --- input: user units -> Fusion internal units ------------------------------------------


def length_to_internal(value: Length, units: str = DEFAULT_LENGTH_UNIT) -> float | str:
    """Number in ``units`` -> cm; expression string -> unchanged."""
    if isinstance(value, str):
        return value
    return _number(value) * _cm_per(units)


def area_to_internal(value: float, units: str = DEFAULT_LENGTH_UNIT) -> float:
    return _number(value) * _cm_per(units) ** 2


def volume_to_internal(value: float, units: str = DEFAULT_LENGTH_UNIT) -> float:
    return _number(value) * _cm_per(units) ** 3


def angle_to_internal(value: Angle) -> float | str:
    """Number in degrees -> radians; expression string -> unchanged."""
    if isinstance(value, str):
        return value
    return math.radians(_number(value))


# --- output: Fusion internal units -> agent-facing units ---------------------------------


def length_from_internal(cm: float, units: str = DEFAULT_LENGTH_UNIT) -> float:
    return _number(cm) / _cm_per(units)


def area_from_internal(cm2: float, units: str = DEFAULT_LENGTH_UNIT) -> float:
    return _number(cm2) / _cm_per(units) ** 2


def volume_from_internal(cm3: float, units: str = DEFAULT_LENGTH_UNIT) -> float:
    return _number(cm3) / _cm_per(units) ** 3


def angle_from_internal(rad: float) -> float:
    return math.degrees(_number(rad))


def density_from_internal(kg_per_cm3: float) -> float:
    """kg/cm³ (Fusion) -> kg/m³."""
    return _number(kg_per_cm3) * KG_PER_M3_PER_KG_PER_CM3


def mass_from_internal(kg: float) -> float:
    """Fusion reports mass in kg already; kept here so every conversion has one home."""
    return _number(kg)


class Units:
    """Converter bound to one input length unit; handed to add-in handlers as ``ctx.units``."""

    def __init__(self, units: str = DEFAULT_LENGTH_UNIT) -> None:
        _cm_per(units)
        self.units = units

    def length(self, value: Length) -> float | str:
        return length_to_internal(value, self.units)

    def angle(self, value: Angle) -> float | str:
        return angle_to_internal(value)

    def area(self, value: float) -> float:
        return area_to_internal(value, self.units)

    def volume(self, value: float) -> float:
        return volume_to_internal(value, self.units)

    def length_out(self, cm: float) -> float:
        return length_from_internal(cm)

    def area_out(self, cm2: float) -> float:
        return area_from_internal(cm2)

    def volume_out(self, cm3: float) -> float:
        return volume_from_internal(cm3)

    def angle_out(self, rad: float) -> float:
        return angle_from_internal(rad)

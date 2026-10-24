"""The single unit-conversion module (INV-6, design D7).

Fusion works in centimetres and radians internally. Tool inputs are in millimetres and
degrees by default (or an explicit ``units`` of mm/cm/m/in/ft); tool outputs are in mm,
mm², mm³, degrees, kg and kg/m³. A string value is a Fusion expression ("10 mm",
"width / 2") and is passed to ``ValueInput.createByString`` unchanged.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
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


def point_to_internal(xyz: Sequence[float], units: str = DEFAULT_LENGTH_UNIT) -> list[float]:
    """Point ``[x, y, z]`` in ``units`` (default mm) -> cm. Directions are unitless and are
    never converted (c10 D3)."""
    if isinstance(xyz, str) or len(xyz) != 3:
        raise ValueError(f"expected [x, y, z], got {xyz!r}")
    return [_number(c) * _cm_per(units) for c in xyz]


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


# --- agent-facing helpers for inspection (c03 D2) ----------------------------------------

USER_ROUND_DIGITS = 6


def vec3_length_to_user(xyz_cm: Sequence[float]) -> list[float]:
    """Point ``[x, y, z]`` in cm -> mm, rounded to 1e-6 mm."""
    if len(xyz_cm) != 3:
        raise ValueError(f"expected 3 coordinates, got {len(xyz_cm)}")
    return [round(length_from_internal(c), USER_ROUND_DIGITS) + 0.0 for c in xyz_cm]


def inertia_to_user(kg_cm2: float) -> float:
    """Moment of inertia kg·cm² (Fusion) -> kg·mm²: scales like an area."""
    return area_from_internal(kg_cm2)


def density_to_user(kg_cm3: float) -> float:
    """Density kg/cm³ (Fusion) -> kg/m³."""
    return density_from_internal(kg_cm3)


# --- parameters (c04 D5) -----------------------------------------------------------------

UnitClass = Literal["length", "angle", "unitless", "other"]

# Canonical Fusion length unit strings; synonyms ("inch", '"') are not accepted.
PARAMETER_LENGTH_UNITS = frozenset({"mm", "cm", "m", "in", "ft", "um", "yd", "mil"})
PARAMETER_ANGLE_UNITS = frozenset({"deg", "rad"})
FUSION_INTERNAL = "fusion_internal"


def classify_unit(unit: str) -> UnitClass:
    """Kind of a Fusion parameter unit: ``length``/``angle``/``unitless``/``other``.

    Case and surrounding whitespace do not matter.
    """
    key = unit.strip().lower()
    if key in PARAMETER_LENGTH_UNITS:
        return "length"
    if key in PARAMETER_ANGLE_UNITS:
        return "angle"
    if key == "":
        return "unitless"
    return "other"


def number_expression(value: float, unit: str) -> str:
    """Number in ``unit`` -> Fusion expression with the unit explicit: ``"12.5 mm"``.

    The number is written ``repr``-exact (no precision lost); a unitless value has no
    unit suffix. No conversion: Fusion interprets the unit.
    """
    number = _number(value)
    text = repr(int(number)) if number.is_integer() and abs(number) < 1e16 else repr(number)
    unit = unit.strip()
    return f"{text} {unit}" if unit else text


def parameter_value_to_user(value_internal: float, unit: str) -> dict[str, float | str]:
    """``Parameter.value`` (cm/radians) -> ``{"value", "value_unit"}`` for the agent.

    Length -> mm, angle -> degrees, unitless -> as is; any other unit is returned as is
    and marked ``value_unit = "fusion_internal"`` (never unmarked raw cm, INV-6).
    """
    kind = classify_unit(unit)
    if kind == "length":
        return {"value": length_from_internal(value_internal), "value_unit": "mm"}
    if kind == "angle":
        return {"value": angle_from_internal(value_internal), "value_unit": "deg"}
    if kind == "unitless":
        return {"value": _number(value_internal), "value_unit": ""}
    return {"value": _number(value_internal), "value_unit": FUSION_INTERNAL}


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

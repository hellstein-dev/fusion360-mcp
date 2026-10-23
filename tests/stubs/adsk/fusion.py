"""Stub of ``adsk.fusion``: ``Design.cast()``, ``DesignTypes`` and the enums the add-in reads."""

from . import Unavailable


class DesignTypes:
    # Values match Fusion's enum.
    DirectDesignType = 0
    ParametricDesignType = 1


class Design:
    """Fakes of a design subclass this, so ``cast`` recognises them like Fusion would."""

    @staticmethod
    def cast(obj: object) -> object:
        if isinstance(obj, Design):
            return obj
        if obj is None:
            return Unavailable("adsk.fusion.Design")
        return None


# Enum values match Fusion's adsk.fusion.
class DistanceUnits:
    MillimeterDistanceUnits = 0
    CentimeterDistanceUnits = 1
    MeterDistanceUnits = 2
    InchDistanceUnits = 3
    FootDistanceUnits = 4


class CalculationAccuracy:
    LowCalculationAccuracy = 0
    MediumCalculationAccuracy = 1
    HighCalculationAccuracy = 2
    VeryHighCalculationAccuracy = 3


class FeatureHealthStates:
    HealthyFeatureHealthState = 0
    WarningFeatureHealthState = 1
    ErrorFeatureHealthState = 2
    SuppressedFeatureHealthState = 3
    RolledBackFeatureHealthState = 4
    UnknownFeatureHealthState = 5

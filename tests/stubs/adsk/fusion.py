"""Stub of ``adsk.fusion``: ``Design.cast()``, ``DesignTypes``, the enums the add-in reads and
the feature extent/start definitions (c07 D21), which record their ``create`` arguments."""

from typing import Any

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


class DimensionOrientations:
    AlignedDimensionOrientation = 0
    HorizontalDimensionOrientation = 1
    VerticalDimensionOrientation = 2


class PatternDistanceType:
    ExtentPatternDistanceType = 0
    SpacingPatternDistanceType = 1


class PatternComputeOptions:
    OptimizedPatternCompute = 0
    IdenticalPatternCompute = 1
    AdjustPatternCompute = 2


class PointContainment:
    PointInsidePointContainment = 0
    PointOnPointContainment = 1
    PointOutsidePointContainment = 2
    UnknownPointContainment = 3


class FeatureHealthStates:
    HealthyFeatureHealthState = 0
    WarningFeatureHealthState = 1
    ErrorFeatureHealthState = 2
    SuppressedFeatureHealthState = 3
    RolledBackFeatureHealthState = 4
    UnknownFeatureHealthState = 5


class FeatureOperations:
    JoinFeatureOperation = 0
    CutFeatureOperation = 1
    IntersectFeatureOperation = 2
    NewBodyFeatureOperation = 3
    NewComponentFeatureOperation = 4


class ExtentDirections:
    PositiveExtentDirection = 0
    NegativeExtentDirection = 1
    SymmetricExtentDirection = 2


class ThinExtrudeWallLocation:
    Side1 = 0
    Side2 = 1
    Center = 2


class SweepOrientationTypes:
    ParallelOrientationType = 0
    PerpendicularOrientationType = 1


class SweepProfileScalingOptions:
    SweepProfileScaleOption = 0
    SweepProfileStretchOption = 1
    SweepProfileNoScalingOption = 2


class ThreadLocations:
    HighEndThreadLocation = 0
    LowEndThreadLocation = 1


class RuleFilletTopologyTypes:
    AllEdgesRuleFilletTopologyType = 0
    BetweenFacesRuleFilletTopologyType = 1


# --- extent and start definitions -------------------------------------------------------------


class _Definition:
    """Base of the ``*ExtentDefinition`` / ``*StartDefinition`` stubs: ``create`` keeps its
    positional arguments in ``args`` and sets the properties Fusion exposes for them."""

    objectType = ""

    def __init__(self, *args: Any) -> None:
        self.args = args


class DistanceExtentDefinition(_Definition):
    objectType = "adsk::fusion::DistanceExtentDefinition"
    distance: Any

    @staticmethod
    def create(distance: Any) -> "DistanceExtentDefinition":
        definition = DistanceExtentDefinition(distance)
        definition.distance = distance
        return definition


class ToEntityExtentDefinition(_Definition):
    objectType = "adsk::fusion::ToEntityExtentDefinition"
    entity: Any
    isChained: bool
    offset: Any

    @staticmethod
    def create(entity: Any, isChained: bool, offset: Any = None) -> "ToEntityExtentDefinition":
        definition = ToEntityExtentDefinition(entity, isChained, offset)
        definition.entity = entity
        definition.isChained = isChained
        definition.offset = offset
        return definition


class ThroughAllExtentDefinition(_Definition):
    objectType = "adsk::fusion::ThroughAllExtentDefinition"
    isFlipped = False

    @staticmethod
    def create() -> "ThroughAllExtentDefinition":
        return ThroughAllExtentDefinition()


class ProfilePlaneStartDefinition(_Definition):
    objectType = "adsk::fusion::ProfilePlaneStartDefinition"

    @staticmethod
    def create() -> "ProfilePlaneStartDefinition":
        return ProfilePlaneStartDefinition()


class OffsetStartDefinition(_Definition):
    objectType = "adsk::fusion::OffsetStartDefinition"
    offset: Any

    @staticmethod
    def create(offset: Any) -> "OffsetStartDefinition":
        definition = OffsetStartDefinition(offset)
        definition.offset = offset
        return definition


class EntityStartDefinition(_Definition):
    objectType = "adsk::fusion::EntityStartDefinition"
    entity: Any
    offset: Any

    @staticmethod
    def create(entity: Any, offset: Any = None) -> "EntityStartDefinition":
        definition = EntityStartDefinition(entity, offset)
        definition.entity = entity
        definition.offset = offset
        return definition

"""Shared input models of the ``feature_*`` tools (c07 D20).

Lengths are a number in the tool's ``units`` (default mm) or a Fusion expression string,
angles a number in degrees or an expression; counts, shares of a path and weights are plain
numbers. Entity inputs are ``ref`` strings from earlier results: faces/edges from c03
(``inspect_*``), profiles from c05 (``sketch_profiles``), planes/axes/points from c06
(``construction_*``) and ``origin:XY|XZ|YZ|X|Y|Z|O``.

The string vocabularies are the tuples of ``fusion_mcp.protocol.features`` written out as
``Literal`` (a test keeps them in sync). ``to_params`` turns tool arguments into bridge params.
"""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from fusion_mcp.protocol.features import DEFAULT_THREAD_TYPE as PROTOCOL_DEFAULT_THREAD_TYPE
from fusion_mcp.protocol.units import LengthUnit
from fusion_mcp.server.tools.sketch_schema import XY

Operation = Literal["new_body", "join", "cut", "intersect", "new_component"]
ExtentType = Literal["distance", "to_entity", "through_all"]
ExtrudeMode = Literal["one_side", "two_sides", "symmetric"]
Direction = Literal["positive", "negative"]
StartType = Literal["profile_plane", "offset", "entity"]
ThinLocation = Literal["side1", "side2", "center"]
HealthState = Literal["ok", "warning", "error", "unknown"]
Units = LengthUnit

LoftEnd = Literal["free", "tangent", "smooth", "direction", "point_sharp", "point_tangent"]
ThreadLocation = Literal["high", "low"]
SweepOrientation = Literal["perpendicular", "parallel"]
RailScaling = Literal["scale", "stretch", "none"]
RibThicknessLocation = Literal["symmetric", "side1", "side2"]
HoleKind = Literal["simple", "counterbore", "countersink"]

DEFAULT_THREAD_TYPE = PROTOCOL_DEFAULT_THREAD_TYPE
DEFAULT_TIP_ANGLE = 118.0
DEFAULT_CS_ANGLE = 90.0


def _value(value: Any) -> Any:
    """A finite number or a non-blank expression string; booleans are refused."""
    if isinstance(value, bool):
        raise ValueError("expected a number or a Fusion expression string, not a boolean")
    if isinstance(value, int | float):
        if not math.isfinite(value):
            raise ValueError("the number must be finite")
        return value
    if isinstance(value, str):
        if not value.strip():
            raise ValueError("the expression must not be empty")
        return value
    raise ValueError("expected a number or a Fusion expression string")


def _number(value: Any) -> Any:
    """A finite number; booleans and strings are refused."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("expected a number")
    if not math.isfinite(value):
        raise ValueError("the number must be finite")
    return value


def _zero(value: Any) -> bool:
    """A plain number equal to 0 (an expression string never counts as zero)."""
    return isinstance(value, int | float) and not isinstance(value, bool) and value == 0


Ref = Annotated[
    str,
    Field(
        min_length=1,
        description="Entity ref from an earlier result (c03 faces/edges, c05 sketch entities, "
        "c06 construction geometry) or origin:XY|XZ|YZ|X|Y|Z|O",
    ),
]
Length = Annotated[
    float | str,
    BeforeValidator(_value),
    Field(description="Number = `units` (default mm), string = Fusion expression ('wall / 2')"),
]
Angle = Annotated[
    float | str,
    BeforeValidator(_value),
    Field(description="Number = degrees, string = Fusion expression ('5 deg', 'draft')"),
]
Number = Annotated[float, BeforeValidator(_number)]
XYZ = Annotated[
    list[Number],
    Field(min_length=3, max_length=3, description="[x, y, z] model space, in `units`"),
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- profiles (c05 D8) -----------------------------------------------------------------------


class ProfileAtPoint(_Model):
    """The profile of ``sketch`` that contains ``point``."""

    sketch: Ref
    point: Annotated[XY, Field(description="[x, y] sketch space, in `units`")]


class ProfileByCurves(_Model):
    """The profile whose outer loop is exactly ``curves``."""

    sketch: Ref
    curves: Annotated[list[Ref], Field(min_length=1)]


class AllProfiles(_Model):
    """Every profile of ``sketch``."""

    sketch: Ref
    all: Literal[True]


ProfileSpec = Annotated[
    Ref | ProfileAtPoint | ProfileByCurves | AllProfiles,
    Field(
        description="Profile ref 'profile:<sketch>@x,y' (sketch_profiles), "
        '{"sketch", "point": [x, y]}, {"sketch", "curves": [refs]}, {"sketch", "all": true}; '
        "where the tool says so also a planar face ref or a sketch text ref"
    ),
]


# --- extents (D6) ----------------------------------------------------------------------------


class DistanceExtent(_Model):
    type: Literal["distance"]
    distance: Annotated[Length, Field(description="Negative = the other way")]


class ToEntityExtent(_Model):
    type: Literal["to_entity"]
    entity: Annotated[Ref, Field(description="Face, body, construction plane or vertex to stop at")]
    offset: Length = 0.0
    chained: Annotated[bool, Field(description="Extend through tangent-chained faces")] = True


class ThroughAllExtent(_Model):
    type: Literal["through_all"]


Extent = Annotated[DistanceExtent | ToEntityExtent | ThroughAllExtent, Field(discriminator="type")]


class ProfilePlaneStart(_Model):
    type: Literal["profile_plane"]


class OffsetStart(_Model):
    type: Literal["offset"]
    offset: Annotated[Length, Field(description="Offset of the start from the profile plane")]


class EntityStart(_Model):
    type: Literal["entity"]
    entity: Annotated[Ref, Field(description="Planar face or construction plane to start from")]
    offset: Length = 0.0


StartExtent = Annotated[ProfilePlaneStart | OffsetStart | EntityStart, Field(discriminator="type")]


class ThinOptions(_Model):
    """Thin extrude: a wall of ``thickness`` along the profile curves."""

    thickness: Length
    location: Annotated[ThinLocation, Field(description="Side of the curves the wall grows")] = (
        "side1"
    )
    thickness2: Annotated[
        Length | None, Field(description="Second wall thickness (two-sided walls)")
    ] = None


# --- revolve (D11) ---------------------------------------------------------------------------


class RevolveAngle(_Model):
    type: Literal["angle"]
    angle: Angle = 360.0
    symmetric: Annotated[bool, Field(description="Split the angle to both sides")] = False


class RevolveTwoAngles(_Model):
    type: Literal["two_angles"]
    angle1: Angle
    angle2: Angle


class RevolveFull(_Model):
    type: Literal["full"]


class RevolveToEntity(_Model):
    type: Literal["to_entity"]
    entity: Annotated[Ref, Field(description="Face, plane or body to revolve up to")]
    direction: Annotated[
        Direction, Field(description="Side of the sketch plane normal to revolve towards")
    ] = "positive"


RevolveExtent = Annotated[
    RevolveAngle | RevolveTwoAngles | RevolveFull | RevolveToEntity, Field(discriminator="type")
]


# --- loft (D13) ------------------------------------------------------------------------------

_WEIGHTED_ENDS = ("tangent", "smooth", "direction", "point_tangent")


class LoftSection(_Model):
    """One loft section; points (sketch point, construction point, vertex) only first/last."""

    entity: ProfileSpec
    end_condition: LoftEnd = "free"
    angle: Annotated[Angle, Field(description="end_condition='direction' only")] = 0.0
    weight: Annotated[
        float,
        BeforeValidator(_number),
        Field(gt=0, description="Tangency weight: tangent, smooth, direction, point_tangent"),
    ] = 1.0

    @model_validator(mode="after")
    def _condition_fields(self) -> LoftSection:
        if self.angle != 0.0 and self.end_condition != "direction":
            raise ValueError("`angle` applies only to end_condition='direction'")
        if self.weight != 1.0 and self.end_condition not in _WEIGHTED_ENDS:
            raise ValueError(
                f"`weight` applies only to end_condition in {', '.join(_WEIGHTED_ENDS)}"
            )
        return self


# --- rib / web (D14) -------------------------------------------------------------------------


class RibToNext(_Model):
    type: Literal["to_next"]


class RibDistance(_Model):
    type: Literal["distance"]
    distance: Annotated[Length, Field(description="Depth of the rib/web from the sketch curves")]


RibDepth = Annotated[RibToNext | RibDistance, Field(discriminator="type")]


# --- hole (D15) ------------------------------------------------------------------------------


class HoleAtPoint(_Model):
    type: Literal["point"]
    face: Annotated[Ref, Field(description="Planar face the hole starts on")]
    point: XYZ


class HoleAtSketchPoints(_Model):
    type: Literal["sketch_points"]
    points: Annotated[list[Ref], Field(min_length=1, description="Sketch point refs")]


class HoleByFaceOffsets(_Model):
    type: Literal["face_offsets"]
    face: Annotated[Ref, Field(description="Planar face the hole starts on")]
    point: XYZ
    edge1: Ref
    offset1: Length
    edge2: Ref
    offset2: Length


class HoleAtCenter(_Model):
    type: Literal["center"]
    face: Annotated[Ref, Field(description="Planar face the hole starts on")]
    edge: Annotated[Ref, Field(description="Circular edge whose center is the hole axis")]


HolePlacement = Annotated[
    HoleAtPoint | HoleAtSketchPoints | HoleByFaceOffsets | HoleAtCenter,
    Field(discriminator="type"),
]


class HoleDistance(_Model):
    type: Literal["distance"]
    depth: Length


class HoleThroughAll(_Model):
    type: Literal["through_all"]


class HoleToEntity(_Model):
    type: Literal["to_entity"]
    entity: Annotated[Ref, Field(description="Face or body the hole stops at")]


HoleExtent = Annotated[HoleDistance | HoleThroughAll | HoleToEntity, Field(discriminator="type")]


# --- thread (D16) ----------------------------------------------------------------------------


class ThreadSpec(_Model):
    """Thread on cylindrical faces; omitted designation/class are recommended by Fusion from
    the cylinder diameter (see feature_thread_options)."""

    thread_type: Annotated[str, Field(min_length=1)] = DEFAULT_THREAD_TYPE
    designation: Annotated[str | None, Field(min_length=1, description="e.g. 'M6x1'")] = None
    thread_class: Annotated[str | None, Field(min_length=1, description="e.g. '6H'")] = None
    modeled: Annotated[bool, Field(description="Real geometry instead of cosmetic")] = False
    full_length: bool = True
    length: Annotated[
        Length | None, Field(description="Thread length; needs full_length=false")
    ] = None
    offset: Annotated[Length, Field(description="Distance of the thread from its end")] = 0.0
    location: Annotated[ThreadLocation, Field(description="End the thread starts from")] = "high"

    @model_validator(mode="after")
    def _length(self) -> ThreadSpec:
        if self.full_length and self.length is not None:
            raise ValueError("`length` needs full_length=false")
        if not self.full_length and self.length is None:
            raise ValueError("full_length=false needs `length`")
        if self.full_length and not _zero(self.offset):
            raise ValueError("`offset` needs full_length=false")
        return self


# --- fillet (D17) ----------------------------------------------------------------------------


class FilletMid(_Model):
    """Intermediate radius of a variable fillet at a share of the edge length."""

    position: Annotated[
        float, BeforeValidator(_number), Field(ge=0, le=1, description="0 = edge start, 1 = end")
    ]
    radius: Length


# --- params ----------------------------------------------------------------------------------


def _dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, list | tuple):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items() if v is not None}
    return value


def to_params(**kwargs: Any) -> dict[str, Any]:
    """Bridge params from tool arguments: ``None`` means "not given" and is dropped; models
    (also inside lists) become dicts without unset optional fields, defaults kept."""
    return {k: _dump(v) for k, v in kwargs.items() if v is not None}

"""Input models of the ``pattern_*`` and ``body_*`` tools (c08 D14).

Builds on the shared feature models (``_feature_models.py``: ``Ref``, ``Length``, ``Angle``,
``Units``, ``to_params``). The string vocabularies are the tuples of
``fusion_mcp.protocol.features`` (c08 D1) written out as ``Literal`` (a test keeps them in
sync). ``Transform`` is the discriminated union of ``body_move`` transforms (D8).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, ConfigDict, Field

from fusion_mcp.server.tools._feature_models import (
    Angle,
    Length,
    Number,
    Ref,
    _Model,
    _value,
)

PatternDistanceType = Literal["extent", "spacing"]
ComputeOption = Literal["optimized", "identical", "adjust"]
CombineOperation = Literal["join", "cut", "intersect"]
MoveType = Literal["translate", "translate_along", "rotate", "rotate_about", "align", "matrix"]
SplitFaceType = Literal["intersection", "along_vector", "closest_point"]
RemoveMode = Literal["remove", "delete"]

Point3 = Annotated[
    list[Number],
    Field(min_length=3, max_length=3, description="[x, y, z] numbers"),
]


def _positive_scalar(value: Any) -> Any:
    """A unitless factor: a positive finite number or a Fusion expression string."""
    value = _value(value)
    if isinstance(value, int | float) and value <= 0:
        raise ValueError("the factor must be > 0")
    return value


Factor = Annotated[
    float | str,
    BeforeValidator(_positive_scalar),
    Field(description="Unitless factor > 0, or a Fusion expression"),
]
Count = Annotated[
    int | str,
    Field(description="Number of instances (>= 1, the original counts), or a Fusion expression"),
]


class ScaleFactors(_Model):
    """Non-uniform scale factors along the model X, Y and Z axes."""

    x: Factor
    y: Factor
    z: Factor


# --- body_move transforms (D8) -------------------------------------------------------------


class TranslateXYZ(_Model):
    """Move by x / y / z (in `units`)."""

    type: Literal["translate"]
    x: Length = 0.0
    y: Length = 0.0
    z: Length = 0.0
    design_space: Annotated[
        bool, Field(description="true = model axes; false = the axes of the bodies' component")
    ] = True


class TranslateAlong(_Model):
    """Move along a straight edge, sketch line or construction axis."""

    type: Literal["translate_along"]
    entity: Annotated[Ref, Field(description="Linear edge, sketch line or axis (origin:X|Y|Z)")]
    distance: Length


class RotateAxis(_Model):
    """Rotate about an axis entity (right-hand rule)."""

    type: Literal["rotate"]
    axis: Annotated[
        Ref, Field(description="Axis: construction axis, origin:X|Y|Z, straight edge, sketch line")
    ]
    angle: Angle


class RotateAbout(_Model):
    """Rotate about a free axis through `point` along `direction`; plain numbers only."""

    type: Literal["rotate_about"]
    point: Annotated[Point3, Field(description="[x, y, z] on the axis, in `units`")]
    direction: Annotated[Point3, Field(description="[dx, dy, dz] axis direction (not zero)")]
    angle: Annotated[Number, Field(description="Degrees, right-hand rule about `direction`")]


class AlignPoints(_Model):
    """Move so that point `from` lands on point `to`."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["align"]
    from_: Annotated[
        Ref,
        Field(
            alias="from",
            description="Vertex, sketch point, construction point or origin:O to move",
        ),
    ]
    to: Annotated[Ref, Field(description="Vertex, sketch point, construction point or origin:O")]


class MatrixTransform(_Model):
    """Rigid 4x4 transform, row-major; the translation (elements 3, 7, 11) is in `units`."""

    type: Literal["matrix"]
    matrix: Annotated[
        list[Number],
        Field(
            min_length=16,
            max_length=16,
            description="16 numbers, row-major; the 3x3 part must be a rotation (no scale)",
        ),
    ]


Transform = Annotated[
    TranslateXYZ | TranslateAlong | RotateAxis | RotateAbout | AlignPoints | MatrixTransform,
    Field(discriminator="type"),
]

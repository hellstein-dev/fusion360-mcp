"""Input models of the ``sketch_*`` tools (c05 D2, D4, D7, D9-D11).

The server rejects a malformed item before the bridge; the add-in re-checks entity types.
Coordinates and geometry sizes are plain numbers (mm of sketch space, or the tool's
``units``); sizes that must be exact or parametric are set with dimensions, whose values are
``Length``/``Angle`` (a number in mm/deg or a Fusion expression string).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    TypeAdapter,
    field_validator,
    model_validator,
)

MAX_ITEMS = 500
NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
LOCAL_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,31}$"
STRING_COORDINATE_HINT = (
    "coordinates and geometry sizes must be numbers (sketch mm or `units`), not expressions; "
    "draw approximately and bind exact or parametric sizes with dimensions "
    "(sketch_draw `dimensions` or sketch_dimension)"
)

SketchUnits = Literal["mm", "cm", "m", "in", "ft"]


def _number(value: Any) -> Any:
    """A real number; strings get the "use a dimension" hint, booleans are refused."""
    if isinstance(value, str):
        raise ValueError(STRING_COORDINATE_HINT)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("expected a number")
    return value


def _point(value: Any) -> Any:
    """``[x, y]`` must be two numbers; ``["w/2", 0]`` is refused with the dimension hint."""
    if isinstance(value, list | tuple):
        if any(isinstance(c, str) for c in value):
            raise ValueError(STRING_COORDINATE_HINT)
        if len(value) != 2:
            raise ValueError("a sketch point is [x, y] (two numbers, sketch mm)")
        return [_number(c) for c in value]
    return value


Number = Annotated[float, BeforeValidator(_number)]
Positive = Annotated[float, BeforeValidator(_number), Field(gt=0)]
XY = Annotated[list[Number], Field(min_length=2, max_length=2, description="[x, y] sketch mm")]
EntityRef = Annotated[
    str,
    Field(
        min_length=1,
        description="Entity ref (token from a previous result), a local path `id` / "
        "`id.<sub>` of this call, or `origin` (sketch origin point)",
    ),
]
Length = Annotated[
    float | str,
    Field(description="Number = mm, string = Fusion expression ('25 mm', 'plate_w / 2')"),
]
Angle = Annotated[
    float | str,
    Field(description="Number = degrees, string = Fusion expression ('30 deg', 'tilt')"),
]
ParameterName = Annotated[str, Field(pattern=NAME_PATTERN, description="Parameter name")]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ModelPoint(_Model):
    """A point in model space (mm); projected onto the sketch plane."""

    model: Annotated[
        list[Number], Field(min_length=3, max_length=3, description="[x, y, z] model mm")
    ]


Point = Annotated[
    XY | ModelPoint | EntityRef,
    BeforeValidator(_point),
    Field(
        description='[x, y] sketch mm | {"model": [x, y, z]} model mm | ref of a sketch point '
        "or curve end (`l1.end`, `origin`, token)"
    ),
]


# --- draw items (D7) -------------------------------------------------------------------------


class _DrawBase(_Model):
    id: Annotated[
        str | None,
        Field(
            pattern=LOCAL_ID_PATTERN,
            description="Local id for later items/constraints/dimensions of this call",
        ),
    ] = None
    construction: Annotated[bool, Field(description="Make the curves construction")] = False

    @field_validator("id")
    @classmethod
    def _not_origin(cls, value: str | None) -> str | None:
        if value == "origin":
            raise ValueError("`origin` is reserved for the sketch origin point")
        return value


class PointItem(_DrawBase):
    kind: Literal["point"]
    at: Point


class LineItem(_DrawBase):
    kind: Literal["line"]
    start: Point
    end: Point


class PolylineItem(_DrawBase):
    kind: Literal["polyline"]
    points: Annotated[list[Point], Field(min_length=2, max_length=MAX_ITEMS)]
    closed: bool = False


class RectangleTwoPoint(_DrawBase):
    kind: Literal["rectangle"]
    mode: Literal["two_point"] = "two_point"
    corner1: Point
    corner2: Point


class RectangleCenter(_DrawBase):
    kind: Literal["rectangle"]
    mode: Literal["center"]
    center: Point
    corner: Point


class RectangleThreePoint(_DrawBase):
    kind: Literal["rectangle"]
    mode: Literal["three_point"]
    p1: Point
    p2: Point
    p3: Point


class CircleCenterRadius(_DrawBase):
    kind: Literal["circle"]
    mode: Literal["center_radius"] = "center_radius"
    center: Point
    radius: Positive


class CircleTwoPoint(_DrawBase):
    kind: Literal["circle"]
    mode: Literal["two_point"]
    p1: Point
    p2: Point


class CircleThreePoint(_DrawBase):
    kind: Literal["circle"]
    mode: Literal["three_point"]
    p1: Point
    p2: Point
    p3: Point


class ArcThreePoint(_DrawBase):
    kind: Literal["arc"]
    mode: Literal["three_point"] = "three_point"
    start: Point
    mid: Point
    end: Point


class ArcCenterStartSweep(_DrawBase):
    kind: Literal["arc"]
    mode: Literal["center_start_sweep"]
    center: Point
    start: Point
    sweep: Annotated[
        Number, Field(ge=-360, le=360, description="Degrees, positive = counter-clockwise")
    ]

    @field_validator("sweep")
    @classmethod
    def _non_zero(cls, value: float) -> float:
        if value == 0:
            raise ValueError("sweep must not be 0")
        return value


class ArcTangent(_DrawBase):
    kind: Literal["arc"]
    mode: Literal["tangent"]
    from_: Annotated[
        EntityRef, Field(alias="from", description="End of a curve to continue, e.g. `l1.end`")
    ]
    to: Point


class EllipseItem(_DrawBase):
    kind: Literal["ellipse"]
    center: Point
    major_radius: Positive
    minor_radius: Positive
    angle: Annotated[Number, Field(description="Major axis angle from sketch X, degrees")] = 0.0


class PolygonItem(_DrawBase):
    kind: Literal["polygon"]
    center: Point
    radius: Positive
    sides: Annotated[int, Field(ge=3, le=64)]
    mode: Literal["inscribed", "circumscribed"] = "inscribed"
    angle: Annotated[Number, Field(description="Rotation of the first vertex, degrees")] = 0.0
    constrain: Annotated[
        bool, Field(description="Equal sides + vertices on / sides tangent to the circle")
    ] = True


class SplineItem(_DrawBase):
    kind: Literal["spline"]
    points: Annotated[list[Point], Field(min_length=2, max_length=MAX_ITEMS)]
    closed: bool = False


class SlotCenterToCenter(_DrawBase):
    kind: Literal["slot"]
    mode: Literal["center_to_center"] = "center_to_center"
    p1: Point
    p2: Point
    width: Positive


class SlotOverall(_DrawBase):
    kind: Literal["slot"]
    mode: Literal["overall"]
    p1: Point
    p2: Point
    width: Positive


class TextItem(_DrawBase):
    kind: Literal["text"]
    text: Annotated[str, Field(min_length=1)]
    height: Positive
    corner: Point
    size: Annotated[
        list[Positive] | None,
        Field(min_length=2, max_length=2, description="[w, h] box, mm"),
    ] = None
    font: str | None = None
    h_align: Literal["left", "center", "right"] = "left"
    v_align: Literal["top", "middle", "bottom"] = "bottom"


def _field(name: str, default: str | None = None) -> Callable[[Any], str | None]:
    def get(value: Any) -> str | None:
        if isinstance(value, dict):
            found = value.get(name, default)
        else:
            found = getattr(value, name, default)
        return found if isinstance(found, str) else None

    return get


def _discriminator(field: str, kind: str, tags: str, default: str | None = None) -> Discriminator:
    """Callable discriminator; a missing `mode` means the kind's default mode."""
    what = f"{kind} mode" if field == "mode" else "or missing kind"
    return Discriminator(
        _field(field, default),
        custom_error_type=f"invalid_{field}",
        custom_error_message=f"unknown {what}; expected one of: {tags}",
    )


RectangleItem = Annotated[
    Annotated[RectangleTwoPoint, Tag("two_point")]
    | Annotated[RectangleCenter, Tag("center")]
    | Annotated[RectangleThreePoint, Tag("three_point")],
    _discriminator("mode", "rectangle", "two_point, center, three_point", "two_point"),
]
CircleItem = Annotated[
    Annotated[CircleCenterRadius, Tag("center_radius")]
    | Annotated[CircleTwoPoint, Tag("two_point")]
    | Annotated[CircleThreePoint, Tag("three_point")],
    _discriminator("mode", "circle", "center_radius, two_point, three_point", "center_radius"),
]
ArcItem = Annotated[
    Annotated[ArcThreePoint, Tag("three_point")]
    | Annotated[ArcCenterStartSweep, Tag("center_start_sweep")]
    | Annotated[ArcTangent, Tag("tangent")],
    _discriminator("mode", "arc", "three_point, center_start_sweep, tangent", "three_point"),
]
SlotItem = Annotated[
    Annotated[SlotCenterToCenter, Tag("center_to_center")] | Annotated[SlotOverall, Tag("overall")],
    _discriminator("mode", "slot", "center_to_center, overall", "center_to_center"),
]

DRAW_KINDS = "point, line, polyline, rectangle, circle, arc, ellipse, polygon, spline, slot, text"
DrawItem = Annotated[
    Annotated[PointItem, Tag("point")]
    | Annotated[LineItem, Tag("line")]
    | Annotated[PolylineItem, Tag("polyline")]
    | Annotated[RectangleItem, Tag("rectangle")]
    | Annotated[CircleItem, Tag("circle")]
    | Annotated[ArcItem, Tag("arc")]
    | Annotated[EllipseItem, Tag("ellipse")]
    | Annotated[PolygonItem, Tag("polygon")]
    | Annotated[SplineItem, Tag("spline")]
    | Annotated[SlotItem, Tag("slot")]
    | Annotated[TextItem, Tag("text")],
    _discriminator("kind", "draw item", DRAW_KINDS),
]


# --- constraints (D9) ------------------------------------------------------------------------


class Coincident(_Model):
    type: Literal["coincident"]
    point: EntityRef
    entity: EntityRef


class HorizontalVertical(_Model):
    """Give either `line` or `points: [p1, p2]`."""

    type: Literal["horizontal", "vertical"]
    line: EntityRef | None = None
    points: Annotated[list[EntityRef] | None, Field(min_length=2, max_length=2)] = None

    @model_validator(mode="after")
    def _one_form(self) -> HorizontalVertical:
        if (self.line is None) == (self.points is None):
            raise ValueError(f"{self.type}: give either `line` or `points: [p1, p2]`")
        return self


class TwoLines(_Model):
    type: Literal["parallel", "perpendicular", "collinear"]
    line1: EntityRef
    line2: EntityRef


class TwoCurves(_Model):
    type: Literal["tangent", "equal", "concentric"]
    curve1: EntityRef
    curve2: EntityRef


class Midpoint(_Model):
    type: Literal["midpoint"]
    point: EntityRef
    curve: EntityRef


class Symmetry(_Model):
    type: Literal["symmetry"]
    entity1: EntityRef
    entity2: EntityRef
    line: EntityRef


class Smooth(_Model):
    type: Literal["smooth"]
    spline: EntityRef
    curve: EntityRef


class Fix(_Model):
    type: Literal["fix"]
    entity: EntityRef
    fixed: bool = True


ConstraintItem = Annotated[
    Coincident | HorizontalVertical | TwoLines | TwoCurves | Midpoint | Symmetry | Smooth | Fix,
    Field(discriminator="type"),
]


# --- dimensions (D10) ------------------------------------------------------------------------


class _DimensionBase(_Model):
    value: Annotated[
        float | str | None,
        Field(description="Number = mm (deg for angular), string = expression; omit to keep"),
    ] = None
    driven: Annotated[
        bool, Field(description="Reference (driven) dimension: measures, no `value`")
    ] = False
    parameter_name: Annotated[
        str | None,
        Field(pattern=NAME_PATTERN, description="Rename the dimension parameter (e.g. plate_w)"),
    ] = None
    text_at: Annotated[XY | None, Field(description="Dimension text position, sketch mm")] = None

    @model_validator(mode="after")
    def _driven_has_no_value(self) -> _DimensionBase:
        if self.driven and self.value is not None:
            raise ValueError("a driven dimension measures the geometry: remove `value`")
        return self


class DistanceDimension(_DimensionBase):
    """A line's length, or the distance between `point1` and `point2`."""

    type: Literal["distance"]
    line: EntityRef | None = None
    point1: EntityRef | None = None
    point2: EntityRef | None = None
    orientation: Literal["aligned", "horizontal", "vertical"] = "aligned"

    @model_validator(mode="after")
    def _line_or_points(self) -> DistanceDimension:
        points = (self.point1 is not None, self.point2 is not None)
        if self.line is not None and not any(points):
            return self
        if self.line is None and all(points):
            return self
        raise ValueError("distance: give either `line` or both `point1` and `point2`")


class OffsetDimension(_DimensionBase):
    type: Literal["offset"]
    line: EntityRef
    entity: EntityRef


class CurveDimension(_DimensionBase):
    type: Literal["radial", "diameter"]
    curve: EntityRef


class AngularDimension(_DimensionBase):
    type: Literal["angular"]
    line1: EntityRef
    line2: EntityRef


class EllipseDimension(_DimensionBase):
    type: Literal["ellipse_major", "ellipse_minor"]
    ellipse: EntityRef


DimensionItem = Annotated[
    DistanceDimension | OffsetDimension | CurveDimension | AngularDimension | EllipseDimension,
    Field(discriminator="type"),
]


class DimensionUpdate(_Model):
    """Change of one existing dimension; give at least one field besides `dimension`."""

    dimension: Annotated[str, Field(min_length=1, description="Dimension ref")]
    value: Annotated[
        float | str | None, Field(description="Number = mm/deg, string = expression")
    ] = None
    driven: bool | None = None
    parameter_name: Annotated[str | None, Field(pattern=NAME_PATTERN)] = None

    @model_validator(mode="after")
    def _check(self) -> DimensionUpdate:
        if self.value is None and self.driven is None and self.parameter_name is None:
            raise ValueError("give `value`, `driven` or `parameter_name`")
        if self.driven and self.value is not None:
            raise ValueError("a driven dimension measures the geometry: remove `value`")
        return self


# --- patterns (D11) --------------------------------------------------------------------------


class RectangularPattern(_Model):
    kind: Literal["rectangular"]
    count_x: Annotated[int, Field(ge=1, le=MAX_ITEMS)]
    spacing_x: Length
    count_y: Annotated[int, Field(ge=1, le=MAX_ITEMS)] = 1
    spacing_y: Length = 0.0
    angle: Annotated[Angle, Field(description="Direction one, degrees from sketch X")] = 0.0
    direction: Annotated[
        str | None, Field(min_length=1, description="Line ref for direction one")
    ] = None


class CircularPattern(_Model):
    kind: Literal["circular"]
    center: Point
    count: Annotated[int, Field(ge=2, le=MAX_ITEMS)]
    angle: Annotated[Angle, Field(description="Total angle, degrees")] = 360.0


PatternSpec = Annotated[RectangularPattern | CircularPattern, Field(discriminator="kind")]
PATTERN_ADAPTER: TypeAdapter[RectangularPattern | CircularPattern] = TypeAdapter(PatternSpec)


def dump(model: BaseModel) -> dict[str, Any]:
    """Wire form: aliases (`from`), unset optional fields dropped, defaults kept."""
    return model.model_dump(by_alias=True, exclude_none=True)

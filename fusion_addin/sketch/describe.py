"""Serialisation of sketch entities and the sketch constraint status (c05 D5).

Coordinates are sketch space (``entity.geometry``: cm, z = 0) and go out as ``[x, y]`` mm;
lengths are mm, angles degrees, all through ``fusion_mcp.protocol.units`` (INV-6). Every
entity is addressed by a c02 ref ``{"ref": entityToken, "type": "SketchLine", "name"?}``
(INV-7). Types are told apart by ``objectType`` strings, not ``isinstance`` (D1), and every
optional property is read defensively: Fusion raises for properties that do not apply.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator
from typing import Any

from fusion_mcp.protocol.units import parameter_value_to_user

from ..core import refs
from ..core.geometry import DEFAULT_UNITS

STATUS_REF_LIMIT = 50

# Short ``objectType`` of a sketch curve -> D5 curve type.
CURVE_TYPES: dict[str, str] = {
    "SketchLine": "line",
    "SketchArc": "arc",
    "SketchCircle": "circle",
    "SketchEllipse": "ellipse",
    "SketchEllipticalArc": "elliptical_arc",
    "SketchFittedSpline": "fitted_spline",
    "SketchFixedSpline": "fixed_spline",
    "SketchControlPointSpline": "control_point_spline",
    "SketchConicCurve": "conic",
}
POINT_TYPE = "SketchPoint"
TEXT_TYPE = "SketchText"

# Entity properties of each geometric constraint class, in output order (D5).
CONSTRAINT_ROLES: dict[str, tuple[str, ...]] = {
    "CoincidentConstraint": ("point", "entity"),
    "CoincidentToSurfaceConstraint": ("point", "surface"),
    "HorizontalConstraint": ("line",),
    "VerticalConstraint": ("line",),
    "HorizontalPointsConstraint": ("pointOne", "pointTwo"),
    "VerticalPointsConstraint": ("pointOne", "pointTwo"),
    "ParallelConstraint": ("lineOne", "lineTwo"),
    "PerpendicularConstraint": ("lineOne", "lineTwo"),
    "CollinearConstraint": ("lineOne", "lineTwo"),
    "TangentConstraint": ("curveOne", "curveTwo"),
    "EqualConstraint": ("curveOne", "curveTwo"),
    "ConcentricConstraint": ("entityOne", "entityTwo"),
    "MidPointConstraint": ("point", "midPointCurve"),
    "SymmetryConstraint": ("entityOne", "entityTwo", "symmetryLine"),
    "SmoothConstraint": ("curveOne", "curveTwo"),
    "OffsetConstraint": ("parentCurves", "childCurves"),
    "RectangularPatternConstraint": ("entities", "createdEntities"),
    "CircularPatternConstraint": ("entities", "centerPoint", "createdEntities"),
    "PolygonConstraint": ("lines",),
}
# Constraint types whose snake_case name differs from the input name of sketch_constrain.
CONSTRAINT_TYPE_NAMES: dict[str, str] = {"MidPointConstraint": "midpoint"}

# Dimension class -> (D10 type, entity properties).
DIMENSION_KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "SketchLinearDimension": ("distance", ("entityOne", "entityTwo")),
    "SketchOffsetDimension": ("offset", ("line", "entity")),
    "SketchRadialDimension": ("radial", ("entity",)),
    "SketchDiameterDimension": ("diameter", ("entity",)),
    "SketchAngularDimension": ("angular", ("lineOne", "lineTwo")),
    "SketchEllipseMajorRadiusDimension": ("ellipse_major", ("ellipse",)),
    "SketchEllipseMinorRadiusDimension": ("ellipse_minor", ("ellipse",)),
    "SketchConcentricCircleDimension": ("concentric_circle", ("circleOne", "circleTwo")),
    "SketchOffsetCurvesDimension": ("offset_curves", ("offsetConstraint",)),
}

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


# --- small helpers -----------------------------------------------------------------------


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Optional attribute: Fusion raises for properties that do not apply to an object."""
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _round(value: float, digits: int = 6) -> float:
    # ``+ 0.0`` turns -0.0 into 0.0.
    return round(float(value), digits) + 0.0


def _mm(cm: float, units: Any = None) -> float:
    return _round((units or DEFAULT_UNITS).length_out(cm))


def xy_mm(p3d: Any, units: Any = None) -> list[float]:
    """Sketch-space ``Point3D`` (cm) -> ``[x, y]`` mm (z dropped: it is 0 in a sketch)."""
    return [_mm(p3d.x, units), _mm(p3d.y, units)]


def items(collection: Any) -> list[Any]:
    """Elements of a Fusion collection (``count``/``item``), a list or a single entity."""
    if collection is None:
        return []
    if hasattr(collection, "count") and hasattr(collection, "item"):
        return [collection.item(i) for i in range(collection.count)]
    if isinstance(collection, list | tuple):
        return list(collection)
    if hasattr(collection, "objectType"):
        return [collection]
    return list(collection)


def type_name(entity: Any) -> str:
    return refs.type_name(entity)


def snake_case(name: str) -> str:
    """``"HorizontalPoints"`` -> ``"horizontal_points"``."""
    return _CAMEL_RE.sub("_", name).lower()


def entity_ref(entity: Any) -> dict[str, Any] | None:
    """c02 ref of a sketch entity: ``{"ref", "type", "name"?}``; ``None`` for ``None``."""
    if entity is None:
        return None
    data: dict[str, Any] = {"ref": refs.to_ref(entity), "type": type_name(entity)}
    name = _get(entity, "name")
    if isinstance(name, str):
        data["name"] = name
    return data


def _refs_of(values: Iterable[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for value in values:
        ref = entity_ref(value)
        if ref is not None:
            out.append(ref)
    return out


def is_curve(entity: Any) -> bool:
    return type_name(entity) in CURVE_TYPES


def is_point(entity: Any) -> bool:
    return type_name(entity) == POINT_TYPE


# --- collections of a sketch -------------------------------------------------------------


def sketch_points(sketch: Any) -> list[Any]:
    return items(sketch.sketchPoints)


def sketch_curves(sketch: Any) -> list[Any]:
    return items(sketch.sketchCurves)


def sketch_constraints(sketch: Any) -> list[Any]:
    return items(sketch.geometricConstraints)


def sketch_dimensions(sketch: Any) -> list[Any]:
    return items(sketch.sketchDimensions)


def sketch_texts(sketch: Any) -> list[Any]:
    return items(_get(sketch, "sketchTexts"))


# --- points and curves (D5 part 1) -------------------------------------------------------


def _flags(entity: Any) -> dict[str, bool]:
    return {
        "fixed": bool(_get(entity, "isFixed", False)),
        "reference": bool(_get(entity, "isReference", False)),
        "fully_constrained": bool(_get(entity, "isFullyConstrained", False)),
    }


def describe_point(point: Any, units: Any = None) -> dict[str, Any]:
    """``{ref, xy, fixed, reference, fully_constrained}``."""
    return {"ref": entity_ref(point), "xy": xy_mm(point.geometry, units), **_flags(point)}


def _point_xy(point: Any, units: Any) -> list[float] | None:
    return None if point is None else xy_mm(point.geometry, units)


def _angle_deg(center: Any, p: Any) -> float:
    return math.degrees(math.atan2(p.y - center.y, p.x - center.x)) % 360.0


def _arc_angles(arc: Any, center: Any, start: Any, end: Any) -> tuple[float, float]:
    """``(start_angle, sweep)`` in degrees at the arc's start point; sweep > 0 is
    counter-clockwise in sketch space (sketch arcs normally are; an arc whose geometry
    normal points to -Z runs clockwise and gets a negative sweep)."""
    a0, a1 = _angle_deg(center, start), _angle_deg(center, end)
    normal = _get(_get(arc, "geometry"), "normal")
    clockwise = normal is not None and float(_get(normal, "z", 1.0)) < 0.0
    sweep = (a0 - a1) % 360.0 if clockwise else (a1 - a0) % 360.0
    if sweep <= 1e-9:
        sweep = 360.0
    return _round(a0, 9), _round(-sweep if clockwise else sweep, 9)


def _unit_xy(vector: Any) -> list[float]:
    x, y = float(vector.x), float(vector.y)
    norm = math.hypot(x, y)
    if norm == 0.0:
        return [0.0, 0.0]
    return [_round(x / norm, 9), _round(y / norm, 9)]


def curve_geometry(curve: Any, kind: str, units: Any = None) -> dict[str, Any]:
    """Type-specific geometry of a sketch curve, sketch-space mm (D5)."""
    start = _get(curve, "startSketchPoint")
    end = _get(curve, "endSketchPoint")
    center = _get(curve, "centerSketchPoint")
    if kind == "line":
        return {"start": _point_xy(start, units), "end": _point_xy(end, units)}
    if kind == "circle":
        return {"center": _point_xy(center, units), "radius": _mm(curve.radius, units)}
    if kind == "arc":
        c, s, e = center.geometry, start.geometry, end.geometry
        start_angle, sweep = _arc_angles(curve, c, s, e)
        return {
            "center": xy_mm(c, units),
            "radius": _mm(curve.radius, units),
            "start": xy_mm(s, units),
            "end": xy_mm(e, units),
            "start_angle": start_angle,
            "sweep": sweep,
        }
    if kind in ("ellipse", "elliptical_arc"):
        data: dict[str, Any] = {
            "center": _point_xy(center, units),
            "major_axis": _unit_xy(curve.majorAxis),
            "major_radius": _mm(curve.majorAxisRadius, units),
            "minor_radius": _mm(curve.minorAxisRadius, units),
        }
        if kind == "elliptical_arc":
            data["start"] = _point_xy(start, units)
            data["end"] = _point_xy(end, units)
        return data
    if kind == "fitted_spline":
        return {
            "fit_points": [xy_mm(p.geometry, units) for p in items(curve.fitPoints)],
            "closed": bool(_get(curve, "isClosed", False)),
        }
    if kind in ("fixed_spline", "control_point_spline"):
        geometry = _get(curve, "geometry")
        count = _get(geometry, "controlPointCount")
        if count is None:
            count = len(items(_get(curve, "controlPoints")))
        closed = _get(curve, "isClosed")
        if closed is None:
            closed = _get(geometry, "isClosed", False)
        return {"control_point_count": int(count), "closed": bool(closed)}
    if kind == "conic":
        return {"start": _point_xy(start, units), "end": _point_xy(end, units)}
    return {}


def curve_type(curve: Any) -> str:
    """D5 type of a sketch curve (``line``...); an unknown class -> snake_case of its name."""
    kind = type_name(curve)
    return CURVE_TYPES.get(kind) or snake_case(kind.removeprefix("Sketch"))


def describe_curve(curve: Any, units: Any = None) -> dict[str, Any]:
    """``{ref, type, construction, fixed, reference, fully_constrained, length_mm, start,
    end, center?, geometry}``; ``start``/``end`` are point refs (``None`` for closed
    curves), ``center`` is present for circles, arcs and ellipses."""
    kind = curve_type(curve)
    length = _get(curve, "length")
    data: dict[str, Any] = {
        "ref": entity_ref(curve),
        "type": kind,
        "construction": bool(_get(curve, "isConstruction", False)),
        **_flags(curve),
        "length_mm": None if length is None else _mm(length, units),
        "start": entity_ref(_get(curve, "startSketchPoint")),
        "end": entity_ref(_get(curve, "endSketchPoint")),
    }
    if kind in ("circle", "ellipse"):
        data["start"] = data["end"] = None
    center = _get(curve, "centerSketchPoint")
    if center is not None:
        data["center"] = entity_ref(center)
    if kind in ("fitted_spline", "fixed_spline", "control_point_spline") and bool(
        _get(curve, "isClosed", False)
    ):
        data["start"] = data["end"] = None
    data["geometry"] = curve_geometry(curve, kind, units)
    return data


# --- constraints, dimensions, texts (D5 part 2) ------------------------------------------


def constraint_type(constraint: Any) -> str:
    kind = type_name(constraint)
    return CONSTRAINT_TYPE_NAMES.get(kind) or snake_case(kind.removesuffix("Constraint"))


def _role_entities(obj: Any, roles: Iterable[str]) -> list[dict[str, Any]]:
    found: list[Any] = []
    for role in roles:
        value = _get(obj, role)
        if value is None or isinstance(value, bool | int | float | str):
            continue
        found.extend(items(value))
    return _refs_of(found)


def describe_constraint(constraint: Any) -> dict[str, Any]:
    """``{ref, type, entities}``; entities come from the class's own properties
    (``CONSTRAINT_ROLES``), an unknown class has ``entities: []``."""
    roles = CONSTRAINT_ROLES.get(type_name(constraint), ())
    return {
        "ref": entity_ref(constraint),
        "type": constraint_type(constraint),
        "entities": _role_entities(constraint, roles),
    }


def dimension_type(dimension: Any) -> str:
    kind = type_name(dimension)
    known = DIMENSION_KINDS.get(kind)
    if known is not None:
        return known[0]
    return snake_case(kind.removeprefix("Sketch").removesuffix("Dimension"))


def describe_dimension(dimension: Any) -> dict[str, Any]:
    """``{ref, type, entities, value, unit, expression, parameter, driving}``: ``value`` in
    mm or degrees (``unit``) from the dimension's model parameter."""
    known = DIMENSION_KINDS.get(type_name(dimension))
    roles = known[1] if known is not None else ()
    parameter = _get(dimension, "parameter")
    value: float | None = None
    unit: str | None = None
    expression: str | None = None
    name: str | None = None
    if parameter is not None:
        shown = parameter_value_to_user(float(parameter.value), str(_get(parameter, "unit", "")))
        value = _round(float(shown["value"]), 9)
        unit = str(shown["value_unit"])
        expression = _get(parameter, "expression")
        name = _get(parameter, "name")
    return {
        "ref": entity_ref(dimension),
        "type": dimension_type(dimension),
        "entities": _role_entities(dimension, roles),
        "value": value,
        "unit": unit,
        "expression": expression,
        "parameter": name,
        "driving": bool(_get(dimension, "isDriving", True)),
    }


def _text_position(text: Any) -> Any:
    position = _get(text, "position")
    if position is not None:
        return position
    definition = _get(text, "definition")
    corner = _get(definition, "cornerPoint")
    if corner is not None:
        return corner
    box = _get(text, "boundingBox")
    return _get(box, "minPoint")


def describe_text(text: Any, units: Any = None) -> dict[str, Any]:
    """``{ref, text, height, position}`` (height mm, position ``[x, y]`` mm)."""
    position = _text_position(text)
    return {
        "ref": entity_ref(text),
        "text": str(_get(text, "text", "")),
        "height": _mm(text.height, units),
        "position": None if position is None else xy_mm(position, units),
    }


def describe_entity(entity: Any, units: Any = None) -> dict[str, Any]:
    """Any sketch entity by its class: point, curve, constraint, dimension or text; other
    objects get their c02 ref only."""
    kind = type_name(entity)
    if kind == POINT_TYPE:
        return describe_point(entity, units)
    if kind in CURVE_TYPES:
        return describe_curve(entity, units)
    if kind == TEXT_TYPE:
        return describe_text(entity, units)
    if kind.endswith("Constraint"):
        return describe_constraint(entity)
    if kind.startswith("Sketch") and kind.endswith("Dimension"):
        return describe_dimension(entity)
    return {"ref": entity_ref(entity)}


# --- status ------------------------------------------------------------------------------


def _constrainable(sketch: Any) -> Iterator[Any]:
    for entity in (*sketch_curves(sketch), *sketch_points(sketch)):
        if not bool(_get(entity, "isReference", False)):
            yield entity


def sketch_status(sketch: Any, limit: int = STATUS_REF_LIMIT) -> dict[str, Any]:
    """``{fully_constrained, under_constrained_count, under_constrained: [refs <= limit]}``.

    ``fully_constrained`` is ``Sketch.isFullyConstrained`` when the API has it, else "every
    non-reference curve and point is fully constrained" (D5, Risks).
    """
    under = [e for e in _constrainable(sketch) if not bool(_get(e, "isFullyConstrained", False))]
    flag = _get(sketch, "isFullyConstrained")
    fully = bool(flag) if flag is not None else not under
    return {
        "fully_constrained": fully,
        "under_constrained_count": len(under),
        "under_constrained": _refs_of(under[:limit]),
    }

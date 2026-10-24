"""Sketch dimensions (c05 D10): ``apply_dimension`` creates, ``set_dimension`` changes.

An item is the wire form of ``sketch_schema.DimensionItem``: ``{"type", ...entity specs,
"value"?, "driven" = false, "parameter_name"?, "text_at"?: [x, y]}``.

| type | fields | Fusion API |
|---|---|---|
| distance | ``line`` or ``point1`` + ``point2``; ``orientation`` | ``addDistanceDimension`` |
| offset | ``line``, ``entity`` (a parallel line or a point) | ``addOffsetDimension`` |
| radial / diameter | ``curve`` (arc or circle) | ``addRadialDimension`` / ``addDiameter...`` |
| angular | ``line1``, ``line2`` | ``addAngularDimension`` |
| ellipse_major / _minor | ``ellipse`` | ``addEllipseMajorRadiusDimension`` / ``...Minor...`` |

``orientation`` is ``aligned`` (default), ``horizontal`` or ``vertical``; a ``line`` is
dimensioned through its end points.

Values (D2): a number is mm (degrees for ``angular``) and becomes the expression
``"80 mm"`` / ``"30 deg"`` through ``units.number_expression`` (INV-6); a string is a Fusion
expression set unchanged. Every expression is checked with
``unitsManager.isValidExpression(expr, "mm" | "deg")`` before the dimension is created
(``INVALID_PARAMS``). ``parameter_name`` renames the dimension's model parameter, so later
dimensions of the same batch can use it (``validate_dimensions`` defers the check of
expressions naming such a pending parameter to ``apply_dimension``). ``driven = true``
creates a reference (non-driving) dimension; giving it a ``value`` is ``INVALID_PARAMS``.

``text_at`` (sketch space, the scope's units) defaults to ``math2d.default_dimension_text_point``
(for angular dimensions it selects the measured quadrant).

``addOffsetDimension(line, point)`` is not accepted by every Fusion version (design Risks):
when that call fails, the offset is built as a construction line from the point,
perpendicular to the line with its end coincident on it, and a distance dimension of that
helper line (``fallback = "construction_perpendicular"`` in the result).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.units import classify_unit, length_from_internal, number_expression

from ..core.dispatcher import to_bridge_error
from ..core.refs import type_name
from . import describe, math2d, space
from .constraints import indexed
from .journal import SketchJournal
from .resolve import (
    CIRCULAR_TYPES,
    ELLIPSE_TYPES,
    LINE_TYPES,
    POINT_TYPES,
    BatchScope,
    entity_ref,
    same_entity,
)
from .specs import Xy

DIMENSION_TYPES: tuple[str, ...] = (
    "distance",
    "offset",
    "radial",
    "diameter",
    "angular",
    "ellipse_major",
    "ellipse_minor",
)
# Classes accepted by ``set_dimension`` (D5 dimension kinds).
DIMENSION_CLASSES: tuple[str, ...] = tuple(describe.DIMENSION_KINDS)
ORIENTATIONS: dict[str, str] = {
    "aligned": "AlignedDimensionOrientation",
    "horizontal": "HorizontalDimensionOrientation",
    "vertical": "VerticalDimensionOrientation",
}
LENGTH_UNIT = "mm"
ANGLE_UNIT = "deg"
OFFSET_ENTITY_TYPES: tuple[str, ...] = (*LINE_TYPES, *POINT_TYPES)
FALLBACK_PERPENDICULAR = "construction_perpendicular"
PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Relative tolerance of "parallel" for offset dimensions between two lines.
PARALLEL_TOL = 1e-6

# type -> ((field, expected types), ...); distance is handled separately (line or points).
_FIELDS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "offset": (("line", LINE_TYPES), ("entity", OFFSET_ENTITY_TYPES)),
    "radial": (("curve", CIRCULAR_TYPES),),
    "diameter": (("curve", CIRCULAR_TYPES),),
    "angular": (("line1", LINE_TYPES), ("line2", LINE_TYPES)),
    "ellipse_major": (("ellipse", ELLIPSE_TYPES),),
    "ellipse_minor": (("ellipse", ELLIPSE_TYPES),),
}


def _invalid(message: str, field: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, {"field": field, **details})


def _get(obj: Any, name: str) -> Any:
    """Optional attribute (Fusion raises for properties that do not apply)."""
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _call(field: str, fn: Callable[[], Any]) -> Any:
    """One Fusion API call; its failure is ``FUSION_API_ERROR`` naming the item ``field``."""
    try:
        return fn()
    except BridgeError:
        raise
    except Exception as exc:
        error = to_bridge_error(exc)
        error.details.setdefault("field", field)
        raise error from exc


def _set(field: str, obj: Any, attr: str, value: Any) -> None:
    """Set one Fusion property through ``_call``."""
    _call(field, lambda: setattr(obj, attr, value))


# --- values and parameter names ------------------------------------------------------------


def value_unit(kind: str) -> str:
    """Unit of a numeric ``value`` for a dimension type: ``deg`` for angular, else ``mm``."""
    return ANGLE_UNIT if kind == "angular" else LENGTH_UNIT


def value_expression(value: Any, unit: str, field: str) -> str:
    """``value`` -> Fusion expression: a string unchanged, a number ``"<n> <unit>"``."""
    if isinstance(value, str):
        if not value.strip():
            raise _invalid(f"{field} must not be an empty expression", field)
        return value
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return number_expression(float(value), unit)
    raise _invalid(
        f"{field} must be a number ({unit}) or a Fusion expression string", field, value=value
    )


def names_in(expression: str) -> set[str]:
    """Identifiers of an expression (parameter names and unit words)."""
    return set(_IDENTIFIER_RE.findall(expression))


def _units_manager(scope: BatchScope) -> Any:
    design = scope.ctx.design
    manager = getattr(design, "unitsManager", None)
    return manager if manager is not None else design.fusionUnitsManager


def check_expression(
    scope: BatchScope, expression: str, unit: str, field: str, pending: Collection[str] = ()
) -> bool:
    """``isValidExpression(expression, unit)`` or ``INVALID_PARAMS``. An expression naming a
    ``pending`` parameter (renamed by an earlier item of the batch, not yet existing) is not
    checked here: returns ``False`` (deferred), ``True`` when checked."""
    if pending and names_in(expression) & set(pending):
        return False
    manager = _units_manager(scope)
    try:
        valid = bool(manager.isValidExpression(expression, unit))
    except Exception:
        valid = False
    if not valid:
        raise _invalid(
            f"{field}: {expression!r} is not a valid expression in {unit} (unknown parameter "
            "or wrong unit kind)",
            field,
            reason="invalid_expression",
            expression=expression,
            unit=unit,
        )
    return True


def _find_parameter(scope: BatchScope, name: str) -> Any:
    try:
        return scope.ctx.design.allParameters.itemByName(name)
    except BridgeError:
        raise
    except Exception:
        return None


def check_parameter_name(
    scope: BatchScope,
    name: Any,
    field: str,
    pending: Collection[str] = (),
    current: Any = None,
) -> str:
    """Grammar and uniqueness of a new parameter name (``current`` may keep its own)."""
    if not isinstance(name, str) or not PARAMETER_NAME_RE.match(name):
        raise _invalid(
            f"{field} {name!r} must match {PARAMETER_NAME_RE.pattern}",
            field,
            value=name,
            reason="invalid_name",
        )
    taken = name in pending
    if not taken:
        existing = _find_parameter(scope, name)
        taken = existing is not None and not (
            current is not None and (existing is current or existing.name == current.name)
        )
    if taken:
        raise _invalid(
            f"{field}: a parameter named {name!r} already exists",
            field,
            value=name,
            reason="parameter_name_taken",
        )
    return name


# --- item form -----------------------------------------------------------------------------


def _type_of(item: Mapping[str, Any], field: str) -> str:
    if not isinstance(item, Mapping):
        raise _invalid(f"{field} must be an object with a `type`", field)
    kind = item.get("type")
    if kind not in DIMENSION_TYPES:
        raise _invalid(
            f"{field}.type {kind!r} is not a dimension type",
            f"{field}.type",
            value=kind,
            expected=list(DIMENSION_TYPES),
        )
    return str(kind)


def _spec(item: Mapping[str, Any], key: str, field: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise _invalid(f"{field}.{key} is required (an entity ref or local path)", f"{field}.{key}")
    return value


def _driven(item: Mapping[str, Any], field: str) -> bool:
    driven = item.get("driven", False)
    if not isinstance(driven, bool):
        raise _invalid(f"{field}.driven must be a boolean", f"{field}.driven", value=driven)
    if driven and item.get("value") is not None:
        raise _invalid(
            f"{field}: a driven dimension measures the geometry: remove `value`",
            f"{field}.value",
            reason="driven_with_value",
        )
    return driven


def _distance_form(item: Mapping[str, Any], field: str) -> tuple[str, list[str]]:
    line = item.get("line")
    p1, p2 = item.get("point1"), item.get("point2")
    if line is not None and p1 is None and p2 is None:
        return "line", [_spec(item, "line", field)]
    if line is None and p1 is not None and p2 is not None:
        return "points", [_spec(item, "point1", field), _spec(item, "point2", field)]
    raise _invalid(f"{field}: give either `line` or both `point1` and `point2`", field)


def _orientation(item: Mapping[str, Any], field: str) -> str:
    orientation = item.get("orientation", "aligned")
    if orientation not in ORIENTATIONS:
        raise _invalid(
            f"{field}.orientation {orientation!r} is not one of {', '.join(ORIENTATIONS)}",
            f"{field}.orientation",
            value=orientation,
            expected=list(ORIENTATIONS),
        )
    return str(orientation)


def _text_at(scope: BatchScope, item: Mapping[str, Any], field: str) -> Any:
    """Explicit ``text_at`` -> sketch ``Point3D`` (cm), or ``None`` for the default."""
    text_at = item.get("text_at")
    if text_at is None:
        return None
    return space.to_point3d(text_at, scope.units, scope.sketch, None, f"{field}.text_at")


# --- validation ----------------------------------------------------------------------------


def validate_dimension(
    scope: BatchScope, item: Mapping[str, Any], field: str, pending: Collection[str] = ()
) -> str | None:
    """Check one item without touching the sketch: form, entity types (declared local paths
    accepted), ``driven``/``value``, the expression (unless it names a ``pending`` parameter)
    and ``parameter_name``. Returns the item's ``parameter_name`` (or ``None``)."""
    kind = _type_of(item, field)
    driven = _driven(item, field)
    if kind == "distance":
        form, specs = _distance_form(item, field)
        _orientation(item, field)
        if form == "line":
            scope.check(specs[0], LINE_TYPES, f"{field}.line")
        else:
            scope.check(specs[0], POINT_TYPES, f"{field}.point1")
            scope.check(specs[1], POINT_TYPES, f"{field}.point2")
    else:
        for key, expected in _FIELDS[kind]:
            scope.check(_spec(item, key, field), expected, f"{field}.{key}")
    value = item.get("value")
    if value is not None and not driven:
        unit = value_unit(kind)
        expression = value_expression(value, unit, f"{field}.value")
        check_expression(scope, expression, unit, f"{field}.value", pending)
    _text_at(scope, item, field)
    name = item.get("parameter_name")
    if name is None:
        return None
    return check_parameter_name(scope, name, f"{field}.parameter_name", pending)


def validate_dimensions(
    scope: BatchScope, items: Sequence[Mapping[str, Any]], field: str = "dimensions"
) -> None:
    """``validate_dimension`` for every item in order; parameters renamed by earlier items
    are pending for later ones. Errors carry ``details.index``."""
    pending: set[str] = set()
    for index, item in enumerate(items):
        with indexed(index):
            name = validate_dimension(scope, item, f"{field}[{index}]", pending)
        if name is not None:
            pending.add(name)


# --- geometry helpers (sketch mm) ----------------------------------------------------------


def _xy(point: Any) -> list[float]:
    return describe.xy_mm(point.geometry)


def _mm(cm: float) -> float:
    return float(length_from_internal(cm))


def _line_xy(line: Any) -> tuple[list[float], list[float]]:
    return _xy(line.startSketchPoint), _xy(line.endSketchPoint)


def _text_point(
    scope: BatchScope,
    explicit: Any,
    kind: str,
    geometry: Callable[[], Mapping[str, Any]],
    field: str,
) -> Any:
    if explicit is not None:
        return explicit
    try:
        x, y = math2d.default_dimension_text_point(kind, geometry())
    except ValueError as exc:
        raise _invalid(f"{field}: {exc}", field, reason="degenerate_geometry") from None
    return space.to_point3d(Xy(x, y), LENGTH_UNIT, scope.sketch)


def _direction(line: Any) -> tuple[float, float]:
    a, b = line.startSketchPoint.geometry, line.endSketchPoint.geometry
    return (b.x - a.x, b.y - a.y)


def _check_parallel(line: Any, other: Any, field: str) -> None:
    d1, d2 = _direction(line), _direction(other)
    n1, n2 = math.hypot(*d1), math.hypot(*d2)
    if n1 == 0.0 or n2 == 0.0:
        raise _invalid(f"{field}: zero-length line", field, reason="degenerate_geometry")
    if abs(d1[0] * d2[1] - d1[1] * d2[0]) > PARALLEL_TOL * n1 * n2:
        raise _invalid(
            f"{field}: an offset dimension between two lines needs parallel lines "
            "(use angular, or a point as `entity`)",
            f"{field}.entity",
            reason="not_parallel",
        )


def _foot(line: Any, point: Any) -> tuple[float, float, float]:
    """Foot of ``point`` on the infinite ``line`` (sketch cm) and the distance (cm)."""
    a = line.startSketchPoint.geometry
    d = _direction(line)
    p = point.geometry
    norm2 = d[0] * d[0] + d[1] * d[1]
    t = ((p.x - a.x) * d[0] + (p.y - a.y) * d[1]) / norm2
    fx, fy = a.x + d[0] * t, a.y + d[1] * t
    return fx, fy, math.hypot(p.x - fx, p.y - fy)


# --- creation ------------------------------------------------------------------------------


def _orientation_value(name: str) -> Any:
    return getattr(adsk.fusion.DimensionOrientations, ORIENTATIONS[name])


def _create_distance(
    scope: BatchScope, item: Mapping[str, Any], field: str, text: Any, driving: bool
) -> tuple[Any, dict[str, Any]]:
    form, specs = _distance_form(item, field)
    orientation = _orientation(item, field)
    if form == "line":
        line = scope.entity(specs[0], LINE_TYPES, f"{field}.line")
        p1, p2 = line.startSketchPoint, line.endSketchPoint
    else:
        p1 = scope.entity(specs[0], POINT_TYPES, f"{field}.point1")
        p2 = scope.entity(specs[1], POINT_TYPES, f"{field}.point2")
        if same_entity(p1, p2):
            raise _invalid(f"{field}: point1 and point2 must differ", field, reason="same_entity")
    text = _text_point(
        scope,
        text,
        "distance",
        lambda: {"p1": _xy(p1), "p2": _xy(p2), "orientation": orientation},
        field,
    )
    dims = scope.sketch.sketchDimensions
    value = _orientation_value(orientation)
    dim = _call(field, lambda: dims.addDistanceDimension(p1, p2, value, text, driving))
    return dim, {}


def _offset_fallback(
    scope: BatchScope,
    journal: SketchJournal,
    line: Any,
    point: Any,
    text: Any,
    driving: bool,
    field: str,
    error: BaseException,
) -> tuple[Any, dict[str, Any]]:
    """Offset line-point without ``addOffsetDimension(line, point)``: construction line from
    the point to its foot on ``line``, perpendicular to it, end coincident with it, and a
    distance dimension of that helper (design Risks)."""
    fx, fy, distance = _foot(line, point)
    if distance <= 1e-9:
        raise _invalid(
            f"{field}: the point lies on the line (offset 0)", f"{field}.entity", reason="on_line"
        ) from error
    sketch = scope.sketch
    foot = space.to_point3d(Xy(_mm(fx), _mm(fy)), LENGTH_UNIT, sketch)
    lines = sketch.sketchCurves.sketchLines
    helper = journal.created(_call(field, lambda: lines.addByTwoPoints(point, foot)))
    _set(field, helper, "isConstruction", True)
    constraints = sketch.geometricConstraints
    perpendicular = journal.created(
        _call(field, lambda: constraints.addPerpendicular(helper, line))
    )
    end = helper.endSketchPoint
    coincident = journal.created(_call(field, lambda: constraints.addCoincident(end, line)))
    start = helper.startSketchPoint
    aligned = _orientation_value("aligned")
    dims = sketch.sketchDimensions
    dim = _call(field, lambda: dims.addDistanceDimension(start, end, aligned, text, driving))
    extra = {
        "fallback": FALLBACK_PERPENDICULAR,
        "helper_line": entity_ref(helper),
        "helper_constraints": [entity_ref(perpendicular), entity_ref(coincident)],
    }
    return dim, extra


def _create_offset(
    scope: BatchScope,
    journal: SketchJournal,
    item: Mapping[str, Any],
    field: str,
    text: Any,
    driving: bool,
) -> tuple[Any, dict[str, Any]]:
    line = scope.entity(_spec(item, "line", field), LINE_TYPES, f"{field}.line")
    other = scope.entity(_spec(item, "entity", field), OFFSET_ENTITY_TYPES, f"{field}.entity")
    if same_entity(line, other):
        raise _invalid(f"{field}: line and entity must differ", field, reason="same_entity")
    is_point = type_name(other) in POINT_TYPES
    if not is_point:
        _check_parallel(line, other, field)
    anchor = other if is_point else other.startSketchPoint
    text = _text_point(
        scope,
        text,
        "offset",
        lambda: {"line": list(_line_xy(line)), "point": _xy(anchor)},
        field,
    )
    dims = scope.sketch.sketchDimensions
    if not is_point:
        return _call(field, lambda: dims.addOffsetDimension(line, other, text, driving)), {}
    try:
        dim = dims.addOffsetDimension(line, other, text, driving)
    except BridgeError:
        raise
    except Exception as exc:
        # Nothing was created by the failed call: take the documented fallback.
        return _offset_fallback(scope, journal, line, other, text, driving, field, exc)
    return dim, {}


def _create_radius(
    scope: BatchScope, item: Mapping[str, Any], kind: str, field: str, text: Any, driving: bool
) -> tuple[Any, dict[str, Any]]:
    curve = scope.entity(_spec(item, "curve", field), CIRCULAR_TYPES, f"{field}.curve")

    def geometry() -> dict[str, Any]:
        data: dict[str, Any] = {
            "center": _xy(curve.centerSketchPoint),
            "radius": _mm(curve.radius),
        }
        if type_name(curve) == "SketchArc":
            arc = describe.curve_geometry(curve, "arc")
            data["angle"] = arc["start_angle"] + arc["sweep"] / 2.0
        return data

    text = _text_point(scope, text, "radial", geometry, field)
    dims = scope.sketch.sketchDimensions
    method = dims.addRadialDimension if kind == "radial" else dims.addDiameterDimension
    return _call(field, lambda: method(curve, text, driving)), {}


def _create_angular(
    scope: BatchScope, item: Mapping[str, Any], field: str, text: Any, driving: bool
) -> tuple[Any, dict[str, Any]]:
    line1 = scope.entity(_spec(item, "line1", field), LINE_TYPES, f"{field}.line1")
    line2 = scope.entity(_spec(item, "line2", field), LINE_TYPES, f"{field}.line2")
    if same_entity(line1, line2):
        raise _invalid(f"{field}: line1 and line2 must differ", field, reason="same_entity")
    text = _text_point(
        scope,
        text,
        "angular",
        lambda: {"line1": list(_line_xy(line1)), "line2": list(_line_xy(line2))},
        field,
    )
    dims = scope.sketch.sketchDimensions
    return _call(field, lambda: dims.addAngularDimension(line1, line2, text, driving)), {}


def _create_ellipse(
    scope: BatchScope, item: Mapping[str, Any], kind: str, field: str, text: Any, driving: bool
) -> tuple[Any, dict[str, Any]]:
    ellipse = scope.entity(_spec(item, "ellipse", field), ELLIPSE_TYPES, f"{field}.ellipse")
    major = kind == "ellipse_major"

    def geometry() -> dict[str, Any]:
        axis = ellipse.majorAxis
        angle = math.degrees(math.atan2(float(axis.y), float(axis.x)))
        radius = ellipse.majorAxisRadius if major else ellipse.minorAxisRadius
        return {
            "center": _xy(ellipse.centerSketchPoint),
            "radius": _mm(radius),
            "angle": angle if major else angle + 90.0,
        }

    text = _text_point(scope, text, "radial", geometry, field)
    dims = scope.sketch.sketchDimensions
    method = dims.addEllipseMajorRadiusDimension if major else dims.addEllipseMinorRadiusDimension
    return _call(field, lambda: method(ellipse, text, driving)), {}


def apply_dimension(
    scope: BatchScope, journal: SketchJournal, item: Mapping[str, Any], field: str
) -> dict[str, Any]:
    """Create one dimension (D10), journal it, set its expression and parameter name.

    The expression and the parameter name are checked before the dimension is created.
    Returns the D5 dimension description ``{ref, type, entities, value, unit, expression,
    parameter, driving}`` (+ ``fallback``, ``helper_line``, ``helper_constraints`` when the
    offset fallback was used)."""
    kind = _type_of(item, field)
    driven = _driven(item, field)
    expression: str | None = None
    value = item.get("value")
    if value is not None:
        unit = value_unit(kind)
        expression = value_expression(value, unit, f"{field}.value")
        check_expression(scope, expression, unit, f"{field}.value")
    name = item.get("parameter_name")
    if name is not None:
        check_parameter_name(scope, name, f"{field}.parameter_name")
    text = _text_at(scope, item, field)
    driving = not driven
    if kind == "distance":
        dim, extra = _create_distance(scope, item, field, text, driving)
    elif kind == "offset":
        dim, extra = _create_offset(scope, journal, item, field, text, driving)
    elif kind in ("radial", "diameter"):
        dim, extra = _create_radius(scope, item, kind, field, text, driving)
    elif kind == "angular":
        dim, extra = _create_angular(scope, item, field, text, driving)
    else:
        dim, extra = _create_ellipse(scope, item, kind, field, text, driving)
    journal.created(dim)
    if expression is not None:
        _set(f"{field}.value", dim.parameter, "expression", expression)
    if name is not None:
        _set(f"{field}.parameter_name", dim.parameter, "name", name)
    return {**describe.describe_dimension(dim), **extra}


def apply_dimensions(
    scope: BatchScope,
    journal: SketchJournal,
    items: Sequence[Mapping[str, Any]],
    field: str = "dimensions",
    validate: bool = True,
) -> list[dict[str, Any]]:
    """Validate every item (unless the caller already did), then apply them in order (a
    ``parameter_name`` is usable by later items); errors carry ``details.index``."""
    if validate:
        validate_dimensions(scope, items, field)
    results: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        with indexed(index):
            results.append(apply_dimension(scope, journal, item, f"{field}[{index}]"))
    return results


# --- changing existing dimensions ----------------------------------------------------------


def dimension_unit(dimension: Any) -> str:
    """``deg`` for an angle parameter (or an angular dimension), else ``mm``."""
    parameter = _get(dimension, "parameter")
    unit = _get(parameter, "unit")
    if isinstance(unit, str) and classify_unit(unit) == "angle":
        return ANGLE_UNIT
    if describe.dimension_type(dimension) == "angular":
        return ANGLE_UNIT
    return LENGTH_UNIT


def resolve_dimension(scope: BatchScope, spec: Any, field: str = "dimension") -> Any:
    """A dimension ref of this sketch (any D5 dimension class)."""
    return scope.entity(spec, DIMENSION_CLASSES, field)


def _plan_update(
    scope: BatchScope,
    dimension: Any,
    value: Any,
    driven: bool | None,
    parameter_name: str | None,
    field: str,
    pending: Collection[str],
) -> tuple[bool, str | None]:
    """Checks of ``set_dimension``; returns ``(will_drive, expression)``."""
    if value is None and driven is None and parameter_name is None:
        raise _invalid(f"{field}: give `value`, `driven` or `parameter_name`", field)
    if driven is not None and not isinstance(driven, bool):
        raise _invalid(f"{field}.driven must be a boolean", f"{field}.driven", value=driven)
    if driven and value is not None:
        raise _invalid(
            f"{field}: a driven dimension measures the geometry: remove `value`",
            f"{field}.value",
            reason="driven_with_value",
        )
    driving_now = bool(dimension.isDriving)
    will_drive = driving_now if driven is None else not driven
    expression: str | None = None
    if value is not None:
        if not will_drive:
            raise _invalid(
                f"{field}: the dimension is driven (a reference); pass `driven: false` to "
                "make it driving and set its value",
                f"{field}.value",
                reason="driven_dimension",
            )
        unit = dimension_unit(dimension)
        expression = value_expression(value, unit, f"{field}.value")
        check_expression(scope, expression, unit, f"{field}.value", pending)
    if parameter_name is not None:
        check_parameter_name(
            scope,
            parameter_name,
            f"{field}.parameter_name",
            pending,
            current=_get(dimension, "parameter"),
        )
    return will_drive, expression


def validate_update(
    scope: BatchScope, update: Mapping[str, Any], field: str, pending: Collection[str] = ()
) -> Any:
    """Check one ``DimensionUpdate`` (``{dimension, value?, driven?, parameter_name?}``)
    without changing anything; returns the resolved dimension."""
    if not isinstance(update, Mapping):
        raise _invalid(f"{field} must be an object", field)
    dimension = resolve_dimension(scope, update.get("dimension"), f"{field}.dimension")
    _plan_update(
        scope,
        dimension,
        update.get("value"),
        update.get("driven"),
        update.get("parameter_name"),
        field,
        pending,
    )
    return dimension


def set_dimension(
    scope: BatchScope,
    journal: SketchJournal,
    dimension: Any,
    value: Any = None,
    driven: bool | None = None,
    parameter_name: str | None = None,
    field: str = "dimension",
) -> dict[str, Any]:
    """Change the value/expression, driving state and/or parameter name of an existing
    dimension; the previous expression, ``isDriving`` and name are journaled as undo.

    ``driven = False`` makes a reference dimension driving (a ``value`` may then be set),
    ``driven = True`` makes it a reference (no ``value``). Returns the D5 description."""
    will_drive, expression = _plan_update(
        scope, dimension, value, driven, parameter_name, field, ()
    )
    parameter = dimension.parameter
    driving_now = bool(dimension.isDriving)
    old_expression = parameter.expression
    old_name = parameter.name
    if driving_now and (expression is not None or will_drive != driving_now):
        # Registered first, so it runs last: after isDriving is back to True.
        def restore_expression() -> None:
            if parameter.expression != old_expression:
                parameter.expression = old_expression

        journal.on_undo(restore_expression, f"expression of {old_name}")
    if will_drive != driving_now:
        _set(f"{field}.driven", dimension, "isDriving", will_drive)

        def restore_driving() -> None:
            dimension.isDriving = driving_now

        journal.on_undo(restore_driving, f"isDriving of {old_name}")
    if expression is not None:
        _set(f"{field}.value", parameter, "expression", expression)
    if parameter_name is not None and parameter_name != old_name:
        _set(f"{field}.parameter_name", parameter, "name", parameter_name)

        def restore_name() -> None:
            parameter.name = old_name

        journal.on_undo(restore_name, f"name of {parameter_name}")
    return describe.describe_dimension(dimension)


def validate_updates(
    scope: BatchScope, updates: Iterable[Mapping[str, Any]], field: str = "updates"
) -> list[Any]:
    """``validate_update`` for every update (names set by earlier updates are pending for
    later ones); returns the resolved dimensions. Errors carry ``details.index``."""
    pending: set[str] = set()
    dimensions: list[Any] = []
    for index, update in enumerate(updates):
        with indexed(index):
            dimensions.append(validate_update(scope, update, f"{field}[{index}]", pending))
        name = update.get("parameter_name")
        if isinstance(name, str):
            pending.add(name)
    return dimensions


def apply_updates(
    scope: BatchScope,
    journal: SketchJournal,
    updates: Sequence[Mapping[str, Any]],
    field: str = "updates",
) -> list[dict[str, Any]]:
    """Validate all updates, then ``set_dimension`` each in order (undo journaled)."""
    dimensions = validate_updates(scope, updates, field)
    results: list[dict[str, Any]] = []
    for index, (update, dimension) in enumerate(zip(updates, dimensions, strict=True)):
        with indexed(index):
            results.append(
                set_dimension(
                    scope,
                    journal,
                    dimension,
                    update.get("value"),
                    update.get("driven"),
                    update.get("parameter_name"),
                    f"{field}[{index}]",
                )
            )
    return results

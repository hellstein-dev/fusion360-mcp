"""Editing existing sketch entities (c05 D11): construction, delete, transform, offset, trim,
extend, fillet, mirror, pattern, project.

Every function takes the call's ``BatchScope`` (entity/point resolution against the sketch,
D4) and its ``SketchJournal`` (INV-5, D6), validates everything first (specs, entity types,
expressions with ``isValidExpression``) and only then calls the Fusion API. Lengths given as
numbers are in the scope's units (mm by default), angles in degrees; strings are Fusion
expressions (D2). Results use c02 refs (``{"ref", "type", "name"?}``) and sketch-space mm.

Irreversible Fusion operations (D6): ``SketchCurve.trim``/``extend`` and
``SketchArcs.addFillet`` replace or cut the source curves, so they handle one element per
call; after the API call only safe steps follow. ``delete`` is the single non-atomic sketch
operation (north-star INV-5 exception): all refs are validated before the first deletion.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from functools import partial
from typing import Any

import adsk.core
import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.units import length_from_internal, length_to_internal, number_expression

from ..core.dispatcher import to_bridge_error
from ..core.refs import type_name
from . import describe, math2d
from .journal import SketchJournal
from .resolve import (
    CURVE_TYPES,
    LINE_TYPES,
    POINT_TYPES,
    TEXT_TYPES,
    BatchScope,
    entity_ref,
    same_entity,
)

LENGTH_UNIT = "mm"
ANGLE_UNIT = "deg"
# trim/extend: the pick point must be this close to the curve (D11).
PICK_DISTANCE_MM = 5.0
# A point closer than this to the mirror axis is reused instead of mirrored (D11).
ON_AXIS_TOL_CM = 1e-6
STROKE_TOLERANCE_CM = 0.001
# Default fillet pick points: this far (fraction of the parameter range) from the corner end.
FILLET_PICK_FRACTION = 0.1
ZERO_TOL = 1e-12

TRANSFORM_TYPES: tuple[str, ...] = (*POINT_TYPES, *CURVE_TYPES, *TEXT_TYPES)
CLOSED_TYPES: tuple[str, ...] = ("SketchCircle", "SketchEllipse")
OPEN_CURVE_TYPES: tuple[str, ...] = tuple(t for t in CURVE_TYPES if t not in CLOSED_TYPES)
MIRROR_TYPES: tuple[str, ...] = (
    "SketchPoint",
    "SketchLine",
    "SketchArc",
    "SketchCircle",
    "SketchFittedSpline",
)
PATTERN_TYPES: tuple[str, ...] = (*POINT_TYPES, *CURVE_TYPES)
PROJECT_MODES: tuple[str, ...] = ("project", "include", "intersect", "cut_edges")
# ``delete`` order (D11): dimensions -> constraints -> texts -> curves -> points.
DELETE_ORDER: tuple[str, ...] = ("dimension", "constraint", "text", "curve", "point")

XY = tuple[float, float]


# --- small helpers ---------------------------------------------------------------------------


def _invalid(message: str, field: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, {"field": field, **details})


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Optional attribute (Fusion raises for properties that do not apply)."""
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _is_valid(entity: Any) -> bool:
    try:
        return bool(getattr(entity, "isValid", True))
    except Exception:
        return False


def _call(field: str, fn: Callable[[], Any]) -> Any:
    """One Fusion API call; its failure is ``FUSION_API_ERROR`` naming ``field``."""
    try:
        return fn()
    except BridgeError:
        raise
    except Exception as exc:
        error = to_bridge_error(exc)
        error.details.setdefault("field", field)
        raise error from exc


def _setter(obj: Any, attr: str, value: Any) -> Callable[[], None]:
    def restore() -> None:
        setattr(obj, attr, value)

    return restore


def _collection(entities: Iterable[Any]) -> Any:
    collection = adsk.core.ObjectCollection.create()
    for entity in entities:
        collection.add(entity)
    return collection


def _xy(p3d: Any) -> XY:
    """Sketch-space ``Point3D`` -> ``(x, y)`` cm."""
    return (float(p3d.x), float(p3d.y))


def _point3d(xy: Sequence[float]) -> Any:
    return adsk.core.Point3D.create(float(xy[0]), float(xy[1]), 0.0)


def _mm(cm: float) -> float:
    return length_from_internal(cm)


def _cm_from_mm(mm: float) -> float:
    return float(length_to_internal(mm, LENGTH_UNIT))


def _refs(entities: Iterable[Any]) -> list[dict[str, Any]]:
    return [entity_ref(e) for e in entities]


def _unique(entities: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    for entity in entities:
        if not any(same_entity(entity, seen) for seen in out):
            out.append(entity)
    return out


def _created_items(journal: SketchJournal, result: Any) -> list[Any]:
    """Journal every entity Fusion returned (``ObjectCollection``/list/one/``None``)."""
    return journal.created_all(e for e in describe.items(result) if e is not None)


def resolve_entities(
    scope: BatchScope, specs: Sequence[Any], expected: Sequence[str] | None, field: str
) -> list[Any]:
    """Resolve a list of EntitySpecs of this sketch (duplicates dropped, order kept)."""
    if not specs:
        raise _invalid(f"{field} must not be empty", field)
    resolved = [
        scope.entity(spec, expected, f"{field}[{index}]") for index, spec in enumerate(specs)
    ]
    return _unique(resolved)


# --- expressions (D2) ------------------------------------------------------------------------


def _units_manager(scope: BatchScope) -> Any:
    design = scope.ctx.design
    manager = _get(design, "unitsManager")
    return manager if manager is not None else design.fusionUnitsManager


def expression(scope: BatchScope, value: Any, kind: str, field: str) -> str:
    """``Length``/``Angle`` -> Fusion expression, checked with ``isValidExpression``.

    ``kind`` is ``"length"`` (a number is in the scope's units) or ``"angle"`` (degrees)."""
    unit = LENGTH_UNIT if kind == "length" else ANGLE_UNIT
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise _invalid(f"{field} must not be an empty expression", field)
    elif isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        number_unit = scope.units.units if kind == "length" else ANGLE_UNIT
        text = number_expression(float(value), number_unit)
    else:
        raise _invalid(
            f"{field} must be a number ({unit}) or a Fusion expression string",
            field,
            value=value,
        )
    try:
        valid = bool(_units_manager(scope).isValidExpression(text, unit))
    except Exception:
        valid = False
    if not valid:
        raise _invalid(
            f"{field}: {text!r} is not a valid expression in {unit} (unknown parameter or "
            "wrong unit kind)",
            field,
            reason="invalid_expression",
            expression=text,
            unit=unit,
        )
    return text


def evaluate(scope: BatchScope, text: str, kind: str, field: str) -> float:
    """Internal value (cm or radians) of a checked expression."""
    unit = LENGTH_UNIT if kind == "length" else ANGLE_UNIT
    try:
        return float(_units_manager(scope).evaluateExpression(text, unit))
    except Exception as exc:
        raise _invalid(
            f"{field}: cannot evaluate {text!r} in {unit}: {exc}",
            field,
            reason="invalid_expression",
            expression=text,
            unit=unit,
        ) from exc


# --- set_construction ------------------------------------------------------------------------


def set_construction(
    scope: BatchScope, journal: SketchJournal, curves: Sequence[Any], construction: bool
) -> dict[str, Any]:
    """``isConstruction = construction`` on every curve; old values are journaled."""
    items = resolve_entities(scope, curves, CURVE_TYPES, "curves")
    for curve in items:
        before = bool(curve.isConstruction)
        if before == construction:
            continue
        _call("curves", _setter(curve, "isConstruction", construction))
        journal.on_undo(_setter(curve, "isConstruction", before), "isConstruction")
    return {"curves": [describe.describe_curve(c) for c in items]}


# --- delete ----------------------------------------------------------------------------------


def _delete_kind(entity: Any) -> str | None:
    kind = type_name(entity)
    if kind in POINT_TYPES:
        return "point"
    if kind in CURVE_TYPES:
        return "curve"
    if kind in TEXT_TYPES:
        return "text"
    if kind.endswith("Constraint"):
        return "constraint"
    if kind.startswith("Sketch") and kind.endswith("Dimension"):
        return "dimension"
    return None


def _related_tokens(entity: Any, kind: str) -> set[str]:
    """Tokens of the entities a constraint/dimension is attached to."""
    try:
        data = (
            describe.describe_constraint(entity)
            if kind == "constraint"
            else describe.describe_dimension(entity)
        )
    except Exception:
        return set()
    return {ref["ref"] for ref in data.get("entities", []) if isinstance(ref, dict)}


def delete(scope: BatchScope, entities: Sequence[Any]) -> dict[str, Any]:
    """Delete sketch entities: validate every ref first, then delete dimensions, constraints,
    texts, curves, points. A constraint/dimension attached to another entity of the batch is
    deleted last: Fusion usually removes it with that entity (it is then ``cascaded``).

    Not atomic (INV-5 exception): a Fusion failure mid-batch is ``FUSION_API_ERROR`` with
    ``details.deleted``/``details.cascaded`` of what is already gone."""
    if not entities:
        raise _invalid("entities must not be empty", "entities")
    planned: list[tuple[Any, dict[str, Any], str]] = []
    for index, spec in enumerate(entities):
        field = f"entities[{index}]"
        entity = scope.entity(spec, None, field)
        if same_entity(entity, scope.sketch.originPoint):
            raise _invalid(f"{field}: the sketch origin point cannot be deleted", field)
        kind = _delete_kind(entity)
        if kind is None:
            raise _invalid(
                f"{field} is a {type_name(entity)}: only sketch curves, points, constraints, "
                "dimensions and texts can be deleted",
                field,
                reason="unsupported",
                got=type_name(entity),
            )
        if _get(entity, "isDeletable", True) is False:
            raise _invalid(
                f"{field}: Fusion does not allow deleting this {type_name(entity)}",
                field,
                reason="not_deletable",
            )
        if not any(same_entity(entity, e) for e, _, _ in planned):
            planned.append((entity, entity_ref(entity), kind))

    tokens = {ref["ref"] for _, ref, _ in planned}
    first: list[tuple[Any, dict[str, Any]]] = []
    last: list[tuple[Any, dict[str, Any]]] = []
    for kind in DELETE_ORDER:
        for entity, ref, entity_kind in planned:
            if entity_kind != kind:
                continue
            dependent = kind in ("constraint", "dimension") and bool(
                _related_tokens(entity, kind) & (tokens - {ref["ref"]})
            )
            (last if dependent else first).append((entity, ref))

    deleted: list[dict[str, Any]] = []
    cascaded: list[dict[str, Any]] = []
    for entity, ref in first + last:
        if not _is_valid(entity):
            cascaded.append(ref)
            continue
        try:
            ok = entity.deleteMe()
            if ok is False:
                raise RuntimeError("deleteMe returned False")
        except Exception as exc:
            error = to_bridge_error(exc)
            error.details.update(
                {"failed": ref, "deleted": list(deleted), "cascaded": list(cascaded)}
            )
            raise error from exc
        deleted.append(ref)
    return {"deleted": deleted, "cascaded": cascaded}


# --- transform -------------------------------------------------------------------------------


def transform_matrix(rotate_rad: float, about: Any, dx_cm: float, dy_cm: float) -> Any:
    """``Matrix3D``: rotation by ``rotate_rad`` about ``about`` (sketch Z), then translation."""
    matrix = adsk.core.Matrix3D.create()
    if abs(rotate_rad) > ZERO_TOL:
        matrix.setToRotation(
            rotate_rad,
            adsk.core.Vector3D.create(0.0, 0.0, 1.0),
            adsk.core.Point3D.create(float(about.x), float(about.y), 0.0),
        )
    if abs(dx_cm) > ZERO_TOL or abs(dy_cm) > ZERO_TOL:
        shift = adsk.core.Matrix3D.create()
        shift.translation = adsk.core.Vector3D.create(dx_cm, dy_cm, 0.0)
        matrix.transformBy(shift)
    return matrix


def rotation_angle(scope: BatchScope, value: Any, field: str) -> float:
    """``Angle`` -> radians (number = degrees, string = expression)."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise _invalid(f"{field} must be finite", field, value=value)
        return float(scope.units.angle(float(value)))
    return evaluate(scope, expression(scope, value, "angle", field), "angle", field)


def transform(
    scope: BatchScope,
    journal: SketchJournal,
    entities: Sequence[Any],
    translate: XY,
    rotate_rad: float,
    about: Any,
    copy: bool,
) -> dict[str, Any]:
    """Rotate about ``about`` then translate (cm) with ``Sketch.move`` (undo: the inverse
    matrix) or ``Sketch.copy`` (created entities journaled)."""
    sketch = scope.sketch
    items = resolve_entities(scope, entities, TRANSFORM_TYPES, "entities")
    for index, entity in enumerate(items):
        if same_entity(entity, sketch.originPoint):
            raise _invalid(
                "the sketch origin point cannot be moved or copied", f"entities[{index}]"
            )
    matrix = transform_matrix(rotate_rad, about, translate[0], translate[1])
    if copy:
        result = _call("entities", lambda: sketch.copy(_collection(items), matrix, sketch))
        created = _created_items(journal, result)
        if not created:
            raise BridgeError(ErrorCode.FUSION_API_ERROR, "Sketch.copy created nothing")
        return {"created": [describe.describe_entity(e) for e in created]}
    inverse = matrix.copy()
    if not inverse.invert():
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "the transform matrix is singular")
    ok = _call("entities", lambda: sketch.move(_collection(items), matrix))
    if ok is False:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "Sketch.move failed")

    def move_back() -> None:
        if sketch.move(_collection(items), inverse) is False:
            raise RuntimeError("Sketch.move returned False")

    journal.on_undo(move_back, "Sketch.move back")
    return {"entities": [describe.describe_entity(e) for e in items]}


# --- offset ----------------------------------------------------------------------------------


def connected_chain(sketch: Any, curves: Sequence[Any]) -> list[Any]:
    """``curves`` extended by ``Sketch.findConnectedCurves`` (no duplicates, order kept)."""
    found: list[Any] = []
    for curve in curves:
        chain = _call("curves", partial(sketch.findConnectedCurves, curve))
        found.extend(describe.items(chain) or [curve])
    return _unique(found)


def offset(
    scope: BatchScope,
    journal: SketchJournal,
    curves: Sequence[Any],
    distance: Any,
    side_point: Any,
    chain: bool,
) -> dict[str, Any]:
    """Offset ``curves`` (their chains with ``chain``) toward ``side_point``: parametric
    (``createOffsetInput``/``addOffset2``, a dimension holds the expression) when the API has
    it, else ``Sketch.offset`` with the evaluated distance (cm), ``parametric: false``."""
    sketch = scope.sketch
    items = resolve_entities(scope, curves, CURVE_TYPES, "curves")
    text = expression(scope, distance, "length", "distance")
    distance_cm = evaluate(scope, text, "length", "distance")
    if abs(distance_cm) <= ZERO_TOL:
        raise _invalid("distance must not be zero", "distance", reason="zero")
    side = scope.position(side_point, "side_point")
    if chain:
        items = connected_chain(sketch, items)
    constraints = sketch.geometricConstraints
    if hasattr(constraints, "createOffsetInput") and hasattr(constraints, "addOffset2"):
        offset_input = _call(
            "curves",
            # A list: the live API takes std::vector<SketchCurve>, not an ObjectCollection.
            lambda: constraints.createOffsetInput(
                list(items), adsk.core.ValueInput.createByString(text)
            ),
        )
        _call("side_point", _setter(offset_input, "dirPoint", side))
        constraint = journal.created(_call("curves", lambda: constraints.addOffset2(offset_input)))
        created = _created_items(journal, _get(constraint, "childCurves"))
        result: dict[str, Any] = {"created": _refs(created), "parametric": True}
        dimension = _get(constraint, "dimension")
        if dimension is not None:
            result["dimension"] = describe.describe_dimension(dimension)
        return result
    created = _created_items(
        journal, _call("curves", lambda: sketch.offset(_collection(items), side, distance_cm))
    )
    return {"created": _refs(created), "parametric": False}


# --- trim / extend ---------------------------------------------------------------------------


def _strokes_mm(curve: Any) -> list[XY]:
    evaluator = curve.geometry.evaluator
    ok, start, end = evaluator.getParameterExtents()
    if not ok:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "cannot evaluate the curve")
    ok, points = evaluator.getStrokes(start, end, STROKE_TOLERANCE_CM)
    if not ok or not points:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "cannot evaluate the curve")
    return [(_mm(p.x), _mm(p.y)) for p in points]


def distance_to_curve_mm(curve: Any, point: Any) -> float:
    """Distance (mm) from a sketch-space ``Point3D`` to a sketch curve (its strokes)."""
    return math2d.distance_to_polyline((_mm(point.x), _mm(point.y)), _strokes_mm(curve))


def _pick(scope: BatchScope, curve_spec: Any, point_spec: Any, expected: Sequence[str]) -> Any:
    curve = scope.entity(curve_spec, expected, "curve")
    point = scope.position(point_spec, "point")
    distance = distance_to_curve_mm(curve, point)
    if distance > PICK_DISTANCE_MM:
        raise _invalid(
            f"point is {distance:.3f} mm from the curve; give a point within "
            f"{PICK_DISTANCE_MM:g} mm of the segment/end to act on",
            "point",
            reason="point_too_far",
            distance_mm=round(distance, 6),
            max_mm=PICK_DISTANCE_MM,
        )
    return curve, point


def _replacement(curve: Any, before: dict[str, Any], result: Any) -> dict[str, Any]:
    pieces = [e for e in describe.items(result) if e is not None]
    if _is_valid(curve):
        if not any(same_entity(curve, piece) for piece in pieces):
            pieces.insert(0, curve)
        return {"created": _refs(_unique(pieces)), "deleted": []}
    return {"created": _refs(_unique(pieces)), "deleted": [before]}


def trim(scope: BatchScope, curve_spec: Any, point_spec: Any) -> dict[str, Any]:
    """``SketchCurve.trim(point, True)`` of ONE curve (D6): ``created`` = the remaining
    pieces, ``deleted`` = the original ref if Fusion replaced it."""
    curve, point = _pick(scope, curve_spec, point_spec, CURVE_TYPES)
    before = entity_ref(curve)
    result = _call("curve", lambda: curve.trim(point, True))
    return _replacement(curve, before, result)


def extend(scope: BatchScope, curve_spec: Any, point_spec: Any) -> dict[str, Any]:
    """``SketchCurve.extend(point, True)`` of ONE open curve (D6); nothing to extend to is
    ``INVALID_PARAMS`` (the sketch is unchanged)."""
    curve, point = _pick(scope, curve_spec, point_spec, OPEN_CURVE_TYPES)
    before = entity_ref(curve)
    result = _call("curve", lambda: curve.extend(point, True))
    if _is_valid(curve) and not describe.items(result):
        raise _invalid(
            "nothing to extend to: no curve crosses the extension of that end",
            "point",
            reason="no_boundary",
        )
    return _replacement(curve, before, result)


# --- fillet ----------------------------------------------------------------------------------


def _end_points(curve: Any) -> list[Any]:
    return [
        p for p in (_get(curve, "startSketchPoint"), _get(curve, "endSketchPoint")) if p is not None
    ]


def _distance_cm(a: Any, b: Any) -> float:
    return math.hypot(float(a.x) - float(b.x), float(a.y) - float(b.y))


def corner_ends(curve1: Any, curve2: Any) -> tuple[Any, Any]:
    """The end points of the two curves forming the corner: a shared point, else the
    nearest pair of ends."""
    best: tuple[float, Any, Any] | None = None
    for p1 in _end_points(curve1):
        for p2 in _end_points(curve2):
            d = 0.0 if same_entity(p1, p2) else _distance_cm(p1.geometry, p2.geometry)
            if best is None or d < best[0]:
                best = (d, p1, p2)
    if best is None:
        raise _invalid(
            "the curves have no end points: give point1 and point2", "curve1", reason="no_ends"
        )
    return best[1], best[2]


def near_end_point(curve: Any, end: Any) -> Any:
    """A point on ``curve`` near its end ``end`` (a pick point for ``addFillet``)."""
    evaluator = curve.geometry.evaluator
    ok, t0, t1 = evaluator.getParameterExtents()
    if not ok:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "cannot evaluate the curve")
    _, p0 = evaluator.getPointAtParameter(t0)
    _, p1 = evaluator.getPointAtParameter(t1)
    target = end.geometry
    step = (t1 - t0) * FILLET_PICK_FRACTION
    t = t0 + step if _distance_cm(p0, target) <= _distance_cm(p1, target) else t1 - step
    ok, point = evaluator.getPointAtParameter(t)
    if not ok:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "cannot evaluate the curve")
    return adsk.core.Point3D.create(float(point.x), float(point.y), 0.0)


def _radial_dimension(scope: BatchScope, arc: Any, text: str) -> Any:
    """Driving radial dimension of ``arc`` with expression ``text``; atomic on its own (a
    failure deletes the half-made dimension and raises)."""
    geometry = describe.curve_geometry(arc, "arc")
    angle = geometry["start_angle"] + geometry["sweep"] / 2.0
    tx, ty = math2d.default_dimension_text_point(
        "radial", {"center": geometry["center"], "radius": geometry["radius"], "angle": angle}
    )
    at = _point3d((_cm_from_mm(tx), _cm_from_mm(ty)))
    dims = scope.sketch.sketchDimensions
    with SketchJournal(scope.sketch) as inner:
        dim = inner.created(_call("dimension", lambda: dims.addRadialDimension(arc, at, True)))
        _call("dimension", _setter(dim.parameter, "expression", text))
    return dim


def fillet(
    scope: BatchScope,
    journal: SketchJournal,
    curve1: Any,
    curve2: Any,
    radius: Any,
    point1: Any,
    point2: Any,
    dimension: bool,
) -> dict[str, Any]:
    """``sketchArcs.addFillet`` at ONE corner (D6), then (``dimension``) a radial dimension
    carrying the radius expression. The fillet itself cannot be undone; if the dimension
    fails it is removed and reported in ``warnings`` (the fillet stays)."""
    c1 = scope.entity(curve1, OPEN_CURVE_TYPES, "curve1")
    c2 = scope.entity(curve2, OPEN_CURVE_TYPES, "curve2")
    if same_entity(c1, c2):
        raise _invalid("curve1 and curve2 must differ", "curve2", reason="same_entity")
    text = expression(scope, radius, "length", "radius")
    radius_cm = evaluate(scope, text, "length", "radius")
    if radius_cm <= ZERO_TOL:
        raise _invalid("radius must be positive", "radius", reason="not_positive")
    pick1 = scope.position(point1, "point1") if point1 is not None else None
    pick2 = scope.position(point2, "point2") if point2 is not None else None
    if pick1 is None or pick2 is None:
        end1, end2 = corner_ends(c1, c2)
        pick1 = pick1 if pick1 is not None else near_end_point(c1, end1)
        pick2 = pick2 if pick2 is not None else near_end_point(c2, end2)
    arcs = scope.sketch.sketchCurves.sketchArcs
    arc = _call("curve1", lambda: arcs.addFillet(c1, pick1, c2, pick2, radius_cm))
    if arc is None:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "Fusion did not create the fillet")
    result: dict[str, Any] = {"arc": entity_ref(arc)}
    if dimension:
        try:
            dim = _radial_dimension(scope, arc, text)
        except BridgeError as exc:
            # The dimension rolled itself back; the fillet cannot be undone (D6).
            result["warnings"] = [
                {
                    "code": "fillet_dimension_failed",
                    "message": f"the fillet was created but its radial dimension failed: "
                    f"{exc.message}",
                }
            ]
        else:
            journal.created(dim)
            result["dimension"] = describe.describe_dimension(dim)
    return result


# --- mirror ----------------------------------------------------------------------------------


class _Mirror:
    """Point map ``source SketchPoint -> mirrored SketchPoint`` of one mirror call."""

    def __init__(self, axis: Any) -> None:
        self.axis = axis
        self.a = _xy(axis.startSketchPoint.geometry)
        self.b = _xy(axis.endSketchPoint.geometry)
        self.map: dict[str, Any] = {}

    @staticmethod
    def key(point: Any) -> str:
        token = _get(point, "entityToken")
        return token if isinstance(token, str) and token else str(id(point))

    def reflect(self, p3d: Any) -> XY:
        x, y = math2d.reflect(_xy(p3d), self.a, self.b)
        return (x, y)

    def on_axis(self, point: Any) -> bool:
        p = _xy(point.geometry)
        q = self.reflect(point.geometry)
        return math.hypot(p[0] - q[0], p[1] - q[1]) / 2.0 < ON_AXIS_TOL_CM

    def spec(self, point: Any) -> Any:
        """What to pass to Fusion for the image of ``point``: the mapped ``SketchPoint`` (shared
        points stay shared), the point itself on the axis, else a ``Point3D``."""
        key = self.key(point)
        if key in self.map:
            return self.map[key]
        if self.on_axis(point):
            self.map[key] = point
            return point
        return _point3d(self.reflect(point.geometry))

    def bind(self, source: Any, candidates: Sequence[Any]) -> None:
        """Map ``source`` to the candidate point nearest to its mirror image."""
        key = self.key(source)
        if key in self.map:
            return
        target = self.reflect(source.geometry)
        self.map[key] = min(
            candidates,
            key=lambda c: math.hypot(c.geometry.x - target[0], c.geometry.y - target[1]),
        )

    def image(self, point: Any) -> Any:
        return self.map[self.key(point)]


def _arc_mid(arc: Any) -> Any:
    evaluator = arc.geometry.evaluator
    ok, t0, t1 = evaluator.getParameterExtents()
    ok2, mid = evaluator.getPointAtParameter((t0 + t1) / 2.0) if ok else (False, None)
    if not ok2 or mid is None:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "cannot evaluate the arc")
    return mid


def mirror(
    scope: BatchScope,
    journal: SketchJournal,
    entities: Sequence[Any],
    axis_spec: Any,
    constrain: bool,
) -> dict[str, Any]:
    """Mirror points, lines, arcs, circles and fitted splines about a line (no Fusion API:
    built from reflected points). Shared points stay shared, points on the axis are reused;
    ``constrain`` adds symmetry for every mirrored point pair (and equal for arcs/circles)."""
    sketch = scope.sketch
    axis = scope.entity(axis_spec, LINE_TYPES, "axis")
    sources: list[Any] = []
    for index, spec in enumerate(entities):
        field = f"entities[{index}]"
        entity = scope.entity(spec, None, field)
        kind = type_name(entity)
        if kind not in MIRROR_TYPES:
            raise _invalid(
                f"{field}: cannot mirror a {kind}; supported: {', '.join(MIRROR_TYPES)}",
                field,
                reason="unsupported",
                got=kind,
                expected=list(MIRROR_TYPES),
            )
        if same_entity(entity, axis):
            raise _invalid(f"{field} is the mirror axis itself", field, reason="same_entity")
        sources.append(entity)
    sources = _unique(sources)
    if not sources:
        raise _invalid("entities must not be empty", "entities")

    m = _Mirror(axis)
    curves = sketch.sketchCurves
    constraints = sketch.geometricConstraints
    mapping: dict[str, dict[str, Any]] = {}
    symmetric: list[Any] = []
    equal: list[tuple[Any, Any]] = []
    out: list[dict[str, Any]] = []

    def keep(points: Iterable[Any]) -> None:
        for point in points:
            if not any(same_entity(point, p) for p in symmetric):
                symmetric.append(point)

    def construction(source: Any, new: Any) -> None:
        if bool(_get(source, "isConstruction", False)):
            _call("entities", _setter(new, "isConstruction", True))

    for source in sources:
        kind = type_name(source)
        new: Any
        if kind == "SketchPoint":
            target = m.spec(source)
            if type_name(target) == "SketchPoint":
                new = target
            else:
                new = journal.created(_call("entities", partial(sketch.sketchPoints.add, target)))
                m.bind(source, [new])
            keep([source])
        elif kind == "SketchLine":
            start, end = source.startSketchPoint, source.endSketchPoint
            if m.on_axis(start) and m.on_axis(end):
                new = source  # lies on the axis: its own mirror image
            else:
                s, e = m.spec(start), m.spec(end)
                new = journal.created(
                    _call("entities", partial(curves.sketchLines.addByTwoPoints, s, e))
                )
                ends = [new.startSketchPoint, new.endSketchPoint]
                m.bind(start, ends)
                m.bind(end, ends)
                construction(source, new)
                keep([start, end])
        elif kind == "SketchCircle":
            center = source.centerSketchPoint
            c = m.spec(center)
            radius = float(source.radius)
            new = journal.created(
                _call("entities", partial(curves.sketchCircles.addByCenterRadius, c, radius))
            )
            m.bind(center, [new.centerSketchPoint])
            construction(source, new)
            keep([center])
            equal.append((source, new))
        elif kind == "SketchArc":
            start, end, center = (
                source.startSketchPoint,
                source.endSketchPoint,
                source.centerSketchPoint,
            )
            s, e = m.spec(start), m.spec(end)
            mid = _point3d(m.reflect(_arc_mid(source)))
            new = journal.created(
                _call("entities", partial(curves.sketchArcs.addByThreePoints, s, mid, e))
            )
            ends = [new.startSketchPoint, new.endSketchPoint]
            m.bind(start, ends)
            m.bind(end, ends)
            new_center = new.centerSketchPoint
            known = m.map.get(m.key(center))
            if known is None and m.on_axis(center):
                known = center
            if known is None:
                m.map[m.key(center)] = new_center
            elif not same_entity(known, new_center):
                # Keep shared centres shared (e.g. an arc concentric with a mirrored circle).
                journal.created(
                    _call("entities", partial(constraints.addCoincident, new_center, known))
                )
            construction(source, new)
            keep([start, end])
            equal.append((source, new))
        else:  # SketchFittedSpline
            fit = describe.items(source.fitPoints)
            targets = [m.spec(p) for p in fit]
            new = journal.created(
                _call("entities", partial(curves.sketchFittedSplines.add, _collection(targets)))
            )
            if bool(_get(source, "isClosed", False)):
                _call("entities", _setter(new, "isClosed", True))
            for point, image in zip(fit, describe.items(new.fitPoints), strict=False):
                m.bind(point, [image])
            construction(source, new)
            keep(fit)
        mapping[entity_ref(source)["ref"]] = entity_ref(new)

    if constrain:
        for point in symmetric:
            image = m.image(point)
            if same_entity(point, image):
                continue
            constraint = journal.created(
                _call("constrain", partial(constraints.addSymmetry, point, image, axis))
            )
            out.append({"ref": entity_ref(constraint), "type": "symmetry"})
        for source, new in equal:
            constraint = journal.created(
                _call("constrain", partial(constraints.addEqual, source, new))
            )
            out.append({"ref": entity_ref(constraint), "type": "equal"})
    return {"mapping": mapping, "constraints": out}


# --- pattern ---------------------------------------------------------------------------------


def _instances(sources: Sequence[Any], groups: Sequence[Sequence[Any]]) -> list[list[Any]]:
    return [_refs(sources), *[_refs(group) for group in groups if group]]


def _chunks(created: Sequence[Any], size: int) -> list[list[Any]]:
    """Created pattern entities grouped per instance (Fusion lists them instance by
    instance); one group if the count does not divide."""
    if size and len(created) % size == 0:
        return [list(created[i : i + size]) for i in range(0, len(created), size)]
    return [list(created)]


def _count(value: Any, field: str, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= 500:
        raise _invalid(f"{field} must be an integer {minimum}..500", field, value=value)
    return value


def _is_zero_angle(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value == 0


def rectangular_pattern(
    scope: BatchScope,
    journal: SketchJournal,
    entities: Sequence[Any],
    count_x: Any,
    spacing_x: Any,
    count_y: Any = 1,
    spacing_y: Any = 0.0,
    angle: Any = 0.0,
    direction: Any = None,
) -> dict[str, Any]:
    """Rectangular pattern: ``createRectangularPatternInput`` (spacing distance type) +
    ``setDirectionOne/Two`` + ``addRectangularPattern``; ``Sketch.copy`` with translation
    matrices when the API lacks it (or when only an ``angle`` gives direction one, which the
    pattern constraint cannot express without a line), ``parametric: false``."""
    sketch = scope.sketch
    items = resolve_entities(scope, entities, PATTERN_TYPES, "entities")
    nx = _count(count_x, "count_x", 1)
    ny = _count(count_y, "count_y", 1)
    if nx * ny < 2:
        raise _invalid("a pattern needs at least two instances", "count_x", reason="too_few")
    sx = expression(scope, spacing_x, "length", "spacing_x")
    sy = expression(scope, spacing_y, "length", "spacing_y")
    line = scope.entity(direction, LINE_TYPES, "direction") if direction is not None else None
    if line is not None and not _is_zero_angle(angle):
        raise _invalid(
            "give either `angle` or `direction`, not both", "angle", reason="conflicting"
        )
    angle_rad = rotation_angle(scope, angle, "angle") if line is None else 0.0
    constraints = sketch.geometricConstraints
    warnings: list[dict[str, Any]] = []
    has_api = hasattr(constraints, "createRectangularPatternInput") and hasattr(
        constraints, "addRectangularPattern"
    )
    if has_api and (line is not None or abs(angle_rad) <= ZERO_TOL):
        distance_type = adsk.fusion.PatternDistanceType.SpacingPatternDistanceType
        pattern_input = _call(
            "entities",
            # A list: the live API takes std::vector<SketchEntity>, not an ObjectCollection.
            lambda: constraints.createRectangularPatternInput(list(items), distance_type),
        )
        value = adsk.core.ValueInput
        # Properties, not setDirectionOne/Two: live Fusion rejects their null direction entity
        # (the default: sketch X, then perpendicular), which the docs allow.
        if line is not None:
            _call("direction", _setter(pattern_input, "directionOneEntity", line))
        _call("count_x", _setter(pattern_input, "quantityOne", value.createByReal(nx)))
        _call("spacing_x", _setter(pattern_input, "distanceOne", value.createByString(sx)))
        if ny > 1:
            _call("count_y", _setter(pattern_input, "quantityTwo", value.createByReal(ny)))
            _call("spacing_y", _setter(pattern_input, "distanceTwo", value.createByString(sy)))
        constraint = journal.created(
            _call("entities", lambda: constraints.addRectangularPattern(pattern_input))
        )
        created = _created_items(journal, _get(constraint, "createdEntities"))
        return {
            "instances": _instances(items, _chunks(created, len(items))),
            "parametric": True,
            "constraint": entity_ref(constraint),
        }
    if has_api:
        warnings.append(
            {
                "code": "pattern_not_parametric",
                "message": "an `angle` direction is patterned with plain copies; give a "
                "`direction` line for a parametric pattern",
            }
        )
    step_x = evaluate(scope, sx, "length", "spacing_x")
    step_y = evaluate(scope, sy, "length", "spacing_y")
    if line is not None:
        a, b = _xy(line.startSketchPoint.geometry), _xy(line.endSketchPoint.geometry)
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length <= ZERO_TOL:
            raise _invalid("the direction line has zero length", "direction")
        d1 = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
    else:
        d1 = (math.cos(angle_rad), math.sin(angle_rad))
    d2 = (-d1[1], d1[0])
    groups: list[list[Any]] = []
    for i in range(nx):
        for j in range(ny):
            if i == j == 0:
                continue
            dx = d1[0] * step_x * i + d2[0] * step_y * j
            dy = d1[1] * step_x * i + d2[1] * step_y * j
            matrix = transform_matrix(0.0, None, dx, dy)
            result = _call("entities", partial(sketch.copy, _collection(items), matrix, sketch))
            groups.append(_created_items(journal, result))
    out: dict[str, Any] = {"instances": _instances(items, groups), "parametric": False}
    if warnings:
        out["warnings"] = warnings
    return out


def circular_pattern(
    scope: BatchScope,
    journal: SketchJournal,
    entities: Sequence[Any],
    center: Any,
    count: Any,
    angle: Any = 360.0,
) -> dict[str, Any]:
    """Circular pattern about ``center``: ``createCircularPatternInput`` + ``quantity`` /
    ``totalAngle`` + ``addCircularPattern`` (a coordinate centre becomes a new sketch point),
    else ``Sketch.copy`` with rotation matrices, ``parametric: false``. A full 360 deg spreads
    ``count`` instances evenly; a partial angle puts the last one at the end of the arc."""
    sketch = scope.sketch
    items = resolve_entities(scope, entities, PATTERN_TYPES, "entities")
    n = _count(count, "count", 2)
    text = expression(scope, angle, "angle", "angle")
    total = evaluate(scope, text, "angle", "angle")
    if abs(total) <= ZERO_TOL:
        raise _invalid("angle must not be zero", "angle", reason="zero")
    pivot = scope.point(center, "center")
    constraints = sketch.geometricConstraints
    if hasattr(constraints, "createCircularPatternInput") and hasattr(
        constraints, "addCircularPattern"
    ):
        if type_name(pivot) not in POINT_TYPES:
            pivot = journal.created(_call("center", lambda: sketch.sketchPoints.add(pivot)))
        pattern_input = _call(
            # A list: the live API takes std::vector<SketchEntity>, not an ObjectCollection.
            "entities",
            lambda: constraints.createCircularPatternInput(list(items), pivot),
        )
        value = adsk.core.ValueInput
        _call("count", _setter(pattern_input, "quantity", value.createByReal(n)))
        _call("angle", _setter(pattern_input, "totalAngle", value.createByString(text)))
        constraint = journal.created(
            _call("entities", lambda: constraints.addCircularPattern(pattern_input))
        )
        created = _created_items(journal, _get(constraint, "createdEntities"))
        return {
            "instances": _instances(items, _chunks(created, len(items))),
            "parametric": True,
            "constraint": entity_ref(constraint),
        }
    position = pivot.geometry if type_name(pivot) in POINT_TYPES else pivot
    full = abs(abs(total) - 2.0 * math.pi) < 1e-9
    step = total / n if full else total / (n - 1)
    groups: list[list[Any]] = []
    for k in range(1, n):
        matrix = transform_matrix(step * k, position, 0.0, 0.0)
        result = _call("entities", partial(sketch.copy, _collection(items), matrix, sketch))
        groups.append(_created_items(journal, result))
    return {"instances": _instances(items, groups), "parametric": False}


# --- project ---------------------------------------------------------------------------------


def _project_one(
    sketch: Any, journal: SketchJournal, entity: Any, mode: str, field: str
) -> list[Any]:
    if mode == "project":
        if type_name(entity) == "BRepBody":
            try:
                result = sketch.project(entity)
            except Exception:
                # Some Fusion versions cannot project a body: project its edges (D11).
                created: list[Any] = []
                for edge in describe.items(entity.edges):
                    projected = _call(field, partial(sketch.project, edge))
                    created.extend(_created_items(journal, projected))
                return created
        else:
            result = _call(field, lambda: sketch.project(entity))
    elif mode == "include":
        result = _call(field, lambda: sketch.include(entity))
    elif mode == "intersect":
        result = _call(field, lambda: sketch.intersectWithSketchPlane(_collection([entity])))
    else:
        result = _call(field, lambda: sketch.projectCutEdges(entity))
    return _created_items(journal, result)


def project(
    scope: BatchScope, journal: SketchJournal, entities: Sequence[Any], mode: str
) -> dict[str, Any]:
    """Bring model geometry into the sketch (D11): ``project`` (a body that Fusion refuses is
    projected edge by edge), ``include``, ``intersect`` (with the sketch plane) or
    ``cut_edges`` (bodies). Result: created refs grouped by the input ref."""
    if mode not in PROJECT_MODES:
        raise _invalid(
            f"mode must be one of {', '.join(PROJECT_MODES)}",
            "mode",
            value=mode,
            expected=list(PROJECT_MODES),
        )
    if not entities:
        raise _invalid("entities must not be empty", "entities")
    sketch = scope.sketch
    sources: list[tuple[str, Any, str]] = []
    for index, spec in enumerate(entities):
        field = f"entities[{index}]"
        if not isinstance(spec, str) or not spec:
            raise _invalid(f"{field} must be an entity ref string", field, value=spec)
        try:
            entity = scope.ctx.refs.resolve(spec)
        except BridgeError as exc:
            exc.details.setdefault("field", field)
            raise
        if not _is_valid(entity):
            raise BridgeError(
                ErrorCode.ENTITY_NOT_FOUND, f"{field} {spec!r} was deleted", {"field": field}
            )
        if same_entity(entity, sketch):
            raise _invalid(f"{field} is this sketch itself", field, reason="same_sketch")
        parent = _get(entity, "parentSketch")
        if parent is not None and same_entity(parent, sketch):
            raise _invalid(
                f"{field} already belongs to this sketch: project entities of the model or of "
                "other sketches",
                field,
                reason="same_sketch",
            )
        if mode == "cut_edges" and type_name(entity) != "BRepBody":
            raise _invalid(
                f"{field} is a {type_name(entity)}: cut_edges needs a body",
                field,
                reason="not_a_body",
                got=type_name(entity),
            )
        if not any(spec == s for s, _, _ in sources):
            sources.append((spec, entity, field))
    created: dict[str, list[dict[str, Any]]] = {}
    for spec, entity, field in sources:
        created[spec] = _refs(_project_one(sketch, journal, entity, mode, field))
    return {"created": created}

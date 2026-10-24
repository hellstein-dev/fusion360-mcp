"""Geometric constraints of a sketch (c05 D9): ``validate_constraint`` / ``apply_constraint``.

An item is the wire form of ``sketch_schema.ConstraintItem``: ``{"type", ...entity specs}``.
Entity specs resolve through ``BatchScope`` (``origin``, local paths of the call, tokens of
this sketch). Every entity type is checked by ``objectType`` before the Fusion API is called
(``INVALID_PARAMS`` with ``details.expected``):

| type | fields | Fusion API |
|---|---|---|
| coincident | ``point``, ``entity`` (point or curve) | ``addCoincident`` |
| horizontal, vertical | ``line`` or ``points: [p1, p2]`` | ``addHorizontal[Points]`` (Vertical) |
| parallel, perpendicular, collinear | ``line1``, ``line2`` | ``addParallel`` ... |
| tangent | ``curve1``, ``curve2`` (not two lines) | ``addTangent`` |
| equal | ``curve1``, ``curve2`` (two lines or two arcs/circles) | ``addEqual`` |
| concentric | ``curve1``, ``curve2`` (arcs, circles, ellipses) | ``addConcentric`` |
| midpoint | ``point``, ``curve`` (line or arc) | ``addMidPoint`` |
| symmetry | ``entity1``, ``entity2`` (two points or two curves), ``line`` | ``addSymmetry`` |
| smooth | ``spline``, ``curve`` | ``addSmooth`` |
| fix | ``entity``, ``fixed = true`` | ``entity.isFixed = fixed`` (undo journaled) |

A created constraint is journaled (``SketchJournal.created``) and reported as ``{ref, type}``;
``fix`` creates no constraint and is reported as ``{entity, type: "fix", fixed}``. Fusion's
error for a conflicting (over-constraining) constraint becomes ``FUSION_API_ERROR`` with
``details.index``; the caller's journal then removes what the call created.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

from ..core.dispatcher import to_bridge_error
from ..core.refs import type_name
from .journal import SketchJournal
from .resolve import (
    CIRCULAR_TYPES,
    CURVE_TYPES,
    ELLIPSE_TYPES,
    LINE_TYPES,
    POINT_TYPES,
    SPLINE_TYPES,
    BatchScope,
    entity_ref,
    same_entity,
)

CONSTRAINT_TYPES: tuple[str, ...] = (
    "coincident",
    "horizontal",
    "vertical",
    "parallel",
    "perpendicular",
    "collinear",
    "tangent",
    "equal",
    "concentric",
    "midpoint",
    "symmetry",
    "smooth",
    "fix",
)

POINT_OR_CURVE: tuple[str, ...] = (*POINT_TYPES, *CURVE_TYPES)
EQUAL_TYPES: tuple[str, ...] = (*LINE_TYPES, *CIRCULAR_TYPES)
CONCENTRIC_TYPES: tuple[str, ...] = (*CIRCULAR_TYPES, *ELLIPSE_TYPES)
MIDPOINT_CURVE_TYPES: tuple[str, ...] = (*LINE_TYPES, "SketchArc")

# type -> ((field, expected types), ...) for the forms with fixed fields.
_FIELDS: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "coincident": (("point", POINT_TYPES), ("entity", POINT_OR_CURVE)),
    "parallel": (("line1", LINE_TYPES), ("line2", LINE_TYPES)),
    "perpendicular": (("line1", LINE_TYPES), ("line2", LINE_TYPES)),
    "collinear": (("line1", LINE_TYPES), ("line2", LINE_TYPES)),
    "tangent": (("curve1", CURVE_TYPES), ("curve2", CURVE_TYPES)),
    "equal": (("curve1", EQUAL_TYPES), ("curve2", EQUAL_TYPES)),
    "concentric": (("curve1", CONCENTRIC_TYPES), ("curve2", CONCENTRIC_TYPES)),
    "midpoint": (("point", POINT_TYPES), ("curve", MIDPOINT_CURVE_TYPES)),
    "symmetry": (("entity1", POINT_OR_CURVE), ("entity2", POINT_OR_CURVE), ("line", LINE_TYPES)),
    "smooth": (("spline", SPLINE_TYPES), ("curve", CURVE_TYPES)),
    "fix": (("entity", POINT_OR_CURVE),),
}

# Two-entity constraints: type -> GeometricConstraints method.
_METHODS: dict[str, str] = {
    "coincident": "addCoincident",
    "parallel": "addParallel",
    "perpendicular": "addPerpendicular",
    "collinear": "addCollinear",
    "tangent": "addTangent",
    "equal": "addEqual",
    "concentric": "addConcentric",
    "midpoint": "addMidPoint",
    "smooth": "addSmooth",
}


def _invalid(message: str, field: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, {"field": field, **details})


def _type_of(item: Mapping[str, Any], field: str) -> str:
    if not isinstance(item, Mapping):
        raise _invalid(f"{field} must be an object with a `type`", field)
    kind = item.get("type")
    if kind not in CONSTRAINT_TYPES:
        raise _invalid(
            f"{field}.type {kind!r} is not a constraint type",
            f"{field}.type",
            value=kind,
            expected=list(CONSTRAINT_TYPES),
        )
    return str(kind)


def _spec(item: Mapping[str, Any], key: str, field: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise _invalid(f"{field}.{key} is required (an entity ref or local path)", f"{field}.{key}")
    return value


def _axis_form(item: Mapping[str, Any], field: str) -> tuple[str, Any]:
    """horizontal/vertical: ``("line", spec)`` or ``("points", [p1, p2])``."""
    line, points = item.get("line"), item.get("points")
    if (line is None) == (points is None):
        raise _invalid(f"{field}: give either `line` or `points: [p1, p2]`", field)
    if line is not None:
        return "line", _spec(item, "line", field)
    if (
        not isinstance(points, list | tuple)
        or len(points) != 2
        or not all(isinstance(p, str) and p for p in points)
    ):
        raise _invalid(f"{field}.points must be two point refs", f"{field}.points")
    return "points", list(points)


def _fixed(item: Mapping[str, Any], field: str) -> bool:
    fixed = item.get("fixed", True)
    if not isinstance(fixed, bool):
        raise _invalid(f"{field}.fixed must be a boolean", f"{field}.fixed", value=fixed)
    return fixed


# --- validation --------------------------------------------------------------------------


def validate_constraint(scope: BatchScope, item: Mapping[str, Any], field: str) -> None:
    """Check the form and entity types of one item without touching the sketch.

    Local paths declared (not yet created) in ``scope`` are accepted with their declared
    type; tokens are resolved. Combination rules that need both actual types (``tangent``
    of two lines, ``equal`` of a line and a circle...) are checked by ``apply_constraint``
    before its API call."""
    kind = _type_of(item, field)
    if kind in ("horizontal", "vertical"):
        form, value = _axis_form(item, field)
        if form == "line":
            scope.check(value, LINE_TYPES, f"{field}.line")
        else:
            for i, point in enumerate(value):
                scope.check(point, POINT_TYPES, f"{field}.points[{i}]")
        return
    if kind == "fix":
        _fixed(item, field)
    for key, expected in _FIELDS[kind]:
        scope.check(_spec(item, key, field), expected, f"{field}.{key}")


def validate_constraints(
    scope: BatchScope, items: Sequence[Mapping[str, Any]], field: str = "constraints"
) -> None:
    """``validate_constraint`` for every item; errors carry ``details.index``."""
    for index, item in enumerate(items):
        with indexed(index):
            validate_constraint(scope, item, f"{field}[{index}]")


# --- application -------------------------------------------------------------------------


@contextlib.contextmanager
def indexed(index: int) -> Iterator[None]:
    """Tag an escaping error with ``details.index`` (non-``BridgeError`` -> ``BridgeError``)."""
    try:
        yield
    except Exception as exc:
        error = to_bridge_error(exc)
        error.details.setdefault("index", index)
        if error is exc:
            raise
        raise error from exc


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


def _assign(obj: Any, attr: str, value: Any) -> None:
    setattr(obj, attr, value)


def _combination(kind: str, entities: Sequence[Any], field: str) -> None:
    names = [type_name(e) for e in entities]
    if kind in ("tangent", "equal", "concentric", "smooth", "symmetry") and same_entity(
        entities[0], entities[1]
    ):
        raise _invalid(f"{field}: the two entities must differ", field, reason="same_entity")
    if kind == "tangent" and names[0] in LINE_TYPES and names[1] in LINE_TYPES:
        raise _invalid(
            f"{field}: tangent needs at least one arc, circle, ellipse or spline",
            field,
            reason="wrong_combination",
            got=names,
            expected=[t for t in CURVE_TYPES if t not in LINE_TYPES],
        )
    if kind == "equal" and (names[0] in LINE_TYPES) != (names[1] in LINE_TYPES):
        raise _invalid(
            f"{field}: equal needs two lines or two arcs/circles",
            field,
            reason="wrong_combination",
            got=names,
        )
    if kind == "symmetry":
        if (names[0] in POINT_TYPES) != (names[1] in POINT_TYPES):
            raise _invalid(
                f"{field}: symmetry needs two points or two curves",
                field,
                reason="wrong_combination",
                got=names[:2],
            )
        if same_entity(entities[2], entities[0]) or same_entity(entities[2], entities[1]):
            raise _invalid(
                f"{field}: the symmetry line must differ from the mirrored entities",
                f"{field}.line",
                reason="same_entity",
            )
    if kind in ("parallel", "perpendicular", "collinear", "coincident") and same_entity(
        entities[0], entities[1]
    ):
        raise _invalid(f"{field}: the two entities must differ", field, reason="same_entity")


def _created(journal: SketchJournal, constraint: Any, kind: str) -> dict[str, Any]:
    journal.created(constraint)
    return {"ref": entity_ref(constraint), "type": kind}


def _apply_fix(
    scope: BatchScope, journal: SketchJournal, item: Mapping[str, Any], field: str
) -> dict[str, Any]:
    entity = scope.entity(_spec(item, "entity", field), POINT_OR_CURVE, f"{field}.entity")
    fixed = _fixed(item, field)
    before = bool(entity.isFixed)
    if before != fixed:

        def restore(target: Any = entity, value: bool = before) -> None:
            target.isFixed = value

        _call(f"{field}.entity", lambda: _assign(entity, "isFixed", fixed))
        journal.on_undo(restore, f"isFixed of {type_name(entity)}")
    return {"entity": entity_ref(entity), "type": "fix", "fixed": fixed}


def apply_constraint(
    scope: BatchScope, journal: SketchJournal, item: Mapping[str, Any], field: str
) -> dict[str, Any]:
    """Create one constraint (D9) and journal it; returns ``{ref, type}`` or, for ``fix``,
    ``{entity, type: "fix", fixed}``."""
    kind = _type_of(item, field)
    constraints = scope.sketch.geometricConstraints
    if kind == "fix":
        return _apply_fix(scope, journal, item, field)
    if kind in ("horizontal", "vertical"):
        form, value = _axis_form(item, field)
        suffix = "Horizontal" if kind == "horizontal" else "Vertical"
        if form == "line":
            line = scope.entity(value, LINE_TYPES, f"{field}.line")
            method = getattr(constraints, f"add{suffix}")
            return _created(journal, _call(field, lambda: method(line)), kind)
        p1 = scope.entity(value[0], POINT_TYPES, f"{field}.points[0]")
        p2 = scope.entity(value[1], POINT_TYPES, f"{field}.points[1]")
        if same_entity(p1, p2):
            raise _invalid(f"{field}: the two points must differ", f"{field}.points")
        method = getattr(constraints, f"add{suffix}Points")
        return _created(journal, _call(field, lambda: method(p1, p2)), kind)
    entities = [
        scope.entity(_spec(item, key, field), expected, f"{field}.{key}")
        for key, expected in _FIELDS[kind]
    ]
    _combination(kind, entities, field)
    if kind == "symmetry":
        one, two, line = entities
        result = _call(field, lambda: constraints.addSymmetry(one, two, line))
        return _created(journal, result, kind)
    method = getattr(constraints, _METHODS[kind])
    first, second = entities
    return _created(journal, _call(field, lambda: method(first, second)), kind)


def apply_constraints(
    scope: BatchScope,
    journal: SketchJournal,
    items: Sequence[Mapping[str, Any]],
    field: str = "constraints",
    validate: bool = True,
) -> list[dict[str, Any]]:
    """Validate every item (unless the caller already did), then apply them in order; an
    error carries ``details.index`` of its item."""
    if validate:
        validate_constraints(scope, items, field)
    results: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        with indexed(index):
            results.append(apply_constraint(scope, journal, item, f"{field}[{index}]"))
    return results

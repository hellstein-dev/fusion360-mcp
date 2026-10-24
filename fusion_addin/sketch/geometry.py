"""Geometry builders of ``sketch.draw`` (c05 D7): one builder per item kind / mode.

Two phases, so that nothing is created before the whole batch is known to be valid (D6):

1. ``validate_items(scope, items)`` checks every item (kind, mode, required fields, numbers,
   point specs against earlier items, degenerate coordinates) and ``declare``s its local id
   and sub paths (with their entity types) in the ``BatchScope``; it returns ``DrawSpec``s.
2. ``build_items(scope, journal, specs)`` draws them in order inside a ``SketchJournal``:
   every created entity (curves, their new points, helper construction curves, builder
   constraints, texts) goes to ``journal.created``; ``scope.register(id, entity, subs)``
   binds the local paths so later items, constraints and dimensions can use them.

Errors of both phases are ``BridgeError``s with ``details.phase = "geometry"``,
``details.index`` and ``details.id`` of the item; Fusion's ``RuntimeError`` becomes
``FUSION_API_ERROR``, invalid geometry (zero-length line, collinear arc points, ...)
``INVALID_PARAMS``.

Item shape: what ``server/tools/sketch_schema.py`` ``DrawItem`` dumps (``{"kind", "id"?,
"construction"?, ...}``, ``"from"`` for tangent arcs). Coordinates and sizes are numbers in
the scope's units (mm by default, converted with ``fusion_mcp.protocol.units``, INV-6),
angles (``sweep``, ``angle``) degrees.

Sub-ids (D7):

==================================  ===============================================
kind / mode                         sub-ids (item id itself -> entity)
==================================  ===============================================
point                               ``id`` -> SketchPoint
line                                ``id`` -> line; ``start``, ``end``
polyline                            group; ``e0..`` edges, ``v0..`` vertices
rectangle two_point / center        group; ``bottom right top left``, ``bl br tr tl``
                                    (center adds ``center``, ``diagonal``)
rectangle three_point               group; ``e0..e3``, ``v0..v3`` (``v0`` at ``p1``)
circle (all modes)                  ``id`` -> circle; ``center``
arc (all modes)                     ``id`` -> arc; ``start``, ``end``, ``center``
ellipse                             ``id`` -> ellipse; ``center``
polygon                             group; ``e0..``, ``v0..``, ``center``, ``circle``
spline                              ``id`` -> fitted spline; ``start``, ``end``, ``fp0..``
slot                                group; ``side0 side1 end0 end1 axis c0 c1``
text                                ``id`` -> SketchText
==================================  ===============================================

A *group* item has no entity of its own: only its sub paths are in ``refs``; it is declared
with the type ``GROUP_TYPE`` so a reference to the bare id is refused at validation.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NoReturn

import adsk.core

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

from ..core.dispatcher import to_bridge_error
from ..core.refs import type_name
from . import math2d, space
from .describe import CONSTRAINT_ROLES, items, sketch_constraints, sketch_curves
from .journal import SketchJournal
from .resolve import BatchScope, same_entity
from .specs import STRING_COORD_HINT, LocalPath, ModelXyz, Xy, parse_entity_spec, parse_point_spec

PHASE = "geometry"
MAX_ITEMS = 500
GROUP_TYPE = "group"

POINT = "SketchPoint"
LINE = "SketchLine"
ARC = "SketchArc"
CIRCLE = "SketchCircle"
ELLIPSE = "SketchEllipse"
SPLINE = "SketchFittedSpline"
TEXT = "SketchText"

# Allowed modes per kind; the first one is the default. ``None``: the kind has no mode.
MODES: dict[str, tuple[str | None, ...]] = {
    "point": (None,),
    "line": (None,),
    "polyline": (None,),
    "rectangle": ("two_point", "center", "three_point"),
    "circle": ("center_radius", "two_point", "three_point"),
    "arc": ("three_point", "center_start_sweep", "tangent"),
    "ellipse": (None,),
    "polygon": ("inscribed", "circumscribed"),
    "spline": (None,),
    "slot": ("center_to_center", "overall"),
    "text": (None,),
}
KINDS: tuple[str, ...] = tuple(MODES)

# Point-spec fields per (kind, mode); polyline/spline ``points`` are handled apart.
POINT_FIELDS: dict[tuple[str, str | None], tuple[str, ...]] = {
    ("point", None): ("at",),
    ("line", None): ("start", "end"),
    ("polyline", None): (),
    ("rectangle", "two_point"): ("corner1", "corner2"),
    ("rectangle", "center"): ("center", "corner"),
    ("rectangle", "three_point"): ("p1", "p2", "p3"),
    ("circle", "center_radius"): ("center",),
    ("circle", "two_point"): ("p1", "p2"),
    ("circle", "three_point"): ("p1", "p2", "p3"),
    ("arc", "three_point"): ("start", "mid", "end"),
    ("arc", "center_start_sweep"): ("center", "start"),
    ("arc", "tangent"): ("from", "to"),
    ("ellipse", None): ("center",),
    ("polygon", "inscribed"): ("center",),
    ("polygon", "circumscribed"): ("center",),
    ("spline", None): (),
    ("slot", "center_to_center"): ("p1", "p2"),
    ("slot", "overall"): ("p1", "p2"),
    ("text", None): ("corner",),
}

RECT_SIDES = ("bottom", "right", "top", "left")
RECT_CORNERS = ("bl", "br", "tr", "tl")
SLOT_SUBS: dict[str, str] = {
    "side0": LINE,
    "side1": LINE,
    "end0": ARC,
    "end1": ARC,
    "axis": LINE,
    "c0": POINT,
    "c1": POINT,
}

H_ALIGN = {
    "left": "LeftHorizontalAlignment",
    "center": "CenterHorizontalAlignment",
    "right": "RightHorizontalAlignment",
}
V_ALIGN = {
    "top": "TopVerticalAlignment",
    "middle": "MiddleVerticalAlignment",
    "bottom": "BottomVerticalAlignment",
}
# Default text box (when ``size`` is omitted): character width and line pitch in text heights.
TEXT_CHAR_WIDTH = 0.8
TEXT_LINE_PITCH = 1.5

# Coordinates closer than this (cm) are the same point.
_EPS = 1e-9


# --- specs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class DrawSpec:
    """One validated draw item: ``item`` is the raw mapping, ``field`` its path
    (``items[3]``) for error details."""

    index: int
    kind: str
    mode: str | None
    id: str | None
    construction: bool
    item: Mapping[str, Any]
    field: str

    def at(self, name: str) -> str:
        return f"{self.field}.{name}"

    def get(self, name: str, default: Any = None) -> Any:
        if name == "from":
            return self.item.get("from", self.item.get("from_", default))
        return self.item.get(name, default)


@dataclass
class DrawnItem:
    """What one builder created: ``entity`` (``None`` for groups), ``subs`` (sub-id ->
    entity), the item's ``curves`` (helper construction curves included) and the
    ``constraints`` the builder added."""

    spec: DrawSpec
    entity: Any
    subs: dict[str, Any]
    curves: list[Any] = field(default_factory=list)
    constraints: list[Any] = field(default_factory=list)


def _invalid(message: str, field_name: str, value: Any = None, **details: Any) -> BridgeError:
    data: dict[str, Any] = {"field": field_name, **details}
    if value is not None:
        data["value"] = value
    return BridgeError(ErrorCode.INVALID_PARAMS, message, data)


def _api_error(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.FUSION_API_ERROR, message, dict(details))


def _relocate(exc: Exception, index: int, item_id: str | None) -> NoReturn:
    """Re-raise ``exc`` as a ``BridgeError`` with ``details.phase/index/id`` of the item
    (Fusion's ``RuntimeError`` -> ``FUSION_API_ERROR``)."""
    error = to_bridge_error(exc)
    error.details["phase"] = PHASE
    error.details["index"] = index
    error.details["id"] = item_id
    if error is exc:
        raise error
    raise error from exc


# --- validation ----------------------------------------------------------------------------


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _number(
    spec: DrawSpec, name: str, default: float | None = None, positive: bool = False
) -> float:
    value = spec.get(name, default)
    where = spec.at(name)
    if value is None:
        raise _invalid(f"{where} is required", where, reason="missing_field")
    if isinstance(value, str):
        raise _invalid(
            f"{where} must be a number: {STRING_COORD_HINT}",
            where,
            value,
            reason="string_size",
            hint=STRING_COORD_HINT,
        )
    if not _is_number(value) or not math.isfinite(float(value)):
        raise _invalid(f"{where} must be a finite number, got {value!r}", where, value)
    if positive and float(value) <= 0:
        raise _invalid(f"{where} must be positive, got {value!r}", where, value)
    return float(value)


def _flag(spec: DrawSpec, name: str, default: bool) -> bool:
    value = spec.get(name, default)
    if not isinstance(value, bool):
        where = spec.at(name)
        raise _invalid(f"{where} must be true or false, got {value!r}", where, value)
    return value


def _required(spec: DrawSpec, name: str) -> Any:
    value = spec.get(name)
    if value is None:
        where = spec.at(name)
        raise _invalid(f"{where} is required for {_label(spec)}", where, reason="missing_field")
    return value


def _label(spec: DrawSpec) -> str:
    return spec.kind if spec.mode is None else f"{spec.kind} {spec.mode}"


def _coords(scope: BatchScope, value: Any, where: str) -> math2d.Point | None:
    """Sketch-space ``(x, y)`` cm of a coordinate spec; ``None`` for an entity spec."""
    parsed = parse_point_spec(value, where)
    if not isinstance(parsed, Xy | ModelXyz):
        return None
    p = space.to_point3d(parsed, scope.units, scope.sketch, None, where)
    return (float(p.x), float(p.y))


def _same(a: math2d.Point | None, b: math2d.Point | None) -> bool:
    return a is not None and b is not None and math.dist(a, b) <= _EPS


def _collinear(a: math2d.Point | None, b: math2d.Point | None, c: math2d.Point | None) -> bool:
    if a is None or b is None or c is None:
        return False
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    scale = max(math.dist(a, b) * math.dist(a, c), _EPS)
    return abs(cross) <= 1e-9 * scale


def declared_subs(spec: DrawSpec) -> tuple[str, dict[str, str]]:
    """``(item type, {sub: type})`` declared in the scope for ``spec`` (D7 table)."""
    kind, mode = spec.kind, spec.mode
    if kind == "point":
        return POINT, {}
    if kind == "line":
        return LINE, {"start": POINT, "end": POINT}
    if kind == "polyline":
        n = len(spec.get("points") or [])
        edges = n if spec.get("closed", False) else n - 1
        return GROUP_TYPE, {
            **{f"e{k}": LINE for k in range(edges)},
            **{f"v{k}": POINT for k in range(n)},
        }
    if kind == "rectangle":
        if mode == "three_point":
            return GROUP_TYPE, {
                **{f"e{k}": LINE for k in range(4)},
                **{f"v{k}": POINT for k in range(4)},
            }
        subs = {**dict.fromkeys(RECT_SIDES, LINE), **dict.fromkeys(RECT_CORNERS, POINT)}
        if mode == "center":
            subs.update(center=POINT, diagonal=LINE)
        return GROUP_TYPE, subs
    if kind == "circle":
        return CIRCLE, {"center": POINT}
    if kind == "arc":
        return ARC, {"start": POINT, "end": POINT, "center": POINT}
    if kind == "ellipse":
        return ELLIPSE, {"center": POINT}
    if kind == "polygon":
        n = int(spec.get("sides"))
        return GROUP_TYPE, {
            **{f"e{k}": LINE for k in range(n)},
            **{f"v{k}": POINT for k in range(n)},
            "center": POINT,
            "circle": CIRCLE,
        }
    if kind == "spline":
        n = len(spec.get("points") or [])
        return SPLINE, {"start": POINT, "end": POINT, **{f"fp{k}": POINT for k in range(n)}}
    if kind == "slot":
        return GROUP_TYPE, dict(SLOT_SUBS)
    return TEXT, {}


def _point_list(scope: BatchScope, spec: DrawSpec, min_count: int) -> list[math2d.Point | None]:
    points = spec.get("points")
    where = spec.at("points")
    if not isinstance(points, list | tuple):
        raise _invalid(f"{where} must be a list of points", where, reason="missing_field")
    if len(points) < min_count:
        raise _invalid(
            f"{where} needs at least {min_count} points for {_label(spec)}, got {len(points)}",
            where,
            reason="too_few_points",
        )
    if len(points) > MAX_ITEMS:
        raise _invalid(f"{where} has more than {MAX_ITEMS} points", where, reason="too_many")
    coords: list[math2d.Point | None] = []
    for k, value in enumerate(points):
        at = f"{where}[{k}]"
        scope.check_point(value, at)
        coords.append(_coords(scope, value, at))
    for k in range(1, len(coords)):
        if _same(coords[k - 1], coords[k]):
            at = f"{where}[{k}]"
            raise _invalid(f"{at} repeats the previous point", at, reason="degenerate")
    return coords


def _validate_geometry(scope: BatchScope, spec: DrawSpec) -> None:
    """Numbers and degenerate coordinates of one item (point specs already checked)."""
    kind, mode = spec.kind, spec.mode
    pts = {
        name: _coords(scope, spec.get(name), spec.at(name))
        for name in POINT_FIELDS[(kind, mode)]
        if name != "from"
    }

    def degenerate(name: str, why: str) -> BridgeError:
        return _invalid(f"{spec.at(name)}: {why}", spec.at(name), reason="degenerate")

    if kind == "line" and _same(pts["start"], pts["end"]):
        raise degenerate("end", "a line needs two distinct points")
    elif kind == "polyline":
        closed = _flag(spec, "closed", False)
        coords = _point_list(scope, spec, 3 if closed else 2)
        if closed and _same(coords[0], coords[-1]):
            raise degenerate("points", "a closed polyline must not repeat its first point")
    elif kind == "rectangle":
        if mode == "two_point":
            a, b = pts["corner1"], pts["corner2"]
            if a is not None and b is not None and min(abs(a[0] - b[0]), abs(a[1] - b[1])) <= _EPS:
                raise degenerate("corner2", "the corners must differ in x and in y")
        elif mode == "center":
            c, k = pts["center"], pts["corner"]
            if c is not None and k is not None and min(abs(c[0] - k[0]), abs(c[1] - k[1])) <= _EPS:
                raise degenerate("corner", "the corner must differ from the center in x and y")
        else:
            if _same(pts["p1"], pts["p2"]):
                raise degenerate("p2", "p1 and p2 must differ")
            if _collinear(pts["p1"], pts["p2"], pts["p3"]):
                raise degenerate("p3", "p3 must not lie on the line p1-p2")
    elif kind == "circle":
        if mode == "center_radius":
            _number(spec, "radius", positive=True)
        elif mode == "two_point" and _same(pts["p1"], pts["p2"]):
            raise degenerate("p2", "the diameter ends must differ")
        elif mode == "three_point" and (
            _same(pts["p1"], pts["p2"])
            or _same(pts["p2"], pts["p3"])
            or _same(pts["p1"], pts["p3"])
            or _collinear(pts["p1"], pts["p2"], pts["p3"])
        ):
            raise degenerate("p3", "three distinct, non-collinear points are required")
    elif kind == "arc":
        if mode == "three_point" and (
            _same(pts["start"], pts["end"]) or _collinear(pts["start"], pts["mid"], pts["end"])
        ):
            raise degenerate("mid", "start, mid and end must be distinct and not collinear")
        if mode == "center_start_sweep":
            sweep = _number(spec, "sweep")
            if sweep == 0 or abs(sweep) > 360:
                where = spec.at("sweep")
                raise _invalid(f"{where} must be in [-360, 360] and not 0", where, sweep)
            if _same(pts["center"], pts["start"]):
                raise degenerate("start", "the start point must differ from the center")
        if mode == "tangent" and not isinstance(spec.get("from"), str):
            where = spec.at("from")
            raise _invalid(
                f"{where} must be the end point of an existing line or arc, e.g. 'l1.end'",
                where,
                spec.get("from"),
                reason="invalid_entity_spec",
            )
    elif kind == "ellipse":
        major = _number(spec, "major_radius", positive=True)
        minor = _number(spec, "minor_radius", positive=True)
        angle = _number(spec, "angle", 0.0)
        try:
            math2d.ellipse_points((0.0, 0.0), major, minor, angle)
        except ValueError as exc:
            raise _invalid(str(exc), spec.at("minor_radius"), minor, reason="degenerate") from None
    elif kind == "polygon":
        radius = _number(spec, "radius", positive=True)
        sides = spec.get("sides")
        where = spec.at("sides")
        if not isinstance(sides, int) or isinstance(sides, bool):
            raise _invalid(f"{where} must be an integer", where, sides)
        _number(spec, "angle", 0.0)
        _flag(spec, "constrain", True)
        try:
            math2d.polygon_vertices((0.0, 0.0), radius, sides, spec.mode or "inscribed")
        except ValueError as exc:
            raise _invalid(str(exc), where, sides) from None
    elif kind == "spline":
        closed = _flag(spec, "closed", False)
        _point_list(scope, spec, 3 if closed else 2)
    elif kind == "slot":
        width = _number(spec, "width", positive=True)
        a, b = pts["p1"], pts["p2"]
        if a is not None and b is not None:
            try:
                math2d.slot(a, b, float(scope.units.length(width)), spec.mode or "center_to_center")
            except ValueError as exc:
                raise _invalid(str(exc), spec.at("p2"), reason="degenerate") from None
    elif kind == "text":
        _validate_text(spec)


def _validate_text(spec: DrawSpec) -> None:
    text = spec.get("text")
    if not isinstance(text, str) or not text:
        where = spec.at("text")
        raise _invalid(f"{where} must be a non-empty string", where, text)
    _number(spec, "height", positive=True)
    size = spec.get("size")
    if size is not None:
        where = spec.at("size")
        if not isinstance(size, list | tuple) or len(size) != 2:
            raise _invalid(f"{where} must be [w, h]", where, size)
        for value in size:
            if not _is_number(value) or not math.isfinite(value) or value <= 0:
                raise _invalid(f"{where} must be two positive numbers, got {size!r}", where, size)
    font = spec.get("font")
    if font is not None and (not isinstance(font, str) or not font):
        where = spec.at("font")
        raise _invalid(f"{where} must be a font name", where, font)
    for name, allowed, default in (("h_align", H_ALIGN, "left"), ("v_align", V_ALIGN, "bottom")):
        value = spec.get(name, default)
        if value not in allowed:
            where = spec.at(name)
            raise _invalid(
                f"{where} must be one of {', '.join(allowed)}, got {value!r}",
                where,
                value,
                expected=list(allowed),
            )


def _make_spec(item: Any, index: int, field_name: str) -> DrawSpec:
    where = f"{field_name}[{index}]"
    if not isinstance(item, Mapping):
        raise _invalid(f"{where} must be an object with 'kind'", where)
    kind = item.get("kind")
    if kind not in MODES:
        raise _invalid(
            f"{where}.kind {kind!r} is unknown; expected one of: {', '.join(KINDS)}",
            f"{where}.kind",
            kind,
            expected=list(KINDS),
        )
    modes = MODES[kind]
    mode = item.get("mode", modes[0]) if modes[0] is not None else item.get("mode")
    if mode not in modes:
        raise _invalid(
            f"{where}.mode {mode!r} is unknown for {kind}; expected one of: "
            f"{', '.join(str(m) for m in modes)}",
            f"{where}.mode",
            mode,
            expected=list(modes),
        )
    item_id = item.get("id")
    construction = item.get("construction", False)
    if not isinstance(construction, bool):
        raise _invalid(f"{where}.construction must be true or false", f"{where}.construction")
    return DrawSpec(index, kind, mode, item_id, construction, item, where)


def validate_item(scope: BatchScope, item: Any, index: int, field_name: str = "items") -> DrawSpec:
    """Check one item against the scope (earlier items are declared) and declare its id."""
    raw_id = item.get("id") if isinstance(item, Mapping) else None
    try:
        spec = _make_spec(item, index, field_name)
        for name in POINT_FIELDS[(spec.kind, spec.mode)]:
            value = _required(spec, name)
            if name == "from" and not isinstance(value, str):
                continue  # reported with a better message by _validate_geometry
            scope.check_point(value, spec.at(name))
        _validate_geometry(scope, spec)
        if spec.id is not None:
            item_type, subs = declared_subs(spec)
            scope.declare(spec.id, item_type, subs, field=spec.at("id"))
    except Exception as exc:
        _relocate(exc, index, raw_id if isinstance(raw_id, str) else None)
    return spec


def validate_items(scope: BatchScope, items_: Any, field_name: str = "items") -> list[DrawSpec]:
    """Validation phase of ``sketch.draw``: every item, in order, before any mutation."""
    if not isinstance(items_, list | tuple):
        raise _invalid(f"{field_name} must be a list of draw items", field_name)
    if len(items_) > MAX_ITEMS:
        raise _invalid(
            f"{field_name} has {len(items_)} items, at most {MAX_ITEMS} per call",
            field_name,
            reason="too_many",
        )
    return [validate_item(scope, item, k, field_name) for k, item in enumerate(items_)]


# --- entity helpers ------------------------------------------------------------------------


def _get(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _is_point(entity: Any) -> bool:
    return type_name(entity) == POINT


def _xy(point: Any) -> math2d.Point:
    """``(x, y)`` cm of a ``SketchPoint`` (its geometry) or a ``Point3D``."""
    p = point.geometry if _is_point(point) else point
    return (float(p.x), float(p.y))


def _p3(xy: Sequence[float]) -> Any:
    return adsk.core.Point3D.create(float(xy[0]), float(xy[1]), 0.0)


def _key(entity: Any) -> str:
    token = _get(entity, "entityToken")
    return token if isinstance(token, str) and token else f"id:{id(entity)}"


_POINT_ATTRS: dict[str, tuple[str, ...]] = {
    LINE: ("startSketchPoint", "endSketchPoint"),
    ARC: ("centerSketchPoint", "startSketchPoint", "endSketchPoint"),
    CIRCLE: ("centerSketchPoint",),
    ELLIPSE: ("centerSketchPoint",),
}


def _curve_points(curve: Any) -> list[Any]:
    kind = type_name(curve)
    if kind == SPLINE:
        return items(_get(curve, "fitPoints"))
    found = (_get(curve, attr) for attr in _POINT_ATTRS.get(kind, ()))
    return [p for p in found if p is not None]


def _constraints_on(sketch: Any, entity: Any) -> list[Any]:
    """Geometric constraints referencing ``entity`` (``SketchEntity.geometricConstraints``,
    else a scan of the sketch's constraints by their role properties)."""
    own = _get(entity, "geometricConstraints")
    if own is not None:
        return items(own)
    return [
        c
        for c in sketch_constraints(sketch)
        if any(
            same_entity(_get(c, role), entity) for role in CONSTRAINT_ROLES.get(type_name(c), ())
        )
    ]


def _lines_at(sketch: Any, point: Any) -> list[Any]:
    connected = _get(point, "connectedEntities")
    pool = items(connected) if connected is not None else sketch_curves(sketch)
    return [c for c in pool if type_name(c) == LINE]


def _has_ends(curve: Any, p: Any, q: Any) -> bool:
    s, e = _get(curve, "startSketchPoint"), _get(curve, "endSketchPoint")
    return (same_entity(s, p) and same_entity(e, q)) or (same_entity(s, q) and same_entity(e, p))


def _unique_points(curves: Sequence[Any]) -> list[Any]:
    points: list[Any] = []
    for curve in curves:
        for p in (curve.startSketchPoint, curve.endSketchPoint):
            if not any(same_entity(p, q) for q in points):
                points.append(p)
    return points


def _arc_subs(arc: Any, start_xy: math2d.Point) -> dict[str, Any]:
    """``start``/``end`` of an arc by geometry: Fusion stores arcs counter-clockwise, so a
    clockwise input has its ends swapped; ``start`` is the end nearest ``start_xy``."""
    s, e = arc.startSketchPoint, arc.endSketchPoint
    if math.dist(_xy(e), start_xy) < math.dist(_xy(s), start_xy):
        s, e = e, s
    return {"start": s, "end": e, "center": arc.centerSketchPoint}


# --- the builder ---------------------------------------------------------------------------


class _Builder:
    """Per-item helper: resolves inputs and journals everything created (once)."""

    def __init__(self, scope: BatchScope, journal: SketchJournal, spec: DrawSpec) -> None:
        self.scope = scope
        self.journal = journal
        self.spec = spec
        self.sketch = scope.sketch
        self.existing: list[Any] = []
        self.curves: list[Any] = []
        self.constraints: list[Any] = []
        self._seen: set[str] = set()

    # inputs
    def resolve_point(self, value: Any, where: str) -> Any:
        point = self.scope.point(value, where)
        if _is_point(point):
            self.existing.append(point)
        return point

    def point(self, name: str) -> Any:
        return self.resolve_point(self.spec.get(name), self.spec.at(name))

    def length(self, name: str) -> float:
        return float(self.scope.units.length(_number(self.spec, name, positive=True)))

    @property
    def lines(self) -> Any:
        return self.sketch.sketchCurves.sketchLines

    @property
    def gc(self) -> Any:
        return self.sketch.geometricConstraints

    # created entities
    def created(self, entity: Any) -> Any:
        if entity is None:
            return self.journal.created(entity)  # raises FUSION_API_ERROR
        key = _key(entity)
        if key not in self._seen:
            self._seen.add(key)
            self.journal.created(entity)
        return entity

    def adopt_point(self, point: Any) -> None:
        """Journal a point Fusion made for a curve, unless it is one of the inputs."""
        if not any(same_entity(point, e) for e in self.existing):
            self.created(point)

    def curve(self, entity: Any, helper: bool = False) -> Any:
        self.created(entity)
        self.curves.append(entity)
        if helper or self.spec.construction:
            entity.isConstruction = True
        for point in _curve_points(entity):
            self.adopt_point(point)
        return entity

    def new_point(self, position: Any) -> Any:
        return self.created(self.sketch.sketchPoints.add(position))

    def constraint(self, constraint: Any) -> Any:
        self.created(constraint)
        self.constraints.append(constraint)
        return constraint

    def coincide(self, point: Any, entity: Any) -> None:
        """``addCoincident`` for an input ``SketchPoint`` that Fusion took as coordinates."""
        if _is_point(point):
            self.constraint(self.gc.addCoincident(point, entity))

    def done(self, entity: Any, subs: Mapping[str, Any] | None = None) -> DrawnItem:
        return DrawnItem(self.spec, entity, dict(subs or {}), self.curves, self.constraints)


Builder = Callable[[_Builder], DrawnItem]


def _geometry_error(exc: ValueError, where: str) -> BridgeError:
    return _invalid(str(exc), where, reason="degenerate")


# point / line / polyline (5.1) -------------------------------------------------------------


def _build_point(b: _Builder) -> DrawnItem:
    at = b.point("at")
    if _is_point(at):
        # A point item on an existing point is that point: nothing to create.
        return b.done(at)
    return b.done(b.new_point(at))


def _build_line(b: _Builder) -> DrawnItem:
    line = b.curve(b.lines.addByTwoPoints(b.point("start"), b.point("end")))
    return b.done(line, {"start": line.startSketchPoint, "end": line.endSketchPoint})


def _chain(b: _Builder, points: Sequence[Any], closed: bool) -> tuple[list[Any], list[Any]]:
    """Lines through ``points`` (SketchPoints or Point3D) on shared end points; ``closed``
    adds the line back to the first point. Returns ``(edges, vertices)``."""
    edges: list[Any] = []
    prev: Any = points[0]
    for target in points[1:]:
        line = b.curve(b.lines.addByTwoPoints(prev, target))
        edges.append(line)
        prev = line.endSketchPoint
    if closed:
        edges.append(b.curve(b.lines.addByTwoPoints(prev, edges[0].startSketchPoint)))
    vertices = [edges[0].startSketchPoint] + [e.endSketchPoint for e in edges[: len(points) - 1]]
    return edges, vertices


def _indexed(edges: Sequence[Any], vertices: Sequence[Any]) -> dict[str, Any]:
    return {
        **{f"e{k}": e for k, e in enumerate(edges)},
        **{f"v{k}": v for k, v in enumerate(vertices)},
    }


def _build_polyline(b: _Builder) -> DrawnItem:
    where = b.spec.at("points")
    points = [b.resolve_point(v, f"{where}[{k}]") for k, v in enumerate(b.spec.get("points"))]
    edges, vertices = _chain(b, points, _flag(b.spec, "closed", False))
    return b.done(None, _indexed(edges, vertices))


# rectangles (5.2, 5.3) ---------------------------------------------------------------------


def _axis_rectangle(lines: Sequence[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Name the sides and corners of an axis-aligned rectangle by geometry."""
    corners = _unique_points(lines)
    if len(lines) != 4 or len(corners) != 4:
        raise _api_error(
            f"expected 4 rectangle lines on 4 corners, got {len(lines)} lines",
            corners=len(corners),
        )
    xy = [_xy(c) for c in corners]
    cx = sum(p[0] for p in xy) / 4.0
    cy = sum(p[1] for p in xy) / 4.0
    named_corners: dict[str, Any] = {}
    for corner, (x, y) in zip(corners, xy, strict=True):
        named_corners[("b" if y < cy else "t") + ("l" if x < cx else "r")] = corner
    sides: dict[str, Any] = {}
    for line in lines:
        (x0, y0), (x1, y1) = _xy(line.startSketchPoint), _xy(line.endSketchPoint)
        dx, dy = (x0 + x1) / 2.0 - cx, (y0 + y1) / 2.0 - cy
        if abs(dy) >= abs(dx):
            sides["bottom" if dy < 0 else "top"] = line
        else:
            sides["left" if dx < 0 else "right"] = line
    if len(named_corners) != 4 or len(sides) != 4:
        raise _api_error("the rectangle Fusion created is not axis-aligned")
    return (
        {name: sides[name] for name in RECT_SIDES},
        {name: named_corners[name] for name in RECT_CORNERS},
    )


def _ensure_axis_constraints(b: _Builder, sides: Mapping[str, Any]) -> None:
    """Add the horizontal/vertical constraints a Fusion version did not create."""
    for name, line in sides.items():
        horizontal = name in ("bottom", "top")
        want = "HorizontalConstraint" if horizontal else "VerticalConstraint"
        if not any(type_name(c) == want for c in _constraints_on(b.sketch, line)):
            add = b.gc.addHorizontal if horizontal else b.gc.addVertical
            b.constraint(add(line))


def _rectangle_lines(b: _Builder, collection: Any) -> list[Any]:
    lines = items(collection)
    for line in lines:
        b.curve(line)
    return lines


def _build_rectangle_two_point(b: _Builder) -> DrawnItem:
    lines = _rectangle_lines(
        b, b.lines.addTwoPointRectangle(b.point("corner1"), b.point("corner2"))
    )
    sides, corners = _axis_rectangle(lines)
    _ensure_axis_constraints(b, sides)
    return b.done(None, {**sides, **corners})


def _diagonals(b: _Builder, corners: Mapping[str, Any]) -> list[Any]:
    """Construction diagonals Fusion may have added with the rectangle (bl-tr first)."""
    found: list[Any] = []
    for one, two in (("bl", "tr"), ("br", "tl")):
        for line in _lines_at(b.sketch, corners[one]):
            if _get(line, "isConstruction") and _has_ends(line, corners[one], corners[two]):
                found.append(line)
                break
    return found


def _build_rectangle_center(b: _Builder) -> DrawnItem:
    center_in = b.point("center")
    center_pos = _p3(_xy(center_in))
    lines = _rectangle_lines(b, b.lines.addCenterPointRectangle(center_pos, b.point("corner")))
    sides, corners = _axis_rectangle(lines)
    _ensure_axis_constraints(b, sides)
    diagonals = _diagonals(b, corners)
    fusion_center: Any = None
    for diagonal in diagonals:
        b.created(diagonal)
        b.curves.append(diagonal)
        for constraint in _constraints_on(b.sketch, diagonal):
            if fusion_center is None and type_name(constraint) == "MidPointConstraint":
                fusion_center = _get(constraint, "point")
    if diagonals:
        diagonal = diagonals[0]
    else:
        diagonal = b.curve(b.lines.addByTwoPoints(corners["bl"], corners["tr"]), helper=True)
    if fusion_center is not None:
        b.adopt_point(fusion_center)
        if _is_point(center_in) and not same_entity(center_in, fusion_center):
            b.constraint(b.gc.addCoincident(fusion_center, center_in))
            center = center_in
        else:
            center = fusion_center
    else:
        center = center_in if _is_point(center_in) else b.new_point(center_pos)
        b.constraint(b.gc.addMidPoint(center, diagonal))
    return b.done(None, {**sides, **corners, "center": center, "diagonal": diagonal})


def _walk_rectangle(
    lines: Sequence[Any], first: math2d.Point, second: math2d.Point
) -> tuple[list[Any], list[Any]]:
    """Edges ``e0..e3`` and vertices ``v0..v3`` of a closed 4-line loop: ``v0`` nearest
    ``first``, ``v1`` nearest ``second``, ``e_k = v_k -> v_{k+1}``."""
    corners = _unique_points(lines)
    if len(lines) != 4 or len(corners) != 4:
        raise _api_error(f"expected 4 rectangle lines on 4 corners, got {len(lines)} lines")
    v0 = min(corners, key=lambda c: math.dist(_xy(c), first))
    rest = [c for c in corners if not same_entity(c, v0)]
    v1 = min(rest, key=lambda c: math.dist(_xy(c), second))
    not_a_loop = _api_error("the rectangle Fusion created is not a closed loop of 4 lines")
    first_edge = next((ln for ln in lines if _has_ends(ln, v0, v1)), None)
    if first_edge is None:
        raise not_a_loop
    vertices, edges, current = [v0, v1], [first_edge], v1
    while len(edges) < 4:
        edge = next(
            (ln for ln in lines if not any(ln is e for e in edges) and _is_end_of(current, ln)),
            None,
        )
        if edge is None:
            raise not_a_loop
        edges.append(edge)
        start, end = _curve_ends(edge)
        current = start if same_entity(end, current) else end
        if len(vertices) < 4:
            vertices.append(current)
    if not same_entity(current, v0):
        raise not_a_loop
    return edges, vertices


def _build_rectangle_three_point(b: _Builder) -> DrawnItem:
    p1, p2, p3 = b.point("p1"), b.point("p2"), b.point("p3")
    lines = _rectangle_lines(b, b.lines.addThreePointRectangle(p1, p2, _p3(_xy(p3))))
    edges, vertices = _walk_rectangle(lines, _xy(p1), _xy(p2))
    b.coincide(p3, edges[2])
    return b.done(None, _indexed(edges, vertices))


# circles and arcs (5.4, 5.5) ---------------------------------------------------------------


def _build_circle_center_radius(b: _Builder) -> DrawnItem:
    circles = b.sketch.sketchCurves.sketchCircles
    circle = b.curve(circles.addByCenterRadius(b.point("center"), b.length("radius")))
    return b.done(circle, {"center": circle.centerSketchPoint})


def _build_circle_points(b: _Builder) -> DrawnItem:
    names = ("p1", "p2") if b.spec.mode == "two_point" else ("p1", "p2", "p3")
    inputs = [b.point(name) for name in names]
    circles = b.sketch.sketchCurves.sketchCircles
    positions = [_p3(_xy(p)) for p in inputs]
    if b.spec.mode == "two_point":
        circle = circles.addByTwoPoints(*positions)
    else:
        circle = circles.addByThreePoints(*positions)
    b.curve(circle)
    for point in inputs:
        b.coincide(point, circle)
    return b.done(circle, {"center": circle.centerSketchPoint})


def _build_arc_three_point(b: _Builder) -> DrawnItem:
    start, mid, end = b.point("start"), b.point("mid"), b.point("end")
    arcs = b.sketch.sketchCurves.sketchArcs
    arc = b.curve(arcs.addByThreePoints(start, _p3(_xy(mid)), end))
    b.coincide(mid, arc)
    return b.done(arc, _arc_subs(arc, _xy(start)))


def _build_arc_center_start_sweep(b: _Builder) -> DrawnItem:
    center, start = b.point("center"), b.point("start")
    sweep = float(b.scope.units.angle(_number(b.spec, "sweep")))
    arcs = b.sketch.sketchCurves.sketchArcs
    arc = b.curve(arcs.addByCenterStartSweep(center, start, sweep))
    return b.done(arc, _arc_subs(arc, _xy(start)))


def _curve_ends(curve: Any) -> tuple[Any, Any]:
    return curve.startSketchPoint, curve.endSketchPoint


def _is_end_of(point: Any, curve: Any) -> bool:
    return any(same_entity(point, p) for p in _curve_ends(curve))


def _tangent_source(b: _Builder, spec_value: str, point: Any, where: str) -> Any:
    """The line or arc the tangent arc continues: the item of ``id.sub`` if it is one, else
    the curve(s) ending at ``point`` (a curve *ending* there is preferred over one starting
    there, non-construction over construction)."""
    parsed = parse_entity_spec(spec_value, where)
    if isinstance(parsed, LocalPath) and parsed.sub is not None:
        owner = b.scope.get(parsed.id)
        if type_name(owner) in (LINE, ARC) and _is_end_of(point, owner):
            return owner
    connected = _get(point, "connectedEntities")
    pool = items(connected) if connected is not None else sketch_curves(b.sketch)
    candidates = [c for c in pool if type_name(c) in (LINE, ARC) and _is_end_of(point, c)]
    for prefer in (
        lambda c: not _get(c, "isConstruction", False),
        lambda c: same_entity(point, c.endSketchPoint),
    ):
        if len(candidates) > 1:
            narrowed = [c for c in candidates if prefer(c)]
            candidates = narrowed or candidates
    if not candidates:
        raise _invalid(
            f"{where} {spec_value!r} is not the end point of a line or arc",
            where,
            spec_value,
            reason="no_tangent_curve",
        )
    if len(candidates) > 1:
        raise _invalid(
            f"{where} {spec_value!r} ends several curves: use the end of one curve ('l1.end')",
            where,
            spec_value,
            reason="ambiguous_tangent_curve",
            candidates=[c.entityToken for c in candidates],
        )
    return candidates[0]


def _tangent_direction(curve: Any, point: Any) -> math2d.Point:
    """Direction that continues ``curve`` beyond its end ``point`` (sketch space)."""
    p = _xy(point)
    at_end = same_entity(point, curve.endSketchPoint)
    if type_name(curve) == LINE:
        other = _xy(curve.startSketchPoint if at_end else curve.endSketchPoint)
        return (p[0] - other[0], p[1] - other[1])
    c = _xy(curve.centerSketchPoint)
    ccw = (-(p[1] - c[1]), p[0] - c[0])  # arcs run counter-clockwise start -> end
    return ccw if at_end else (-ccw[0], -ccw[1])


def _build_arc_tangent(b: _Builder) -> DrawnItem:
    source_spec = b.spec.get("from")
    where = b.spec.at("from")
    start = b.resolve_point(source_spec, where)
    source = _tangent_source(b, source_spec, start, where)
    to = b.point("to")
    try:
        mid = math2d.tangent_arc_mid(_xy(start), _tangent_direction(source, start), _xy(to))
    except ValueError as exc:
        raise _geometry_error(exc, b.spec.at("to")) from None
    arcs = b.sketch.sketchCurves.sketchArcs
    arc = b.curve(arcs.addByThreePoints(start, _p3(mid), to))
    b.constraint(b.gc.addTangent(source, arc))
    return b.done(arc, _arc_subs(arc, _xy(start)))


# ellipse and spline (5.6) ------------------------------------------------------------------


def _build_ellipse(b: _Builder) -> DrawnItem:
    center = b.point("center")
    major, minor = b.length("major_radius"), b.length("minor_radius")
    try:
        major_point, minor_point = math2d.ellipse_points(
            _xy(center), major, minor, _number(b.spec, "angle", 0.0)
        )
    except ValueError as exc:
        raise _geometry_error(exc, b.spec.at("minor_radius")) from None
    ellipses = b.sketch.sketchCurves.sketchEllipses
    ellipse = b.curve(ellipses.add(center, _p3(major_point), _p3(minor_point)))
    return b.done(ellipse, {"center": ellipse.centerSketchPoint})


def _build_spline(b: _Builder) -> DrawnItem:
    where = b.spec.at("points")
    fit = adsk.core.ObjectCollection.create()
    for k, value in enumerate(b.spec.get("points")):
        fit.add(b.resolve_point(value, f"{where}[{k}]"))
    spline = b.curve(b.sketch.sketchCurves.sketchFittedSplines.add(fit))
    if _flag(b.spec, "closed", False):
        spline.isClosed = True
    subs = {"start": spline.startSketchPoint, "end": spline.endSketchPoint}
    subs.update({f"fp{k}": p for k, p in enumerate(items(spline.fitPoints))})
    return b.done(spline, subs)


# polygon (5.7) -----------------------------------------------------------------------------


def _build_polygon(b: _Builder) -> DrawnItem:
    center_in = b.point("center")
    radius = b.length("radius")
    sides = int(b.spec.get("sides"))
    mode = b.spec.mode or "inscribed"
    try:
        vertices_xy = math2d.polygon_vertices(
            _xy(center_in), radius, sides, mode, _number(b.spec, "angle", 0.0)
        )
    except ValueError as exc:
        raise _geometry_error(exc, b.spec.at("radius")) from None
    circles = b.sketch.sketchCurves.sketchCircles
    circle = b.curve(circles.addByCenterRadius(center_in, radius), helper=True)
    edges, vertices = _chain(b, [_p3(v) for v in vertices_xy], closed=True)
    if _flag(b.spec, "constrain", True):
        for edge in edges[1:]:
            b.constraint(b.gc.addEqual(edges[0], edge))
        if mode == "inscribed":
            for vertex in vertices:
                b.constraint(b.gc.addCoincident(vertex, circle))
        else:
            for edge in edges:
                b.constraint(b.gc.addTangent(edge, circle))
    subs = _indexed(edges, vertices)
    subs.update(center=circle.centerSketchPoint, circle=circle)
    return b.done(None, subs)


# slot (5.8) --------------------------------------------------------------------------------


def _build_slot(b: _Builder) -> DrawnItem:
    p1, p2 = b.point("p1"), b.point("p2")
    mode = b.spec.mode or "center_to_center"
    try:
        g = math2d.slot(_xy(p1), _xy(p2), b.length("width"), mode)
    except ValueError as exc:
        raise _geometry_error(exc, b.spec.at("p2")) from None
    shared = mode == "center_to_center"
    c0 = p1 if shared and _is_point(p1) else _p3(g.c0)
    c1 = p2 if shared and _is_point(p2) else _p3(g.c1)
    arcs = b.sketch.sketchCurves.sketchArcs
    end0 = b.curve(arcs.addByCenterStartSweep(c0, _p3(g.end0[0]), math.pi))
    end1 = b.curve(arcs.addByCenterStartSweep(c1, _p3(g.end1[0]), math.pi))
    ends0, ends1 = _arc_subs(end0, g.end0[0]), _arc_subs(end1, g.end1[0])
    side0 = b.curve(b.lines.addByTwoPoints(ends0["end"], ends1["start"]))
    side1 = b.curve(b.lines.addByTwoPoints(ends1["end"], ends0["start"]))
    center0, center1 = end0.centerSketchPoint, end1.centerSketchPoint
    axis = b.curve(b.lines.addByTwoPoints(center0, center1), helper=True)
    for side in (side0, side1):
        for end in (end0, end1):
            b.constraint(b.gc.addTangent(side, end))
    b.constraint(b.gc.addEqual(end0, end1))
    subs = {
        "side0": side0,
        "side1": side1,
        "end0": end0,
        "end1": end1,
        "axis": axis,
        "c0": center0,
        "c1": center1,
    }
    return b.done(None, subs)


# text (5.9) --------------------------------------------------------------------------------


def default_text_size(text: str, height: float) -> tuple[float, float]:
    """Box ``(w, h)`` (units of ``height``) that fits ``text``: ``TEXT_CHAR_WIDTH`` heights
    per character of the longest line, ``TEXT_LINE_PITCH`` heights per line."""
    lines = text.split("\n")
    longest = max(1, *(len(line) for line in lines))
    return longest * TEXT_CHAR_WIDTH * height, len(lines) * TEXT_LINE_PITCH * height


def _build_text(b: _Builder) -> DrawnItem:
    text = str(b.spec.get("text"))
    height = b.length("height")
    corner = _xy(b.point("corner"))
    size = b.spec.get("size")
    if size is not None:
        width, box_height = (float(b.scope.units.length(v)) for v in size)
    else:
        width, box_height = default_text_size(text, height)
    texts = b.sketch.sketchTexts
    if hasattr(texts, "createInput2"):
        data = texts.createInput2(text, height)
        h_align = getattr(adsk.core.HorizontalAlignments, H_ALIGN[b.spec.get("h_align", "left")])
        v_align = getattr(adsk.core.VerticalAlignments, V_ALIGN[b.spec.get("v_align", "bottom")])
        diagonal = (corner[0] + width, corner[1] + box_height)
        data.setAsMultiLine(_p3(corner), _p3(diagonal), h_align, v_align, 0)
    else:
        data = texts.createInput(text, height, _p3(corner))
    font = b.spec.get("font")
    if font:
        data.fontName = font
    return b.done(b.created(texts.add(data)))


# --- dispatch ------------------------------------------------------------------------------

BUILDERS: dict[tuple[str, str | None], Builder] = {
    ("point", None): _build_point,
    ("line", None): _build_line,
    ("polyline", None): _build_polyline,
    ("rectangle", "two_point"): _build_rectangle_two_point,
    ("rectangle", "center"): _build_rectangle_center,
    ("rectangle", "three_point"): _build_rectangle_three_point,
    ("circle", "center_radius"): _build_circle_center_radius,
    ("circle", "two_point"): _build_circle_points,
    ("circle", "three_point"): _build_circle_points,
    ("arc", "three_point"): _build_arc_three_point,
    ("arc", "center_start_sweep"): _build_arc_center_start_sweep,
    ("arc", "tangent"): _build_arc_tangent,
    ("ellipse", None): _build_ellipse,
    ("polygon", "inscribed"): _build_polygon,
    ("polygon", "circumscribed"): _build_polygon,
    ("spline", None): _build_spline,
    ("slot", "center_to_center"): _build_slot,
    ("slot", "overall"): _build_slot,
    ("text", None): _build_text,
}


def build_item(scope: BatchScope, journal: SketchJournal, spec: DrawSpec) -> DrawnItem:
    """Draw one validated item, journal what it created and register its local paths."""
    try:
        drawn = BUILDERS[(spec.kind, spec.mode)](_Builder(scope, journal, spec))
        if spec.id is not None:
            scope.register(spec.id, drawn.entity, drawn.subs, field=spec.at("id"))
    except Exception as exc:
        _relocate(exc, spec.index, spec.id)
    return drawn


def build_items(
    scope: BatchScope, journal: SketchJournal, specs: Sequence[DrawSpec]
) -> list[DrawnItem]:
    """Mutation phase of ``sketch.draw``: the items in order (later items see earlier ids)."""
    return [build_item(scope, journal, spec) for spec in specs]

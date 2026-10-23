"""Pure 2D geometry for sketch tools (c05 D7, D8, D10, D11). No ``adsk``.

Points are ``(x, y)`` tuples of floats (any sequence of two numbers is accepted as input);
all lengths are in one unit chosen by the caller (sketch-space mm in the handlers). Angles
given as parameters are in degrees. Invalid geometric input (zero-length axis, collinear
tangent arc, region without interior, ...) raises ``ValueError``; handlers turn it into
``INVALID_PARAMS`` with their own context.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Literal

Point = tuple[float, float]
Vec = Sequence[float]
Loop = Sequence[Vec]

RegionLocation = Literal["inside", "outside", "on_boundary"]
INSIDE: RegionLocation = "inside"
OUTSIDE: RegionLocation = "outside"
ON_BOUNDARY: RegionLocation = "on_boundary"

POLYGON_MIN_SIDES = 3
POLYGON_MAX_SIDES = 64
BOUNDARY_TOL = 1e-3
INTERIOR_CLEARANCE = 0.005
EXTRA_SCANLINES = 8
TEXT_OFFSET = 5.0
ANGULAR_TEXT_DISTANCE = 10.0
RADIAL_TEXT_ANGLE = 45.0
_EPS = 1e-12


# --- vector helpers -------------------------------------------------------------------


def _pt(p: Vec) -> Point:
    if len(p) != 2:
        raise ValueError(f"expected a 2D point, got {p!r}")
    return (float(p[0]), float(p[1]))


def _add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _scale(a: Point, k: float) -> Point:
    return (a[0] * k, a[1] * k)


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _norm(a: Point) -> float:
    return math.hypot(a[0], a[1])


def _unit(a: Point, what: str = "vector") -> Point:
    n = _norm(a)
    if n <= _EPS:
        raise ValueError(f"{what} has zero length")
    return (a[0] / n, a[1] / n)


def _left(a: Point) -> Point:
    """``a`` rotated by +90 degrees."""
    return (-a[1], a[0])


def _polar(center: Point, radius: float, angle_rad: float) -> Point:
    return (center[0] + radius * math.cos(angle_rad), center[1] + radius * math.sin(angle_rad))


def _midpoint(a: Point, b: Point) -> Point:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


# --- construction helpers (D7) --------------------------------------------------------


def polygon_vertices(
    center: Vec,
    radius: float,
    sides: int,
    mode: str = "inscribed",
    angle: float = 0.0,
) -> list[Point]:
    """Vertices ``v0..v{n-1}`` of a regular polygon, counter-clockwise.

    ``inscribed``: the vertices lie on the circle of ``radius``; ``v0`` is at ``angle``
    degrees from +X. ``circumscribed``: the edges are tangent to the circle; the midpoint of
    edge ``e0`` (``v0 -> v1``) is at ``angle`` (Fusion UI convention: the picked point is a
    vertex for inscribed and an edge midpoint for circumscribed polygons).
    """
    c = _pt(center)
    if not POLYGON_MIN_SIDES <= sides <= POLYGON_MAX_SIDES:
        raise ValueError(
            f"polygon sides must be {POLYGON_MIN_SIDES}..{POLYGON_MAX_SIDES}, got {sides}"
        )
    if radius <= 0:
        raise ValueError(f"polygon radius must be positive, got {radius}")
    step = 2.0 * math.pi / sides
    start = math.radians(angle)
    if mode == "inscribed":
        r = float(radius)
    elif mode == "circumscribed":
        r = radius / math.cos(math.pi / sides)
        start -= step / 2.0
    else:
        raise ValueError(f"polygon mode must be 'inscribed' or 'circumscribed', got {mode!r}")
    return [_polar(c, r, start + k * step) for k in range(sides)]


@dataclass(frozen=True)
class SlotGeometry:
    """A slot: two half-circle ends joined by two side lines (counter-clockwise loop).

    ``end0``/``end1`` are ``(start, end)`` of the 180-degree arcs swept counter-clockwise
    around ``c0``/``c1`` (``sketchArcs.addByCenterStartSweep(center, start, pi)``);
    ``side0`` runs ``c0 -> c1`` on the right of the axis, ``side1`` ``c1 -> c0`` on the left,
    so consecutive pieces share their end points.
    """

    c0: Point
    c1: Point
    radius: float
    side0: tuple[Point, Point]
    side1: tuple[Point, Point]
    end0: tuple[Point, Point]
    end1: tuple[Point, Point]


def slot(p1: Vec, p2: Vec, width: float, mode: str = "center_to_center") -> SlotGeometry:
    """Slot along ``p1 -> p2``. ``center_to_center``: ``p1``/``p2`` are the arc centres;
    ``overall``: they are the extreme points of the slot (centres inset by ``width / 2``)."""
    a, b = _pt(p1), _pt(p2)
    if width <= 0:
        raise ValueError(f"slot width must be positive, got {width}")
    r = width / 2.0
    u = _unit(_sub(b, a), "slot axis")
    if mode == "center_to_center":
        c0, c1 = a, b
    elif mode == "overall":
        if _norm(_sub(b, a)) <= width + _EPS:
            raise ValueError("overall slot length must be greater than its width")
        c0, c1 = _add(a, _scale(u, r)), _sub(b, _scale(u, r))
    else:
        raise ValueError(f"slot mode must be 'center_to_center' or 'overall', got {mode!r}")
    n = _scale(_left(u), r)
    right0, right1 = _sub(c0, n), _sub(c1, n)
    left0, left1 = _add(c0, n), _add(c1, n)
    return SlotGeometry(
        c0=c0,
        c1=c1,
        radius=r,
        side0=(right0, right1),
        side1=(left1, left0),
        end0=(left0, right0),
        end1=(right1, left1),
    )


def ellipse_points(
    center: Vec, major_radius: float, minor_radius: float, angle: float = 0.0
) -> tuple[Point, Point]:
    """``(major_axis_point, minor_point)`` for ``sketchEllipses.add(center, major, point)``.

    The major axis points at ``angle`` degrees from +X; the minor point is the end of the
    minor axis, 90 degrees counter-clockwise from it.
    """
    c = _pt(center)
    if minor_radius <= 0 or major_radius <= 0:
        raise ValueError("ellipse radii must be positive")
    if minor_radius > major_radius:
        raise ValueError(
            f"ellipse minor radius {minor_radius} is greater than major radius {major_radius}"
        )
    a = math.radians(angle)
    return _polar(c, major_radius, a), _polar(c, minor_radius, a + math.pi / 2.0)


def tangent_arc_mid(start: Vec, tangent_dir: Vec, end: Vec) -> Point:
    """Mid point of the circular arc that leaves ``start`` along ``tangent_dir`` and ends at
    ``end`` (for ``sketchArcs.addByThreePoints(start, mid, end)``).

    ``ValueError`` when ``end`` is on the tangent line (no finite arc) or inputs degenerate.
    """
    s, e = _pt(start), _pt(end)
    d = _unit(_pt(tangent_dir), "tangent direction")
    w = _sub(e, s)
    chord = _norm(w)
    if chord <= _EPS:
        raise ValueError("tangent arc end coincides with its start")
    n = _left(d)
    nw = _dot(n, w)
    if abs(nw) <= 1e-9 * chord:
        raise ValueError("tangent arc end lies on the tangent line: no arc exists")
    # Centre C = s + t*n with |C - e| = |t|  =>  t = |w|^2 / (2 n.w); t > 0 -> turns left.
    t = chord * chord / (2.0 * nw)
    c = _add(s, _scale(n, t))
    radius = abs(t)
    a_start = math.atan2(s[1] - c[1], s[0] - c[0])
    a_end = math.atan2(e[1] - c[1], e[0] - c[0])
    if t > 0:
        sweep = (a_end - a_start) % (2.0 * math.pi)
        a_mid = a_start + sweep / 2.0
    else:
        sweep = (a_start - a_end) % (2.0 * math.pi)
        a_mid = a_start - sweep / 2.0
    return _polar(c, radius, a_mid)


def reflect(point: Vec, line_p1: Vec, line_p2: Vec) -> Point:
    """Mirror image of ``point`` about the infinite line through ``line_p1``, ``line_p2``."""
    p, a = _pt(point), _pt(line_p1)
    u = _unit(_sub(_pt(line_p2), a), "mirror axis")
    ap = _sub(p, a)
    foot = _add(a, _scale(u, _dot(ap, u)))
    return _sub(_scale(foot, 2.0), p)


# --- distances and regions (D8) -------------------------------------------------------


def _segment_distance(p: Point, a: Point, b: Point) -> float:
    ab = _sub(b, a)
    length2 = _dot(ab, ab)
    if length2 <= _EPS * _EPS:
        return _norm(_sub(p, a))
    t = max(0.0, min(1.0, _dot(_sub(p, a), ab) / length2))
    return _norm(_sub(p, _add(a, _scale(ab, t))))


def _closed(loop: Loop) -> list[Point]:
    pts = [_pt(p) for p in loop]
    if len(pts) < 3:
        raise ValueError(f"a loop needs at least 3 points, got {len(pts)}")
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def distance_to_polyline(point: Vec, polyline: Loop, closed: bool = False) -> float:
    """Shortest distance from ``point`` to the polyline (closing segment if ``closed``)."""
    p = _pt(point)
    pts = [_pt(q) for q in polyline]
    if not pts:
        raise ValueError("polyline is empty")
    if len(pts) == 1:
        return _norm(_sub(p, pts[0]))
    if closed and pts[0] != pts[-1]:
        pts.append(pts[0])
    return min(_segment_distance(p, a, b) for a, b in pairwise(pts))


def _crossings(p: Point, loop: list[Point]) -> int:
    count = 0
    for a, b in pairwise(loop):
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if p[0] < x:
                count += 1
    return count


def _loops(outer: Loop, inners: Sequence[Loop]) -> list[list[Point]]:
    return [_closed(outer), *(_closed(inner) for inner in inners)]


def point_in_region(
    point: Vec, outer: Loop, inners: Sequence[Loop] = (), tol: float = BOUNDARY_TOL
) -> RegionLocation:
    """Where ``point`` is relative to the region inside ``outer`` and outside all ``inners``.

    Even-odd rule over all loops; closer than ``tol`` to any loop -> ``"on_boundary"``.
    Loops are polylines, implicitly closed.
    """
    p = _pt(point)
    loops = _loops(outer, inners)
    if min(distance_to_polyline(p, loop) for loop in loops) < tol:
        return ON_BOUNDARY
    crossings = sum(_crossings(p, loop) for loop in loops)
    return INSIDE if crossings % 2 == 1 else OUTSIDE


def _signed_area_centroid(loop: list[Point]) -> tuple[float, Point]:
    area2 = 0.0
    cx = cy = 0.0
    for a, b in pairwise(loop):
        cross = _cross(a, b)
        area2 += cross
        cx += (a[0] + b[0]) * cross
        cy += (a[1] + b[1]) * cross
    if abs(area2) <= _EPS:
        return 0.0, loop[0]
    return area2 / 2.0, (cx / (3.0 * area2), cy / (3.0 * area2))


def region_centroid(outer: Loop, inners: Sequence[Loop] = ()) -> Point:
    """Area centroid of ``outer`` minus ``inners`` (loop orientation does not matter)."""
    loops = _loops(outer, inners)
    total = 0.0
    sx = sy = 0.0
    for k, loop in enumerate(loops):
        area, (cx, cy) = _signed_area_centroid(loop)
        weight = abs(area) if k == 0 else -abs(area)
        total += weight
        sx += weight * cx
        sy += weight * cy
    if total <= _EPS:
        raise ValueError("region has no area")
    return (sx / total, sy / total)


def _bbox(loop: list[Point]) -> tuple[Point, Point]:
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    return (min(xs), min(ys)), (max(xs), max(ys))


def _widest_interval(
    y: float, loops: list[list[Point]], min_clearance: float
) -> tuple[float, float] | None:
    """Widest ``[x0, x1]`` inside the region on the horizontal line ``y`` (even-odd).

    Intervals whose middle is within ``min_clearance`` of a loop are skipped: they run along
    a horizontal edge rather than through the interior.
    """
    xs: list[float] = []
    for loop in loops:
        for a, b in pairwise(loop):
            if (a[1] > y) != (b[1] > y):
                xs.append(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]))
    xs.sort()
    best: tuple[float, float] | None = None
    for x0, x1 in zip(xs[0::2], xs[1::2], strict=False):
        if x1 - x0 <= _EPS or (best is not None and x1 - x0 <= best[1] - best[0]):
            continue
        mid = ((x0 + x1) / 2.0, y)
        if min(distance_to_polyline(mid, loop) for loop in loops) > min_clearance:
            best = (x0, x1)
    return best


def interior_point(outer: Loop, inners: Sequence[Loop] = (), centroid: Vec | None = None) -> Point:
    """A point strictly inside the region (inside ``outer``, outside every inner loop), D8.

    1. The centroid (``centroid`` if given, e.g. from ``areaProperties``, else computed from
       the loops) when it is inside and at least 0.5 % of the outer bbox diagonal away from
       every loop.
    2. Else the middle of the widest inside interval of the horizontal line through the
       centroid.
    3. Else the widest such interval over 8 more horizontals evenly spread over the bbox.
    ``ValueError`` if the region has no interior.
    """
    loops = _loops(outer, inners)
    lo, hi = _bbox(loops[0])
    diagonal = _norm(_sub(hi, lo))
    clearance = INTERIOR_CLEARANCE * diagonal
    on_edge = 1e-9 * diagonal
    c = _pt(centroid) if centroid is not None else region_centroid(outer, inners)
    if (
        sum(_crossings(c, loop) for loop in loops) % 2 == 1
        and min(distance_to_polyline(c, loop) for loop in loops) >= clearance
    ):
        return c
    interval = _widest_interval(c[1], loops, on_edge)
    if interval is not None:
        return ((interval[0] + interval[1]) / 2.0, c[1])
    height = hi[1] - lo[1]
    best: tuple[float, float, float] | None = None
    for k in range(1, EXTRA_SCANLINES + 1):
        y = lo[1] + height * k / (EXTRA_SCANLINES + 1)
        found = _widest_interval(y, loops, on_edge)
        if found is not None and (best is None or found[1] - found[0] > best[1] - best[0]):
            best = (found[0], found[1], y)
    if best is None:
        raise ValueError("region has no interior")
    return ((best[0] + best[1]) / 2.0, best[2])


# --- dimension text placement (D10) ---------------------------------------------------


def _segment(value: Any, what: str) -> tuple[Point, Point]:
    if not isinstance(value, Sequence) or len(value) != 2:
        raise ValueError(f"{what} must be [[x, y], [x, y]]")
    return _pt(value[0]), _pt(value[1])


def _line_intersection(l1: tuple[Point, Point], l2: tuple[Point, Point]) -> Point:
    d1, d2 = _sub(l1[1], l1[0]), _sub(l2[1], l2[0])
    denom = _cross(d1, d2)
    if abs(denom) <= 1e-9 * max(_norm(d1) * _norm(d2), _EPS):
        raise ValueError("angular dimension lines are parallel")
    t = _cross(_sub(l2[0], l1[0]), d2) / denom
    return _add(l1[0], _scale(d1, t))


def _away_from(x: Point, seg: tuple[Point, Point]) -> Point:
    """Unit direction from ``x`` towards the farther end of ``seg``."""
    far = max(seg, key=lambda q: _norm(_sub(q, x)))
    return _unit(_sub(far, x), "angular dimension line")


def default_dimension_text_point(
    kind: str,
    geometry: Mapping[str, Any],
    offset: float = TEXT_OFFSET,
    angular_distance: float = ANGULAR_TEXT_DISTANCE,
) -> Point:
    """Default ``text_at`` of a sketch dimension (D10), sketch space.

    ``geometry`` by ``kind``:
    - ``distance``: ``{"p1", "p2", "orientation"?: aligned|horizontal|vertical}`` ->
      midpoint shifted by ``offset`` along the left normal of ``p1 -> p2`` (aligned), or
      along +Y / -X for horizontal / vertical dimensions;
    - ``offset``: ``{"line": [p, q], "point"}`` (``point`` is the point or a point of the
      parallel line) -> midpoint between ``point`` and its foot on the line, shifted by
      ``offset`` along the line;
    - ``radial`` / ``diameter``: ``{"center", "radius", "angle"?: deg = 45}`` -> ``offset``
      outside the circle at ``angle``;
    - ``angular``: ``{"line1": [p, q], "line2": [p, q]}`` -> on the bisector of the interior
      angle (towards the far ends of both lines) at ``angular_distance`` from the
      intersection; this point selects the measured quadrant.
    """
    if kind == "distance":
        p1, p2 = _pt(geometry["p1"]), _pt(geometry["p2"])
        mid = _midpoint(p1, p2)
        orientation = geometry.get("orientation", "aligned")
        if orientation == "horizontal":
            normal: Point = (0.0, 1.0)
        elif orientation == "vertical":
            normal = (-1.0, 0.0)
        elif orientation == "aligned":
            d = _sub(p2, p1)
            normal = _left(_unit(d)) if _norm(d) > _EPS else (0.0, 1.0)
        else:
            raise ValueError(f"unknown distance orientation {orientation!r}")
        return _add(mid, _scale(normal, offset))
    if kind == "offset":
        a, b = _segment(geometry["line"], "offset line")
        p = _pt(geometry["point"])
        u = _unit(_sub(b, a), "offset dimension line")
        foot = _add(a, _scale(u, _dot(_sub(p, a), u)))
        if _norm(_sub(p, foot)) <= _EPS:
            return _add(foot, _scale(_left(u), offset))
        return _add(_midpoint(foot, p), _scale(u, offset))
    if kind in ("radial", "diameter"):
        center = _pt(geometry["center"])
        radius = float(geometry["radius"])
        angle = math.radians(float(geometry.get("angle", RADIAL_TEXT_ANGLE)))
        return _polar(center, radius + offset, angle)
    if kind == "angular":
        l1 = _segment(geometry["line1"], "line1")
        l2 = _segment(geometry["line2"], "line2")
        x = _line_intersection(l1, l2)
        bisector = _add(_away_from(x, l1), _away_from(x, l2))
        if _norm(bisector) <= 1e-9:
            raise ValueError("angular dimension lines point in opposite directions")
        return _add(x, _scale(_unit(bisector), angular_distance))
    raise ValueError(f"no default text point for dimension kind {kind!r}")

"""``fusion_addin.sketch.math2d``: pure 2D sketch geometry (c05 1.3-1.6, D7, D8, D10)."""

import math

import pytest

from fusion_addin.sketch import math2d
from fusion_addin.sketch.math2d import (
    INSIDE,
    ON_BOUNDARY,
    OUTSIDE,
    Point,
    default_dimension_text_point,
    distance_to_polyline,
    ellipse_points,
    interior_point,
    point_in_region,
    polygon_vertices,
    reflect,
    region_centroid,
    slot,
    tangent_arc_mid,
)

S2 = math.sqrt(2.0) / 2.0


def approx_pt(p: Point, abs_tol: float = 1e-9) -> object:
    return pytest.approx(p, abs=abs_tol)


def rect(x0: float, y0: float, x1: float, y1: float) -> list[Point]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def circle_loop(cx: float, cy: float, r: float, n: int = 64) -> list[Point]:
    return [
        (cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n))
        for k in range(n)
    ]


def circumcenter(a: Point, b: Point, c: Point) -> Point:
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    ux = (
        (a[0] ** 2 + a[1] ** 2) * (b[1] - c[1])
        + (b[0] ** 2 + b[1] ** 2) * (c[1] - a[1])
        + (c[0] ** 2 + c[1] ** 2) * (a[1] - b[1])
    ) / d
    uy = (
        (a[0] ** 2 + a[1] ** 2) * (c[0] - b[0])
        + (b[0] ** 2 + b[1] ** 2) * (a[0] - c[0])
        + (c[0] ** 2 + c[1] ** 2) * (b[0] - a[0])
    ) / d
    return (ux, uy)


# --- polygon ------------------------------------------------------------------------


def test_polygon_inscribed_hexagon() -> None:
    v = polygon_vertices((0, 0), 20, 6, "inscribed")
    assert len(v) == 6
    assert v[0] == approx_pt((20.0, 0.0))
    assert v[1] == approx_pt((10.0, 10.0 * math.sqrt(3)))
    assert v[3] == approx_pt((-20.0, 0.0))
    assert all(math.hypot(*p) == pytest.approx(20.0) for p in v)
    edges = [math.dist(v[k], v[(k + 1) % 6]) for k in range(6)]
    assert edges == pytest.approx([20.0] * 6)


def test_polygon_inscribed_angle_and_center() -> None:
    v = polygon_vertices([10, 5], 2, 4, "inscribed", angle=90)
    assert v[0] == approx_pt((10.0, 7.0))
    assert v[1] == approx_pt((8.0, 5.0))


def test_polygon_circumscribed_square() -> None:
    v = polygon_vertices((0, 0), 10, 4, "circumscribed")
    assert v == [
        approx_pt((10.0, -10.0)),
        approx_pt((10.0, 10.0)),
        approx_pt((-10.0, 10.0)),
        approx_pt((-10.0, -10.0)),
    ]


def test_polygon_circumscribed_edges_tangent() -> None:
    v = polygon_vertices((3, -2), 15, 7, "circumscribed", angle=30)
    for k in range(7):
        a, b = v[k], v[(k + 1) % 7]
        assert distance_to_polyline((3, -2), [a, b]) == pytest.approx(15.0)
    mid0 = ((v[0][0] + v[1][0]) / 2, (v[0][1] + v[1][1]) / 2)
    assert mid0 == approx_pt((3 + 15 * math.cos(math.radians(30)), -2 + 7.5))


@pytest.mark.parametrize(
    ("radius", "sides", "mode"),
    [(10, 2, "inscribed"), (10, 65, "inscribed"), (0, 6, "inscribed"), (10, 6, "spiral")],
)
def test_polygon_invalid(radius: float, sides: int, mode: str) -> None:
    with pytest.raises(ValueError):
        polygon_vertices((0, 0), radius, sides, mode)


# --- slot ---------------------------------------------------------------------------


def test_slot_center_to_center() -> None:
    s = slot((0, 0), (40, 0), 10, "center_to_center")
    assert s.c0 == (0.0, 0.0)
    assert s.c1 == (40.0, 0.0)
    assert s.radius == 5.0
    assert s.side0 == (approx_pt((0, -5)), approx_pt((40, -5)))
    assert s.side1 == (approx_pt((40, 5)), approx_pt((0, 5)))
    assert s.end0 == (approx_pt((0, 5)), approx_pt((0, -5)))
    assert s.end1 == (approx_pt((40, -5)), approx_pt((40, 5)))
    # The pieces form a closed chain: side0 -> end1 -> side1 -> end0.
    assert s.side0[1] == s.end1[0]
    assert s.end1[1] == s.side1[0]
    assert s.side1[1] == s.end0[0]
    assert s.end0[1] == s.side0[0]


def test_slot_overall_diagonal() -> None:
    s = slot((0, 0), (30, 40), 10, "overall")
    assert s.c0 == approx_pt((3.0, 4.0))
    assert s.c1 == approx_pt((27.0, 36.0))
    # The side lines are offset by the radius from the axis.
    assert distance_to_polyline(s.side0[0], [(0, 0), (30, 40)]) == pytest.approx(5.0)
    assert math.dist(*s.side0) == pytest.approx(40.0)


@pytest.mark.parametrize(
    ("p1", "p2", "width", "mode"),
    [
        ((0, 0), (0, 0), 10, "center_to_center"),
        ((0, 0), (10, 0), 0, "center_to_center"),
        ((0, 0), (10, 0), 10, "overall"),
        ((0, 0), (10, 0), 2, "round"),
    ],
)
def test_slot_invalid(p1: Point, p2: Point, width: float, mode: str) -> None:
    with pytest.raises(ValueError):
        slot(p1, p2, width, mode)


# --- ellipse ------------------------------------------------------------------------


def test_ellipse_points() -> None:
    assert ellipse_points((0, 0), 10, 4) == (approx_pt((10, 0)), approx_pt((0, 4)))
    major, minor = ellipse_points((1, 2), 10, 4, angle=90)
    assert major == approx_pt((1, 12))
    assert minor == approx_pt((-3, 2))


@pytest.mark.parametrize(("big", "small"), [(4, 10), (0, 0), (10, -1)])
def test_ellipse_invalid(big: float, small: float) -> None:
    with pytest.raises(ValueError):
        ellipse_points((0, 0), big, small)


# --- tangent arc ----------------------------------------------------------------------


def test_tangent_arc_quarter_left() -> None:
    assert tangent_arc_mid((0, 0), (1, 0), (1, 1)) == approx_pt((S2, 1 - S2))


def test_tangent_arc_quarter_right() -> None:
    assert tangent_arc_mid((0, 0), (1, 0), (1, -1)) == approx_pt((S2, -1 + S2))


def test_tangent_arc_major_arc_when_end_is_behind() -> None:
    assert tangent_arc_mid((0, 0), (2, 0), (-1, 1)) == approx_pt((S2, 1 + S2))


@pytest.mark.parametrize(
    ("start", "tangent", "end"),
    [((40, 0), (1, 0), (60, 20)), ((1, 2), (-3, 1), (5, 7)), ((0, 0), (0, -1), (-2, -9))],
)
def test_tangent_arc_is_tangent(start: Point, tangent: Point, end: Point) -> None:
    mid = tangent_arc_mid(start, tangent, end)
    c = circumcenter(start, mid, end)
    radius = math.dist(c, start)
    assert math.dist(c, mid) == pytest.approx(radius)
    # Tangent at start: radius is perpendicular to the direction.
    assert (start[0] - c[0]) * tangent[0] + (start[1] - c[1]) * tangent[1] == pytest.approx(
        0.0, abs=1e-9
    )
    # The arc leaves start along +tangent: the mid point is ahead of start.
    assert (mid[0] - start[0]) * tangent[0] + (mid[1] - start[1]) * tangent[1] > 0


@pytest.mark.parametrize(
    ("start", "tangent", "end"),
    [
        ((0, 0), (1, 0), (5, 0)),
        ((0, 0), (1, 0), (-5, 0)),
        ((0, 0), (1, 0), (0, 0)),
        ((0, 0), (0, 0), (1, 1)),
    ],
)
def test_tangent_arc_invalid(start: Point, tangent: Point, end: Point) -> None:
    with pytest.raises(ValueError):
        tangent_arc_mid(start, tangent, end)


# --- reflect / distance -------------------------------------------------------------------


def test_reflect() -> None:
    assert reflect((1, 2), (0, 0), (10, 0)) == approx_pt((1, -2))
    assert reflect((1, 2), (0, 0), (5, 5)) == approx_pt((2, 1))
    assert reflect((3, 3), (0, 0), (0, 1)) == approx_pt((-3, 3))
    assert reflect((0, 7), (0, 0), (0, 1)) == approx_pt((0, 7))
    assert reflect((5, 0), (1, 1), (2, 2)) == approx_pt((0, 5))


def test_reflect_degenerate_axis() -> None:
    with pytest.raises(ValueError):
        reflect((1, 1), (2, 2), (2, 2))


def test_distance_to_polyline() -> None:
    line = [(0, 0), (10, 0)]
    assert distance_to_polyline((5, 5), line) == pytest.approx(5.0)
    assert distance_to_polyline((13, 4), line) == pytest.approx(5.0)
    assert distance_to_polyline((-3, -4), line) == pytest.approx(5.0)
    square = rect(0, 0, 10, 10)
    assert distance_to_polyline((1, 4), square) == pytest.approx(4.0)
    assert distance_to_polyline((1, 4), square, closed=True) == pytest.approx(1.0)
    assert distance_to_polyline((3, 4), [(0, 0)]) == pytest.approx(5.0)
    with pytest.raises(ValueError):
        distance_to_polyline((0, 0), [])


# --- regions ------------------------------------------------------------------------------

PLATE = rect(0, 0, 40, 20)
HOLE = rect(15, 5, 25, 15)
L_SHAPE = [(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)]


@pytest.mark.parametrize(
    ("point", "expected"),
    [
        ((5, 5), INSIDE),
        ((20, 10), OUTSIDE),
        ((50, 5), OUTSIDE),
        ((-1, 10), OUTSIDE),
        ((40, 10), ON_BOUNDARY),
        ((40.0005, 10), ON_BOUNDARY),
        ((15, 10), ON_BOUNDARY),
        ((14.99, 10), INSIDE),
        ((0, 0), ON_BOUNDARY),
        ((25, 20), ON_BOUNDARY),
    ],
)
def test_point_in_rectangle_with_hole(point: Point, expected: str) -> None:
    assert point_in_region(point, PLATE, [HOLE]) == expected


def test_point_in_region_tolerance() -> None:
    assert point_in_region((14.9, 10), PLATE, [HOLE]) == INSIDE
    assert point_in_region((14.9, 10), PLATE, [HOLE], tol=0.2) == ON_BOUNDARY


def test_point_in_l_shape() -> None:
    assert point_in_region((15, 15), L_SHAPE) == OUTSIDE
    assert point_in_region((5, 15), L_SHAPE) == INSIDE
    assert point_in_region((15, 5), L_SHAPE) == INSIDE
    # Scanline through a vertex (y = 10) is counted once.
    assert point_in_region((5, 10), L_SHAPE) == INSIDE
    assert point_in_region((15, 10), L_SHAPE) == ON_BOUNDARY


def test_point_in_annulus_and_closed_loop_input() -> None:
    outer, inner = circle_loop(0, 0, 10), circle_loop(0, 0, 5)
    assert point_in_region((0, 0), outer, [inner]) == OUTSIDE
    assert point_in_region((7.5, 0), outer, [inner]) == INSIDE
    assert point_in_region((0, -7.5), [*outer, outer[0]], [inner]) == INSIDE


def test_point_in_region_bad_loop() -> None:
    with pytest.raises(ValueError):
        point_in_region((0, 0), [(0, 0), (1, 1)])


def test_region_centroid() -> None:
    assert region_centroid(PLATE) == approx_pt((20, 10))
    assert region_centroid(list(reversed(PLATE))) == approx_pt((20, 10))
    assert region_centroid(L_SHAPE) == approx_pt((25 / 3, 25 / 3))
    # Hole on the right shifts the centroid left: (800*20 - 100*30) / 700.
    assert region_centroid(PLATE, [rect(25, 5, 35, 15)]) == approx_pt((130 / 7, 10))


def test_interior_point_plain_rectangle_is_centroid() -> None:
    assert interior_point(PLATE) == approx_pt((20, 10))


def test_interior_point_l_shape_is_centroid() -> None:
    assert interior_point(L_SHAPE) == approx_pt((25 / 3, 25 / 3))


def test_interior_point_rectangle_with_centred_hole() -> None:
    p = interior_point(PLATE, [HOLE])
    assert p == approx_pt((7.5, 10))
    assert point_in_region(p, PLATE, [HOLE]) == INSIDE


def test_interior_point_annulus() -> None:
    outer, inner = circle_loop(0, 0, 10), circle_loop(0, 0, 5)
    p = interior_point(outer, [inner])
    # The centroid is in the hole; the scanline gives two equal intervals, either is fine.
    assert abs(p[0]) == pytest.approx(7.5, abs=1e-6)
    assert p[1] == pytest.approx(0.0, abs=1e-6)
    assert point_in_region(p, outer, [inner]) == INSIDE


def test_interior_point_uses_given_centroid() -> None:
    assert interior_point(PLATE, centroid=(30, 5)) == approx_pt((30, 5))


def test_interior_point_centroid_too_close_to_boundary() -> None:
    # 0.5 % of the diagonal (~44.7) is ~0.22: a centroid 0.1 from the edge is rejected and
    # the middle of the scanline interval is taken instead.
    assert interior_point(PLATE, centroid=(39.9, 10)) == approx_pt((20, 10))


def test_interior_point_c_shape() -> None:
    c_shape = [(0, 0), (30, 0), (30, 5), (5, 5), (5, 25), (30, 25), (30, 30), (0, 30)]
    p = interior_point(c_shape)
    assert point_in_region(p, c_shape) == INSIDE


def test_interior_point_extra_scanlines_when_centroid_line_misses() -> None:
    p = interior_point(PLATE, centroid=(5, 100))
    assert p == approx_pt((20, 20 / 9))
    assert point_in_region(p, PLATE) == INSIDE


def test_interior_point_skips_interval_along_hole_edge() -> None:
    # The line y = 10 runs along the top edge of the hole: its single "interval" [0, 40]
    # has its middle on the boundary, so the extra scanlines are used.
    hole = rect(10, 5, 30, 10)
    p = interior_point(PLATE, [hole], centroid=(20, 10))
    assert point_in_region(p, PLATE, [hole]) == INSIDE


def test_interior_point_degenerate() -> None:
    flat = [(0, 0), (10, 0), (20, 0)]
    with pytest.raises(ValueError):
        interior_point(flat)
    with pytest.raises(ValueError):
        interior_point(flat, centroid=(5, 0))


# --- dimension text points -------------------------------------------------------------


def test_distance_text_aligned() -> None:
    assert default_dimension_text_point("distance", {"p1": [0, 0], "p2": [10, 0]}) == (
        approx_pt((5, 5))
    )
    assert default_dimension_text_point("distance", {"p1": [0, 0], "p2": [0, 10]}) == (
        approx_pt((-5, 5))
    )


def test_distance_text_orientations() -> None:
    geo = {"p1": [0, 0], "p2": [10, 3]}
    assert default_dimension_text_point("distance", {**geo, "orientation": "horizontal"}) == (
        approx_pt((5, 6.5))
    )
    assert default_dimension_text_point("distance", {**geo, "orientation": "vertical"}) == (
        approx_pt((0, 1.5))
    )
    with pytest.raises(ValueError):
        default_dimension_text_point("distance", {**geo, "orientation": "diagonal"})


def test_distance_text_coincident_points() -> None:
    assert default_dimension_text_point("distance", {"p1": [1, 1], "p2": [1, 1]}) == (
        approx_pt((1, 6))
    )


def test_offset_text() -> None:
    line = [[0, 0], [10, 0]]
    assert default_dimension_text_point("offset", {"line": line, "point": [3, 8]}) == (
        approx_pt((8, 4))
    )
    assert default_dimension_text_point("offset", {"line": line, "point": [3, 0]}) == (
        approx_pt((3, 5))
    )


def test_radial_text() -> None:
    geo = {"center": [0, 0], "radius": 10}
    assert default_dimension_text_point("radial", geo) == approx_pt((15 * S2, 15 * S2))
    assert default_dimension_text_point("diameter", {**geo, "angle": 90}) == approx_pt((0, 15))


def test_angular_text_right_angle() -> None:
    geo = {"line1": [[0, 0], [10, 0]], "line2": [[0, 0], [0, 10]]}
    assert default_dimension_text_point("angular", geo) == approx_pt((10 * S2, 10 * S2))
    # Lines that do not share an end point: the intersection is extrapolated.
    geo = {"line1": [[10, 0], [2, 0]], "line2": [[0, 3], [0, 10]]}
    assert default_dimension_text_point("angular", geo) == approx_pt((10 * S2, 10 * S2))


def test_angular_text_obtuse_and_quadrant() -> None:
    geo = {"line1": [[0, 0], [10, 0]], "line2": [[0, 0], [-10, 10]]}
    a = math.radians(67.5)
    assert default_dimension_text_point("angular", geo) == approx_pt(
        (10 * math.cos(a), 10 * math.sin(a))
    )
    # Crossing lines: the bisector points towards the far ends of both lines.
    geo = {"line1": [[-2, 0], [10, 0]], "line2": [[0, -2], [0, 10]]}
    assert default_dimension_text_point("angular", geo) == approx_pt((10 * S2, 10 * S2))


def test_angular_text_parallel() -> None:
    geo = {"line1": [[0, 0], [10, 0]], "line2": [[0, 5], [10, 5]]}
    with pytest.raises(ValueError):
        default_dimension_text_point("angular", geo)


def test_text_point_custom_offsets() -> None:
    assert default_dimension_text_point(
        "distance", {"p1": [0, 0], "p2": [10, 0]}, offset=2
    ) == approx_pt((5, 2))
    geo = {"line1": [[0, 0], [10, 0]], "line2": [[0, 0], [0, 10]]}
    assert default_dimension_text_point("angular", geo, angular_distance=S2 * 2) == approx_pt(
        (1, 1)
    )


def test_text_point_unknown_kind() -> None:
    with pytest.raises(ValueError):
        default_dimension_text_point("ellipse_major", {})


def test_constants() -> None:
    assert math2d.TEXT_OFFSET == 5.0
    assert math2d.ANGULAR_TEXT_DISTANCE == 10.0
    assert math2d.BOUNDARY_TOL == 1e-3

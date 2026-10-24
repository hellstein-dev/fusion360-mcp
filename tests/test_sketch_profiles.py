"""``fusion_addin.sketch.profiles``: ProfileSummary, ``resolve_profiles`` and
``resolve_curves`` (c05 D8, tasks 3.7-3.8)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.sketch import profiles as pr
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.sketch_refs import format_profile_ref, parse_profile_ref
from tests.fakes.design import FakeApp
from tests.fakes.fusion_sketch import (
    add_profile,
    auto_profiles,
    circle_curve,
    fake_sketch_design,
    make_sketch,
    polygon_curves,
)

adsk = fusion_addin.adsk

PLATE = [(0, 0), (10, 0), (10, 6), (0, 6)]  # cm: a 100 x 60 mm plate
HOLES = [(1.5, 1.5), (8.5, 1.5), (1.5, 4.5), (8.5, 4.5)]
L_SHAPE = [(0, 0), (4, 0), (4, 1), (1, 1), (1, 4), (0, 4)]


def _ref(entity: Any) -> dict[str, Any]:
    data = {"ref": entity.entityToken, "type": entity.objectType.rsplit("::", 1)[-1]}
    name = getattr(entity, "name", None)
    if isinstance(name, str):
        data["name"] = name
    return data


def _plate(design: Any = None, plane: str = "XY") -> tuple[Any, list[Any], list[Any]]:
    """Auto-profiled sketch: plate rectangle + 4 holes of r = 5 mm."""
    sketch = make_sketch(design, plane)
    lines = polygon_curves(sketch, PLATE)
    holes = [circle_curve(sketch, c, 0.5) for c in HOLES]
    sketch.profile_builder = auto_profiles
    return sketch, lines, holes


def _ctx(sketch: Any, units: str = "mm") -> Context:
    return Context(FakeApp(sketch.design), {"units": units})


def _error(fn: Any, *args: Any) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        fn(*args)
    return info.value


def _inner_count(profile: Any) -> int:
    return sum(1 for loop in profile.profileLoops if not loop.isOuter)


# --- summaries ---------------------------------------------------------------------------


def test_plate_with_four_holes() -> None:
    sketch, lines, holes = _plate()
    summaries = pr.summarize_all(sketch)
    assert len(summaries) == 5
    assert [s["ref"]["name"] for s in summaries] == ["P1", "P2", "P3", "P4", "P5"]
    plate = next(s for s in summaries if s["inner_loops"])
    assert len(plate["inner_loops"]) == 4
    assert plate["outer_loop"]["curves"] == [_ref(line) for line in lines]
    assert {loop["curves"][0]["ref"] for loop in plate["inner_loops"]} == {
        h.entityToken for h in holes
    }
    assert plate["sketch"] == _ref(sketch)
    assert plate["bbox"] == {"min": [0.0, 0.0], "max": [100.0, 60.0]}
    assert plate["centroid"] == pytest.approx([50.0, 30.0], abs=1e-6)
    assert plate["point"] == [50.0, 30.0]
    # 100 x 60 minus four (polygonal) circles of r = 5 mm.
    assert plate["area_mm2"] == pytest.approx(6000 - 4 * 3.14159 * 25, rel=1e-3)
    assert plate["ref"] == {
        "ref": format_profile_ref(sketch.entityToken, 50.0, 30.0),
        "type": "Profile",
        "name": plate["ref"]["name"],
    }
    hole = next(s for s in summaries if s["outer_loop"]["curves"] == [_ref(holes[0])])
    assert hole["point"] == pytest.approx([15.0, 15.0], abs=1e-4)
    assert hole["inner_loops"] == []


def test_concave_profile_point_is_inside_region() -> None:
    sketch = make_sketch()
    lines = polygon_curves(sketch, L_SHAPE)
    profile = add_profile(sketch, lines)
    summary = pr.summarize_profile(sketch, profile)
    assert summary["ref"]["name"] == "P1"
    cx, cy = summary["centroid"]
    assert cx == pytest.approx(95 / 7, abs=1e-6)
    assert cy == pytest.approx(95 / 7, abs=1e-6)
    x, y = summary["point"]
    assert (x, y) != (cx, cy)
    inside_foot = 0 < x < 40 and 0 < y < 10
    inside_leg = 0 < x < 10 and 0 < y < 40
    assert inside_foot or inside_leg
    token, rx, ry = parse_profile_ref(summary["ref"]["ref"])
    assert (token, rx, ry) == (sketch.entityToken, x, y)
    assert profile.accuracy_calls == [adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy]


def test_centroid_is_converted_to_sketch_space_on_xz() -> None:
    sketch = make_sketch(plane="XZ")
    lines = polygon_curves(sketch, [(1, 1), (3, 1), (3, 2), (1, 2)])
    add_profile(sketch, lines, centroid_space="model")
    (summary,) = pr.summarize_all(sketch)
    assert summary["centroid"] == pytest.approx([20.0, 15.0], abs=1e-6)
    assert summary["point"] == [20.0, 15.0]
    assert summary["bbox"] == {"min": [10.0, 10.0], "max": [30.0, 20.0]}


def test_summary_index_and_degenerate_profile() -> None:
    sketch = make_sketch(fake_sketch_design())
    empty = add_profile(sketch, [])
    summary = pr.summarize_profile(sketch, empty, index=3)
    assert summary["ref"]["name"] == "P3"
    assert summary["outer_loop"] == {"curves": []}
    assert summary["area_mm2"] == 0.0


def test_chain_polyline_orients_pieces() -> None:
    pieces = [[(0, 0), (1, 0)], [(1, 1), (1, 0)], [(1, 1), (0, 0)]]
    assert pr.chain_polyline(pieces) == [(0, 0), (1, 0), (1, 1)]
    assert pr.chain_polyline([[(0, 1), (0, 0)], [(0, 0), (1, 0)]]) == [(0, 1), (0, 0), (1, 0)]
    assert pr.chain_polyline([]) == []


# --- resolve_profiles ----------------------------------------------------------------------


def test_resolve_by_ref_and_by_point() -> None:
    sketch, _, holes = _plate()
    ctx = _ctx(sketch)
    plate = next(s for s in pr.summarize_all(sketch) if s["inner_loops"])
    (found,) = pr.resolve_profiles(ctx, plate["ref"]["ref"])
    assert _inner_count(found) == 4
    (hole,) = pr.resolve_profiles(ctx, {"sketch": sketch.entityToken, "point": [15, 15]})
    assert [pc.sketchEntity for pc in hole.profileLoops.item(0).profileCurves] == [holes[0]]


def test_point_uses_request_units() -> None:
    sketch, _, holes = _plate()
    ctx = _ctx(sketch, units="cm")
    (hole,) = pr.resolve_profiles(ctx, {"sketch": {"ref": sketch.entityToken}, "point": [8.5, 4.5]})
    assert hole.profileLoops.item(0).profileCurves.item(0).sketchEntity is holes[3]


def test_ref_survives_a_dimension_change() -> None:
    sketch, lines, _ = _plate()
    ctx = _ctx(sketch)
    ref = next(s for s in pr.summarize_all(sketch) if s["inner_loops"])["ref"]["ref"]
    old = sketch.profiles
    # The plate width goes from 100 mm to 110 mm: the right edge moves.
    lines[1].startSketchPoint._set((11.0, 0.0, 0.0))
    lines[1].endSketchPoint._set((11.0, 6.0, 0.0))
    (found,) = pr.resolve_profiles(ctx, ref)
    assert found not in list(old)
    assert _inner_count(found) == 4
    assert found.isValid


def test_point_outside_every_profile() -> None:
    sketch, _, _ = _plate()
    error = _error(
        pr.resolve_profiles, _ctx(sketch), format_profile_ref(sketch.entityToken, 200, 5)
    )
    assert error.code is ErrorCode.ENTITY_NOT_FOUND
    assert len(error.details["profiles"]) == 5
    assert all(p["ref"]["type"] == "Profile" for p in error.details["profiles"])


def test_point_on_boundary() -> None:
    sketch, _, _ = _plate()
    error = _error(
        pr.resolve_profiles, _ctx(sketch), {"sketch": sketch.entityToken, "point": [0, 30]}
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["reason"] == "point_on_boundary"


def test_resolve_by_boundary_curves() -> None:
    sketch, lines, holes = _plate()
    ctx = _ctx(sketch)
    (hole,) = pr.resolve_profiles(
        ctx, {"sketch": sketch.entityToken, "curves": [holes[1].entityToken]}
    )
    assert hole.profileLoops.item(0).profileCurves.item(0).sketchEntity is holes[1]
    tokens = [line.entityToken for line in reversed(lines)]
    (plate,) = pr.resolve_profiles(ctx, {"sketch": sketch.entityToken, "curves": tokens})
    assert _inner_count(plate) == 4


def test_curves_without_match_or_ambiguous() -> None:
    sketch, lines, _ = _plate()
    ctx = _ctx(sketch)
    spec = {"sketch": sketch.entityToken, "curves": [lines[0].entityToken]}
    error = _error(pr.resolve_profiles, ctx, spec)
    assert error.code is ErrorCode.ENTITY_NOT_FOUND
    assert len(error.details["profiles"]) == 5

    sketch2 = make_sketch(sketch.design)
    circle = circle_curve(sketch2, (0, 0), 1)
    add_profile(sketch2, [circle])
    add_profile(sketch2, [circle])
    error = _error(
        pr.resolve_profiles, ctx, {"sketch": sketch2.entityToken, "curves": [circle.entityToken]}
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["reason"] == "ambiguous"
    assert len(error.details["candidates"]) == 2

    error = _error(
        pr.resolve_profiles, ctx, {"sketch": sketch.entityToken, "curves": [circle.entityToken]}
    )
    assert error.details["reason"] == "foreign_sketch_entity"


def test_all_profiles_and_union_without_duplicates() -> None:
    sketch = make_sketch()
    for k in range(3):
        circle_curve(sketch, (3 * k, 0), 1)
    sketch.profile_builder = auto_profiles
    ctx = _ctx(sketch)
    every = pr.resolve_profiles(ctx, {"sketch": sketch.entityToken, "all": True})
    assert len(every) == 3
    assert len({id(p) for p in every}) == 3
    first = pr.summarize_all(sketch)[0]["ref"]["ref"]
    union = pr.resolve_profiles(ctx, [first, {"sketch": sketch.entityToken, "all": True}, first])
    assert len(union) == 3
    assert union[0] is pr.resolve_profiles(ctx, first)[0]


def test_all_on_sketch_without_profiles() -> None:
    sketch = make_sketch()
    error = _error(pr.resolve_profiles, _ctx(sketch), {"sketch": sketch.entityToken, "all": True})
    assert error.code is ErrorCode.ENTITY_NOT_FOUND
    assert error.details["profiles"] == []


@pytest.mark.parametrize(
    "spec",
    [
        [],
        "not-a-profile",
        "profile:tok",
        42,
        {"point": [0, 0]},
        {"sketch": "SKETCH", "point": [0, 0], "all": True},
        {"sketch": "SKETCH"},
        {"sketch": "SKETCH", "all": False},
        {"sketch": "SKETCH", "point": ["1", 2]},
        {"sketch": "SKETCH", "curves": []},
        {"sketch": "SKETCH", "all": True, "extra": 1},
    ],
)
def test_invalid_specs(spec: Any) -> None:
    sketch, _, _ = _plate()
    if isinstance(spec, dict) and spec.get("sketch") == "SKETCH":
        spec = {**spec, "sketch": sketch.entityToken}
    error = _error(pr.resolve_profiles, _ctx(sketch), spec)
    assert error.code is ErrorCode.INVALID_PARAMS


def test_sketch_ref_must_be_a_sketch_and_exist() -> None:
    sketch, lines, _ = _plate()
    ctx = _ctx(sketch)
    error = _error(pr.resolve_profiles, ctx, {"sketch": lines[0].entityToken, "all": True})
    assert error.details["reason"] == "not_a_sketch"
    error = _error(pr.resolve_profiles, ctx, format_profile_ref("missing-token", 1, 1))
    assert error.code is ErrorCode.ENTITY_NOT_FOUND


def test_curve_ref_must_be_a_curve() -> None:
    sketch, lines, _ = _plate()
    spec = {"sketch": sketch.entityToken, "curves": [lines[0].startSketchPoint.entityToken]}
    error = _error(pr.resolve_profiles, _ctx(sketch), spec)
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["expected"] == "SketchCurve"


# --- resolve_curves --------------------------------------------------------------------------


def test_resolve_curves_plain_and_chained() -> None:
    sketch = make_sketch()
    lines = polygon_curves(sketch, PLATE)
    tail = polygon_curves(sketch, [(20, 0), (21, 0), (21, 1)], closed=False)
    ctx = _ctx(sketch)
    token = lines[0].entityToken
    assert pr.resolve_curves(ctx, [token, {"ref": token}]) == [lines[0]]
    assert pr.resolve_curves(ctx, token) == [lines[0]]
    chained = pr.resolve_curves(ctx, [token, tail[1].entityToken], chain=True)
    assert len(chained) == 6
    assert {id(c) for c in chained} == {id(c) for c in lines + tail}
    assert sketch.call_names().count("Sketch.findConnectedCurves") == 2


def test_resolve_curves_errors() -> None:
    sketch = make_sketch()
    lines = polygon_curves(sketch, PLATE)
    ctx = _ctx(sketch)
    assert _error(pr.resolve_curves, ctx, []).code is ErrorCode.INVALID_PARAMS
    error = _error(pr.resolve_curves, ctx, [lines[0].startSketchPoint.entityToken])
    assert error.code is ErrorCode.INVALID_PARAMS
    assert _error(pr.resolve_curves, ctx, ["gone"]).code is ErrorCode.ENTITY_NOT_FOUND

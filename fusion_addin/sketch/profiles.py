"""Sketch profiles: summaries and resolution by interior point (c05 D8) - CONTRACT for c07+.

A ``Profile`` has no token and ``Sketch.profiles`` is recomputed and reordered on every sketch
change, so a profile is addressed by a point inside its region (INV-7):
``profile:<sketchEntityToken>@<x>,<y>`` (sketch-space mm, ``protocol.sketch_refs``).

- ``summarize_profile`` / ``summarize_all``: ProfileSummary dicts (ref, interior ``point``,
  ``area_mm2``, ``centroid``, ``bbox``, outer/inner loop curve refs), sketch-space mm.
- ``resolve_profiles(ctx, spec)``: the entry point of feature tools (extrude, revolve, ...).
- ``resolve_curves(ctx, specs, chain)``: sketch curves for sweep paths, optionally extended to
  their connected chains (``Sketch.findConnectedCurves``).

Loop geometry comes from ``profileCurves[j].geometry.evaluator.getStrokes`` (sketch space);
so is the ``areaProperties`` centroid of a profile ("relative to the sketch origin", checked on
the live Fusion in c15 2.4 - c05 design Risks assumed model space).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.sketch_refs import (
    PROFILE_REF_DECIMALS as REF_DECIMALS,
)
from fusion_mcp.protocol.sketch_refs import (
    format_profile_ref,
    is_profile_ref,
    parse_profile_ref,
)
from fusion_mcp.protocol.units import area_from_internal, length_from_internal

from ..core import refs
from . import math2d
from .describe import CURVE_TYPES, entity_ref, items, type_name

STROKE_TOLERANCE_CM = 0.001
BOUNDARY_TOL_MM = math2d.BOUNDARY_TOL
SKETCH_TYPE = "Sketch"
PROFILE_TYPE = "Profile"

XY = tuple[float, float]


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _round(value: float, digits: int = 6) -> float:
    return round(float(value), digits) + 0.0


def _xy(p: Sequence[float]) -> list[float]:
    return [_round(p[0]), _round(p[1])]


def _mm(cm: float) -> float:
    return length_from_internal(float(cm))


# --- loop geometry -----------------------------------------------------------------------


def _strokes(curve_geometry: Any) -> list[XY]:
    """Polyline (sketch-space mm) of one ``Curve3D`` over its whole parameter range."""
    evaluator = curve_geometry.evaluator
    ok, start, end = evaluator.getParameterExtents()
    if not ok:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "cannot evaluate a profile curve")
    ok, points = evaluator.getStrokes(start, end, STROKE_TOLERANCE_CM)
    if not ok:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "cannot tessellate a profile curve")
    return [(_mm(p.x), _mm(p.y)) for p in items(points)]


def _dist2(a: XY, b: XY) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def chain_polyline(pieces: Sequence[Sequence[XY]]) -> list[XY]:
    """Join stroke polylines of a loop's curves end to end into one closed polyline.

    Profile curves come in loop order but each in its own parameter direction, so every
    piece is reversed when its far end is the one next to the chain.
    """
    usable = [list(piece) for piece in pieces if piece]
    if not usable:
        return []
    chain = usable[0]
    if len(usable) > 1:
        nxt = usable[1]
        head = min(_dist2(chain[0], nxt[0]), _dist2(chain[0], nxt[-1]))
        tail = min(_dist2(chain[-1], nxt[0]), _dist2(chain[-1], nxt[-1]))
        if head < tail:
            chain.reverse()
        for piece in usable[1:]:
            if _dist2(chain[-1], piece[-1]) < _dist2(chain[-1], piece[0]):
                piece = piece[::-1]
            chain.extend(piece[1:])
    if len(chain) > 1 and _dist2(chain[0], chain[-1]) < 1e-18:
        chain.pop()
    return chain


@dataclass
class _Loop:
    curves: list[Any]
    polyline: list[XY]


@dataclass
class ProfileShape:
    """A profile with its loops as sketch-space mm polylines (for summaries and lookups)."""

    sketch: Any
    profile: Any
    index: int
    outer: _Loop | None
    inners: list[_Loop] = field(default_factory=list)

    def tokens(self) -> frozenset[str]:
        """Tokens of the outer loop's sketch curves."""
        return frozenset(refs.to_ref(c) for c in self.outer.curves) if self.outer else frozenset()

    def locate(self, xy: Sequence[float]) -> str:
        """``inside`` / ``outside`` / ``on_boundary`` of the region (math2d)."""
        if self.outer is None or len(self.outer.polyline) < 3:
            return math2d.OUTSIDE
        inners = [loop.polyline for loop in self.inners if len(loop.polyline) >= 3]
        return math2d.point_in_region(xy, self.outer.polyline, inners, BOUNDARY_TOL_MM)


def _loop(profile_loop: Any) -> _Loop:
    curves: list[Any] = []
    pieces: list[list[XY]] = []
    for profile_curve in items(profile_loop.profileCurves):
        curves.append(profile_curve.sketchEntity)
        pieces.append(_strokes(profile_curve.geometry))
    return _Loop(curves, chain_polyline(pieces))


def profile_shape(sketch: Any, profile: Any, index: int = 0) -> ProfileShape:
    outer: _Loop | None = None
    inners: list[_Loop] = []
    for profile_loop in items(profile.profileLoops):
        loop = _loop(profile_loop)
        if bool(profile_loop.isOuter) and outer is None:
            outer = loop
        else:
            inners.append(loop)
    return ProfileShape(sketch, profile, index, outer, inners)


def profile_shapes(sketch: Any) -> list[ProfileShape]:
    return [profile_shape(sketch, p, i) for i, p in enumerate(items(sketch.profiles))]


# --- summaries ---------------------------------------------------------------------------


def _centroid_xy(centroid: Any) -> XY:
    """Centroid of a profile's ``areaProperties`` (already sketch space, cm) -> sketch mm.

    Not ``modelToSketchSpace``: on a sketch whose axes differ from model axes (``origin:XZ``)
    that conversion moved the centroid off the profile (c15 2.4)."""
    return (_mm(centroid.x), _mm(centroid.y))


def _bbox(polyline: Sequence[XY]) -> dict[str, list[float]]:
    xs = [p[0] for p in polyline] or [0.0]
    ys = [p[1] for p in polyline] or [0.0]
    return {"min": _xy((min(xs), min(ys))), "max": _xy((max(xs), max(ys)))}


def _summary(shape: ProfileShape, name: str) -> dict[str, Any]:
    sketch, profile = shape.sketch, shape.profile
    accuracy = adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy
    props = profile.areaProperties(accuracy)
    centroid = _centroid_xy(props.centroid)
    outer = shape.outer.polyline if shape.outer else []
    inners = [loop.polyline for loop in shape.inners if len(loop.polyline) >= 3]
    try:
        point = math2d.interior_point(outer, inners, centroid)
    except ValueError:
        point = centroid
    ref = format_profile_ref(refs.to_ref(sketch), point[0], point[1])
    return {
        "ref": {"ref": ref, "type": PROFILE_TYPE, "name": name},
        "sketch": entity_ref(sketch),
        # Exactly the point encoded in the ref (4 decimals), so both resolve the same way.
        "point": [_round(point[0], REF_DECIMALS), _round(point[1], REF_DECIMALS)],
        "area_mm2": _round(area_from_internal(float(props.area))),
        "centroid": _xy(centroid),
        "bbox": _bbox(outer),
        "outer_loop": {
            "curves": [entity_ref(c) for c in shape.outer.curves] if shape.outer else []
        },
        "inner_loops": [{"curves": [entity_ref(c) for c in loop.curves]} for loop in shape.inners],
    }


def summarize_profile(sketch: Any, profile: Any, index: int = 1) -> dict[str, Any]:
    """ProfileSummary of one profile; ``name`` is ``P<index>`` (for humans only)."""
    return _summary(profile_shape(sketch, profile, index - 1), f"P{index}")


def summarize_shapes(shapes: Sequence[ProfileShape]) -> list[dict[str, Any]]:
    return [_summary(shape, f"P{k}") for k, shape in enumerate(shapes, start=1)]


def summarize_all(sketch: Any) -> list[dict[str, Any]]:
    """ProfileSummary of every current profile of ``sketch``, named ``P1..Pn``."""
    return summarize_shapes(profile_shapes(sketch))


# --- resolution (c07+ contract) ----------------------------------------------------------


class _Resolver:
    """One ``resolve_profiles`` call: sketches and their profile shapes are read once, so
    the same ``Profile`` object is returned for repeated specs (no duplicates)."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._shapes: dict[str, tuple[Any, list[ProfileShape]]] = {}

    def sketch(self, ref: Any) -> Any:
        sketch = self.ctx.refs.resolve(ref)
        if type_name(sketch) != SKETCH_TYPE:
            raise _invalid(
                f"{ref!r} is a {type_name(sketch)}, not a sketch",
                ref=ref,
                reason="not_a_sketch",
                type=type_name(sketch),
            )
        return sketch

    def shapes(self, sketch: Any) -> list[ProfileShape]:
        token = refs.to_ref(sketch)
        cached = self._shapes.get(token)
        if cached is None:
            cached = (sketch, profile_shapes(sketch))
            self._shapes[token] = cached
        return cached[1]

    def by_point(self, sketch: Any, xy: XY, spec: Any) -> list[ProfileShape]:
        shapes = self.shapes(sketch)
        located = [(shape, shape.locate(xy)) for shape in shapes]
        on_boundary = [shape for shape, where in located if where == math2d.ON_BOUNDARY]
        if on_boundary:
            raise _invalid(
                f"point {list(xy)} lies on a profile boundary (closer than "
                f"{BOUNDARY_TOL_MM} mm): pick a point inside the region",
                spec=spec,
                reason="point_on_boundary",
                point=list(xy),
                profiles=summarize_shapes(shapes),
            )
        inside = [shape for shape, where in located if where == math2d.INSIDE]
        if not inside:
            raise BridgeError(
                ErrorCode.ENTITY_NOT_FOUND,
                f"no profile of the sketch contains the point {list(xy)} (sketch-space mm): "
                "the sketch changed; pick a current profile from details.profiles",
                {"spec": spec, "point": list(xy), "profiles": summarize_shapes(shapes)},
            )
        return inside[:1]

    def by_curves(self, sketch: Any, curve_refs: Any, spec: Any) -> list[ProfileShape]:
        if not isinstance(curve_refs, list) or not curve_refs:
            raise _invalid(
                "'curves' must be a non-empty list of sketch curve refs",
                spec=spec,
                field="curves",
            )
        sketch_token = refs.to_ref(sketch)
        wanted: set[str] = set()
        for ref in curve_refs:
            curve = self.ctx.refs.resolve(ref)
            if type_name(curve) not in CURVE_TYPES:
                raise _invalid(
                    f"{ref!r} is a {type_name(curve)}, not a sketch curve",
                    spec=spec,
                    ref=ref,
                    expected="SketchCurve",
                )
            if refs.to_ref(curve.parentSketch) != sketch_token:
                raise _invalid(
                    f"curve {ref!r} belongs to another sketch",
                    spec=spec,
                    ref=ref,
                    reason="foreign_sketch_entity",
                )
            wanted.add(refs.to_ref(curve))
        shapes = self.shapes(sketch)
        matches = [shape for shape in shapes if shape.tokens() == wanted]
        if not matches:
            raise BridgeError(
                ErrorCode.ENTITY_NOT_FOUND,
                "no profile has exactly these curves as its outer loop; "
                "pick a profile from details.profiles",
                {"spec": spec, "profiles": summarize_shapes(shapes)},
            )
        if len(matches) > 1:
            raise _invalid(
                f"{len(matches)} profiles have exactly these curves as their outer loop; "
                "address the profile by an interior point instead",
                spec=spec,
                reason="ambiguous",
                candidates=summarize_shapes(matches),
            )
        return matches

    def everything(self, sketch: Any, spec: Any) -> list[ProfileShape]:
        shapes = self.shapes(sketch)
        if not shapes:
            raise BridgeError(
                ErrorCode.ENTITY_NOT_FOUND,
                "the sketch has no closed profiles",
                {"spec": spec, "profiles": []},
            )
        return list(shapes)

    def one(self, spec: Any) -> list[ProfileShape]:
        if isinstance(spec, str):
            if not is_profile_ref(spec):
                raise _invalid(
                    f"{spec!r} is not a profile ref: expected 'profile:<sketch>@<x>,<y>' from "
                    'sketch_profiles, or {"sketch", "point" | "curves" | "all"}',
                    spec=spec,
                    reason="not_a_profile_ref",
                )
            token, x, y = parse_profile_ref(spec)
            return self.by_point(self.sketch(token), (x, y), spec)
        if not isinstance(spec, dict) or "sketch" not in spec:
            raise _invalid(
                "a profile spec is a profile ref string or an object "
                '{"sketch": ref, "point": [x, y]} | {"sketch", "curves": [refs]} | '
                '{"sketch", "all": true}',
                spec=spec,
                reason="invalid_profile_spec",
            )
        modes = [key for key in ("point", "curves", "all") if key in spec]
        extra = sorted(set(spec) - {"sketch", "point", "curves", "all"})
        if len(modes) != 1 or extra:
            raise _invalid(
                "a profile spec object needs 'sketch' and exactly one of 'point', 'curves', 'all'",
                spec=spec,
                reason="invalid_profile_spec",
            )
        sketch = self.sketch(spec["sketch"])
        mode = modes[0]
        if mode == "point":
            return self.by_point(sketch, self._point(spec["point"], spec), spec)
        if mode == "curves":
            return self.by_curves(sketch, spec["curves"], spec)
        if spec["all"] is not True:
            raise _invalid("'all' must be true", spec=spec, field="all")
        return self.everything(sketch, spec)

    def _point(self, value: Any, spec: Any) -> XY:
        """``[x, y]`` in the request's units -> sketch-space mm."""
        if (
            not isinstance(value, list | tuple)
            or len(value) != 2
            or not all(isinstance(c, int | float) and not isinstance(c, bool) for c in value)
        ):
            raise _invalid(
                "'point' must be [x, y] numbers in sketch space", spec=spec, field="point"
            )
        units = self.ctx.units
        return (_mm(units.length(value[0])), _mm(units.length(value[1])))


def resolve_profiles(ctx: Any, spec: Any) -> list[Any]:
    """Fusion ``Profile`` objects for a profile spec (D8), without duplicates.

    ``spec``: a profile ref ``"profile:<sketch>@x,y"``; ``{"sketch": ref, "point": [x, y]}``
    (request units, sketch space); ``{"sketch": ref, "curves": [refs]}`` (profile whose
    outer loop is exactly these curves); ``{"sketch": ref, "all": true}``; or a list of
    these (union, in order of first appearance).

    Errors: ``ENTITY_NOT_FOUND`` (no region matches; ``details.profiles`` = current
    summaries), ``INVALID_PARAMS`` with ``reason`` ``point_on_boundary`` / ``ambiguous`` /
    ``foreign_sketch_entity`` / ``not_a_sketch`` / ``invalid_profile_spec``.
    """
    resolver = _Resolver(ctx)
    specs = spec if isinstance(spec, list) else [spec]
    if not specs:
        raise _invalid("profile spec list is empty", spec=spec, reason="invalid_profile_spec")
    seen: set[tuple[str, int]] = set()
    out: list[Any] = []
    for one in specs:
        for shape in resolver.one(one):
            key = (refs.to_ref(shape.sketch), shape.index)
            if key not in seen:
                seen.add(key)
                out.append(shape.profile)
    return out


def resolve_curves(ctx: Any, specs: Any, chain: bool = False) -> list[Any]:
    """Sketch curves for refs (a ref or a list of refs), without duplicates; ``chain``
    extends each curve to every curve connected to it end to end
    (``Sketch.findConnectedCurves``)."""
    ref_list = specs if isinstance(specs, list) else [specs]
    if not ref_list:
        raise _invalid("curve list is empty", field="curves")
    seen: set[str] = set()
    out: list[Any] = []

    def add(curve: Any) -> None:
        token = refs.to_ref(curve)
        if token not in seen:
            seen.add(token)
            out.append(curve)

    for ref in ref_list:
        curve = ctx.refs.resolve(ref)
        if type_name(curve) not in CURVE_TYPES:
            raise _invalid(
                f"{ref!r} is a {type_name(curve)}, not a sketch curve",
                ref=ref,
                expected="SketchCurve",
            )
        if chain:
            for connected in items(curve.parentSketch.findConnectedCurves(curve)):
                add(connected)
        else:
            add(curve)
    return out

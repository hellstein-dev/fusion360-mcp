"""``pattern.*`` handlers (c08 D3-D7): rectangular, circular and path patterns and mirror.

Every handler follows the c07 order (INV-5): read and validate every parameter, resolve every
ref (``core.patterns.pattern_inputs``: one kind of input - features, bodies, faces or
occurrences) and build every Fusion value first, so a bad parameter fails with
``INVALID_PARAMS`` and no side effects; then the snapshot (bodies and, for occurrence
patterns, occurrences of the owner component), ``createInput``, setters (``False`` answers ->
``FUSION_API_ERROR``), ``add`` and ``pattern_result`` (c07 D8 + ``created_occurrences``). A
pattern Fusion computes with an error raises ``FUSION_API_ERROR`` and the dispatcher rolls the
timeline back (in a direct design the feature is deleted here).

Lengths are in the request ``units`` (default mm), angles in degrees, counts and the path
start are unitless; conversion happens only in ``core.features`` helpers (INV-6).
"""

from __future__ import annotations

import contextlib
import math
from typing import Any

from fusion_mcp.protocol.errors import BridgeError
from fusion_mcp.protocol.refs import is_origin_ref

from ..core.entity_kinds import require_kind
from ..core.features import angle_value, length_value, make_path, scalar_value
from ..core.patterns import (
    PatternInput,
    apply_compute,
    compute_option_enum,
    distance_type_enum,
    pattern_inputs,
    pattern_result,
    pattern_snapshot,
    resolve_axis,
)
from ..core.registry import handler
from .feature_sketch import _bool, _call, _given, _invalid, _opt, _parametric, _set


def _quantity(params: dict[str, Any], name: str, default: Any = None) -> tuple[Any, int | None]:
    """``(ValueInput, count or None for an expression)``; a count must be an integer >= 1."""
    value = params.get(name)
    if value is None:
        if default is None:
            raise _invalid(f"missing required parameter '{name}'", param=name)
        value = default
    if isinstance(value, str):
        return scalar_value(value, name), None
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not float(value).is_integer()
        or value < 1
    ):
        raise _invalid(
            f"parameter '{name}' must be a whole number >= 1 (the original counts) or a Fusion "
            "expression",
            param=name,
            value=value if isinstance(value, int | float) else repr(value),
        )
    return scalar_value(int(value), name), int(value)


def _entities(ctx: Any, params: dict[str, Any]) -> tuple[PatternInput, Any]:
    pattern = pattern_inputs(ctx, ctx.require(params, "entities", list))
    compute = _opt(params, "compute", "adjust")
    compute_option_enum(compute)
    return pattern, compute


def _resolve_in(ctx: Any, ref: Any, component: Any, param: str, kind: str) -> Any:
    """Entity of ``kind`` (``linear`` / ``planar``); ``origin:*`` is the origin element of
    ``component`` (the pattern's owner), any other ref resolves as is."""
    raw = ref.get("ref") if isinstance(ref, dict) else ref
    owner = component if is_origin_ref(raw) else None
    try:
        entity = ctx.refs.resolve(ref, owner)
    except BridgeError as exc:
        exc.details.setdefault("param", param)
        raise
    return require_kind(entity, kind, param)


def _direction(ctx: Any, ref: Any, component: Any, param: str) -> Any:
    """Linear entity of a rectangular pattern direction; ``origin:X|Y|Z`` of ``component``."""
    return _resolve_in(ctx, ref, component, param, "linear")


def _finish(ctx: Any, feature: Any, before: Any, touched: Any = ()) -> dict[str, Any]:
    try:
        return pattern_result(ctx, feature, before, touched)
    except Exception:
        if feature is not None and not _parametric(ctx):
            with contextlib.suppress(Exception):
                feature.deleteMe()
        raise


def _touched(pattern: PatternInput) -> list[Any]:
    """Bodies whose faces are patterned are changed by the pattern."""
    return pattern.bodies if pattern.kind == "face" else []


# --- rectangular (D4) ------------------------------------------------------------------------


@handler("pattern.rectangular", mutating=True)
def pattern_rectangular(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Rectangular pattern along one or two directions (D4); result per c07 D8."""
    pattern, compute = _entities(ctx, params)
    component = pattern.component
    direction1 = _direction(ctx, ctx.require(params, "direction1"), component, "direction1")
    quantity1, _ = _quantity(params, "quantity1", 2)
    if not _given(params, "distance1"):
        raise _invalid("missing required parameter 'distance1'", param="distance1")
    distance1 = length_value(ctx, params["distance1"], param="distance1")
    distance_type = distance_type_enum(_opt(params, "distance_type", "spacing"))
    symmetric1 = _bool(ctx, params, "symmetric1", False)
    symmetric2 = _bool(ctx, params, "symmetric2", False)
    quantity2, count2 = _quantity(params, "quantity2", 1)

    second: tuple[Any, ...] | None = None
    if _given(params, "direction2"):
        direction2 = _direction(ctx, params["direction2"], component, "direction2")
        if (count2 is None or count2 > 1) and not _given(params, "distance2"):
            raise _invalid(
                "parameter 'direction2' with quantity2 > 1 needs 'distance2'",
                param="distance2",
            )
        distance2 = length_value(ctx, _opt(params, "distance2", 0), param="distance2")
        second = (direction2, quantity2, distance2)
    else:
        if count2 != 1:
            raise _invalid("parameter 'quantity2' needs 'direction2'", param="quantity2")
        for name in ("distance2", "symmetric2"):
            if _given(params, name) and params[name] is not False:
                raise _invalid(f"parameter '{name}' needs 'direction2'", param=name)

    before = pattern_snapshot(pattern)
    patterns = component.features.rectangularPatternFeatures
    inp = patterns.createInput(pattern.collection, direction1, quantity1, distance1, distance_type)
    if second is not None:
        _call("RectangularPatternFeature", inp, "setDirectionTwo", *second)
    inp.isSymmetricInDirectionOne = symmetric1
    if second is not None:
        inp.isSymmetricInDirectionTwo = symmetric2
    apply_compute(inp, pattern.kind, compute, "RectangularPatternFeature")
    feature = patterns.add(inp)
    return _finish(ctx, feature, before, _touched(pattern))


# --- circular (D5) ---------------------------------------------------------------------------


@handler("pattern.circular", mutating=True)
def pattern_circular(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Circular pattern around an axis (D5); result per c07 D8."""
    pattern, compute = _entities(ctx, params)
    component = pattern.component
    axis = resolve_axis(ctx, ctx.require(params, "axis"), component)
    quantity, _ = _quantity(params, "quantity")
    total_angle = angle_value(ctx, _opt(params, "total_angle", 360), "total_angle")
    symmetric = _bool(ctx, params, "symmetric", False)

    before = pattern_snapshot(pattern)
    patterns = component.features.circularPatternFeatures
    inp = patterns.createInput(pattern.collection, axis)
    inp.quantity = quantity
    inp.totalAngle = total_angle
    inp.isSymmetric = symmetric
    apply_compute(inp, pattern.kind, compute, "CircularPatternFeature")
    feature = patterns.add(inp)
    return _finish(ctx, feature, before, _touched(pattern))


# --- path (D6) -------------------------------------------------------------------------------


@handler("pattern.path", mutating=True)
def pattern_path(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Pattern along a path of sketch curves or edges (D6); result per c07 D8."""
    pattern, compute = _entities(ctx, params)
    component = pattern.component
    path_refs = ctx.require(params, "path", list)
    chain = _bool(ctx, params, "path_chain", True)
    quantity, _ = _quantity(params, "quantity")
    if not _given(params, "distance"):
        raise _invalid("missing required parameter 'distance'", param="distance")
    distance = length_value(ctx, params["distance"], param="distance")
    distance_type = distance_type_enum(_opt(params, "distance_type", "extent"))
    start = _opt(params, "start", 0)
    if isinstance(start, bool) or not isinstance(start, int | float) or not 0 <= start <= 1:
        raise _invalid(
            "parameter 'start' must be a number from 0 (path start) to 1 (path end)",
            param="start",
            value=start if isinstance(start, int | float) else repr(start),
        )
    start_value = scalar_value(start, "start")
    flip = _bool(ctx, params, "flip", False)
    along = _bool(ctx, params, "orientation_along_path", False)
    symmetric = _bool(ctx, params, "symmetric", False)
    path = make_path(ctx, component, path_refs, chain)

    before = pattern_snapshot(pattern)
    patterns = component.features.pathPatternFeatures
    inp = patterns.createInput(pattern.collection, path, quantity, distance, distance_type)
    inp.isFlipDirection = flip
    inp.isOrientationAlongPath = along
    inp.isSymmetric = symmetric
    if start != 0:
        _set("PathPatternFeature", inp, "startPoint", start_value)
    apply_compute(inp, pattern.kind, compute, "PathPatternFeature")
    feature = patterns.add(inp)
    return _finish(ctx, feature, before, _touched(pattern))


# --- mirror (D7) -----------------------------------------------------------------------------


@handler("pattern.mirror", mutating=True)
def pattern_mirror(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Mirror across a planar face or construction plane (D7); ``combine`` joins mirrored
    bodies with the originals (body input only)."""
    pattern, compute = _entities(ctx, params)
    component = pattern.component
    plane = _resolve_in(ctx, ctx.require(params, "plane"), component, "plane", "planar")
    combine = _bool(ctx, params, "combine", False)
    if combine and pattern.kind != "body":
        raise _invalid(
            f"parameter 'combine' applies only to bodies, not {pattern.kind} input",
            param="combine",
            kind=pattern.kind,
        )

    before = pattern_snapshot(pattern)
    mirrors = component.features.mirrorFeatures
    inp = mirrors.createInput(pattern.collection, plane)
    if pattern.kind == "body" and (combine or hasattr(inp, "isCombine")):
        _set("MirrorFeature", inp, "isCombine", combine)
    apply_compute(inp, pattern.kind, compute, "MirrorFeature")
    feature = mirrors.add(inp)
    touched = pattern.bodies if combine else _touched(pattern)
    return _finish(ctx, feature, before, touched)

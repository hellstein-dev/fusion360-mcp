"""Shared helpers of the ``surface.*`` handlers and of the surface mode of c07 features (c11).

- Enums (D2): ``surface_enum(kind, param, value)`` checks ``value`` against the vocabulary of
  ``fusion_mcp.protocol.surfaces`` (``INVALID_PARAMS``) and maps it to the ``adsk.fusion``
  enum value; an enum this Fusion version lacks is ``FUSION_API_ERROR`` "... not available".
  ``surface_operation`` is the c07 ``operation_enum`` limited to ``new_body | new_component``.
- Result (D3): ``surface_result`` = c07 ``feature_result`` plus ``"is_solid"`` on every
  created / modified body; a failed result deletes the feature in a direct design (no timeline
  rollback there, c02 D5).
- Open profiles (D10, D12): ``open_profile`` turns sketch curve refs into
  ``Component.createOpenProfile`` and edge refs into ``Component.createBRepEdgeProfile``;
  mixing both is ``INVALID_PARAMS``.
- ``bbox_distance``: distance (cm) from a point to a bounding box, the fallback of trim cell
  selection when ``measureMinimumDistance`` refuses a temporary cell body (design R2).
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterable, Sequence
from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import check_choice
from fusion_mcp.protocol.surfaces import (
    CONTINUITY,
    EXTEND_ALIGNMENTS,
    EXTEND_TYPES,
    OFFSET_TYPES,
    RULED_CORNERS,
    RULED_TYPES,
    SURFACE_OPERATIONS,
    UNTRIM_LOOP_TYPES,
)

from ..sketch.resolve import CURVE_TYPES
from .features import (
    EDGE,
    BodySnapshot,
    feature_result,
    object_collection,
    operation_enum,
    resolve_one,
    target_component,
)
from .refs import type_name

# kind -> (adsk.fusion enum class, vocabulary, value -> enum attribute names to try in order).
SURFACE_ENUMS: dict[str, tuple[str, Sequence[str], dict[str, tuple[str, ...]]]] = {
    "continuity": (
        "SurfaceContinuityTypes",
        CONTINUITY,
        {
            "connected": ("ConnectedSurfaceContinuityType",),
            "tangent": ("TangentSurfaceContinuityType",),
            "curvature": ("CurvatureSurfaceContinuityType",),
        },
    ),
    "extend_type": (
        "SurfaceExtendTypes",
        EXTEND_TYPES,
        {
            "natural": ("NaturalSurfaceExtendType",),
            "tangent": ("TangentSurfaceExtendType",),
            "perpendicular": ("PerpendicularSurfaceExtendType",),
        },
    ),
    "alignment": (
        "SurfaceExtendAlignment",
        EXTEND_ALIGNMENTS,
        {
            "free": ("FreeEdges", "FreeEdgesSurfaceExtendAlignment"),
            "align": ("AlignEdges", "AlignEdgesSurfaceExtendAlignment"),
        },
    ),
    "loop_type": (
        "UntrimLoopTypes",
        UNTRIM_LOOP_TYPES,
        {
            "all": ("AllLoopsUntrimType",),
            "external": ("ExternalLoopsUntrimType",),
            "internal": ("InternalLoopsUntrimType",),
        },
    ),
    "ruled_type": (
        "RuledSurfaceTypes",
        RULED_TYPES,
        {
            "normal": ("NormalRuledSurfaceType",),
            "tangent": ("TangentRuledSurfaceType",),
            "direction": ("DirectionRuledSurfaceType",),
        },
    ),
    "corner": (
        "RuledSurfaceCornerTypes",
        RULED_CORNERS,
        {
            "rounded": ("RoundedRuledSurfaceCornerType",),
            "mitered": ("MiteredRuledSurfaceCornerType",),
        },
    ),
    "offset_type": (
        "OffsetTypes",
        OFFSET_TYPES,
        {
            "sharp": ("SharpOffsetType",),
            "rounded": ("RoundedOffsetType",),
        },
    ),
}

OPEN_PROFILE_TYPES: tuple[str, ...] = (*CURVE_TYPES, EDGE)


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def not_available(feature_type: str, what: str) -> BridgeError:
    return BridgeError(
        ErrorCode.FUSION_API_ERROR,
        f"{what} not available in this Fusion API version",
        {"feature_type": feature_type},
    )


# --- enums (D2) ------------------------------------------------------------------------------


def surface_enum(kind: str, param: str, value: Any) -> Any:
    """``value`` of vocabulary ``kind`` (a key of ``SURFACE_ENUMS``) -> the ``adsk.fusion``
    enum value. Unknown value -> ``INVALID_PARAMS`` (``details.param``, ``expected``)."""
    enum_name, choices, attrs = SURFACE_ENUMS[kind]
    choice = check_choice(param, value, choices)
    enum = getattr(adsk.fusion, enum_name, None)
    for attr in attrs[choice]:
        if enum is not None and hasattr(enum, attr):
            return getattr(enum, attr)
    raise BridgeError(
        ErrorCode.FUSION_API_ERROR,
        f"adsk.fusion.{enum_name} '{choice}' is not available in this Fusion API version",
        {"param": param, "value": choice, "enum": enum_name},
    )


def surface_operation(value: Any, param: str = "operation") -> Any:
    """``"new_body" | "new_component"`` -> ``FeatureOperations.*``; ``join``/``cut``/... are
    ``INVALID_PARAMS`` (surface features create bodies only)."""
    return operation_enum(check_choice(param, value, SURFACE_OPERATIONS), param)


# --- result (D3) -----------------------------------------------------------------------------


def _items(collection: Any) -> list[Any]:
    if collection is None:
        return []
    if hasattr(collection, "count") and hasattr(collection, "item"):
        return [collection.item(i) for i in range(collection.count)]
    return list(collection)


def _parametric(ctx: Any) -> bool:
    try:
        return bool(ctx.design.designType == adsk.fusion.DesignTypes.ParametricDesignType)
    except Exception:
        return True


def _is_solid(body: Any) -> bool | None:
    try:
        value = body.isSolid
    except Exception:
        return None
    return value if isinstance(value, bool) else None


def surface_result(
    ctx: Any,
    feature: Any,
    before: BodySnapshot,
    touched: Iterable[Any] = (),
    operation: str | None = None,
) -> dict[str, Any]:
    """D3: ``feature_result`` (c07 D8/D9: an ``error`` feature -> ``FUSION_API_ERROR``, the
    dispatcher rolls back) with ``"is_solid"`` on each created / modified body. In a direct
    design a failure deletes the feature here."""
    touched = list(touched)
    try:
        result = feature_result(ctx, feature, before, touched, operation=operation)
    except Exception:
        if feature is not None and not _parametric(ctx):
            with contextlib.suppress(Exception):
                feature.deleteMe()
        raise
    after = BodySnapshot.take(before.components)
    solid: dict[str, bool | None] = {}
    for body in [*_items(getattr(feature, "bodies", None)), *touched, *after.bodies.values()]:
        if body is None:
            continue
        with contextlib.suppress(Exception):
            solid.setdefault(ctx.refs.to_ref(body), _is_solid(body))
    for key in ("created_bodies", "modified_bodies"):
        for entry in result[key]:
            entry["is_solid"] = solid.get(entry["ref"])
    return result


def first_body_is_solid(feature: Any) -> bool | None:
    """``isSolid`` of the first body of ``feature.bodies`` (stitch result, D8)."""
    bodies = _items(getattr(feature, "bodies", None))
    return _is_solid(bodies[0]) if bodies else None


# --- open profiles (D10, D12) ----------------------------------------------------------------


def open_profile(ctx: Any, refs: Any, chain: bool = True, param: str = "profiles") -> Any:
    """Open profile from a non-empty list of sketch curve refs (``createOpenProfile``) **or**
    BRep edge refs (``createBRepEdgeProfile``) of one component; ``chain`` lets Fusion add
    connected curves / tangent edges. Mixed kinds -> ``INVALID_PARAMS`` (``reason =
    "mixed_profile"``); a set Fusion cannot use -> ``FUSION_API_ERROR``. Creates no timeline
    item."""
    if not isinstance(refs, list | tuple) or not refs:
        raise _invalid(
            f"parameter '{param}' must be a non-empty list of sketch curve or edge refs",
            param=param,
            expected=["SketchCurve", EDGE],
            reason="empty",
        )
    entities: list[Any] = []
    for index, ref in enumerate(refs):
        entity = resolve_one(ctx, ref, OPEN_PROFILE_TYPES, param, index)
        if all(entity is not e for e in entities):
            entities.append(entity)
    edges = [type_name(e) == EDGE for e in entities]
    if any(edges) and not all(edges):
        raise _invalid(
            f"parameter '{param}' mixes sketch curves and edges; use one kind",
            param=param,
            reason="mixed_profile",
            expected=["SketchCurve", EDGE],
        )
    component = target_component(entities[0])
    method = "createBRepEdgeProfile" if all(edges) else "createOpenProfile"
    if not hasattr(component, method):
        raise not_available("Profile", f"Component.{method} is")
    try:
        profile = getattr(component, method)(object_collection(entities), chain)
    except RuntimeError as exc:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"Fusion could not build an open profile from '{param}': {exc}",
            {"param": param, "count": len(entities)},
        ) from None
    if profile is None:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"Fusion could not build an open profile from '{param}'",
            {"param": param, "count": len(entities)},
        )
    return profile


# --- trim cell selection (D6) ----------------------------------------------------------------


def bbox_distance(box: Any, point: Any) -> float:
    """Distance (cm) from ``point`` (``Point3D``) to the axis-aligned ``box``
    (``BoundingBox3D``); 0 inside the box."""
    lo, hi = box.minPoint, box.maxPoint
    gaps = (
        max(lo.x - point.x, 0.0, point.x - hi.x),
        max(lo.y - point.y, 0.0, point.y - hi.y),
        max(lo.z - point.z, 0.0, point.z - hi.z),
    )
    return math.hypot(*gaps)

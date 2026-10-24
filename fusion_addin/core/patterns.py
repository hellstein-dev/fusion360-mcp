"""Shared helpers of the ``pattern.*`` handlers (c08 D3).

- ``pattern_inputs(ctx, refs)`` -> ``PatternInput``: what is patterned (``feature``, ``body``,
  ``face`` or ``occurrence`` - Fusion takes one kind per pattern, a mix is
  ``INVALID_PARAMS`` with ``details.kinds``), the ``ObjectCollection`` for ``createInput``
  and the component the pattern is created in.
- ``compute_option_enum`` / ``distance_type_enum``: protocol words -> Fusion enums;
  ``apply_compute`` sets ``patternComputeOption`` only for feature patterns (Fusion ignores it
  for bodies, faces and occurrences).
- ``OccurrenceSnapshot`` / ``pattern_result``: c07 ``feature_result`` plus, for occurrence
  patterns, ``created_occurrences`` - occurrences of the owner component whose
  ``fullPathName`` was not there before ``add``.
- ``resolve_axis``: axis of a circular pattern or a rotation (sketch line, construction axis,
  ``origin:X|Y|Z`` of the owner component, straight edge, cylindrical / conical face).

Counts are unitless (``scalar_value``, c07 D4), never converted as lengths (INV-6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import COMPUTE_OPTIONS, PATTERN_DISTANCE_TYPES, check_choice
from fusion_mcp.protocol.refs import is_origin_ref

from .entity_kinds import describe_kind, is_linear, is_tangent_face
from .features import BodySnapshot, feature_result, object_collection, target_component
from .refs import type_name

KINDS = ("feature", "body", "face", "occurrence")
EXPECTED = ["Feature", "BRepBody", "BRepFace", "Occurrence"]

_COMPUTE_ATTRS = {
    "optimized": "OptimizedPatternCompute",
    "identical": "IdenticalPatternCompute",
    "adjust": "AdjustPatternCompute",
}
_DISTANCE_ATTRS = {
    "extent": "ExtentPatternDistanceType",
    "spacing": "SpacingPatternDistanceType",
}


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _token(entity: Any) -> Any:
    try:
        token = entity.entityToken
    except Exception:
        token = None
    return token if isinstance(token, str) and token else id(entity)


def _attr(entity: Any, name: str) -> Any:
    try:
        return getattr(entity, name, None)
    except Exception:
        return None


def _items(collection: Any) -> list[Any]:
    if collection is None:
        return []
    if hasattr(collection, "count") and hasattr(collection, "item"):
        return [collection.item(i) for i in range(collection.count)]
    return list(collection)


def entity_kind(entity: Any) -> str | None:
    """``feature`` / ``body`` / ``face`` / ``occurrence`` by ``objectType``, else ``None``."""
    kind = type_name(entity)
    if kind == "BRepBody":
        return "body"
    if kind == "BRepFace":
        return "face"
    if kind == "Occurrence":
        return "occurrence"
    if kind.endswith("Feature"):
        return "feature"
    return None


@dataclass
class PatternInput:
    kind: str
    entities: list[Any]
    collection: Any
    component: Any
    bodies: list[Any] = field(default_factory=list)


def _owner(ctx: Any, kind: str, first: Any) -> Any:
    if kind == "feature":
        owner = _attr(first, "parentComponent")
        return owner if owner is not None else target_component(first)
    if kind == "occurrence":
        context = _attr(first, "assemblyContext")
        owner = _attr(context, "component") if context is not None else None
        return owner if owner is not None else ctx.root
    return target_component(first)


def _bodies(kind: str, entities: list[Any]) -> list[Any]:
    """Bodies a body/face pattern starts from (the snapshot also covers their components)."""
    if kind == "body":
        return list(entities)
    if kind == "face":
        out: list[Any] = []
        for face in entities:
            body = _attr(face, "body")
            if body is not None and all(_token(body) != _token(b) for b in out):
                out.append(body)
        return out
    return []


def pattern_inputs(ctx: Any, refs: Any, param: str = "entities") -> PatternInput:
    """Refs of what to pattern -> ``PatternInput``. Every ref must be a feature, body, face
    or occurrence and all of one kind; duplicates are dropped."""
    if not isinstance(refs, list | tuple) or not refs:
        raise _invalid(
            f"parameter '{param}' must be a non-empty list of refs",
            param=param,
            expected=EXPECTED,
            reason="empty",
        )
    entities: list[Any] = []
    kinds: list[str] = []
    for index, ref in enumerate(refs):
        try:
            entity = ctx.refs.resolve(ref)
        except BridgeError as exc:
            exc.details.setdefault("param", param)
            raise
        kind = entity_kind(entity)
        if kind is None:
            actual = describe_kind(entity)
            raise BridgeError(
                ErrorCode.INVALID_PARAMS,
                f"parameter '{param}[{index}]' must be {' | '.join(EXPECTED)}, got {actual}",
                {
                    "param": param,
                    "index": index,
                    "ref": ref,
                    "expected": EXPECTED,
                    "actual": actual,
                },
            )
        if all(_token(entity) != _token(seen) for seen in entities):
            entities.append(entity)
            kinds.append(kind)
    distinct = sorted(set(kinds), key=KINDS.index)
    if len(distinct) > 1:
        raise _invalid(
            f"parameter '{param}' mixes {', '.join(distinct)}; Fusion patterns one kind at a "
            "time (features, bodies, faces or occurrences)",
            param=param,
            reason="mixed_kinds",
            kinds=distinct,
        )
    kind = distinct[0]
    return PatternInput(
        kind=kind,
        entities=entities,
        collection=object_collection(entities),
        component=_owner(ctx, kind, entities[0]),
        bodies=_bodies(kind, entities),
    )


def compute_option_enum(name: Any, param: str = "compute") -> Any:
    """``"optimized" | "identical" | "adjust"`` -> ``adsk.fusion.PatternComputeOptions.*``."""
    choice = check_choice(param, name, COMPUTE_OPTIONS)
    return getattr(adsk.fusion.PatternComputeOptions, _COMPUTE_ATTRS[choice])


def distance_type_enum(name: Any, param: str = "distance_type") -> Any:
    """``"extent" | "spacing"`` -> ``adsk.fusion.PatternDistanceType.*``."""
    choice = check_choice(param, name, PATTERN_DISTANCE_TYPES)
    return getattr(adsk.fusion.PatternDistanceType, _DISTANCE_ATTRS[choice])


def apply_compute(feature_input: Any, kind: str, name: Any, feature_type: str) -> None:
    """``feature_input.patternComputeOption`` for feature patterns; other kinds have no
    compute option and the value is only validated. Missing property -> not available."""
    option = compute_option_enum(name)
    if kind != "feature":
        return
    if not hasattr(feature_input, "patternComputeOption"):
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"{feature_type}Input.patternComputeOption is not available in this Fusion API version",
            {"feature_type": feature_type},
        )
    feature_input.patternComputeOption = option


def resolve_axis(ctx: Any, ref: Any, component: Any, param: str = "axis") -> Any:
    """Axis ref -> sketch line, construction axis, straight edge or cylindrical/conical face
    (its axis); ``origin:X|Y|Z`` is the origin axis of ``component``."""
    raw = ref.get("ref") if isinstance(ref, dict) else ref
    try:
        if is_origin_ref(raw) and component is not None:
            entity = ctx.refs.resolve(ref, component)
        else:
            entity = ctx.refs.resolve(ref)
    except BridgeError as exc:
        exc.details.setdefault("param", param)
        raise
    if is_linear(entity) or is_tangent_face(entity):
        return entity
    got = describe_kind(entity)
    raise BridgeError(
        ErrorCode.INVALID_PARAMS,
        f"parameter '{param}' must be a straight edge, a sketch line, a construction axis or a "
        f"cylindrical / conical face, got {got}",
        {"param": param, "expected": "axis", "got": got},
    )


# --- result ----------------------------------------------------------------------------------


@dataclass
class OccurrenceSnapshot:
    """``fullPathName`` of every occurrence of ``component`` right before ``add``."""

    component: Any
    paths: set[str]

    @classmethod
    def take(cls, component: Any) -> OccurrenceSnapshot:
        paths = {_attr(o, "fullPathName") for o in _items(_attr(component, "occurrences"))}
        return cls(component, {p for p in paths if isinstance(p, str)})

    def created(self) -> list[Any]:
        return [
            o
            for o in _items(_attr(self.component, "occurrences"))
            if _attr(o, "fullPathName") not in self.paths
        ]


@dataclass
class PatternSnapshot:
    bodies: BodySnapshot
    occurrences: OccurrenceSnapshot | None


def pattern_snapshot(pattern: PatternInput) -> PatternSnapshot:
    """Bodies of the owner component (and of the patterned bodies' components) and, for an
    occurrence pattern, the owner's occurrences - taken right before ``add``."""
    components = [pattern.component]
    for body in pattern.bodies:
        owner = _attr(body, "parentComponent")
        if owner is not None:
            components.append(owner)
    occurrences = (
        OccurrenceSnapshot.take(pattern.component) if pattern.kind == "occurrence" else None
    )
    return PatternSnapshot(BodySnapshot.take(components), occurrences)


def pattern_result(
    ctx: Any, feature: Any, before: PatternSnapshot, touched: Any = ()
) -> dict[str, Any]:
    """c07 D8 result (health per D9: an ``error`` pattern raises and is rolled back) plus
    ``created_occurrences`` for an occurrence pattern."""
    result = feature_result(ctx, feature, before.bodies, touched)
    if before.occurrences is not None:
        result["created_occurrences"] = [
            {
                "ref": ctx.refs.to_ref(o),
                "type": type_name(o),
                "name": _attr(o, "name"),
                "full_path_name": _attr(o, "fullPathName"),
            }
            for o in before.occurrences.created()
        ]
    return result

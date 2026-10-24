"""Shared helpers of the ``feature.*`` handlers (c07 D3-D9); c08 reuses them.

- Values (D4, INV-6): ``length_value`` / ``angle_value`` / ``scalar_value`` / ``point3d`` - the
  only way feature handlers build ``ValueInput``/``Point3D``; numbers go through
  ``fusion_mcp.protocol.units`` (request units -> cm, degrees -> radians), strings are Fusion
  expressions passed through unchanged.
- Inputs (D3): ``resolve_many`` (refs -> entities of allowed types), ``object_collection``,
  ``feature_profiles`` (c05 profile specs, planar faces, sketch texts); ``make_path`` (D12,
  sweep paths and guide rails from sketch curves or edges).
- Operation and participants (D5): ``operation_enum``, ``participant_bodies``,
  ``apply_participants``.
- Extents (D6): ``extent_definition``, ``start_definition``, ``direction_enum``.
- Component (D7): ``target_component``.
- Result (D8, D9): ``BodySnapshot.take`` before ``add``, ``feature_result`` after it; health
  comes from ``core.health`` (c03), an ``error`` feature raises ``FUSION_API_ERROR`` and the
  dispatcher rolls the timeline back.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import adsk.core
import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import (
    DIRECTIONS,
    EXTENT_TYPES,
    OPERATIONS,
    START_TYPES,
    check_choice,
)
from fusion_mcp.protocol.sketch_refs import is_profile_ref
from fusion_mcp.protocol.units import Units, angle_to_internal, length_to_internal

from ..sketch.profiles import resolve_curves, resolve_profiles
from ..sketch.resolve import CURVE_TYPES, TEXT_TYPES
from .entity_kinds import describe_kind, is_face, is_planar
from .health import check_health
from .refs import type_name

BODY = "BRepBody"
PROFILE = "Profile"
SKETCH_TEXT = "SketchText"

# Entities a ``to_entity`` extent / ``entity`` start may end at (Fusion API docs).
EXTENT_ENTITY_TYPES: tuple[str, ...] = (
    "BRepBody",
    "BRepFace",
    "BRepVertex",
    "ConstructionPlane",
    "ConstructionPoint",
)

_SKETCH_TYPES = frozenset({PROFILE, SKETCH_TEXT, "SketchPoint", *CURVE_TYPES})
_TOPOLOGY_TYPES = frozenset({"BRepFace", "BRepEdge", "BRepVertex", "BRepLoop", "BRepShell"})
_CONSTRUCTION_TYPES = frozenset({"ConstructionPlane", "ConstructionAxis", "ConstructionPoint"})


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _items(collection: Any) -> list[Any]:
    """Elements of a Fusion collection (``count``/``item``) or of an iterable."""
    if collection is None:
        return []
    if hasattr(collection, "count") and hasattr(collection, "item"):
        return [collection.item(i) for i in range(collection.count)]
    return list(collection)


def _token(entity: Any) -> str | None:
    try:
        token = entity.entityToken
    except Exception:
        return None
    return token if isinstance(token, str) and token else None


def _is_valid(entity: Any) -> bool:
    try:
        return bool(getattr(entity, "isValid", True))
    except Exception:
        return False


def _same(a: Any, b: Any) -> bool:
    if a is b:
        return True
    token = _token(a)
    return token is not None and token == _token(b)


# --- values and units (D4) ---------------------------------------------------------------


def _unit_name(ctx: Any, units: Units | str | None) -> str:
    if units is None:
        units = getattr(ctx, "units", None)
    if isinstance(units, Units):
        return units.units
    if units is None:
        return Units().units
    return units


def _value_input(converted: float | str) -> Any:
    if isinstance(converted, str):
        return adsk.core.ValueInput.createByString(converted)
    return adsk.core.ValueInput.createByReal(converted)


def _check_value(value: Any, param: str, what: str) -> None:
    if isinstance(value, str):
        if not value.strip():
            raise _invalid(f"parameter '{param}' must not be an empty expression", param=param)
        return
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _invalid(
            f"parameter '{param}' must be a {what} (number) or a Fusion expression (string), "
            f"got {type(value).__name__}",
            param=param,
            value=repr(value),
        )


def _convert(fn: Any, *args: Any, param: str) -> float | str:
    try:
        converted: float | str = fn(*args)
    except (TypeError, ValueError) as exc:
        raise _invalid(f"parameter '{param}': {exc}", param=param) from None
    return converted


def length_value(
    ctx: Any, value: Any, units: Units | str | None = None, param: str = "length"
) -> Any:
    """Length -> ``ValueInput``: a number in ``units`` (default: the request's ``ctx.units``)
    becomes ``createByReal(<cm>)``, a string ``createByString`` unchanged."""
    _check_value(value, param, "length")
    unit = _unit_name(ctx, units)
    return _value_input(_convert(length_to_internal, value, unit, param=param))


def angle_value(ctx: Any, value: Any, param: str = "angle") -> Any:
    """Angle -> ``ValueInput``: a number in degrees becomes ``createByReal(<radians>)``, a
    string ``createByString`` unchanged."""
    _check_value(value, param, "angle in degrees")
    return _value_input(_convert(angle_to_internal, value, param=param))


def scalar_value(value: Any, param: str = "value") -> Any:
    """Unitless value (count, path fraction, scale, weight) -> ``ValueInput`` without any
    conversion: ``createByReal(float(v))`` or ``createByString(v)``."""
    _check_value(value, param, "unitless value")
    if isinstance(value, str):
        return adsk.core.ValueInput.createByString(value)
    number = float(value)
    if not math.isfinite(number):
        raise _invalid(f"parameter '{param}' must be a finite number", param=param)
    return adsk.core.ValueInput.createByReal(number)


def point3d(ctx: Any, xyz: Any, units: Units | str | None = None, param: str = "point") -> Any:
    """``[x, y, z]`` numbers in ``units`` (default ``ctx.units``) -> ``Point3D`` in cm."""
    if (
        not isinstance(xyz, list | tuple)
        or len(xyz) != 3
        or not all(isinstance(c, int | float) and not isinstance(c, bool) for c in xyz)
    ):
        raise _invalid(
            f"parameter '{param}' must be [x, y, z] numbers", param=param, value=repr(xyz)
        )
    unit = _unit_name(ctx, units)
    x, y, z = (float(_convert(length_to_internal, c, unit, param=param)) for c in xyz)
    return adsk.core.Point3D.create(x, y, z)


# --- input entities (D3) -----------------------------------------------------------------


def _short(name: str) -> str:
    return name.rsplit("::", 1)[-1]


def _matches(entity: Any, allowed: Sequence[str]) -> bool:
    object_type = getattr(entity, "objectType", None)
    kind = type_name(entity)
    return any(a == object_type or _short(a) == kind for a in allowed)


def _ref_key(ref: Any) -> Any:
    return ref.get("ref") if isinstance(ref, dict) else ref


def resolve_one(
    ctx: Any, ref: Any, allowed: Sequence[str], param: str, index: int | None = None
) -> Any:
    """One ref -> its entity, which must be one of the ``allowed`` types (``objectType`` or
    short names such as ``"BRepEdge"``); else ``INVALID_PARAMS`` with ``details = {param, ref,
    expected, actual}``. ``ENTITY_NOT_FOUND`` from the resolver is passed through (with
    ``details.param``). Proxies are returned as is."""
    try:
        entity = ctx.refs.resolve(ref)
    except BridgeError as exc:
        exc.details.setdefault("param", param)
        raise
    if not _matches(entity, allowed):
        expected = [_short(a) for a in allowed]
        actual = describe_kind(entity)
        where = param if index is None else f"{param}[{index}]"
        details: dict[str, Any] = {
            "param": param,
            "ref": _ref_key(ref),
            "expected": expected,
            "actual": actual,
        }
        if index is not None:
            details["index"] = index
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"parameter '{where}' must be {' | '.join(expected)}, got {actual}",
            details,
        )
    return entity


def resolve_many(ctx: Any, refs: Any, allowed: Sequence[str], param: str) -> list[Any]:
    """A non-empty list of refs -> entities of the ``allowed`` types, in order, without
    duplicates (same object or ``entityToken``). An empty list / not a list or a foreign type
    is ``INVALID_PARAMS`` (``details.param``, ``ref``, ``expected``, ``actual``)."""
    expected = [_short(a) for a in allowed]
    if not isinstance(refs, list | tuple):
        raise _invalid(
            f"parameter '{param}' must be a list of refs", param=param, expected=expected
        )
    if not refs:
        raise _invalid(
            f"parameter '{param}' must not be empty",
            param=param,
            ref=None,
            expected=expected,
            actual=None,
            reason="empty",
        )
    out: list[Any] = []
    for index, ref in enumerate(refs):
        entity = resolve_one(ctx, ref, allowed, param, index)
        if not any(_same(entity, seen) for seen in out):
            out.append(entity)
    return out


def object_collection(items: Iterable[Any]) -> Any:
    """``adsk.core.ObjectCollection`` holding ``items`` in order."""
    collection = adsk.core.ObjectCollection.create()
    for item in items:
        collection.add(item)
    return collection


EDGE = "BRepEdge"
PATH_TYPES: tuple[str, ...] = (*CURVE_TYPES, EDGE)


def make_path(ctx: Any, component: Any, refs: Any, chain: bool = True, param: str = "path") -> Any:
    """Sweep path / guide rail (D12) from a non-empty list of refs to sketch curves **or**
    BRep edges (never both: ``INVALID_PARAMS`` with ``reason = "mixed_path"``).

    Sketch curves go through c05 ``sketch.profiles.resolve_curves`` (``chain`` extends each
    curve to every curve connected end to end) and become
    ``component.features.createPath(<curve | ObjectCollection>, False)`` - the chain is
    already expanded. A single edge is ``createPath(edge, chain)`` (Fusion chains tangent
    edges); several edges are ``createPath(ObjectCollection, False)``. ``createPath`` creates
    no timeline item; a set Fusion cannot join into one path (a gap) -> ``FUSION_API_ERROR``.
    """
    if not isinstance(refs, list | tuple) or not refs:
        raise _invalid(
            f"parameter '{param}' must be a non-empty list of sketch curve or edge refs",
            param=param,
            expected=["SketchCurve", EDGE],
            reason="empty",
        )
    entities = [resolve_one(ctx, ref, PATH_TYPES, param, index) for index, ref in enumerate(refs)]
    edges = [type_name(e) == EDGE for e in entities]
    if any(edges) and not all(edges):
        raise _invalid(
            f"parameter '{param}' mixes sketch curves and edges; build the path from one kind",
            param=param,
            reason="mixed_path",
            expected=["SketchCurve", EDGE],
        )
    if all(edges):
        items = resolve_many(ctx, list(refs), (EDGE,), param)
        args: tuple[Any, bool] = (
            (items[0], chain) if len(items) == 1 else (object_collection(items), False)
        )
    else:
        try:
            items = resolve_curves(ctx, list(refs), chain)
        except BridgeError as exc:
            exc.details.setdefault("param", param)
            raise
        args = (items[0], False) if len(items) == 1 else (object_collection(items), False)
    try:
        path = component.features.createPath(*args)
    except RuntimeError as exc:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"Fusion could not build a path from '{param}': {exc}",
            {"param": param, "count": len(items)},
        ) from None
    if path is None:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"Fusion could not build a path from '{param}'",
            {"param": param, "count": len(items)},
        )
    return path


def feature_profiles(
    ctx: Any,
    specs: Any,
    allow_faces: bool = False,
    allow_text: bool = False,
    param: str = "profiles",
) -> Any:
    """Profile input of a feature (D3) -> one entity, or an ``ObjectCollection`` of several.

    ``specs`` is a ProfileSpec or a list of them: a ``profile:<sketch>@x,y`` ref or a
    ``{"sketch", "point" | "curves" | "all"}`` dict is resolved by c05
    ``sketch.profiles.resolve_profiles`` (its ``ENTITY_NOT_FOUND`` with ``details.profiles``
    and ``INVALID_PARAMS`` are passed through); any other string is an entity ref: a planar
    ``BRepFace`` (``allow_faces``) or a ``SketchText`` (``allow_text``). Duplicates are
    dropped: all profile specs go to one ``resolve_profiles`` call (which dedupes profiles by
    region) and take the place of the first of them; faces and texts are deduped by token.
    """
    spec_list = specs if isinstance(specs, list) else [specs]
    if not spec_list:
        raise _invalid(f"parameter '{param}' must not be empty", param=param, reason="empty")
    allowed = [PROFILE]
    if allow_faces:
        allowed.append("BRepFace (planar)")
    if allow_text:
        allowed.append(SKETCH_TEXT)

    out: list[Any] = []
    keys: set[Any] = set()
    profile_specs: list[Any] = []
    profiles_at: int | None = None

    def add(entity: Any) -> None:
        key = _token(entity) or id(entity)
        if key not in keys:
            keys.add(key)
            out.append(entity)

    for index, spec in enumerate(spec_list):
        if isinstance(spec, dict) or (isinstance(spec, str) and is_profile_ref(spec)):
            if profiles_at is None:
                profiles_at = len(out)
            profile_specs.append(spec)
            continue
        if not isinstance(spec, str) or not spec:
            raise _invalid(
                f"parameter '{param}[{index}]' must be a profile ref, a profile spec object "
                "or an entity ref",
                param=param,
                index=index,
                ref=repr(spec),
                expected=allowed,
            )
        try:
            entity = ctx.refs.resolve(spec)
        except BridgeError as exc:
            exc.details.setdefault("param", param)
            raise
        kind = type_name(entity)
        ok = (
            kind == PROFILE
            or (allow_faces and is_face(entity) and is_planar(entity))
            or (allow_text and kind in TEXT_TYPES)
        )
        if not ok:
            actual = describe_kind(entity)
            raise BridgeError(
                ErrorCode.INVALID_PARAMS,
                f"parameter '{param}[{index}]' must be {' | '.join(allowed)}, got {actual}",
                {
                    "param": param,
                    "index": index,
                    "ref": spec,
                    "expected": allowed,
                    "actual": actual,
                },
            )
        add(entity)
    if profile_specs:
        resolved = [p for p in resolve_profiles(ctx, profile_specs) if all(p is not o for o in out)]
        at = profiles_at if profiles_at is not None else len(out)
        out[at:at] = resolved
    return out[0] if len(out) == 1 else object_collection(out)


# --- operation and participant bodies (D5) -----------------------------------------------

_OPERATION_ATTRS = {
    "new_body": "NewBodyFeatureOperation",
    "join": "JoinFeatureOperation",
    "cut": "CutFeatureOperation",
    "intersect": "IntersectFeatureOperation",
    "new_component": "NewComponentFeatureOperation",
}
_NO_PARTICIPANTS = ("new_body", "new_component")


def operation_enum(name: Any, param: str = "operation") -> Any:
    """``"new_body" | "join" | "cut" | "intersect" | "new_component"`` ->
    ``adsk.fusion.FeatureOperations.*FeatureOperation``."""
    choice = check_choice(param, name, OPERATIONS)
    return getattr(adsk.fusion.FeatureOperations, _OPERATION_ATTRS[choice])


def participant_bodies(
    ctx: Any, refs: Any, operation: str | None = None, param: str = "participant_bodies"
) -> list[Any] | None:
    """``None`` or ``[]`` -> ``None`` (Fusion uses every body); else the resolved
    ``BRepBody`` list. A non-empty list with ``new_body``/``new_component`` is
    ``INVALID_PARAMS``. Call it before any change to the design."""
    if refs is None:
        return None
    if not isinstance(refs, list | tuple):
        raise _invalid(
            f"parameter '{param}' must be a list of body refs", param=param, expected=[BODY]
        )
    if not refs:
        return None
    if operation is not None:
        check_choice("operation", operation, OPERATIONS)
        if operation in _NO_PARTICIPANTS:
            raise _invalid(
                f"parameter '{param}' cannot be used with operation '{operation}': "
                "it only limits join, cut and intersect",
                param=param,
                operation=operation,
            )
    return resolve_many(ctx, list(refs), (BODY,), param)


def apply_participants(
    ctx: Any,
    feature_input: Any,
    refs: Any,
    operation: str | None = None,
    param: str = "participant_bodies",
) -> list[Any] | None:
    """Resolve ``refs`` with ``participant_bodies`` and, unless ``None``, set
    ``feature_input.participantBodies`` to the Python list of bodies (as the API requires).
    Returns the bodies (or ``None`` when the input was left untouched)."""
    bodies = participant_bodies(ctx, refs, operation, param)
    if bodies is not None:
        feature_input.participantBodies = list(bodies)
    return bodies


# --- extents (D6) ------------------------------------------------------------------------


def direction_enum(name: Any, param: str = "direction") -> Any:
    """``"positive" | "negative"`` -> ``adsk.fusion.ExtentDirections.*ExtentDirection``."""
    choice = check_choice(param, name, DIRECTIONS)
    directions = adsk.fusion.ExtentDirections
    if choice == "positive":
        return directions.PositiveExtentDirection
    return directions.NegativeExtentDirection


def _spec(spec: Any, param: str, choices: Sequence[str]) -> tuple[dict[str, Any], str]:
    if not isinstance(spec, dict):
        raise _invalid(
            f"parameter '{param}' must be an object with 'type' ({', '.join(choices)})",
            param=param,
        )
    kind = check_choice(f"{param}.type", spec.get("type"), choices)
    return spec, kind


def _field(spec: dict[str, Any], name: str, param: str) -> Any:
    if spec.get(name) is None:
        raise _invalid(
            f"parameter '{param}' of type '{spec.get('type')}' needs '{name}'",
            param=f"{param}.{name}",
        )
    return spec[name]


def _bool(spec: dict[str, Any], name: str, default: bool, param: str) -> bool:
    value = spec.get(name, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise _invalid(f"parameter '{param}.{name}' must be a boolean", param=f"{param}.{name}")
    return value


def extent_definition(
    ctx: Any, spec: Any, units: Units | str | None = None, param: str = "extent"
) -> Any:
    """Extent (D6) -> Fusion extent definition:
    ``{"type": "distance", "distance"}`` -> ``DistanceExtentDefinition``,
    ``{"type": "to_entity", "entity", "offset" = 0, "chained" = true}`` ->
    ``ToEntityExtentDefinition``, ``{"type": "through_all"}`` -> ``ThroughAllExtentDefinition``.
    A negative distance is passed as is (Fusion flips the side)."""
    spec, kind = _spec(spec, param, EXTENT_TYPES)
    if kind == "distance":
        distance = length_value(ctx, _field(spec, "distance", param), units, f"{param}.distance")
        return adsk.fusion.DistanceExtentDefinition.create(distance)
    if kind == "to_entity":
        entity = resolve_one(
            ctx, _field(spec, "entity", param), EXTENT_ENTITY_TYPES, f"{param}.entity"
        )
        chained = _bool(spec, "chained", True, param)
        offset_raw = spec.get("offset")
        offset = length_value(
            ctx, 0 if offset_raw is None else offset_raw, units, f"{param}.offset"
        )
        return adsk.fusion.ToEntityExtentDefinition.create(entity, chained, offset)
    return adsk.fusion.ThroughAllExtentDefinition.create()


def start_definition(
    ctx: Any, spec: Any, units: Units | str | None = None, param: str = "start"
) -> Any:
    """Start (D6) -> Fusion start definition:
    ``{"type": "profile_plane"}`` -> ``ProfilePlaneStartDefinition``,
    ``{"type": "offset", "offset"}`` -> ``OffsetStartDefinition``,
    ``{"type": "entity", "entity", "offset" = 0}`` -> ``EntityStartDefinition``."""
    spec, kind = _spec(spec, param, START_TYPES)
    if kind == "profile_plane":
        return adsk.fusion.ProfilePlaneStartDefinition.create()
    if kind == "offset":
        offset = length_value(ctx, _field(spec, "offset", param), units, f"{param}.offset")
        return adsk.fusion.OffsetStartDefinition.create(offset)
    entity = resolve_one(ctx, _field(spec, "entity", param), EXTENT_ENTITY_TYPES, f"{param}.entity")
    offset_raw = spec.get("offset")
    offset = length_value(ctx, 0 if offset_raw is None else offset_raw, units, f"{param}.offset")
    return adsk.fusion.EntityStartDefinition.create(entity, offset)


# --- target component (D7) ---------------------------------------------------------------


def _attr(entity: Any, name: str) -> Any:
    try:
        return getattr(entity, name, None)
    except Exception:
        return None


def _native(entity: Any) -> Any:
    """``nativeObject`` of an assembly-context proxy, else the entity itself."""
    native = _attr(entity, "nativeObject")
    return native if native is not None else entity


def target_component(entity: Any) -> Any:
    """Component a feature on ``entity`` is created in (D7): sketch entities and profiles ->
    ``parentSketch.parentComponent``; faces/edges/vertices -> ``body.parentComponent``; a body
    or a feature -> ``parentComponent``; construction geometry -> ``component``. A proxy is
    looked up through its ``nativeObject`` (the proxy itself is still what goes to Fusion)."""
    native = _native(entity)
    kind = type_name(native)
    component = None
    if kind in _SKETCH_TYPES or (kind.startswith("Sketch") and _attr(native, "parentSketch")):
        sketch = _attr(native, "parentSketch")
        component = _attr(sketch, "parentComponent") if sketch is not None else None
    elif kind in _TOPOLOGY_TYPES:
        body = _attr(native, "body")
        component = _attr(body, "parentComponent") if body is not None else None
    elif kind in _CONSTRUCTION_TYPES:
        component = _attr(native, "component") or _attr(native, "parentComponent")
    elif kind == "Component":
        component = native
    else:
        component = _attr(native, "parentComponent")
    if component is None:
        raise _invalid(
            f"cannot determine the component of a {kind}",
            reason="no_component",
            actual=kind,
        )
    return component


# --- result and body snapshot (D8, D9) ---------------------------------------------------


def _ref_object(ctx: Any, entity: Any) -> dict[str, Any]:
    """``{ref, type, name}``; a body also gets ``is_solid`` (``BRepBody.isSolid``: false for a
    surface body), so surface tools' results tell sheets from solids (c15 4.1)."""
    data: dict[str, Any] = {"ref": ctx.refs.to_ref(entity), "type": type_name(entity)}
    name = _attr(entity, "name")
    if isinstance(name, str):
        data["name"] = name
    if data["type"] == BODY:
        solid = _attr(entity, "isSolid")
        if isinstance(solid, bool):
            data["is_solid"] = solid
    return data


@dataclass
class BodySnapshot:
    """``entityToken`` -> body of every ``bRepBodies`` entry of ``components`` (D8), taken
    right before ``add``. ``components`` are remembered for the snapshot after ``add``."""

    components: list[Any] = field(default_factory=list)
    bodies: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def take(cls, components: Iterable[Any]) -> BodySnapshot:
        unique: list[Any] = []
        for component in components:
            if component is not None and not any(_same(component, c) for c in unique):
                unique.append(component)
        bodies: dict[str, Any] = {}
        for component in unique:
            for body in _items(_attr(component, "bRepBodies")):
                token = _token(body)
                if token is not None and _is_valid(body):
                    bodies.setdefault(token, body)
        return cls(unique, bodies)

    def tokens(self) -> set[str]:
        return set(self.bodies)

    def token_of(self, body: Any) -> str | None:
        """Token ``body`` had in this snapshot: by its current token or by identity (Fusion
        may give a modified body a new token, design R4)."""
        token = _token(body)
        if token is not None and token in self.bodies:
            return token
        for old, known in self.bodies.items():
            if known is body:
                return old
        return None


def feature_result(
    ctx: Any,
    feature: Any,
    before: BodySnapshot,
    touched: Iterable[Any] = (),
    operation: str | None = None,
) -> dict[str, Any]:
    """D8 result of a feature just added; health per D9.

    ``check_health`` (c03) raises ``FUSION_API_ERROR`` (``details.health``,
    ``details.feature_type``) for an ``error`` feature - the dispatcher rolls back; a
    ``warning`` stays in ``health``. ``touched`` are bodies the feature changed besides
    ``feature.bodies`` (parents of a modifier's edges/faces). With ``operation =
    "new_component"`` the result also has ``component``.
    """
    if feature is None:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "Fusion did not create the feature")
    health = check_health(feature)
    after = BodySnapshot.take(before.components)
    old_tokens = before.tokens()

    created: list[Any] = []
    modified: list[Any] = []
    kept_old: set[str] = set()

    def push(target: list[Any], body: Any) -> None:
        if not any(_same(body, b) for b in (*created, *modified)):
            target.append(body)

    for body in [*_items(_attr(feature, "bodies")), *touched]:
        if body is None or not _is_valid(body):
            continue
        old = before.token_of(body)
        if old is not None:
            kept_old.add(old)
            push(modified, body)
    for body in _items(_attr(feature, "bodies")):
        if body is not None and _is_valid(body) and before.token_of(body) is None:
            push(created, body)
    for token, body in after.bodies.items():
        if token not in old_tokens and before.token_of(body) is None:
            push(created, body)

    after_tokens = after.tokens()
    removed = [
        token
        for token, body in before.bodies.items()
        if token not in after_tokens
        and token not in kept_old
        and not any(known is body for known in after.bodies.values())
    ]

    result: dict[str, Any] = {
        "feature": _ref_object(ctx, feature),
        "health": health,
        "created_bodies": [_ref_object(ctx, b) for b in created],
        "modified_bodies": [_ref_object(ctx, b) for b in modified],
        "removed_bodies": removed,
    }
    if operation == "new_component":
        owner = next(
            (c for c in (_attr(b, "parentComponent") for b in created) if c is not None),
            None,
        )
        if owner is None:
            owner = _attr(feature, "parentComponent")
        if owner is not None:
            result["component"] = _ref_object(ctx, owner)
    return result

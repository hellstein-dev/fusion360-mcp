"""``BatchScope`` (c05 D4): local ids of one call and resolution of entity/point specs.

An EntitySpec string is resolved in the order of D4:

1. ``"origin"`` -> ``sketch.originPoint``;
2. a local path ``id`` / ``id.sub`` registered earlier in this call;
3. otherwise an entity ref -> ``ctx.refs.resolve``; the entity must belong to this sketch
   (``parentSketch`` with the same token), else ``INVALID_PARAMS``
   ``reason = "foreign_sketch_entity"``.

A string that looks like a local path but is neither registered nor a known token is
``INVALID_PARAMS`` ``reason = "unknown_local_id"``. Types are checked by ``objectType``
(``type_name``), not ``isinstance``: ``expected`` lists type names such as ``"SketchLine"``
and a mismatch is ``INVALID_PARAMS`` with ``details.expected``.

Validation before mutation (D6): a handler first ``declare``s every item's paths (with their
types when known) and ``check``s every spec, then creates geometry and ``register``s the
entities. Local ids live only for one call; ``refs()`` gives the flat outward map.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.units import Units

from ..core.refs import describe, type_name
from . import space
from .specs import (
    LocalPath,
    ModelXyz,
    Origin,
    PointSpec,
    Token,
    Xy,
    parse_entity_spec,
    parse_point_spec,
    validate_local_id,
)

POINT_TYPES: tuple[str, ...] = ("SketchPoint",)
LINE_TYPES: tuple[str, ...] = ("SketchLine",)
CIRCULAR_TYPES: tuple[str, ...] = ("SketchCircle", "SketchArc")
ELLIPSE_TYPES: tuple[str, ...] = ("SketchEllipse", "SketchEllipticalArc")
SPLINE_TYPES: tuple[str, ...] = (
    "SketchFittedSpline",
    "SketchFixedSpline",
    "SketchControlPointSpline",
)
CURVE_TYPES: tuple[str, ...] = (
    *LINE_TYPES,
    *CIRCULAR_TYPES,
    *ELLIPSE_TYPES,
    *SPLINE_TYPES,
    "SketchConicCurve",
)
TEXT_TYPES: tuple[str, ...] = ("SketchText",)

# How many known local ids an "unknown local id" error lists.
KNOWN_IDS_LIMIT = 50

_PENDING = object()


def _invalid(message: str, field: str, value: Any, **details: Any) -> BridgeError:
    return BridgeError(
        ErrorCode.INVALID_PARAMS, message, {"field": field, "value": value, **details}
    )


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


def same_entity(a: Any, b: Any) -> bool:
    """Identity or equal ``entityToken`` (Fusion hands out new wrappers on every access)."""
    if a is b:
        return True
    token = _token(a)
    return token is not None and token == _token(b)


def entity_ref(entity: Any) -> dict[str, Any]:
    """The c02 outward ref ``{"ref", "type", "name"?}`` of an entity."""
    return describe(entity)


class BatchScope:
    """Local id table of one call plus spec resolution against ``sketch``.

    ``units`` (default ``ctx.units``) converts ``[x, y]``/``{"model"}`` coordinates;
    projection warnings of model points collect in ``warnings``.
    """

    def __init__(self, ctx: Any, sketch: Any, units: Units | str | None = None) -> None:
        self.ctx = ctx
        self.sketch = sketch
        self.units = space.as_units(units if units is not None else getattr(ctx, "units", None))
        self.warnings: list[dict[str, Any]] = []
        self._entities: dict[str, Any] = {}
        self._declared: dict[str, str | None] = {}
        self._items: dict[str, list[str]] = {}

    # --- table -------------------------------------------------------------------------

    def _add_id(self, item_id: str, field: str) -> None:
        validate_local_id(item_id, field)
        if item_id in self._items:
            raise _invalid(
                f"{field} {item_id!r} is used by an earlier item of this call",
                field,
                item_id,
                reason="duplicate_id",
            )
        self._items[item_id] = []

    def declare(
        self,
        item_id: str | None,
        item_type: str | None = None,
        subs: Mapping[str, str | None] | Iterable[str] = (),
        field: str = "id",
    ) -> None:
        """Validation phase: announce ``item_id`` and its sub paths (``sub -> type name`` or
        names only) so later specs can be checked before anything is created."""
        if item_id is None:
            return
        self._add_id(item_id, field)
        types = dict(subs) if isinstance(subs, Mapping) else dict.fromkeys(subs)
        self._declared[item_id] = item_type
        self._items[item_id].append(item_id)
        for sub, kind in types.items():
            path = f"{item_id}.{sub}"
            self._declared[path] = kind
            self._items[item_id].append(path)

    def register(
        self,
        item_id: str | None,
        entity: Any,
        subs: Mapping[str, Any] | None = None,
        field: str = "id",
    ) -> None:
        """Mutation phase: bind ``item_id`` (and ``id.sub``) to the created entities. An item
        declared earlier keeps its declaration; anything else is validated here."""
        if item_id is None:
            return
        if item_id in self._entities:
            raise _invalid(
                f"{field} {item_id!r} is used by an earlier item of this call",
                field,
                item_id,
                reason="duplicate_id",
            )
        if item_id not in self._items:
            self._add_id(item_id, field)
        paths = self._items[item_id]
        self._entities[item_id] = entity
        if item_id not in paths:
            paths.append(item_id)
        for sub, sub_entity in (subs or {}).items():
            path = f"{item_id}.{sub}"
            self._entities[path] = sub_entity
            if path not in paths:
                paths.append(path)

    def has(self, path: str) -> bool:
        """``True`` for a registered local path."""
        return path in self._entities

    def get(self, path: str) -> Any:
        """The entity registered at ``path`` (``None`` if absent)."""
        return self._entities.get(path)

    def ids(self) -> list[str]:
        return list(self._items)

    def refs(self) -> dict[str, dict[str, Any]]:
        """Flat map ``path -> ref`` of every registered local path (item order)."""
        out: dict[str, dict[str, Any]] = {}
        for paths in self._items.values():
            for path in paths:
                entity = self._entities.get(path, _PENDING)
                if entity is not _PENDING and entity is not None:
                    out[path] = entity_ref(entity)
        return out

    # --- errors ------------------------------------------------------------------------

    def _check_type(
        self, kind: str | None, expected: Sequence[str] | None, field: str, value: str
    ) -> None:
        if expected is None or kind is None or kind in expected:
            return
        raise _invalid(
            f"{field} {value!r} is a {kind}, expected {' | '.join(expected)}",
            field,
            value,
            reason="wrong_type",
            expected=list(expected),
            got=kind,
        )

    def _unknown_local(self, path: LocalPath, field: str) -> BridgeError:
        if path.id in self._items and path.sub is not None:
            subs = [p.split(".", 1)[1] for p in self._items[path.id] if "." in p]
            return _invalid(
                f"{field} {path.path!r}: item {path.id!r} has no sub-entity {path.sub!r}",
                field,
                path.path,
                reason="unknown_sub",
                available=subs,
            )
        return _invalid(
            f"{field} {path.path!r} is neither a local id of an earlier item of this call nor"
            " an entity ref",
            field,
            path.path,
            reason="unknown_local_id",
            known_ids=list(self._items)[:KNOWN_IDS_LIMIT],
        )

    # --- entities ----------------------------------------------------------------------

    def _parse(self, spec: Any, field: str) -> Origin | LocalPath | Token:
        if isinstance(spec, Origin | LocalPath | Token):
            return spec
        return parse_entity_spec(spec, field)

    def _resolve_token(self, ref: str, field: str, own: bool) -> Any:
        try:
            entity = self.ctx.refs.resolve(ref)
        except BridgeError as exc:
            exc.details.setdefault("field", field)
            raise
        if not _is_valid(entity):
            raise BridgeError(
                ErrorCode.ENTITY_NOT_FOUND,
                f"{field} {ref!r} was deleted",
                {"ref": ref, "field": field},
            )
        if own:
            try:
                parent = entity.parentSketch
            except Exception:
                parent = None
            if parent is None or not same_entity(parent, self.sketch):
                raise _invalid(
                    f"{field} {ref!r} is not an entity of this sketch",
                    field,
                    ref,
                    reason="foreign_sketch_entity",
                    got=type_name(entity),
                )
        return entity

    def entity(
        self,
        spec: Any,
        expected: Sequence[str] | None = None,
        field: str = "entity",
        own: bool = True,
    ) -> Any:
        """Resolve an EntitySpec (D4 order) and check its type against ``expected``.

        ``own=False`` skips the "belongs to this sketch" check (projection sources)."""
        parsed = self._parse(spec, field)
        if isinstance(parsed, Origin):
            entity = self.sketch.originPoint
            self._check_type(type_name(entity), expected, field, "origin")
            return entity
        if isinstance(parsed, LocalPath):
            path = parsed.path
            if path in self._entities:
                entity = self._entities[path]
                self._check_type(type_name(entity), expected, field, path)
                return entity
            if path in self._declared:
                raise _invalid(
                    f"{field} {path!r} refers to an item that is not created yet: only earlier"
                    " items of the call can be referenced",
                    field,
                    path,
                    reason="forward_reference",
                )
            if parsed.id in self._items:
                raise self._unknown_local(parsed, field)
            try:
                entity = self._resolve_token(path, field, own)
            except BridgeError as exc:
                if exc.code is ErrorCode.ENTITY_NOT_FOUND:
                    raise self._unknown_local(parsed, field) from None
                raise
            self._check_type(type_name(entity), expected, field, path)
            return entity
        entity = self._resolve_token(parsed.token, field, own)
        self._check_type(type_name(entity), expected, field, parsed.token)
        return entity

    def check(
        self,
        spec: Any,
        expected: Sequence[str] | None = None,
        field: str = "entity",
        own: bool = True,
    ) -> None:
        """Validation phase: like ``entity`` but a declared local path is enough (its
        declared type, when known, is checked)."""
        parsed = self._parse(spec, field)
        if isinstance(parsed, LocalPath) and parsed.path not in self._entities:
            path = parsed.path
            if path in self._declared:
                self._check_type(self._declared[path], expected, field, path)
                return
            if parsed.id in self._items:
                raise self._unknown_local(parsed, field)
        self.entity(parsed, expected, field, own)

    # --- points ------------------------------------------------------------------------

    def point(self, spec: Any, field: str = "point") -> Any:
        """PointSpec -> an existing ``SketchPoint`` (passed to Fusion as is, so points are
        shared) or a sketch-space ``Point3D`` (cm) for coordinates."""
        parsed = spec if isinstance(spec, Xy | ModelXyz) else self._parse_point(spec, field)
        if isinstance(parsed, Xy | ModelXyz):
            return space.to_point3d(parsed, self.units, self.sketch, self.warnings, field)
        return self.entity(parsed, POINT_TYPES, field)

    def position(self, spec: Any, field: str = "point") -> Any:
        """PointSpec -> sketch-space ``Point3D`` (cm): a ``SketchPoint``'s ``geometry``."""
        resolved = self.point(spec, field)
        if type_name(resolved) in POINT_TYPES:
            return resolved.geometry
        return resolved

    def check_point(self, spec: Any, field: str = "point") -> None:
        """Validation phase for a PointSpec: coordinates are parsed, entities checked."""
        parsed = spec if isinstance(spec, Xy | ModelXyz) else self._parse_point(spec, field)
        if not isinstance(parsed, Xy | ModelXyz):
            self.check(parsed, POINT_TYPES, field)

    def _parse_point(self, spec: Any, field: str) -> PointSpec:
        if isinstance(spec, Origin | LocalPath | Token):
            return spec
        return parse_point_spec(spec, field)

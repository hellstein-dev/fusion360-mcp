"""Parsing of point and entity specs of sketch tools into tagged values (c05 D2, D4).

Pure Python, no ``adsk``: the values are resolved against a sketch later by ``BatchScope``
(``resolve.py``).

- **EntitySpec** (which entity): a string, classified by ``parse_entity_spec`` as
  ``Origin`` (the reserved name ``"origin"``), ``LocalPath`` (``id`` or ``id.sub`` of the
  current batch) or ``Token`` (an ``entityToken`` or any other c02/c03 ref). A ``LocalPath``
  is only a *candidate*: the resolver looks it up in the batch table first and, if absent,
  may still treat ``LocalPath.path`` as a token (D4 order origin -> local path -> token;
  base64 tokens are far longer than 32 characters, so in practice they never collide).
- **PointSpec**: ``[x, y]`` (numbers, sketch space, tool units) -> ``Xy``;
  ``{"model": [x, y, z]}`` (model space) -> ``ModelXyz``; a string -> an EntitySpec that
  must resolve to a ``SketchPoint`` (or a curve sub such as ``l1.end``).

Coordinates are numbers only: geometry is approximate, exact sizes and expressions are bound
with ``sketch_dimension`` (D2). Every malformed spec is ``INVALID_PARAMS``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

ORIGIN_ID = "origin"
LOCAL_ID_PATTERN = r"[A-Za-z][A-Za-z0-9_]{0,31}"
LOCAL_ID_RE = re.compile(LOCAL_ID_PATTERN)
LOCAL_PATH_RE = re.compile(rf"({LOCAL_ID_PATTERN})(?:\.({LOCAL_ID_PATTERN}))?")
STRING_COORD_HINT = (
    "coordinates must be numbers (approximate geometry); bind exact sizes, expressions and "
    "parameters with sketch_dimension"
)


@dataclass(frozen=True)
class Xy:
    """A sketch-space point in the tool's units (mm by default)."""

    x: float
    y: float


@dataclass(frozen=True)
class ModelXyz:
    """A model-space point in the tool's units; projected onto the sketch plane later."""

    x: float
    y: float
    z: float


@dataclass(frozen=True)
class Origin:
    """The sketch origin point (``Sketch.originPoint``)."""


@dataclass(frozen=True)
class LocalPath:
    """``id`` or ``id.sub`` of an item created earlier in the same call."""

    id: str
    sub: str | None = None

    @property
    def path(self) -> str:
        return self.id if self.sub is None else f"{self.id}.{self.sub}"


@dataclass(frozen=True)
class Token:
    """Any other ref string: an ``entityToken`` (checked to belong to the sketch later)."""

    token: str


EntitySpec = Origin | LocalPath | Token
PointSpec = Xy | ModelXyz | Origin | LocalPath | Token


def _invalid(message: str, field: str, value: Any, **details: Any) -> BridgeError:
    return BridgeError(
        ErrorCode.INVALID_PARAMS, message, {"field": field, "value": value, **details}
    )


def is_local_id(value: object) -> bool:
    """``True`` for a string matching the local id grammar (``origin`` included)."""
    return isinstance(value, str) and LOCAL_ID_RE.fullmatch(value) is not None


def validate_local_id(value: object, field: str = "id") -> str:
    """Check an item ``id`` given by the agent; returns it unchanged."""
    if not isinstance(value, str) or LOCAL_ID_RE.fullmatch(value) is None:
        raise _invalid(
            f"{field} {value!r} is not a valid local id: a letter followed by up to 31 letters,"
            " digits or '_'",
            field,
            value,
            reason="invalid_local_id",
            pattern=LOCAL_ID_RE.pattern,
        )
    if value == ORIGIN_ID:
        raise _invalid(
            f"{field} 'origin' is reserved for the sketch origin point",
            field,
            value,
            reason="reserved_id",
        )
    return value


def parse_entity_spec(value: object, field: str = "entity") -> EntitySpec:
    """Classify an EntitySpec string: ``Origin``, ``LocalPath`` candidate or ``Token``."""
    if not isinstance(value, str) or not value:
        raise _invalid(
            f"{field} must be a non-empty string: 'origin', a local id / 'id.sub' of this call,"
            " or an entity ref",
            field,
            value,
            reason="invalid_entity_spec",
        )
    if value == ORIGIN_ID:
        return Origin()
    match = LOCAL_PATH_RE.fullmatch(value)
    if match is None:
        return Token(value)
    entity_id, sub = match.group(1), match.group(2)
    if entity_id == ORIGIN_ID:
        raise _invalid(
            f"{field} {value!r}: 'origin' is the sketch origin point and has no sub-entities",
            field,
            value,
            reason="reserved_id",
        )
    return LocalPath(entity_id, sub)


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _coords(value: Any, count: int, field: str, shape: str) -> tuple[float, ...]:
    if not isinstance(value, list | tuple) or len(value) != count:
        raise _invalid(
            f"{field} must be {shape}, got {value!r}",
            field,
            value,
            reason="invalid_point_spec",
        )
    if any(isinstance(c, str) for c in value):
        raise _invalid(
            f"{field} has a string coordinate: {STRING_COORD_HINT}",
            field,
            value,
            reason="string_coordinate",
            hint=STRING_COORD_HINT,
        )
    if not all(_is_number(c) for c in value):
        raise _invalid(
            f"{field} coordinates must be numbers, got {value!r}",
            field,
            value,
            reason="invalid_point_spec",
        )
    coords = tuple(float(c) for c in value)
    if not all(math.isfinite(c) for c in coords):
        raise _invalid(
            f"{field} coordinates must be finite, got {value!r}",
            field,
            value,
            reason="invalid_point_spec",
        )
    return coords


def parse_point_spec(value: object, field: str = "point") -> PointSpec:
    """``[x, y]`` -> ``Xy``; ``{"model": [x, y, z]}`` -> ``ModelXyz``; str -> EntitySpec."""
    if isinstance(value, str):
        return parse_entity_spec(value, field)
    if isinstance(value, dict):
        if set(value) != {"model"}:
            raise _invalid(
                f'{field} object must be {{"model": [x, y, z]}}, got keys {sorted(value)}',
                field,
                value,
                reason="invalid_point_spec",
            )
        x, y, z = _coords(value["model"], 3, field, '{"model": [x, y, z]} with 3 numbers')
        return ModelXyz(x, y, z)
    if isinstance(value, list | tuple):
        x, y = _coords(
            value,
            2,
            field,
            '[x, y] in sketch space (use {"model": [x, y, z]} for a model-space point)',
        )
        return Xy(x, y)
    raise _invalid(
        f'{field} must be [x, y], {{"model": [x, y, z]}} or an entity ref string, got {value!r}',
        field,
        value,
        reason="invalid_point_spec",
    )

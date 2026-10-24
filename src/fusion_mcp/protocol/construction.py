"""Construction geometry methods (c06 D3), shared by the MCP server and the add-in. Stdlib only.

``METHODS[kind][method]`` is the one table of what each ``construction.<kind>`` method takes:
its fields, their value types and, for refs, the entity kind the add-in requires
(``fusion_addin/core/entity_kinds.py``). ``validate(kind, method, params)`` checks a request
against it and returns the method's fields with defaults filled in; the server runs it before
the bridge, the add-in again before touching the design.

Field types: ``ref`` (one entity ref), ``refs`` (a list of exactly ``count`` refs), ``length``
(mm number or Fusion expression), ``angle`` (degrees number or expression), ``proportion``
(number in 0..1). Common parameters of every method (``COMMON_PARAMS``) are not method fields.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .errors import BridgeError, ErrorCode

KINDS = ("plane", "axis", "point")
COMMON_PARAMS = frozenset({"method", "component", "name", "units"})

REF = "ref"
REFS = "refs"
LENGTH = "length"
ANGLE = "angle"
PROPORTION = "proportion"

_REQUIRED = object()


@dataclass(frozen=True)
class FieldSpec:
    """One method field: ``entity_kind`` for ``ref``/``refs``, ``count`` for ``refs``;
    a field with a ``default`` is optional."""

    name: str
    field_type: str
    entity_kind: str | None = None
    count: int | None = None
    default: Any = _REQUIRED

    @property
    def required(self) -> bool:
        return self.default is _REQUIRED


@dataclass(frozen=True)
class MethodSpec:
    fields: tuple[FieldSpec, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    @property
    def required(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.required)


def _ref(name: str, entity_kind: str) -> FieldSpec:
    return FieldSpec(name, REF, entity_kind)


def _refs(name: str, entity_kind: str, count: int) -> FieldSpec:
    return FieldSpec(name, REFS, entity_kind, count)


METHODS: dict[str, dict[str, MethodSpec]] = {
    "plane": {
        "offset": MethodSpec((_ref("plane", "planar"), FieldSpec("distance", LENGTH))),
        "angle": MethodSpec(
            (_ref("edge", "linear"), FieldSpec("angle", ANGLE), _ref("plane", "planar"))
        ),
        "midplane": MethodSpec((_ref("plane", "planar"), _ref("plane2", "planar"))),
        "two_edges": MethodSpec((_ref("edge", "linear"), _ref("edge2", "linear"))),
        "three_points": MethodSpec((_refs("points", "point", 3),)),
        "tangent": MethodSpec(
            (
                _ref("face", "tangent_face"),
                FieldSpec("angle", ANGLE, default=0),
                _ref("plane", "planar"),
            )
        ),
        "tangent_at_point": MethodSpec((_ref("face", "tangent_face"), _ref("point", "point"))),
        "along_path": MethodSpec((_ref("path", "path"), FieldSpec("proportion", PROPORTION))),
    },
    "axis": {
        "two_points": MethodSpec((_refs("points", "point", 2),)),
        "edge": MethodSpec((_ref("edge", "linear"),)),
        "cylinder": MethodSpec((_ref("face", "circular_face"),)),
        "perpendicular_at_point": MethodSpec((_ref("face", "face"), _ref("point", "point"))),
        "two_planes": MethodSpec((_ref("plane", "planar"), _ref("plane2", "planar"))),
    },
    "point": {
        "vertex": MethodSpec((_ref("point", "point"),)),
        "two_edges": MethodSpec((_ref("edge", "edge"), _ref("edge2", "edge"))),
        "three_planes": MethodSpec(
            (_ref("plane", "planar"), _ref("plane2", "planar"), _ref("plane3", "planar"))
        ),
        "edge_plane": MethodSpec((_ref("edge", "edge"), _ref("plane", "planar"))),
        "center": MethodSpec((_ref("entity", "center_source"),)),
    },
}


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def method_spec(kind: str, method: Any) -> MethodSpec:
    """The spec of ``construction.<kind>`` ``method``; an unknown method is ``INVALID_PARAMS``."""
    methods = METHODS[kind]
    if not isinstance(method, str) or method not in methods:
        raise _invalid(
            f"unknown construction {kind} method {method!r}; expected one of " + ", ".join(methods),
            param="method",
            value=method if isinstance(method, str | None) else repr(method),
            expected=list(methods),
        )
    return methods[method]


def _is_ref(value: Any) -> bool:
    if isinstance(value, dict):
        value = value.get("ref")
    return isinstance(value, str) and bool(value)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _check(spec: FieldSpec, method: str, value: Any) -> Any:
    name = spec.name
    if spec.field_type == REF:
        if not _is_ref(value):
            raise _invalid(
                f"parameter '{name}' must be a non-empty ref string", param=name, method=method
            )
    elif spec.field_type == REFS:
        if not isinstance(value, list | tuple) or not all(_is_ref(v) for v in value):
            raise _invalid(
                f"parameter '{name}' must be a list of {spec.count} refs",
                param=name,
                method=method,
            )
        if len(value) != spec.count:
            raise _invalid(
                f"method '{method}' needs exactly {spec.count} refs in '{name}', got {len(value)}",
                param=name,
                method=method,
                expected_count=spec.count,
                got_count=len(value),
            )
        return list(value)
    elif spec.field_type in (LENGTH, ANGLE):
        unit = "mm" if spec.field_type == LENGTH else "degrees"
        if not (_is_number(value) or (isinstance(value, str) and value.strip())):
            raise _invalid(
                f"parameter '{name}' must be a number in {unit} or a Fusion expression string",
                param=name,
                method=method,
            )
    elif spec.field_type == PROPORTION and not (_is_number(value) and 0.0 <= value <= 1.0):
        raise _invalid(
            f"parameter '{name}' must be a number from 0 to 1 (share of the path length)",
            param=name,
            method=method,
            value=value if _is_number(value) else repr(value),
        )
    return value


def validate(kind: str, method: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Check ``params`` for ``construction.<kind>`` ``method`` (D3) and return the method's
    fields (defaults filled in). ``None`` means "not given". Missing required fields,
    fields of other methods, a wrong ``points`` length and ``proportion`` outside 0..1 are
    ``INVALID_PARAMS``; ``COMMON_PARAMS`` are ignored here."""
    if kind not in METHODS:
        raise ValueError(f"unknown construction kind {kind!r}; expected one of {KINDS}")
    spec = method_spec(kind, method)
    given = {k: v for k, v in params.items() if v is not None and k not in COMMON_PARAMS}
    extra = sorted(set(given) - set(spec.names))
    if extra:
        raise _invalid(
            f"method '{method}' does not take {', '.join(extra)}; it takes "
            + ", ".join(spec.names),
            param=extra[0],
            method=method,
            unexpected=extra,
            allowed=list(spec.names),
        )
    missing = [name for name in spec.required if name not in given]
    if missing:
        raise _invalid(
            f"method '{method}' needs {', '.join(missing)}; it takes " + ", ".join(spec.names),
            param=missing[0],
            method=method,
            missing=missing,
            required=list(spec.required),
        )
    fields: dict[str, Any] = {}
    for field in spec.fields:
        if field.name in given:
            fields[field.name] = _check(field, str(method), given[field.name])
        else:
            fields[field.name] = field.default
    return fields

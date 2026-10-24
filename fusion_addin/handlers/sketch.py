"""``sketch.*``: create, list and read 2D sketches and their profiles (c05 D3, D5, D8, D12).

Handlers here are thin wrappers (D1): validate params, then call ``fusion_addin.sketch``
modules. All coordinates go out in sketch-space mm (INV-6), entities as c02 refs (INV-7),
profiles as ``profile:<sketch token>@x,y`` (D8).

Shared helpers for every sketch handler (later groups: draw, constrain, dimension, edit):
``resolve_sketch`` (the ``sketch`` param -> a valid ``Sketch``), ``sketch_brief`` (sketch,
name, component, plane), ``sketch_state`` (``status`` and, on demand, ``profiles``: the tail
of every mutating answer, D12).
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Callable
from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.units import length_from_internal

from ..core.registry import handler
from ..sketch import describe, math2d, planes, profiles, space

SKETCH_TYPE = "Sketch"
DEFAULT_LIMIT = 500
MAX_LIMIT = 5000


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _safe_ref(entity: Any) -> dict[str, Any] | None:
    """c02 ref of ``entity``, ``None`` if there is none (or Fusion refuses to give it)."""
    try:
        return describe.entity_ref(entity)
    except Exception:
        return None


def _get(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _count(collection: Any) -> int:
    try:
        return int(collection.count)
    except Exception:
        return 0


# --- shared helpers (used by every sketch handler) -------------------------------------------


def resolve_sketch(ctx: Any, params: dict[str, Any], name: str = "sketch") -> Any:
    """The ``params[name]`` ref -> a valid ``Sketch``; ``INVALID_PARAMS`` (``reason =
    "not_a_sketch"``) for another entity, ``ENTITY_NOT_FOUND`` for a stale ref."""
    ref = ctx.require(params, name, (str, dict))
    sketch = ctx.refs.resolve(ref)
    kind = describe.type_name(sketch)
    if kind != SKETCH_TYPE:
        raise _invalid(
            f"parameter '{name}' is a {kind}, not a sketch",
            param=name,
            reason="not_a_sketch",
            type=kind,
        )
    if not bool(_get(sketch, "isValid", True)):
        raise BridgeError(
            ErrorCode.ENTITY_NOT_FOUND,
            f"sketch {ref!r} was deleted",
            {"param": name, "ref": ref if isinstance(ref, str) else ref.get("ref")},
        )
    return sketch


def sketch_brief(sketch: Any) -> dict[str, Any]:
    """``{sketch, name, component, plane}`` (refs; ``plane`` is the reference plane or face,
    ``None`` when Fusion does not report one)."""
    return {
        "sketch": describe.entity_ref(sketch),
        "name": _get(sketch, "name"),
        "component": _safe_ref(_get(sketch, "parentComponent")),
        "plane": _safe_ref(_get(sketch, "referencePlane")),
    }


def sketch_state(sketch: Any, return_profiles: bool = True) -> dict[str, Any]:
    """``{"status"}`` plus ``"profiles"`` (ProfileSummary list) when ``return_profiles``:
    the common tail of mutating sketch answers (D12)."""
    state: dict[str, Any] = {"status": describe.sketch_status(sketch)}
    if return_profiles:
        state["profiles"] = profiles.summarize_all(sketch)
    return state


def _xy_param(ctx: Any, params: dict[str, Any], name: str) -> tuple[float, float] | None:
    """Optional ``[x, y]`` in the request units -> sketch-space mm."""
    value = params.get(name)
    if value is None:
        return None
    if (
        not isinstance(value, list | tuple)
        or len(value) != 2
        or not all(
            isinstance(c, int | float) and not isinstance(c, bool) and math.isfinite(c)
            for c in value
        )
    ):
        raise _invalid(
            f"parameter '{name}' must be [x, y]: two finite numbers in sketch space",
            param=name,
        )
    return (
        length_from_internal(ctx.units.length(value[0])),
        length_from_internal(ctx.units.length(value[1])),
    )


# --- sketch.create (D3) ----------------------------------------------------------------------


@handler("sketch.create", mutating=True)
def sketch_create(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    plane_ref = ctx.require(params, "plane", (str, dict))
    name = ctx.require(params, "name", str, None)
    if name is not None and not name.strip():
        raise _invalid("parameter 'name' must not be empty", param="name")
    component_ref = ctx.require(params, "component", (str, dict), None)
    project_edges = ctx.require(params, "project_edges", bool, False)

    plane = planes.resolve_sketch_plane(ctx, plane_ref, component_ref)
    sketch = planes.create_sketch(plane, project_edges)
    try:
        if name is not None:
            sketch.name = name
        transform = space.describe_transform(sketch)
    except Exception:
        # A direct design has no timeline rollback (c02 D5): take the new sketch back here.
        with contextlib.suppress(Exception):
            sketch.deleteMe()
        raise
    return {
        "sketch": describe.entity_ref(sketch),
        "name": _get(sketch, "name"),
        "plane": _safe_ref(plane.entity),
        "component": _safe_ref(plane.component),
        "transform": transform,
    }


# --- sketch.list -----------------------------------------------------------------------------


def _list_entry(sketch: Any) -> dict[str, Any]:
    brief = sketch_brief(sketch)
    try:
        fully = describe.sketch_status(sketch, limit=0)["fully_constrained"]
    except Exception:
        fully = False
    return {
        **brief,
        "fully_constrained": fully,
        "curve_count": _count(_get(sketch, "sketchCurves")),
        "profile_count": _count(_get(sketch, "profiles")),
        "visible": bool(_get(sketch, "isVisible", True)),
    }


@handler("sketch.list")
def sketch_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    component_ref = ctx.require(params, "component", (str, dict), None)
    if component_ref is not None:
        components = [planes.resolve_component(ctx, component_ref)]
    else:
        components = describe.items(ctx.design.allComponents)
    sketches = [
        _list_entry(sketch)
        for component in components
        for sketch in describe.items(component.sketches)
    ]
    return {"sketches": sketches}


# --- sketch.get (D5) -------------------------------------------------------------------------


def _section(entities: list[Any], limit: int, fn: Callable[[Any], Any]) -> dict[str, Any]:
    return {
        "items": [fn(e) for e in entities[:limit]],
        "total": len(entities),
        "truncated": len(entities) > limit,
    }


def _profiles_section(sketch: Any, limit: int) -> dict[str, Any]:
    shapes = profiles.profile_shapes(sketch)
    return {
        "items": profiles.summarize_shapes(shapes[:limit]),
        "total": len(shapes),
        "truncated": len(shapes) > limit,
    }


SECTIONS: dict[str, Callable[[Any, int], dict[str, Any]]] = {
    "points": lambda s, n: _section(describe.sketch_points(s), n, describe.describe_point),
    "curves": lambda s, n: _section(describe.sketch_curves(s), n, describe.describe_curve),
    "constraints": lambda s, n: _section(
        describe.sketch_constraints(s), n, describe.describe_constraint
    ),
    "dimensions": lambda s, n: _section(
        describe.sketch_dimensions(s), n, describe.describe_dimension
    ),
    "texts": lambda s, n: _section(describe.sketch_texts(s), n, describe.describe_text),
    "profiles": _profiles_section,
}


def _include(ctx: Any, params: dict[str, Any]) -> list[str]:
    include = ctx.require(params, "include", list, None)
    if include is None:
        return list(SECTIONS)
    if not include:
        raise _invalid("parameter 'include' must not be empty", param="include")
    unknown = [s for s in include if s not in SECTIONS]
    if unknown:
        raise _invalid(
            f"unknown sketch_get sections {unknown!r}; expected any of {', '.join(SECTIONS)}",
            param="include",
            unknown=unknown,
            expected=list(SECTIONS),
        )
    return [s for s in SECTIONS if s in include]


@handler("sketch.get")
def sketch_get(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    sketch = resolve_sketch(ctx, params)
    include = _include(ctx, params)
    limit = ctx.require(params, "limit", int, DEFAULT_LIMIT)
    if not 1 <= limit <= MAX_LIMIT:
        raise _invalid(f"parameter 'limit' must be 1..{MAX_LIMIT}, got {limit}", param="limit")
    out: dict[str, Any] = sketch_brief(sketch)
    for name in include:
        out[name] = SECTIONS[name](sketch, limit)
    out["transform"] = space.describe_transform(sketch)
    out["status"] = describe.sketch_status(sketch)
    return out


# --- sketch.profiles (D8) --------------------------------------------------------------------


@handler("sketch.profiles")
def sketch_profiles(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    sketch = resolve_sketch(ctx, params)
    contains = _xy_param(ctx, params, "contains")
    shapes = profiles.profile_shapes(sketch)
    if contains is not None:
        # ``locate`` is ``math2d.point_in_region`` over the region (outer minus holes).
        shapes = [s for s in shapes if s.locate(contains) == math2d.INSIDE]
    return {"profiles": profiles.summarize_shapes(shapes)}

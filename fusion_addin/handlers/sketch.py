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

from ..core.dispatcher import to_bridge_error
from ..core.registry import handler
from ..sketch import constraints as draw_constraints
from ..sketch import describe, edit, math2d, planes, profiles, space
from ..sketch import dimensions as draw_dimensions
from ..sketch import geometry as draw_geometry
from ..sketch.journal import SketchJournal
from ..sketch.resolve import BatchScope

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


# === draw, constrain, dimension, dimension_set (D6, D7, D9, D10; tasks 7.1-7.4) ==============
#
# All or nothing (INV-5): every item is validated before the first API call, the mutation
# runs inside a ``SketchJournal`` and an item error carries ``details.phase`` (``geometry``,
# ``constraints``, ``dimensions`` or ``updates``), ``details.index`` and ``details.id``.

MAX_BATCH = draw_geometry.MAX_ITEMS
PHASE_GEOMETRY = draw_geometry.PHASE
PHASE_CONSTRAINTS = "constraints"
PHASE_DIMENSIONS = "dimensions"
PHASE_UPDATES = "updates"


class _Phase:
    """Tag an escaping item error with ``details.phase`` (``index``/``id`` default to
    ``None``; ``ids[index]`` names the item when given). A non-``BridgeError`` (Fusion's
    ``RuntimeError``) becomes ``FUSION_API_ERROR``."""

    def __init__(self, phase: str, ids: list[str | None] | None = None) -> None:
        self.phase = phase
        self.ids = ids

    def __enter__(self) -> None:
        return None

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object
    ) -> None:
        if not isinstance(exc, Exception):
            return
        error = to_bridge_error(exc)
        error.details["phase"] = self.phase
        index = error.details.setdefault("index", None)
        item_id = None
        if self.ids is not None and isinstance(index, int) and 0 <= index < len(self.ids):
            item_id = self.ids[index]
        error.details.setdefault("id", item_id)
        if error is not exc:
            raise error from exc
        # A BridgeError propagates as is, tagged in place.


def _batch(ctx: Any, params: dict[str, Any], name: str, required: bool) -> list[Any]:
    """A list parameter of at most ``MAX_BATCH`` items (non-empty when ``required``)."""
    value: list[Any] = (
        ctx.require(params, name, list) if required else ctx.require(params, name, list, [])
    )
    if required and not value:
        raise _invalid(f"parameter '{name}' must not be empty", param=name)
    if len(value) > MAX_BATCH:
        raise _invalid(
            f"parameter '{name}' has {len(value)} items, at most {MAX_BATCH} per call",
            param=name,
            reason="too_many",
        )
    return value


def _return_profiles(ctx: Any, params: dict[str, Any]) -> bool:
    return bool(ctx.require(params, "return_profiles", bool, True))


def _refresh_dimensions(ctx: Any, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-describe dimensions created while compute was deferred: a driven dimension's
    value is measured by the solver, which runs once ``isComputeDeferred`` is restored."""
    fresh: list[dict[str, Any]] = []
    for result in results:
        ref = result.get("ref")
        token = ref.get("ref") if isinstance(ref, dict) else None
        try:
            dim = ctx.refs.resolve(token) if isinstance(token, str) else None
            fresh.append({**result, **describe.describe_dimension(dim)} if dim else result)
        except Exception:
            fresh.append(result)
    return fresh


@handler("sketch.draw", mutating=True)
def sketch_draw(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    sketch = resolve_sketch(ctx, params)
    items = _batch(ctx, params, "items", required=True)
    constraint_items = _batch(ctx, params, "constraints", required=False)
    dimension_items = _batch(ctx, params, "dimensions", required=False)
    return_profiles = _return_profiles(ctx, params)
    scope = BatchScope(ctx, sketch)  # ctx.units honours the ``units`` param

    # D6: the whole batch is checked before anything is created; constraints and dimensions
    # may name the local ids the geometry items declare.
    specs = draw_geometry.validate_items(scope, items)
    with _Phase(PHASE_CONSTRAINTS):
        draw_constraints.validate_constraints(scope, constraint_items)
    with _Phase(PHASE_DIMENSIONS):
        draw_dimensions.validate_dimensions(scope, dimension_items)

    with SketchJournal(sketch) as journal:
        journal.defer()
        draw_geometry.build_items(scope, journal, specs)
        with _Phase(PHASE_CONSTRAINTS):
            constraints_out = draw_constraints.apply_constraints(
                scope, journal, constraint_items, validate=False
            )
        with _Phase(PHASE_DIMENSIONS):
            dimensions_out = draw_dimensions.apply_dimensions(
                scope, journal, dimension_items, validate=False
            )
        journal.undefer()  # before measuring dimensions and computing profiles (D6)
        return {
            "sketch": describe.entity_ref(sketch),
            "refs": scope.refs(),
            "constraints": constraints_out,
            "dimensions": _refresh_dimensions(ctx, dimensions_out),
            **sketch_state(sketch, return_profiles),
            "warnings": scope.warnings,
        }


@handler("sketch.constrain", mutating=True)
def sketch_constrain(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    sketch = resolve_sketch(ctx, params)
    items = _batch(ctx, params, "constraints", required=True)
    return_profiles = _return_profiles(ctx, params)
    scope = BatchScope(ctx, sketch)
    with _Phase(PHASE_CONSTRAINTS):
        draw_constraints.validate_constraints(scope, items)
    with SketchJournal(sketch) as journal:
        with _Phase(PHASE_CONSTRAINTS):
            out = draw_constraints.apply_constraints(scope, journal, items, validate=False)
        return {"constraints": out, **sketch_state(sketch, return_profiles)}


@handler("sketch.dimension", mutating=True)
def sketch_dimension(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    sketch = resolve_sketch(ctx, params)
    items = _batch(ctx, params, "dimensions", required=True)
    return_profiles = _return_profiles(ctx, params)
    scope = BatchScope(ctx, sketch)  # ``units``: numeric ``text_at``
    with _Phase(PHASE_DIMENSIONS):
        draw_dimensions.validate_dimensions(scope, items)
    with SketchJournal(sketch) as journal:
        with _Phase(PHASE_DIMENSIONS):
            out = draw_dimensions.apply_dimensions(scope, journal, items, validate=False)
        return {"dimensions": out, **sketch_state(sketch, return_profiles)}


@handler("sketch.dimension_set", mutating=True)
def sketch_dimension_set(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    sketch = resolve_sketch(ctx, params)
    updates = _batch(ctx, params, "updates", required=True)
    return_profiles = _return_profiles(ctx, params)
    scope = BatchScope(ctx, sketch)
    ids = [
        u.get("dimension") if isinstance(u, dict) and isinstance(u.get("dimension"), str) else None
        for u in updates
    ]
    with SketchJournal(sketch) as journal:
        # ``apply_updates`` validates every update before changing the first one; the
        # journal restores earlier updates if a later one fails.
        with _Phase(PHASE_UPDATES, ids):
            out = draw_dimensions.apply_updates(scope, journal, updates)
        return {"dimensions": out, **sketch_state(sketch, return_profiles)}


# === editing: set_construction ... project (D11; tasks 8.1-8.11) =============================
#
# Logic lives in ``fusion_addin.sketch.edit``. Each edit runs in a ``SketchJournal`` (INV-5)
# and reads ``status``/``profiles`` inside the journal block, so a failure there still rolls
# the edit back. ``sketch.delete`` is the documented non-atomic exception (north-star INV-5):
# every ref is validated before the first deletion.

EDIT_POINT_PARAM = (str, list, dict)
EDIT_VALUE_PARAM = (int, float, str)
PATTERN_FIELDS: dict[str, tuple[str, ...]] = {
    "rectangular": ("count_x", "spacing_x", "count_y", "spacing_y", "angle", "direction"),
    "circular": ("center", "count", "angle"),
}


def _ref_list(ctx: Any, params: dict[str, Any], name: str) -> list[str]:
    """Required non-empty list (at most ``MAX_BATCH``) of entity ref strings."""
    values = _batch(ctx, params, name, required=True)
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value:
            raise _invalid(
                f"parameter '{name}[{index}]' must be an entity ref string",
                param=name,
                index=index,
            )
    return [str(v) for v in values]


def _translate_cm(ctx: Any, params: dict[str, Any]) -> tuple[float, float]:
    """``translate`` ``[dx, dy]`` (request units, default ``[0, 0]``) -> cm."""
    value = params.get("translate")
    if value is None:
        return (0.0, 0.0)
    if (
        not isinstance(value, list | tuple)
        or len(value) != 2
        or not all(
            isinstance(c, int | float) and not isinstance(c, bool) and math.isfinite(c)
            for c in value
        )
    ):
        raise _invalid(
            "parameter 'translate' must be [dx, dy]: two finite numbers (sketch mm)",
            param="translate",
        )
    return (float(ctx.units.length(value[0])), float(ctx.units.length(value[1])))


def _edit(
    ctx: Any,
    params: dict[str, Any],
    fn: Callable[[BatchScope, SketchJournal], dict[str, Any]],
) -> dict[str, Any]:
    """Run one journaled edit and append ``status``/``profiles`` (D12)."""
    sketch = resolve_sketch(ctx, params)
    return_profiles = _return_profiles(ctx, params)
    scope = BatchScope(ctx, sketch)
    with SketchJournal(sketch) as journal:
        result = fn(scope, journal)
        if scope.warnings:
            result["warnings"] = [*result.get("warnings", []), *scope.warnings]
        journal.undefer()
        result.update(sketch_state(sketch, return_profiles))
    return result


@handler("sketch.set_construction", mutating=True)
def sketch_set_construction(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    curves = _ref_list(ctx, params, "curves")
    construction = bool(ctx.require(params, "construction", bool, True))
    return _edit(
        ctx, params, lambda scope, j: edit.set_construction(scope, j, curves, construction)
    )


@handler("sketch.delete", mutating=True)
def sketch_delete(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    sketch = resolve_sketch(ctx, params)
    entities = _ref_list(ctx, params, "entities")
    return_profiles = _return_profiles(ctx, params)
    # Not journaled: Fusion cannot undo a deletion (the INV-5 exception, validated first).
    result = edit.delete(BatchScope(ctx, sketch), entities)
    result.update(sketch_state(sketch, return_profiles))
    return result


@handler("sketch.transform", mutating=True)
def sketch_transform(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    entities = _ref_list(ctx, params, "entities")
    translate = _translate_cm(ctx, params)
    rotate = ctx.require(params, "rotate", EDIT_VALUE_PARAM, 0)
    about = ctx.require(params, "about", EDIT_POINT_PARAM, [0, 0])
    copy = bool(ctx.require(params, "copy", bool, False))

    def run(scope: BatchScope, journal: SketchJournal) -> dict[str, Any]:
        rotate_rad = edit.rotation_angle(scope, rotate, "rotate")
        pivot = scope.position(about, "about")
        return edit.transform(scope, journal, entities, translate, rotate_rad, pivot, copy)

    return _edit(ctx, params, run)


@handler("sketch.offset", mutating=True)
def sketch_offset(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    curves = _ref_list(ctx, params, "curves")
    distance = ctx.require(params, "distance", EDIT_VALUE_PARAM)
    side_point = ctx.require(params, "side_point", EDIT_POINT_PARAM)
    chain = bool(ctx.require(params, "chain", bool, True))
    return _edit(
        ctx,
        params,
        lambda scope, j: edit.offset(scope, j, curves, distance, side_point, chain),
    )


@handler("sketch.trim", mutating=True)
def sketch_trim(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    curve = ctx.require(params, "curve", str)
    point = ctx.require(params, "point", EDIT_POINT_PARAM)
    return _edit(ctx, params, lambda scope, j: edit.trim(scope, curve, point))


@handler("sketch.extend", mutating=True)
def sketch_extend(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    curve = ctx.require(params, "curve", str)
    point = ctx.require(params, "point", EDIT_POINT_PARAM)
    return _edit(ctx, params, lambda scope, j: edit.extend(scope, curve, point))


@handler("sketch.fillet", mutating=True)
def sketch_fillet(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    curve1 = ctx.require(params, "curve1", str)
    curve2 = ctx.require(params, "curve2", str)
    radius = ctx.require(params, "radius", EDIT_VALUE_PARAM)
    point1 = ctx.require(params, "point1", EDIT_POINT_PARAM, None)
    point2 = ctx.require(params, "point2", EDIT_POINT_PARAM, None)
    dimension = bool(ctx.require(params, "dimension", bool, True))
    return _edit(
        ctx,
        params,
        lambda scope, j: edit.fillet(scope, j, curve1, curve2, radius, point1, point2, dimension),
    )


@handler("sketch.mirror", mutating=True)
def sketch_mirror(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    entities = _ref_list(ctx, params, "entities")
    axis = ctx.require(params, "axis", str)
    constrain = bool(ctx.require(params, "constrain", bool, True))
    return _edit(ctx, params, lambda scope, j: edit.mirror(scope, j, entities, axis, constrain))


@handler("sketch.pattern", mutating=True)
def sketch_pattern(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    entities = _ref_list(ctx, params, "entities")
    kind = ctx.require(params, "kind", str)
    if kind not in PATTERN_FIELDS:
        raise _invalid(
            f"parameter 'kind' must be rectangular or circular, got {kind!r}",
            param="kind",
            expected=list(PATTERN_FIELDS),
        )
    allowed = set(PATTERN_FIELDS[kind])
    foreign = sorted(
        {
            name
            for names in PATTERN_FIELDS.values()
            for name in names
            if name not in allowed and params.get(name) is not None
        }
    )
    if foreign:
        raise _invalid(
            f"parameters {foreign} do not apply to a {kind} pattern",
            param=foreign[0],
            unexpected=foreign,
        )
    if kind == "rectangular":
        count_x = ctx.require(params, "count_x", int)
        spacing_x = ctx.require(params, "spacing_x", EDIT_VALUE_PARAM)
        count_y = ctx.require(params, "count_y", int, 1)
        spacing_y = ctx.require(params, "spacing_y", EDIT_VALUE_PARAM, 0.0)
        angle = ctx.require(params, "angle", EDIT_VALUE_PARAM, 0.0)
        direction = ctx.require(params, "direction", str, None)
        return _edit(
            ctx,
            params,
            lambda scope, j: edit.rectangular_pattern(
                scope, j, entities, count_x, spacing_x, count_y, spacing_y, angle, direction
            ),
        )
    center = ctx.require(params, "center", EDIT_POINT_PARAM)
    count = ctx.require(params, "count", int)
    total = ctx.require(params, "angle", EDIT_VALUE_PARAM, 360.0)
    return _edit(
        ctx,
        params,
        lambda scope, j: edit.circular_pattern(scope, j, entities, center, count, total),
    )


@handler("sketch.project", mutating=True)
def sketch_project(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    entities = _ref_list(ctx, params, "entities")
    mode = ctx.require(params, "mode", str, "project")
    return _edit(ctx, params, lambda scope, j: edit.project(scope, j, entities, mode))

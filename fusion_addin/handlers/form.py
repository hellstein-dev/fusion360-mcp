"""``form.*``: T-spline forms (c14) - list, primitives, TSM in and out, control-mesh inspection
and edits (move, crease, insert edge) and an independent B-Rep copy.

Every mutating handler reads and checks all parameters and resolves every selector before it
changes anything (INV-5). A new form feature is a timeline item the dispatcher rolls back
(c02 D5); control-mesh edits and visibility changes are no timeline items and are undone by a
``Compensation`` (c09) inside ``regenerate_on_error``, so the form leaves edit mode and its
B-Rep is regenerated after the undo too (D3, D8).

Lengths are in the request ``units`` (default mm) and numbers only - T-spline primitives are
not parametric (D5); answers give positions in mm (INV-6). Vertices, edges and faces have no
entity token and are selected by points (D2, INV-7).
"""

from __future__ import annotations

import math
import os
from typing import Any

import adsk.core
import adsk.fusion

from fusion_mcp.protocol.features import check_choice
from fusion_mcp.protocol.form import (
    ALL_FACES,
    ALL_SIZES,
    CIRCULAR_SHAPES,
    DEFAULT_INSPECT_LIMIT,
    DEFAULT_TOLERANCE_MM,
    INSERTION_MODES,
    INSPECT_PARTS,
    MOVE_KINDS,
    SHAPE_FACES,
    SHAPE_SIZES,
    SHAPES,
    SYMMETRY_AXES,
    TSM_EXTENSIONS,
    TSM_INPUT_MAX_BYTES,
    TSM_OUTPUT_MAX_BYTES,
)
from fusion_mcp.protocol.paths import check_input_path, check_output_path
from fusion_mcp.protocol.units import length_to_internal

from ..core import sheet_metal_api as api
from ..core.compensate import Compensation
from ..core.features import point3d
from ..core.form import (
    api_error,
    begin_edit,
    describe_tsbody,
    element_position,
    elements,
    finish,
    get,
    get_or_create_form,
    is_parametric,
    items,
    move_element,
    regenerate_on_error,
    resolve_tsbody,
    select,
    set_crease,
    to_mm,
    transform_body,
)
from ..core.health import entity_health
from ..core.registry import handler
from ..core.sheet_metal import component_ref, resolve_component
from .feature_sketch import _bool, _given, _invalid, _opt

_CREATE = {
    "box": "createBoxInput",
    "plane": "createPlaneInput",
    "cylinder": "createCylinderInput",
    "sphere": "createSphereInput",
    "torus": "createTorusInput",
    "quadball": "createQuadballInput",
}
_FACE_ATTRS = {
    "x_faces": "xFaceCount",
    "y_faces": "yFaceCount",
    "z_faces": "zFaceCount",
    "radial_faces": "radialFaceCount",
    "length_faces": "lengthFaceCount",
    "longitude_faces": "longitudeFaceCount",
    "latitude_faces": "latitudeFaceCount",
    "major_faces": "majorFaceCount",
    "minor_faces": "minorFaceCount",
    "faces": "faceCount",
}
_PERPENDICULAR_TOLERANCE = 1e-6


# --- shared parameter readers ----------------------------------------------------------------


def _name(params: dict[str, Any]) -> str | None:
    name = params.get("name")
    if name is None:
        return None
    if not isinstance(name, str) or not name.strip():
        raise _invalid("parameter 'name' must be a non-empty string", param="name")
    return name


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _points(
    ctx: Any, params: dict[str, Any], param: str = "near"
) -> list[tuple[float, float, float]]:
    """``near``: a non-empty list of ``[x, y, z]`` in ``units`` -> points in cm."""
    raw = params.get(param)
    if not isinstance(raw, list) or not raw:
        raise _invalid(
            f"parameter '{param}' must be a non-empty list of [x, y, z] points (positions from "
            "form_inspect)",
            param=param,
        )
    points: list[tuple[float, float, float]] = []
    for xyz in raw:
        p = point3d(ctx, xyz, param=param)
        points.append((p.x, p.y, p.z))
    return points


def _tolerance(ctx: Any, params: dict[str, Any]) -> float:
    """``tolerance`` in ``units`` -> cm; the default is 0.5 mm whatever the units."""
    value = params.get("tolerance")
    if value is None:
        return float(length_to_internal(DEFAULT_TOLERANCE_MM, "mm"))
    if not _number(value) or value <= 0:
        raise _invalid("parameter 'tolerance' must be a positive number", param="tolerance")
    return float(ctx.units.length(value))


def _form_of(body: Any) -> Any:
    form = get(body, "parentFormFeature")
    if form is None:
        raise api_error("the T-spline body has no form feature", body=get(body, "name"))
    return form


def _rename(body: Any, name: str | None) -> None:
    if name is not None:
        body.name = name


def _delete_body(body: Any) -> None:
    if body.deleteMe() is False:
        raise RuntimeError("Fusion did not delete the T-spline body")


def _created(ctx: Any, form: Any, body: Any, created: bool) -> dict[str, Any]:
    return {
        "form": ctx.refs.to_ref(form),
        "body": describe_tsbody(ctx, body),
        "created_form": created,
    }


def _add_body(ctx: Any, params: dict[str, Any], make: Any, what: str) -> dict[str, Any]:
    """New T-spline body in the given or a new form: ``make(tSplineBodies)`` creates it; a
    body added to an existing form is deleted again on error (a new form is rolled back)."""
    name = _name(params)
    form, created = get_or_create_form(ctx, params.get("form"), params.get("component"))
    with regenerate_on_error(form), Compensation() as comp:
        begin_edit(form)
        body = make(form.tSplineBodies)
        if body is None:
            raise api_error(f"Fusion did not create the T-spline body ({what})", reason=what)
        if not created:
            comp.add(lambda: _delete_body(body), "delete the new T-spline body")
        _rename(body, name)
        finish(form, [body])
        result = _created(ctx, form, body, created)
    return result


# --- list (D4) -------------------------------------------------------------------------------


@handler("form.list")
def form_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Form features of the design (or one component) with their T-spline bodies (D4)."""
    if params.get("component") is not None:
        components = [resolve_component(ctx, params["component"])]
    else:
        components = items(ctx.design.allComponents)
    forms = []
    for component in components:
        for form in items(get(get(component, "features"), "formFeatures")):
            forms.append(
                {
                    "feature": ctx.refs.to_ref(form),
                    "name": get(form, "name"),
                    "component": component_ref(ctx, component),
                    "health": entity_health(form),
                    "bodies": [describe_tsbody(ctx, b) for b in items(form.tSplineBodies)],
                }
            )
    return {"forms": forms, "count": len(forms)}


# --- primitives (D5) -------------------------------------------------------------------------


def _size(ctx: Any, params: dict[str, Any], shape: str, name: str) -> float:
    value = params.get(name)
    if value is None:
        raise _invalid(f"shape '{shape}' needs '{name}'", param=name)
    if isinstance(value, str):
        raise _invalid(
            f"T-spline primitives are not parametric: parameter '{name}' must be a number, "
            "not an expression",
            param=name,
            reason="not_parametric",
        )
    if not _number(value) or value <= 0:
        raise _invalid(f"parameter '{name}' must be a positive number", param=name)
    return float(ctx.units.length(value))


def _count(params: dict[str, Any], name: str) -> int | None:
    value = params.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _invalid(f"parameter '{name}' must be an integer >= 1", param=name)
    return value


def _direction(params: dict[str, Any], name: str, default: list[float]) -> Any:
    raw = _opt(params, name, default)
    if not isinstance(raw, list | tuple) or len(raw) != 3 or not all(_number(c) for c in raw):
        raise _invalid(f"parameter '{name}' must be [x, y, z] numbers", param=name)
    vector = adsk.core.Vector3D.create(*(float(c) for c in raw))
    if vector.length == 0 or not vector.normalize():
        raise _invalid(f"parameter '{name}' must not be a zero vector", param=name)
    return vector


def _symmetry(params: dict[str, Any], shape: str) -> tuple[dict[str, bool] | None, bool]:
    """``(axis flags or None, circular)``."""
    raw = params.get("symmetry")
    if raw is None:
        return None, False
    if raw == "circular":
        if shape not in CIRCULAR_SHAPES:
            raise _invalid(
                f"circular symmetry applies to {', '.join(CIRCULAR_SHAPES)}, not '{shape}'",
                param="symmetry",
            )
        return None, True
    if not isinstance(raw, dict) or set(raw) - set(SYMMETRY_AXES):
        raise _invalid(
            'parameter \'symmetry\' must be {"x", "y", "z": bool} or "circular"',
            param="symmetry",
        )
    if not all(isinstance(v, bool) for v in raw.values()):
        raise _invalid("symmetry flags must be booleans", param="symmetry")
    return {axis: bool(raw.get(axis, False)) for axis in SYMMETRY_AXES}, False


@handler("form.create_primitive", mutating=True)
def form_create_primitive(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Box / plane / cylinder / sphere / torus / quadball T-spline body (D5)."""
    shape = check_choice("shape", params.get("shape"), SHAPES)
    for name in (*ALL_SIZES, *ALL_FACES):
        if _given(params, name) and name not in (*SHAPE_SIZES[shape], *SHAPE_FACES[shape]):
            raise _invalid(
                f"parameter '{name}' does not apply to shape '{shape}' (sizes: "
                f"{', '.join(SHAPE_SIZES[shape])}; face counts: {', '.join(SHAPE_FACES[shape])})",
                param=name,
                reason="not_for_shape",
            )
    sizes = [_size(ctx, params, shape, name) for name in SHAPE_SIZES[shape]]
    if shape == "torus" and sizes[1] >= sizes[0]:
        raise _invalid("'minor_radius' must be smaller than 'major_radius'", param="minor_radius")
    counts = {name: _count(params, name) for name in SHAPE_FACES[shape]}
    origin = point3d(ctx, _opt(params, "origin", [0, 0, 0]), param="origin")
    x_dir = _direction(params, "x_direction", [1.0, 0.0, 0.0])
    y_dir = _direction(params, "y_direction", [0.0, 1.0, 0.0])
    if abs(x_dir.dotProduct(y_dir)) > _PERPENDICULAR_TOLERANCE:
        raise _invalid("'x_direction' and 'y_direction' must be perpendicular", param="y_direction")
    axes, circular = _symmetry(params, shape)
    symmetric_faces = _count(params, "symmetric_faces")
    if symmetric_faces is not None and not circular:
        raise _invalid(
            "parameter 'symmetric_faces' needs symmetry 'circular'", param="symmetric_faces"
        )
    method = _CREATE[shape]

    def make(bodies: Any) -> Any:
        create = api.require_api(bodies, method, f"TSplineBodies.{method}")
        inp = create(origin, x_dir, y_dir, *sizes)
        if inp is None:
            raise api_error(f"Fusion did not create the {shape} input", shape=shape)
        for name, count in counts.items():
            if count is not None:
                setattr(inp, _FACE_ATTRS[name], count)
        if axes is not None:
            inp.isXSymmetric, inp.isYSymmetric, inp.isZSymmetric = (
                axes["x"],
                axes["y"],
                axes["z"],
            )
        if circular:
            inp.isCircularSymmetry = True
            if symmetric_faces is not None:
                inp.symmetricFaceCount = symmetric_faces
        return bodies.addByShapeInput(inp)

    return _add_body(ctx, params, make, "shape_input")


# --- TSM (D6) --------------------------------------------------------------------------------


@handler("form.create_from_tsm", mutating=True)
def form_create_from_tsm(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """T-spline body from TSM text or an absolute ``.tsm`` file (D6)."""
    tsm, path = params.get("tsm"), params.get("path")
    if (tsm is None) == (path is None):
        raise _invalid("pass exactly one of 'tsm' (text) and 'path' (.tsm file)", param="tsm")
    if tsm is not None:
        if not isinstance(tsm, str) or not tsm.strip():
            raise _invalid("parameter 'tsm' must be non-empty TSM text", param="tsm")
        size = len(tsm.encode("utf-8"))
        if size > TSM_INPUT_MAX_BYTES:
            raise _invalid(
                f"TSM text is {size} bytes (limit {TSM_INPUT_MAX_BYTES}): pass 'path' instead",
                param="tsm",
                reason="too_large",
                size_bytes=size,
            )
        return _add_body(ctx, params, lambda bodies: bodies.addByTSMDescription(tsm), "invalid_tsm")
    target = check_input_path(path, TSM_EXTENSIONS)
    return _add_body(ctx, params, lambda bodies: bodies.addByTSMFile(target), "invalid_tsm")


@handler("form.get_tsm")
def form_get_tsm(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """TSM text of a body (up to 1 MiB) or a ``.tsm`` file written via c12 path rules (D6)."""
    body = resolve_tsbody(ctx, ctx.require(params, "body"))
    overwrite = _bool(ctx, params, "overwrite", False)
    ref = ctx.refs.to_ref(body)
    if params.get("path") is None:
        text = body.getTSMDescription()
        if not isinstance(text, str):
            raise api_error("Fusion returned no TSM description")
        size = len(text.encode("utf-8"))
        if size > TSM_OUTPUT_MAX_BYTES:
            raise _invalid(
                f"TSM text is {size} bytes (more than {TSM_OUTPUT_MAX_BYTES}): use path to write "
                "it to a file",
                param="path",
                reason="too_large",
                size_bytes=size,
            )
        return {"body": ref, "tsm": text, "size_bytes": size}
    target = check_output_path(params["path"], TSM_EXTENSIONS, overwrite)
    if body.saveAsTSMFile(target) is False or not os.path.isfile(target):
        raise api_error("Fusion did not write the TSM file", path=target)
    return {"body": ref, "files": [{"path": target, "size_bytes": os.path.getsize(target)}]}


# --- inspect (D7) ----------------------------------------------------------------------------


def _vertex(vertex: Any) -> dict[str, Any]:
    smooth = get(vertex, "smoothGeometry")
    return {
        "position": to_mm(element_position(vertex, "vertex")),
        "smooth_position": to_mm((smooth.x, smooth.y, smooth.z)) if smooth is not None else None,
    }


def _edge(edge: Any) -> dict[str, Any]:
    return {
        "midpoint": to_mm(element_position(edge, "edge")),
        "start": to_mm(element_position(edge.startVertex, "vertex")),
        "end": to_mm(element_position(edge.endVertex, "vertex")),
        "is_creased": bool(get(edge, "isCreased", False)),
        "is_degenerate": bool(get(edge, "isDegenerate", False)),
    }


def _face(face: Any) -> dict[str, Any]:
    return {
        "center": to_mm(element_position(face, "face")),
        "vertex_count": len(items(face.vertices)),
    }


_PARTS = {"vertices": ("vertex", _vertex), "edges": ("edge", _edge), "faces": ("face", _face)}


@handler("form.inspect")
def form_inspect(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Control mesh of a T-spline body in mm, ``limit`` entries per list (D7)."""
    body = resolve_tsbody(ctx, ctx.require(params, "body"))
    include = _opt(params, "include", list(INSPECT_PARTS))
    if not isinstance(include, list) or not include:
        raise _invalid("parameter 'include' must be a non-empty list", param="include")
    wanted = {check_choice("include", part, INSPECT_PARTS) for part in include}
    limit = _opt(params, "limit", DEFAULT_INSPECT_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise _invalid("parameter 'limit' must be an integer >= 1", param="limit")
    result: dict[str, Any] = {"body": describe_tsbody(ctx, body)}
    truncated = False
    for part in INSPECT_PARTS:
        if part not in wanted:
            continue
        kind, describe = _PARTS[part]
        found = elements(body, kind)
        result[part] = [describe(e) for e in found[:limit]]
        result[f"{part}_truncated"] = len(found) > limit
        truncated = truncated or len(found) > limit
    result["truncated"] = truncated
    return result


# --- control-mesh edits (D8) -----------------------------------------------------------------


def _vector(ctx: Any, params: dict[str, Any]) -> Any:
    point = point3d(ctx, params.get("vector"), param="vector")
    vector = adsk.core.Vector3D.create(point.x, point.y, point.z)
    if vector.length == 0:
        raise _invalid("parameter 'vector' must not be zero", param="vector")
    return vector


def _edit_result(ctx: Any, body: Any, changed: int, **extra: Any) -> dict[str, Any]:
    return {"body": describe_tsbody(ctx, body), "changed": changed, **extra}


@handler("form.move", mutating=True)
def form_move(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Move selected vertices / faces or the whole body by a vector (D8)."""
    body = resolve_tsbody(ctx, ctx.require(params, "body"))
    kind = check_choice("kind", params.get("kind"), MOVE_KINDS)
    vector = _vector(ctx, params)
    selected: list[Any] = []
    if kind != "body":
        selected = select(body, kind, _points(ctx, params), _tolerance(ctx, params))
    form = _form_of(body)
    with regenerate_on_error(form), Compensation() as comp:
        if kind == "body":
            matrix = adsk.core.Matrix3D.create()
            matrix.translation = vector
            transform_body(comp, body, matrix)
            changed = 1
        else:
            for index, element in enumerate(selected):
                move_element(comp, element, vector, f"move {kind} {index} back")
            changed = len(selected)
        finish(form, [body])
        result = _edit_result(ctx, body, changed)
    return result


@handler("form.crease", mutating=True)
def form_crease(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Set or clear the crease of selected edges (D8)."""
    body = resolve_tsbody(ctx, ctx.require(params, "body"))
    creased = _bool(ctx, params, "creased", True)
    edges = select(body, "edge", _points(ctx, params), _tolerance(ctx, params))
    form = _form_of(body)
    with regenerate_on_error(form), Compensation() as comp:
        changed = sum(1 for edge in edges if set_crease(comp, edge, creased))
        finish(form, [body])
        result = _edit_result(ctx, body, changed)
    return result


@handler("form.insert_edge", mutating=True)
def form_insert_edge(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Insert edges next to selected edges (D8, preview ``FormFeature.insertEdge``)."""
    body = resolve_tsbody(ctx, ctx.require(params, "body"))
    form = _form_of(body)
    insert = api.require_api(form, "insertEdge", "FormFeature.insertEdge")
    input_class = api.require_api(adsk.fusion, "FormInsertEdgeInput", "FormInsertEdgeInput")
    position = _opt(params, "position", 0.5)
    if not _number(position) or not -1.0 <= position <= 1.0:
        raise _invalid("parameter 'position' must be a number in [-1, 1]", param="position")
    mode = check_choice("mode", _opt(params, "mode", "simple"), INSERTION_MODES)
    both = _bool(ctx, params, "both_sides", False)
    edges = select(body, "edge", _points(ctx, params), _tolerance(ctx, params))
    modes = adsk.fusion.FormInsertionModes
    sides = adsk.fusion.FormEdgeInsertionSides
    before = len(elements(body, "edge"))
    with regenerate_on_error(form):
        inp = input_class.create(list(edges), float(position))
        if inp is None:
            raise api_error("Fusion did not create the insert edge input")
        inp.insertionMode = (
            modes.SimpleFormInsertionMode if mode == "simple" else modes.ExactFormInsertionMode
        )
        inp.insertionSide = (
            sides.BothFormEdgeInsertionSide if both else sides.SingleFormEdgeInsertionSide
        )
        if insert(inp) is False:
            raise api_error("Fusion did not insert the edges (are they one loop?)")
        finish(form, [body])
        new_edges = len(elements(body, "edge")) - before
        result = _edit_result(ctx, body, len(edges), new_edges=new_edges)
    return result


# --- convert to B-Rep (D9) -------------------------------------------------------------------


def _hide(comp: Compensation, entity: Any) -> None:
    old = get(entity, "isLightBulbOn", True)
    comp.add(lambda: setattr(entity, "isLightBulbOn", old), "show the source again")
    entity.isLightBulbOn = False


@handler("form.convert_to_brep", mutating=True)
def form_convert_to_brep(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Independent B-Rep copy of a T-spline body's B-Rep (D9): in a base feature in a
    parametric design, added directly in a direct one; optionally hides the source."""
    body = resolve_tsbody(ctx, ctx.require(params, "body"))
    hide = _bool(ctx, params, "hide_form", True)
    name = _name(params)
    form = _form_of(body)
    component = get(form, "parentComponent")
    manager_class = api.require_api(adsk.fusion, "TemporaryBRepManager", "TemporaryBRepManager")
    parametric = is_parametric(ctx)

    with Compensation() as comp:
        finish(form, [body])
        source = body.bRepBody
        copy = manager_class.get().copy(source)
        if copy is None:
            raise api_error("Fusion could not copy the B-Rep body of the form")
        base = None
        if parametric:
            base = component.features.baseFeatures.add()
            if base is None:
                raise api_error("Fusion did not create a base feature")
            base.startEdit()
            try:
                new = component.bRepBodies.add(copy, base)
            finally:
                base.finishEdit()
        else:
            new = component.bRepBodies.add(copy)
            if new is not None and get(new, "deleteMe") is not None:
                comp.add(new.deleteMe, "delete the copied body")
        if new is None:
            raise api_error("Fusion did not add the copied body")
        _rename(new, name)
        if hide:
            _hide(comp, body)
            _hide(comp, source)
        result = {
            "body": ctx.refs.to_ref(new),
            "name": get(new, "name"),
            "source": ctx.refs.to_ref(body),
            "base_feature": ctx.refs.to_ref(base) if base is not None else None,
            "is_solid": bool(get(new, "isSolid", False)),
        }
    return result

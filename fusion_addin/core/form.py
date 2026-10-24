"""Helpers of the ``form.*`` handlers (c14 D2-D4, D8).

- Selection (D2): control-mesh vertices, edges and faces have no ``entityToken`` (INV-7), so
  they are addressed by points. ``element_position`` is the point a selector is matched to
  (vertex: ``geometry``; edge: midpoint of its end vertices; face: mean of its vertices);
  ``select`` takes the nearest element per point, refusing a match farther than the tolerance
  or one tied with the second nearest (``INVALID_PARAMS`` with ``details.nearest``).
- Form lifecycle (D3): ``get_or_create_form``; ``finish`` ends any edit mode, regenerates the
  B-Rep (with one ``startEdit`` / ``finishEdit`` round as a fallback) and fails if a body
  still has none; ``regenerate_on_error`` does the same without the check on the error path.
- ``describe_tsbody`` (D4): the outward form of a T-spline body, lengths in mm.
- Undo of control-mesh edits (D8): moves, transforms and crease flags add no timeline item,
  so ``move_element`` / ``transform_body`` / ``set_crease`` register their inverse in a c09
  ``Compensation``.
"""

from __future__ import annotations

import contextlib
import math
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

import adsk.core
import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.form import AMBIGUITY_MM, NEAREST_LIMIT
from fusion_mcp.protocol.units import length_from_internal, length_to_internal, vec3_length_to_user

from .compensate import Compensation
from .dispatcher import to_bridge_error
from .features import resolve_one
from .sheet_metal import resolve_component

FORM = ("FormFeature",)
TSBODY = ("TSplineBody",)
_COLLECTIONS = {"vertex": "vertices", "edge": "edges", "face": "faces"}

XYZ = tuple[float, float, float]


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def api_error(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.FUSION_API_ERROR, message, details)


def get(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def items(collection: Any) -> list[Any]:
    if collection is None:
        return []
    if hasattr(collection, "count") and hasattr(collection, "item"):
        return [collection.item(i) for i in range(collection.count)]
    return list(collection)


def _xyz(point: Any) -> XYZ:
    return (float(point.x), float(point.y), float(point.z))


def to_mm(xyz_cm: Sequence[float]) -> list[float]:
    return vec3_length_to_user(list(xyz_cm))


# --- selection (D2) --------------------------------------------------------------------------


def element_position(element: Any, kind: str) -> XYZ:
    """Point (cm) a selector of ``kind`` is matched to."""
    if kind == "vertex":
        return _xyz(element.geometry)
    if kind == "edge":
        a, b = _xyz(element.startVertex.geometry), _xyz(element.endVertex.geometry)
        return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2)
    if kind == "face":
        points = [_xyz(v.geometry) for v in items(element.vertices)]
        if not points:
            raise api_error("a T-spline face without vertices")
        n = len(points)
        return (
            sum(p[0] for p in points) / n,
            sum(p[1] for p in points) / n,
            sum(p[2] for p in points) / n,
        )
    raise ValueError(f"unknown element kind {kind!r}")


def elements(body: Any, kind: str) -> list[Any]:
    """Vertices, edges or faces of a T-spline body's control mesh."""
    return items(get(body, _COLLECTIONS[kind]))


def select(
    body: Any, kind: str, points: Sequence[XYZ], tolerance: float, param: str = "near"
) -> list[Any]:
    """Nearest ``kind`` element of ``body`` for each point (cm), in order, without duplicates.

    No element within ``tolerance`` (cm) or two nearest elements at distances closer than
    ``AMBIGUITY_MM`` -> ``INVALID_PARAMS`` (``details``: ``param``, ``index``, ``reason``
    ``no_match`` | ``ambiguous``, ``point`` / ``tolerance_mm`` and up to ``NEAREST_LIMIT``
    ``nearest`` candidates with ``position`` and ``distance_mm``)."""
    candidates = [(element_position(e, kind), e) for e in elements(body, kind)]
    if not candidates:
        raise _invalid(f"the T-spline body has no {kind}", param=param, reason="empty_body")
    tie = float(length_to_internal(AMBIGUITY_MM, "mm"))
    chosen: list[Any] = []
    for index, point in enumerate(points):
        ranked = sorted(
            ((math.dist(position, point), position, e) for position, e in candidates),
            key=lambda c: c[0],
        )
        distance, _, element = ranked[0]
        reason = None
        if distance > tolerance:
            reason = "no_match"
        elif len(ranked) > 1 and ranked[1][0] - distance < tie:
            reason = "ambiguous"
        if reason is not None:
            what = (
                f"no {kind} within {length_from_internal(tolerance):g} mm"
                if reason == "no_match"
                else f"several {kind}s are equally near"
            )
            raise _invalid(
                f"parameter '{param}[{index}]': {what} of {to_mm(point)} mm; take positions "
                "from form_inspect",
                param=param,
                index=index,
                reason=reason,
                kind=kind,
                point=to_mm(point),
                tolerance_mm=round(length_from_internal(tolerance), 6),
                nearest=[
                    {"position": to_mm(p), "distance_mm": round(length_from_internal(d), 6)}
                    for d, p, _ in ranked[:NEAREST_LIMIT]
                ],
            )
        if not any(element is c for c in chosen):
            chosen.append(element)
    return chosen


# --- form lifecycle (D3) ---------------------------------------------------------------------


def is_parametric(ctx: Any) -> bool:
    try:
        return bool(ctx.design.designType == adsk.fusion.DesignTypes.ParametricDesignType)
    except Exception:
        return True


def resolve_tsbody(ctx: Any, ref: Any, param: str = "body") -> Any:
    return resolve_one(ctx, ref, TSBODY, param)


def get_or_create_form(ctx: Any, form_ref: Any, component_ref: Any) -> tuple[Any, bool]:
    """``(form feature, created)``: the given ``FormFeature`` or a new empty one in the
    component (default: root). Both refs -> ``INVALID_PARAMS``."""
    if form_ref is not None:
        if component_ref is not None:
            raise _invalid(
                "pass either 'form' (an existing form feature) or 'component', not both",
                param="component",
            )
        return resolve_one(ctx, form_ref, FORM, "form"), False
    component = resolve_component(ctx, component_ref)
    form = component.features.formFeatures.add()
    if form is None:
        raise api_error("Fusion did not create a form feature")
    return form, True


def _regenerate(form: Any) -> None:
    form.finishEdit()
    form.updateBRep()


def finish(form: Any, bodies: Iterable[Any] = ()) -> None:
    """End edit mode and regenerate the B-Rep (D3); a body of ``bodies`` still without a
    B-Rep after one ``startEdit`` / ``finishEdit`` round -> ``FUSION_API_ERROR``."""
    _regenerate(form)
    missing = [b for b in bodies if get(b, "bRepBody") is None]
    if missing:
        form.startEdit()
        form.finishEdit()
        missing = [b for b in missing if get(b, "bRepBody") is None]
    if missing:
        raise api_error(
            "B-Rep was not generated for the T-spline body",
            reason="no_brep",
            bodies=[get(b, "name") for b in missing],
        )


@contextlib.contextmanager
def regenerate_on_error(form: Any) -> Iterator[None]:
    """On an error in the block: leave edit mode and regenerate the B-Rep (after any
    compensation inside the block ran); a failure of that goes to ``details.finish_error``."""
    try:
        yield
    except Exception as exc:
        error = to_bridge_error(exc)
        try:
            _regenerate(form)
        except Exception as fail:
            error.details["finish_error"] = str(fail) or type(fail).__name__
        if error is exc:
            raise
        raise error from exc


# --- description (D4) ------------------------------------------------------------------------


def _ref(ctx: Any, entity: Any) -> str | None:
    if entity is None:
        return None
    try:
        ref: str = ctx.refs.to_ref(entity)
    except Exception:
        return None
    return ref


def describe_tsbody(ctx: Any, body: Any) -> dict[str, Any]:
    """``{"ref", "type", "name", "form", "vertex_count", "edge_count", "face_count", "bbox":
    {"min", "max"} mm | null, "brep_body": ref | null, "is_visible"}``."""
    box = get(body, "boundingBox")
    bbox = None
    if box is not None:
        bbox = {"min": to_mm(_xyz(box.minPoint)), "max": to_mm(_xyz(box.maxPoint))}
    return {
        "ref": ctx.refs.to_ref(body),
        "type": "TSplineBody",
        "name": get(body, "name"),
        "form": _ref(ctx, get(body, "parentFormFeature")),
        "vertex_count": len(elements(body, "vertex")),
        "edge_count": len(elements(body, "edge")),
        "face_count": len(elements(body, "face")),
        "bbox": bbox,
        "brep_body": _ref(ctx, get(body, "bRepBody")),
        "is_visible": get(body, "isLightBulbOn", True) is not False,
    }


# --- undo of control-mesh edits (D8) ---------------------------------------------------------


def move_element(comp: Compensation, element: Any, vector: Any, label: str) -> None:
    """``element.move(vector)``, then register the move back. Registered after the call: a
    move Fusion refused changed nothing, and moving it back would shift it."""
    element.move(vector)
    back = adsk.core.Vector3D.create(-vector.x, -vector.y, -vector.z)
    comp.add(lambda: element.move(back), label)


def transform_body(comp: Compensation, body: Any, matrix: Any) -> None:
    """``body.transform(matrix)``, then register the inverse transform."""
    inverse = matrix.copy()
    if not inverse.invert():
        raise _invalid("the transform cannot be inverted", param="vector")
    body.transform(matrix)
    comp.add(lambda: body.transform(inverse), f"transform {get(body, 'name')!r} back")


def set_crease(comp: Compensation, edge: Any, creased: bool) -> bool:
    """Set ``edge.isCreased``; ``False`` when it already had that value (nothing changed)."""
    old = bool(edge.isCreased)
    if old == creased:
        return False
    comp.add(lambda: setattr(edge, "isCreased", old), "restore crease")
    edge.isCreased = creased
    return True

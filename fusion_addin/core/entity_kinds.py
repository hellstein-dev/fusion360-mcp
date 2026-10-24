"""Entity kinds for input validation (c06 D2): pure predicates on ``objectType`` and the
surface/curve type of the entity's geometry. Reused by later feature handlers (c07+).

``KINDS`` maps a kind name (as in ``fusion_mcp.protocol.construction.METHODS``) to its
predicate; ``require_kind(entity, kind, param)`` raises ``INVALID_PARAMS`` with
``details = {param, expected, got}`` when the entity is not of that kind.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import adsk.core

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

from .refs import type_name

SKETCH_PATH_TYPES = frozenset(
    {
        "SketchLine",
        "SketchArc",
        "SketchCircle",
        "SketchEllipse",
        "SketchFittedSpline",
        "SketchFixedSpline",
    }
)


def _geometry(entity: Any) -> Any:
    try:
        return entity.geometry
    except Exception:
        return None


def _surface(face: Any) -> str | None:
    """``"plane" | "cylinder" | "cone" | "sphere" | "torus"`` or ``None`` for others."""
    st = adsk.core.SurfaceTypes
    names: dict[Any, str] = {
        st.PlaneSurfaceType: "plane",
        st.CylinderSurfaceType: "cylinder",
        st.ConeSurfaceType: "cone",
        st.SphereSurfaceType: "sphere",
        st.TorusSurfaceType: "torus",
    }
    return names.get(getattr(_geometry(face), "surfaceType", None))


def _curve(edge: Any) -> str | None:
    """``"line" | "arc" | "circle"`` or ``None`` for others."""
    ct = adsk.core.Curve3DTypes
    names: dict[Any, str] = {
        ct.Line3DCurveType: "line",
        ct.Arc3DCurveType: "arc",
        ct.Circle3DCurveType: "circle",
    }
    return names.get(getattr(_geometry(edge), "curveType", None))


def _face_of(entity: Any, *surfaces: str) -> bool:
    return type_name(entity) == "BRepFace" and _surface(entity) in surfaces


def _edge_of(entity: Any, *curves: str) -> bool:
    return type_name(entity) == "BRepEdge" and _curve(entity) in curves


def is_planar(entity: Any) -> bool:
    return type_name(entity) == "ConstructionPlane" or _face_of(entity, "plane")


def is_linear(entity: Any) -> bool:
    return type_name(entity) in ("ConstructionAxis", "SketchLine") or _edge_of(entity, "line")


def is_point(entity: Any) -> bool:
    return type_name(entity) in ("BRepVertex", "ConstructionPoint", "SketchPoint")


def is_path(entity: Any) -> bool:
    return type_name(entity) == "BRepEdge" or type_name(entity) in SKETCH_PATH_TYPES


def is_circular_face(entity: Any) -> bool:
    return _face_of(entity, "cylinder", "cone", "torus")


def is_tangent_face(entity: Any) -> bool:
    return _face_of(entity, "cylinder", "cone")


def is_center_source(entity: Any) -> bool:
    return (
        type_name(entity) in ("SketchCircle", "SketchArc")
        or _edge_of(entity, "circle", "arc")
        or _face_of(entity, "sphere", "torus")
    )


def is_edge(entity: Any) -> bool:
    return type_name(entity) in ("BRepEdge", "ConstructionAxis", "SketchLine")


def is_face(entity: Any) -> bool:
    return type_name(entity) == "BRepFace"


KINDS: dict[str, Callable[[Any], bool]] = {
    "planar": is_planar,
    "linear": is_linear,
    "point": is_point,
    "path": is_path,
    "circular_face": is_circular_face,
    "tangent_face": is_tangent_face,
    "center_source": is_center_source,
    "edge": is_edge,
    "face": is_face,
}

DESCRIPTIONS = {
    "planar": "a planar face or a construction plane",
    "linear": "a straight edge, a sketch line or a construction axis",
    "point": "a vertex, a sketch point or a construction point",
    "path": "an edge or a sketch curve",
    "circular_face": "a cylindrical, conical or toroidal face",
    "tangent_face": "a cylindrical or conical face",
    "center_source": "a circular edge or arc, a sketch circle or arc, or a sphere or torus face",
    "edge": "an edge, a sketch line or a construction axis",
    "face": "a face",
}


def describe_kind(entity: Any) -> str:
    """What the entity is, for error messages: ``"BRepFace (cylinder)"``, ``"SketchLine"``."""
    kind = type_name(entity)
    if kind == "BRepFace":
        surface = _surface(entity) or "other surface"
        return f"{kind} ({surface})"
    if kind == "BRepEdge":
        curve = _curve(entity) or "other curve"
        return f"{kind} ({curve})"
    return kind


def is_kind(entity: Any, kind: str) -> bool:
    try:
        predicate = KINDS[kind]
    except KeyError:
        raise ValueError(f"unknown entity kind {kind!r}; expected one of {sorted(KINDS)}") from None
    return predicate(entity)


def require_kind(entity: Any, kind: str, param: str) -> Any:
    """Return ``entity`` if it is of ``kind``; else ``INVALID_PARAMS`` naming the parameter,
    the expected kind and what the entity is."""
    if is_kind(entity, kind):
        return entity
    got = describe_kind(entity)
    raise BridgeError(
        ErrorCode.INVALID_PARAMS,
        f"parameter '{param}' must be {DESCRIPTIONS[kind]}, got {got}",
        {"param": param, "expected": kind, "got": got},
    )

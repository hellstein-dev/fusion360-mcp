"""The one serialisation of Fusion entities (c03 D4): ``describe(entity, units, detail)``.

Dispatches on the short ``objectType`` (``adsk::fusion::BRepFace`` -> ``BRepFace``); an
unknown type gets ``{"ref", "type", "name"}``. Every field name carries its unit
(``_mm``, ``_mm2``, ``_mm3``, ``_deg``); directions are unit vectors. Proxy entities
(through an occurrence) report assembly coordinates, as Fusion does.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import adsk.core

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

from . import geometry, health, refs
from .geometry import DEFAULT_UNITS, bbox, point, vector

DETAILS = ("brief", "full")

Describer = Callable[[Any, Any, bool], dict[str, Any]]


def _surface_names() -> dict[Any, str]:
    st = adsk.core.SurfaceTypes
    return {
        st.PlaneSurfaceType: "plane",
        st.CylinderSurfaceType: "cylinder",
        st.ConeSurfaceType: "cone",
        st.SphereSurfaceType: "sphere",
        st.TorusSurfaceType: "torus",
        st.EllipticalCylinderSurfaceType: "elliptical_cylinder",
        st.EllipticalConeSurfaceType: "elliptical_cone",
        st.NurbsSurfaceType: "nurbs",
    }


def _curve_names() -> dict[Any, str]:
    ct = adsk.core.Curve3DTypes
    return {
        ct.Line3DCurveType: "line",
        ct.Arc3DCurveType: "arc",
        ct.Circle3DCurveType: "circle",
        ct.Ellipse3DCurveType: "ellipse",
        ct.EllipticalArc3DCurveType: "elliptical_arc",
        ct.NurbsCurve3DCurveType: "nurbs",
    }


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Optional attribute: Fusion raises for properties that do not apply to an object."""
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _ref(entity: Any) -> str | None:
    return None if entity is None else refs.to_ref(entity)


def surface_type(face: Any) -> str:
    return _surface_names().get(face.geometry.surfaceType, "other")


def curve_type(edge: Any) -> str:
    return _curve_names().get(edge.geometry.curveType, "other")


def face_normal(face: Any) -> list[float]:
    """Outward normal of a planar face: ``evaluator.getNormalAtPoint`` respects the face
    orientation; the plane's own normal is the fallback."""
    try:
        ok, normal = face.evaluator.getNormalAtPoint(face.pointOnFace)
    except Exception:
        ok, normal = False, None
    if ok and normal is not None:
        return vector(normal)
    return vector(face.geometry.normal)


def surface_geometry(face: Any, kind: str, units: Any) -> dict[str, Any]:
    geom = face.geometry
    if kind == "plane":
        return {"origin_mm": point(geom.origin, units), "normal": face_normal(face)}
    if kind == "cylinder":
        return {
            "origin_mm": point(geom.origin, units),
            "axis": vector(geom.axis),
            "radius_mm": geometry.length(geom.radius, units),
        }
    if kind == "cone":
        return {
            "origin_mm": point(geom.origin, units),
            "axis": vector(geom.axis),
            "radius_mm": geometry.length(geom.radius, units),
            "half_angle_deg": round(units.angle_out(geom.halfAngle), 9),
        }
    if kind == "sphere":
        return {
            "origin_mm": point(geom.origin, units),
            "radius_mm": geometry.length(geom.radius, units),
        }
    if kind == "torus":
        return {
            "origin_mm": point(geom.origin, units),
            "axis": vector(geom.axis),
            "major_radius_mm": geometry.length(geom.majorRadius, units),
            "minor_radius_mm": geometry.length(geom.minorRadius, units),
        }
    return {}


def curve_geometry(edge: Any, kind: str, units: Any) -> dict[str, Any]:
    geom = edge.geometry
    if kind == "line":
        return {"direction": geometry.direction(geom.startPoint, geom.endPoint)}
    if kind in ("arc", "circle"):
        return {
            "center_mm": point(geom.center, units),
            "normal": vector(geom.normal),
            "radius_mm": geometry.length(geom.radius, units),
        }
    if kind in ("ellipse", "elliptical_arc"):
        return {
            "center_mm": point(geom.center, units),
            "normal": vector(geom.normal),
            "major_radius_mm": geometry.length(geom.majorRadius, units),
            "minor_radius_mm": geometry.length(geom.minorRadius, units),
        }
    return {}


def _area(units: Any, cm2: float) -> float:
    return round(units.area_out(cm2), 6) + 0.0


def _volume(units: Any, cm3: float) -> float:
    return round(units.volume_out(cm3), 6) + 0.0


# --- describers --------------------------------------------------------------------------


def _face(face: Any, units: Any, full: bool) -> dict[str, Any]:
    kind = surface_type(face)
    data: dict[str, Any] = {
        "surface_type": kind,
        "area_mm2": _area(units, face.area),
        "centroid_mm": point(face.centroid, units),
        "normal": face_normal(face) if kind == "plane" else None,
        "body": _ref(_get(face, "body")),
    }
    if full:
        data["bbox"] = bbox(face.boundingBox, units)
        data["point_on_face_mm"] = point(face.pointOnFace, units)
        data["edge_count"] = face.edges.count
        data["geometry"] = surface_geometry(face, kind, units)
    return data


def _same_vertex(a: Any, b: Any) -> bool:
    if a is b:
        return True
    token_a, token_b = _get(a, "entityToken"), _get(b, "entityToken")
    return bool(token_a) and token_a == token_b


def is_closed(edge: Any) -> bool:
    start, end = _get(edge, "startVertex"), _get(edge, "endVertex")
    return start is None or end is None or _same_vertex(start, end)


def _edge(edge: Any, units: Any, full: bool) -> dict[str, Any]:
    kind = curve_type(edge)
    data: dict[str, Any] = {
        "curve_type": kind,
        "length_mm": geometry.length(edge.length, units),
        "mid_mm": point(edge.pointOnEdge, units),
        "body": _ref(_get(edge, "body")),
    }
    if full:
        closed = is_closed(edge)
        data["start_mm"] = None if closed else point(edge.startVertex.geometry, units)
        data["end_mm"] = None if closed else point(edge.endVertex.geometry, units)
        data["bbox"] = bbox(edge.boundingBox, units)
        data["faces"] = [refs.to_ref(f) for f in edge.faces]
        data["geometry"] = curve_geometry(edge, kind, units)
    return data


def _vertex(vertex: Any, units: Any, full: bool) -> dict[str, Any]:
    data: dict[str, Any] = {"point_mm": point(vertex.geometry, units)}
    if full:
        data["edge_count"] = vertex.edges.count
        data["body"] = _ref(_get(vertex, "body"))
    return data


def _body(body: Any, units: Any, full: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "is_solid": bool(body.isSolid),
        "is_visible": bool(body.isVisible),
        "volume_mm3": _volume(units, body.volume),
        "area_mm2": _area(units, body.area),
        "component": _ref(_get(body, "parentComponent")),
    }
    if full:
        material = _get(body, "material")
        data["face_count"] = body.faces.count
        data["edge_count"] = body.edges.count
        data["vertex_count"] = body.vertices.count
        data["bbox"] = bbox(body.boundingBox, units)
        data["material"] = material.name if material is not None else None
    return data


def _component(component: Any, units: Any, full: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "body_count": component.bRepBodies.count,
        "sketch_count": component.sketches.count,
        "occurrence_count": component.occurrences.count,
    }
    if full:
        data["bbox"] = bbox(component.boundingBox, units)
    return data


def transform(matrix: Any, units: Any) -> dict[str, Any]:
    """``Matrix3D`` -> translation in mm and the 3x3 rotation rows."""
    m = list(matrix.asArray())
    return {
        "translation_mm": point(matrix.translation, units),
        "rotation": [[round(v, 9) + 0.0 for v in m[row * 4 : row * 4 + 3]] for row in range(3)],
    }


def _occurrence(occurrence: Any, units: Any, full: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "component": _ref(occurrence.component),
        "full_path_name": occurrence.fullPathName,
        "is_grounded": bool(occurrence.isGrounded),
        "is_visible": bool(occurrence.isVisible),
    }
    if full:
        data["transform"] = transform(occurrence.transform2, units)
    return data


def _sketch(sketch: Any, units: Any, full: bool) -> dict[str, Any]:
    return {
        "profile_count": sketch.profiles.count,
        "is_visible": bool(sketch.isVisible),
        "plane": _ref(_get(sketch, "referencePlane")),
    }


def _construction_plane(plane: Any, units: Any, full: bool) -> dict[str, Any]:
    geom = plane.geometry
    return {"origin_mm": point(geom.origin, units), "normal": vector(geom.normal)}


def _construction_axis(axis: Any, units: Any, full: bool) -> dict[str, Any]:
    geom = axis.geometry
    return {"origin_mm": point(geom.origin, units), "direction": vector(geom.direction)}


def _construction_point(cpoint: Any, units: Any, full: bool) -> dict[str, Any]:
    return {"point_mm": point(cpoint.geometry, units)}


def _feature(feature: Any, units: Any, full: bool) -> dict[str, Any]:
    timeline_object = _get(feature, "timelineObject")
    return {
        "health": health.health_of(feature),
        "error_message": health.entity_health(feature)["message"],
        "timeline_index": _get(timeline_object, "index") if timeline_object else None,
        "is_suppressed": bool(_get(feature, "isSuppressed", False)),
    }


_DESCRIBERS: dict[str, Describer] = {
    "BRepFace": _face,
    "BRepEdge": _edge,
    "BRepVertex": _vertex,
    "BRepBody": _body,
    "Component": _component,
    "Occurrence": _occurrence,
    "Sketch": _sketch,
    "ConstructionPlane": _construction_plane,
    "ConstructionAxis": _construction_axis,
    "ConstructionPoint": _construction_point,
}


def check_detail(detail: Any) -> str:
    if detail not in DETAILS:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"parameter 'detail' must be one of {', '.join(DETAILS)}",
            {"param": "detail", "value": detail},
        )
    return str(detail)


def describer_for(kind: str) -> Describer | None:
    found = _DESCRIBERS.get(kind)
    if found is None and kind.endswith("Feature"):
        return _feature
    return found


def describe(entity: Any, units: Any = None, detail: str = "brief") -> dict[str, Any]:
    """``{"ref", "type", "name"?}`` plus the type's fields; ``detail="full"`` adds more."""
    check_detail(detail)
    units = units or DEFAULT_UNITS
    kind = refs.type_name(entity)
    data: dict[str, Any] = {"ref": refs.to_ref(entity), "type": kind}
    name = _get(entity, "name")
    if isinstance(name, str):
        data["name"] = name
    describer = describer_for(kind)
    if describer is not None:
        data.update(describer(entity, units, detail == "full"))
    return data

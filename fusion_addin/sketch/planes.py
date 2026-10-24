"""Sketch plane resolution and sketch creation (c05 D3).

``resolve_sketch_plane`` accepts, in this order:

1. ``origin:XY|XZ|YZ`` -> the origin construction plane of the target component (c03 D15,
   ``ctx.refs.resolve(plane, component=...)``); ``origin:X|Y|Z|O`` is ``INVALID_PARAMS``
   ``reason = "not_a_plane"``;
2. any other ref -> ``ctx.refs.resolve``: a ``ConstructionPlane`` as is; a ``BRepFace``
   only if planar (``face.geometry.objectType == "adsk::core::Plane"``), else
   ``INVALID_PARAMS`` ``reason = "face_not_planar"`` with ``details.surface_type``; anything
   else is ``reason = "not_a_plane"``.

The target component is the ``component`` ref (an occurrence stands for its component) or
``ctx.root``. ``create_sketch`` adds the sketch: a planar face with ``project_edges`` uses
``sketches.add`` (Fusion projects the face edges), without it ``addWithoutEdges`` (a clean
sketch, the default); a construction plane always uses ``add``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.refs import is_origin_ref, parse_origin_ref

from ..core.refs import type_name

ORIGIN_PLANES = ("XY", "XZ", "YZ")
PLANE_SURFACE_TYPE = "adsk::core::Plane"

KIND_ORIGIN = "origin_plane"
KIND_CONSTRUCTION = "construction_plane"
KIND_FACE = "face"


@dataclass(frozen=True)
class SketchPlane:
    """A resolved sketch plane: the planar ``entity``, the target ``component`` and the
    ``kind`` (``origin_plane`` | ``construction_plane`` | ``face``)."""

    entity: Any
    component: Any
    kind: str


def _ref_string(value: Any, param: str) -> str:
    ref = value.get("ref") if isinstance(value, dict) else value
    if not isinstance(ref, str) or not ref:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"parameter '{param}' must be a non-empty ref string",
            {"param": param, "value": ref if isinstance(ref, str | None) else repr(ref)},
        )
    return ref


def _not_a_plane(ref: str, kind: str) -> BridgeError:
    return BridgeError(
        ErrorCode.INVALID_PARAMS,
        f"plane {ref!r} is a {kind}, not a plane: use origin:XY|XZ|YZ, a construction plane or"
        " a planar face",
        {"param": "plane", "ref": ref, "reason": "not_a_plane", "type": kind},
    )


def resolve_component(ctx: Any, component: Any = None) -> Any:
    """``None`` -> ``ctx.root``; a ``Component`` entity as is; a ref (string or
    ``{"ref"}``) -> resolved; an ``Occurrence`` -> its ``component``."""
    if component is None:
        return ctx.root
    if isinstance(component, str | dict):
        ref = _ref_string(component, "component")
        component = ctx.refs.resolve(ref)
    else:
        ref = None
    kind = type_name(component)
    if kind == "Occurrence":
        return component.component
    if kind != "Component":
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"component {ref!r} is a {kind}, not a component or occurrence",
            {"param": "component", "ref": ref, "reason": "not_a_component", "type": kind},
        )
    return component


def resolve_sketch_plane(ctx: Any, plane: Any, component: Any = None) -> SketchPlane:
    """Resolve the ``plane`` param of ``sketch.create`` (D3) in ``component`` (default root)."""
    ref = _ref_string(plane, "plane")
    target = resolve_component(ctx, component)
    if is_origin_ref(ref):
        name = parse_origin_ref(ref)
        if name not in ORIGIN_PLANES:
            raise _not_a_plane(ref, "ConstructionAxis" if name != "O" else "ConstructionPoint")
        return SketchPlane(ctx.refs.resolve(ref, component=target), target, KIND_ORIGIN)
    entity = ctx.refs.resolve(ref)
    kind = type_name(entity)
    if kind == "ConstructionPlane":
        return SketchPlane(entity, target, KIND_CONSTRUCTION)
    if kind == "BRepFace":
        surface = getattr(entity, "geometry", None)
        surface_kind = getattr(surface, "objectType", None)
        if surface_kind != PLANE_SURFACE_TYPE:
            surface_type = type_name(surface) if surface is not None else None
            raise BridgeError(
                ErrorCode.INVALID_PARAMS,
                f"face {ref!r} is not planar ({surface_type}): a sketch needs a planar face",
                {
                    "param": "plane",
                    "ref": ref,
                    "reason": "face_not_planar",
                    "surface_type": surface_type,
                },
            )
        return SketchPlane(entity, target, KIND_FACE)
    raise _not_a_plane(ref, kind)


def create_sketch(plane: SketchPlane, project_edges: bool = False) -> Any:
    """Add a sketch on ``plane`` to its component (D3); ``FUSION_API_ERROR`` if Fusion
    returns nothing."""
    sketches = plane.component.sketches
    if plane.kind == KIND_FACE and not project_edges:
        sketch = sketches.addWithoutEdges(plane.entity)
    else:
        sketch = sketches.add(plane.entity)
    if sketch is None:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "Fusion did not create the sketch")
    return sketch

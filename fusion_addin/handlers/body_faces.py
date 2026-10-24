"""``body.*`` face operations (c08 D10, D12): split face, delete faces, replace face.

They live in the ``body`` group (not ``face``) so they do not clash with the c03 face queries.
Order as in every feature handler (INV-5): validate and resolve everything, check the API this
Fusion version has (``FUSION_API_ERROR`` "not available", design R5), then snapshot,
``createInput`` / ``add`` and the c07 D8 result with the faces' bodies in ``modified_bodies``.
"""

from __future__ import annotations

from typing import Any

from fusion_mcp.protocol.errors import BridgeError
from fusion_mcp.protocol.features import SPLIT_FACE_TYPES, check_choice
from fusion_mcp.protocol.refs import is_origin_ref

from ..core.entity_kinds import describe_kind, is_linear, is_planar
from ..core.features import object_collection, resolve_many, target_component
from ..core.refs import type_name
from ..core.registry import handler
from ..sketch.resolve import CURVE_TYPES
from .feature_modify import _bodies_of
from .feature_sketch import _bool, _call, _finish, _given, _invalid, _not_available, _opt, _snapshot

FACE = ("BRepFace",)
SPLIT_FACE_TOOLS = ("BRepFace", "ConstructionPlane", "BRepBody", *CURVE_TYPES)
REPLACE_TARGETS = ("BRepFace", "ConstructionPlane", "BRepBody")
_SPLIT_SETTERS = {
    "along_vector": "setAlongVectorSplitType",
    "closest_point": "setClosestPointSplitType",
}


def _token(entity: Any) -> Any:
    try:
        token = entity.entityToken
    except Exception:
        token = None
    return token if isinstance(token, str) and token else id(entity)


def _items(collection: Any) -> list[Any]:
    if collection is None:
        return []
    if hasattr(collection, "count") and hasattr(collection, "item"):
        return [collection.item(i) for i in range(collection.count)]
    return list(collection)


def _faces(ctx: Any, params: dict[str, Any]) -> list[Any]:
    return resolve_many(ctx, ctx.require(params, "faces", list), FACE, "faces")


def _entity(ctx: Any, ref: Any, component: Any, allowed: tuple[str, ...], param: str) -> Any:
    """Entity of one of ``allowed`` (short type names); ``origin:*`` is the origin element of
    ``component``."""
    raw = ref.get("ref") if isinstance(ref, dict) else ref
    owner = component if is_origin_ref(raw) else None
    try:
        entity = ctx.refs.resolve(ref, owner)
    except BridgeError as exc:
        exc.details.setdefault("param", param)
        raise
    if type_name(entity) not in allowed:
        actual = describe_kind(entity)
        raise _invalid(
            f"parameter '{param}' must be {' | '.join(allowed)}, got {actual}",
            param=param,
            ref=raw,
            expected=list(allowed),
            actual=actual,
        )
    return entity


def _ref_object(ctx: Any, entity: Any) -> dict[str, Any]:
    return {"ref": ctx.refs.to_ref(entity), "type": type_name(entity)}


# --- split face (D10) ------------------------------------------------------------------------


@handler("body.split_face", mutating=True)
def body_split_face(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Split faces with a face, plane, body or sketch curve (D10); result per c07 D8 plus
    ``faces`` - faces of the split bodies that did not exist before."""
    faces = _faces(ctx, params)
    component = target_component(faces[0])
    tool = _entity(ctx, ctx.require(params, "tool"), component, SPLIT_FACE_TOOLS, "tool")
    if any(_token(tool) == _token(face) for face in faces):
        raise _invalid("the split 'tool' cannot be one of the 'faces'", param="tool")
    extend = _bool(ctx, params, "extend_tool", True)
    split_type = check_choice(
        "split_type", _opt(params, "split_type", "intersection"), SPLIT_FACE_TYPES
    )
    direction = None
    if split_type == "along_vector":
        if not _given(params, "direction"):
            raise _invalid(
                "split_type 'along_vector' needs 'direction' (a linear entity or a plane)",
                param="direction",
            )
        direction = _entity(
            ctx,
            params["direction"],
            component,
            ("BRepEdge", "SketchLine", "ConstructionAxis", "BRepFace", "ConstructionPlane"),
            "direction",
        )
        if not (is_linear(direction) or is_planar(direction)):
            raise _invalid(
                "parameter 'direction' must be a straight edge, a sketch line, an axis or a "
                f"plane, got {describe_kind(direction)}",
                param="direction",
            )
    elif _given(params, "direction"):
        raise _invalid(
            f"parameter 'direction' applies only to split_type 'along_vector', not '{split_type}'",
            param="direction",
        )
    bodies = _bodies_of(faces)
    known = {_token(face) for body in bodies for face in _items(getattr(body, "faces", None))}

    before = _snapshot(component, bodies)
    splits = component.features.splitFaceFeatures
    inp = splits.createInput(object_collection(faces), tool, extend)
    setter = _SPLIT_SETTERS.get(split_type)
    if setter is not None:
        if not hasattr(inp, setter):
            raise _not_available("SplitFaceFeature", f"Split type '{split_type}' is")
        args = (direction,) if direction is not None else ()
        _call("SplitFaceFeature", inp, setter, *args)
    feature = splits.add(inp)
    result = _finish(ctx, feature, before, None, bodies)
    result["faces"] = [
        _ref_object(ctx, face)
        for body in bodies
        for face in _items(getattr(body, "faces", None))
        if _token(face) not in known
    ]
    return result


# --- delete / replace faces (D12) ------------------------------------------------------------


@handler("body.delete_faces", mutating=True)
def body_delete_faces(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Delete faces and let Fusion heal the body (D12)."""
    faces = _faces(ctx, params)
    component = target_component(faces[0])
    bodies = _bodies_of(faces)
    features = component.features
    if not hasattr(features, "deleteFaceFeatures"):
        raise _not_available("DeleteFaceFeature", "Delete face features are")

    before = _snapshot(component, bodies)
    feature = features.deleteFaceFeatures.add(object_collection(faces))
    return _finish(ctx, feature, before, None, bodies)


@handler("body.replace_face", mutating=True)
def body_replace_face(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Replace faces with a target face, plane or surface body (D12)."""
    faces = _faces(ctx, params)
    component = target_component(faces[0])
    target = _entity(ctx, ctx.require(params, "target"), component, REPLACE_TARGETS, "target")
    if type_name(target) == "BRepBody" and getattr(target, "isSolid", False):
        raise _invalid(
            "parameter 'target' must be a surface body, a face or a plane, not a solid body",
            param="target",
            actual="BRepBody (solid)",
        )
    if any(_token(target) == _token(face) for face in faces):
        raise _invalid("the 'target' cannot be one of the 'faces'", param="target")
    tangent_chain = _bool(ctx, params, "tangent_chain", True)
    bodies = _bodies_of(faces)

    before = _snapshot(component, bodies)
    replaces = component.features.replaceFaceFeatures
    inp = replaces.createInput(object_collection(faces), tangent_chain, target)
    feature = replaces.add(inp)
    return _finish(ctx, feature, before, None, bodies)

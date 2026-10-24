"""``feature.*`` modifiers of existing bodies (c07 D17-D19): fillet, rule fillet, chamfer,
shell, draft and offset faces.

The handlers follow the order of ``feature_sketch`` (INV-5): every parameter is read, every ref
resolved and every Fusion value built first (``INVALID_PARAMS`` with no side effects), an API
this Fusion version lacks is reported as ``FUSION_API_ERROR`` "... not available" before the
design changes (design R3), then ``BodySnapshot.take``, ``createInput``, edge sets / setters
(``None`` / ``False`` answers -> ``FUSION_API_ERROR``), ``add`` and ``feature_result`` (D8/D9)
with ``touched`` = the parent bodies of the input edges/faces, so they are reported as
``modified_bodies``.

Fallbacks for older Fusion API versions (design R3, D17, D18):

- fillet without ``FilletFeatureInput.edgeSetInputs``: mode ``constant`` through the retired
  ``FilletFeatureInput.addConstantRadiusEdgeSet``; ``variable`` / ``chord`` -> not available;
- fillet ``chord`` without ``edgeSetInputs.addChordLengthEdgeSet`` -> not available; ``mid``
  radii without ``midPositions`` / ``midRadii`` on the variable edge set -> not available;
- no ``component.features.ruleFilletFeatures`` (or no ``RuleFilletTopologyTypes``) -> not
  available;
- chamfer without ``ChamferFeatures.createInput2``: ``createInput(edges, tangent_chain)`` +
  ``setToEqualDistance`` / ``setToTwoDistances`` (``flip`` swaps the distances) /
  ``setToDistanceAndAngle`` (``flip`` -> ``isFlipped``).

Lengths are in the request ``units`` (default mm), angles in degrees, strings are Fusion
expressions; conversion happens only in ``core.features`` helpers (INV-6).
"""

from __future__ import annotations

from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.features import (
    CHAMFER_MODES,
    FILLET_CORNERS,
    FILLET_MODES,
    RULE_FILLET_RULES,
    check_choice,
)

from ..core.entity_kinds import describe_kind
from ..core.features import (
    angle_value,
    length_value,
    object_collection,
    resolve_many,
    scalar_value,
    target_component,
)
from ..core.refs import type_name
from ..core.registry import handler
from .feature_hole import _number, _planar, _positive
from .feature_sketch import (
    _bool,
    _call,
    _finish,
    _given,
    _invalid,
    _not_available,
    _only_in,
    _opt,
    _rejected,
    _set,
    _snapshot,
)

EDGE = ("BRepEdge",)
FACE = ("BRepFace",)
BODY = ("BRepBody",)
DEFAULT_CHAMFER_ANGLE = 45.0

# Name of the second face/feature set of a ``between_faces`` rule fillet on
# ``RuleFilletFeatureInput`` (design R3: confirmed by the smoke test; absent -> not available).
RULE_FILLET_SECOND_SET = "entitiesTwo"

_FILLET_FIELDS: dict[str, tuple[str, ...]] = {
    "constant": ("radius",),
    "variable": ("start_radius", "end_radius"),
    "chord": ("chord_length",),
}
_CHAMFER_FIELDS: dict[str, tuple[str, ...]] = {
    "equal": ("distance",),
    "two_distances": ("distance", "distance2"),
    "distance_angle": ("distance",),
}
_FILLET_METHODS = {
    "constant": "addConstantRadiusEdgeSet",
    "variable": "addVariableRadiusEdgeSet",
    "chord": "addChordLengthEdgeSet",
}
_CHAMFER_METHODS = {
    "equal": "addEqualDistanceChamferEdgeSet",
    "two_distances": "addTwoDistancesChamferEdgeSet",
    "distance_angle": "addDistanceAndAngleChamferEdgeSet",
}
_SHELL_THICKNESS = {"inside_thickness": "insideThickness", "outside_thickness": "outsideThickness"}
_MID_FIELDS = frozenset({"position", "radius"})
_TOPOLOGY_ATTRS = {
    "all_edges": "AllEdgesRuleFilletTopologyType",
    "between_faces": "BetweenFacesRuleFilletTopologyType",
}


# --- shared ----------------------------------------------------------------------------------


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


def _bodies_of(entities: list[Any]) -> list[Any]:
    """Parent bodies of edges/faces, the bodies themselves, bodies of features; no repeats."""
    out: list[Any] = []
    seen: set[Any] = set()
    for entity in entities:
        kind = type_name(entity)
        if kind == "BRepBody":
            found = [entity]
        elif kind in ("BRepEdge", "BRepFace", "BRepVertex"):
            found = [getattr(entity, "body", None)]
        else:
            found = _items(getattr(entity, "bodies", None))
        for body in found:
            if body is not None and _token(body) not in seen:
                seen.add(_token(body))
                out.append(body)
    return out


def _mode_fields(
    params: dict[str, Any], mode: str, fields: dict[str, tuple[str, ...]], extra: tuple[str, ...]
) -> None:
    """Fields of other modes are refused; the fields of ``mode`` are required and, when plain
    numbers, must be > 0. ``extra`` are optional fields of ``mode`` checked elsewhere."""
    own = set(fields[mode]) | set(extra)
    for names in fields.values():
        for name in names:
            if name not in own:
                _only_in(params, name, mode, tuple(m for m, f in fields.items() if name in f))
    for name in fields[mode]:
        if not _given(params, name):
            raise _invalid(f"mode '{mode}' needs '{name}'", param=name, mode=mode)
        _positive(params[name], name)


def _length(ctx: Any, params: dict[str, Any], name: str) -> Any:
    return length_value(ctx, params[name], param=name)


def _edges(ctx: Any, params: dict[str, Any]) -> list[Any]:
    return resolve_many(ctx, ctx.require(params, "edges", list), EDGE, "edges")


def _faces(ctx: Any, params: dict[str, Any], name: str = "faces") -> list[Any]:
    return resolve_many(ctx, ctx.require(params, name, list), FACE, name)


def _edge_set(feature_type: str, owner: Any, method: str, *args: Any) -> Any:
    """``owner.<method>(*args)`` that must hand back an edge set."""
    edge_set = getattr(owner, method)(*args)
    if edge_set is None or edge_set is False:
        raise _rejected(feature_type, method)
    return edge_set


# --- fillet (D17) ----------------------------------------------------------------------------


def _mids(ctx: Any, raw: Any) -> tuple[list[Any], list[Any]]:
    """``mid`` of a variable fillet -> (positions, radii) ``ValueInput`` lists."""
    if not isinstance(raw, list):
        raise _invalid(
            'parameter \'mid\' must be a list of {"position", "radius"} objects', param="mid"
        )
    positions: list[Any] = []
    radii: list[Any] = []
    for index, item in enumerate(raw):
        where = f"mid[{index}]"
        if not isinstance(item, dict):
            raise _invalid(
                f"parameter '{where}' must be an object with 'position' and 'radius'",
                param="mid",
                index=index,
            )
        unknown = sorted(set(item) - _MID_FIELDS)
        if unknown:
            raise _invalid(
                f"parameter '{where}' has unknown fields {unknown}", param="mid", index=index
            )
        position: Any = item.get("position")
        if not _number(position) or not 0 <= position <= 1:
            raise _invalid(
                f"parameter '{where}.position' must be a number from 0 (edge start) to 1 "
                "(edge end)",
                param=f"{where}.position",
                index=index,
                value=position if _number(position) else repr(position),
            )
        radius = item.get("radius")
        if radius is None:
            raise _invalid(f"parameter '{where}' needs 'radius'", param=f"{where}.radius")
        _positive(radius, f"{where}.radius")
        positions.append(scalar_value(position, f"{where}.position"))
        radii.append(length_value(ctx, radius, param=f"{where}.radius"))
    return positions, radii


@handler("feature.fillet", mutating=True)
def feature_fillet(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Constant / variable / chord-length fillet of edges (D17); result per D8 with the edges'
    bodies in ``modified_bodies``."""
    edges = _edges(ctx, params)
    mode = check_choice("mode", _opt(params, "mode", "constant"), FILLET_MODES)
    _mode_fields(params, mode, _FILLET_FIELDS, ("mid",))
    _only_in(params, "mid", mode, ("variable",))
    tangent_chain = _bool(ctx, params, "tangent_chain", True)
    corner = check_choice("corner", _opt(params, "corner", "rolling_ball"), FILLET_CORNERS)
    g2 = _bool(ctx, params, "g2", False)
    values = [_length(ctx, params, name) for name in _FILLET_FIELDS[mode]]
    positions, radii = _mids(ctx, params["mid"]) if _given(params, "mid") else ([], [])
    component = target_component(edges[0])
    touched = _bodies_of(edges)

    before = _snapshot(component, None)
    fillets = component.features.filletFeatures
    inp = fillets.createInput()
    collection = object_collection(edges)
    if hasattr(inp, "edgeSetInputs"):
        edge_sets = inp.edgeSetInputs
        if mode == "chord" and not hasattr(edge_sets, "addChordLengthEdgeSet"):
            raise _not_available("FilletFeature", "Chord length fillets are")
        method = _FILLET_METHODS[mode]
        edge_set = _edge_set("FilletFeature", edge_sets, method, collection, *values, tangent_chain)
        for name, items in (("midPositions", positions), ("midRadii", radii)):
            if not items:
                continue
            if not hasattr(edge_set, name):
                raise _not_available("FilletFeature", f"VariableRadiusFilletEdgeSetInput.{name} is")
            setattr(edge_set, name, object_collection(items))
    else:
        # Older Fusion API: only the retired constant-radius call exists.
        if mode != "constant" or not hasattr(inp, "addConstantRadiusEdgeSet"):
            raise _not_available("FilletFeature", f"Fillet mode '{mode}' is")
        _edge_set(
            "FilletFeature", inp, "addConstantRadiusEdgeSet", collection, *values, tangent_chain
        )
    _set("FilletFeature", inp, "isRollingBallCorner", corner == "rolling_ball")
    if g2 or hasattr(inp, "isG2"):
        _set("FilletFeature", inp, "isG2", g2)
    feature = fillets.add(inp)
    return _finish(ctx, feature, before, None, touched)


# --- rule fillet (D17) -----------------------------------------------------------------------


def _faces_or_features(ctx: Any, refs: Any, param: str) -> list[Any]:
    """Refs to faces or timeline features (any ``*Feature``), in order, without repeats."""
    if not isinstance(refs, list) or not refs:
        raise _invalid(
            f"parameter '{param}' must be a non-empty list of face or feature refs",
            param=param,
            expected=["BRepFace", "Feature"],
            reason="empty",
        )
    out: list[Any] = []
    for index, ref in enumerate(refs):
        try:
            entity = ctx.refs.resolve(ref)
        except BridgeError as exc:
            exc.details.setdefault("param", param)
            raise
        kind = type_name(entity)
        if kind != "BRepFace" and not kind.endswith("Feature"):
            actual = describe_kind(entity)
            raise BridgeError(
                ErrorCode.INVALID_PARAMS,
                f"parameter '{param}[{index}]' must be BRepFace | Feature, got {actual}",
                {
                    "param": param,
                    "index": index,
                    "ref": ref,
                    "expected": ["BRepFace", "Feature"],
                    "actual": actual,
                },
            )
        if all(_token(entity) != _token(seen) for seen in out):
            out.append(entity)
    return out


@handler("feature.rule_fillet", mutating=True)
def feature_rule_fillet(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Rule fillet of all edges of faces/features or of the edges between two sets (D17);
    ``FUSION_API_ERROR`` "not available" when this Fusion API has no rule fillets."""
    rule = check_choice("rule", _opt(params, "rule", "all_edges"), RULE_FILLET_RULES)
    entities = _faces_or_features(ctx, ctx.require(params, "faces", list), "faces")
    second: list[Any] = []
    if rule == "between_faces":
        if not _given(params, "faces2"):
            raise _invalid("rule 'between_faces' needs 'faces2'", param="faces2", rule=rule)
        second = _faces_or_features(ctx, params["faces2"], "faces2")
    else:
        _only_in(params, "faces2", rule, ("between_faces",))
    if not _given(params, "radius"):
        raise _invalid("missing required parameter 'radius'", param="radius")
    _positive(params["radius"], "radius")
    radius = _length(ctx, params, "radius")
    component = target_component(entities[0])
    touched = _bodies_of([*entities, *second])
    features = component.features
    topologies = getattr(adsk.fusion, "RuleFilletTopologyTypes", None)
    if not hasattr(features, "ruleFilletFeatures") or topologies is None:
        raise _not_available("RuleFilletFeature", "Rule fillets are")
    topology = getattr(topologies, _TOPOLOGY_ATTRS[rule], None)
    if topology is None:
        raise _not_available("RuleFilletFeature", f"Rule fillet rule '{rule}' is")

    before = _snapshot(component, None)
    rules = features.ruleFilletFeatures
    inp = rules.createInput(object_collection(entities), radius, topology)
    if inp is None:
        raise _rejected("RuleFilletFeature", "createInput")
    if second:
        _set("RuleFilletFeature", inp, RULE_FILLET_SECOND_SET, object_collection(second))
    feature = rules.add(inp)
    return _finish(ctx, feature, before, None, touched)


# --- chamfer (D18) ---------------------------------------------------------------------------


def _chamfer_angle(ctx: Any, params: dict[str, Any], mode: str) -> Any:
    _only_in(params, "angle", mode, ("distance_angle",))
    if mode != "distance_angle":
        return None
    raw = _opt(params, "angle", DEFAULT_CHAMFER_ANGLE)
    if _number(raw) and not 0 < raw < 90:
        raise _invalid("parameter 'angle' must be in (0, 90) degrees", param="angle", value=raw)
    return angle_value(ctx, raw, "angle")


@handler("feature.chamfer", mutating=True)
def feature_chamfer(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Equal-distance / two-distances / distance-and-angle chamfer of edges (D18); result per
    D8 with the edges' bodies in ``modified_bodies``."""
    edges = _edges(ctx, params)
    mode = check_choice("mode", _opt(params, "mode", "equal"), CHAMFER_MODES)
    _mode_fields(params, mode, _CHAMFER_FIELDS, ())
    angle = _chamfer_angle(ctx, params, mode)
    flip = _bool(ctx, params, "flip", False)
    if flip and mode == "equal":
        raise _invalid(
            "parameter 'flip' applies only to mode two_distances | distance_angle, not 'equal'",
            param="flip",
            mode=mode,
        )
    tangent_chain = _bool(ctx, params, "tangent_chain", True)
    distance = _length(ctx, params, "distance")
    distance2 = _length(ctx, params, "distance2") if mode == "two_distances" else None
    component = target_component(edges[0])
    touched = _bodies_of(edges)

    before = _snapshot(component, None)
    chamfers = component.features.chamferFeatures
    collection = object_collection(edges)
    if hasattr(chamfers, "createInput2"):
        inp = chamfers.createInput2()
        args: tuple[Any, ...] = (distance,)
        if mode == "two_distances":
            args = (distance, distance2, flip)
        elif mode == "distance_angle":
            args = (distance, angle, flip)
        _edge_set(
            "ChamferFeature",
            inp.chamferEdgeSets,
            _CHAMFER_METHODS[mode],
            collection,
            *args,
            tangent_chain,
        )
    else:
        # Older Fusion API: one edge set per input, set by setTo* methods.
        inp = chamfers.createInput(collection, tangent_chain)
        if mode == "equal":
            _call("ChamferFeature", inp, "setToEqualDistance", distance)
        elif mode == "two_distances":
            first, second = (distance2, distance) if flip else (distance, distance2)
            _call("ChamferFeature", inp, "setToTwoDistances", first, second)
        else:
            _call("ChamferFeature", inp, "setToDistanceAndAngle", distance, angle)
            if flip:
                _set("ChamferFeature", inp, "isFlipped", True)
    feature = chamfers.add(inp)
    return _finish(ctx, feature, before, None, touched)


# --- shell, draft, offset faces (D19) --------------------------------------------------------


def _list_param(params: dict[str, Any], name: str) -> list[Any]:
    value = params.get(name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid(f"parameter '{name}' must be a list of refs", param=name)
    return value


@handler("feature.shell", mutating=True)
def feature_shell(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Shell: remove ``faces`` (open shell) or hollow ``bodies`` (closed), with inside and/or
    outside thickness (D19); result per D8 with the shelled bodies in ``modified_bodies``."""
    face_refs = _list_param(params, "faces")
    body_refs = _list_param(params, "bodies")
    if face_refs and body_refs:
        raise _invalid(
            "pass either 'faces' (faces to remove) or 'bodies' (hollow without opening), not both",
            param="bodies",
            reason="faces_and_bodies",
        )
    if not face_refs and not body_refs:
        raise _invalid(
            "shell needs 'faces' (faces to remove) or 'bodies' (hollow without opening)",
            param="faces",
            reason="no_input",
        )
    if not _given(params, "inside_thickness") and not _given(params, "outside_thickness"):
        raise _invalid(
            "shell needs 'inside_thickness' and/or 'outside_thickness'",
            param="inside_thickness",
            reason="no_thickness",
        )
    thickness: dict[str, Any] = {}
    for name, attr in _SHELL_THICKNESS.items():
        if _given(params, name):
            _positive(params[name], name)
            thickness[attr] = _length(ctx, params, name)
    tangent_chain = _bool(ctx, params, "tangent_chain", True)
    if face_refs:
        entities = resolve_many(ctx, face_refs, FACE, "faces")
    else:
        entities = resolve_many(ctx, body_refs, BODY, "bodies")
    component = target_component(entities[0])
    touched = _bodies_of(entities)

    before = _snapshot(component, None)
    shells = component.features.shellFeatures
    inp = shells.createInput(object_collection(entities), tangent_chain)
    for attr, value in thickness.items():
        setattr(inp, attr, value)
    feature = shells.add(inp)
    return _finish(ctx, feature, before, None, touched)


def _draft_angle(ctx: Any, value: Any, param: str) -> Any:
    if _number(value) and (value == 0 or not -90 < value < 90):
        raise _invalid(
            f"parameter '{param}' must be a non-zero angle in (-90, 90) degrees",
            param=param,
            value=value,
        )
    return angle_value(ctx, value, param)


@handler("feature.draft", mutating=True)
def feature_draft(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Draft faces against a neutral plane that sets the pull direction (D19), one angle
    (optionally symmetric) or two; result per D8 with the faces' bodies in
    ``modified_bodies``."""
    faces = _faces(ctx, params)
    plane = _planar(ctx, ctx.require(params, "plane"), "plane")
    if any(_token(face) == _token(plane) for face in faces):
        raise _invalid(
            "the neutral 'plane' cannot be one of the drafted 'faces'",
            param="plane",
            reason="plane_in_faces",
        )
    if not _given(params, "angle"):
        raise _invalid("missing required parameter 'angle'", param="angle")
    angle = _draft_angle(ctx, params["angle"], "angle")
    symmetric = _bool(ctx, params, "symmetric", False)
    angle2 = None
    if _given(params, "angle2"):
        if symmetric:
            raise _invalid(
                "parameter 'angle2' cannot be combined with symmetric=true",
                param="angle2",
            )
        angle2 = _draft_angle(ctx, params["angle2"], "angle2")
    flip = _bool(ctx, params, "flip", False)
    tangent_chain = _bool(ctx, params, "tangent_chain", True)
    component = target_component(faces[0])
    touched = _bodies_of(faces)

    before = _snapshot(component, None)
    drafts = component.features.draftFeatures
    inp = drafts.createInput(list(faces), plane, tangent_chain)
    if angle2 is not None:
        _call("DraftFeature", inp, "setTwoAngles", angle, angle2)
    else:
        _call("DraftFeature", inp, "setSingleAngle", symmetric, angle)
    inp.isDirectionFlipped = flip
    feature = drafts.add(inp)
    return _finish(ctx, feature, before, None, touched)


def _same_face(a: Any, b: Any) -> bool:
    if a is b:
        return True
    token = getattr(a, "entityToken", None)
    return token is not None and token == getattr(b, "entityToken", None)


def tangent_chain(faces: list[Any]) -> list[Any]:
    """``faces`` plus every face reached through ``BRepFace.tangentiallyConnectedFaces`` (only
    the direct smooth neighbours in the API, so it is walked to the whole chain), in order."""
    out: list[Any] = []
    pending = list(faces)
    while pending:
        face = pending.pop(0)
        if any(_same_face(face, seen) for seen in out):
            continue
        out.append(face)
        pending.extend(getattr(face, "tangentiallyConnectedFaces", None) or ())
    return out


@handler("feature.offset_faces", mutating=True)
def feature_offset_faces(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    """Press-pull faces by a signed distance (positive = outward) (D19); result per D8 with
    the faces' bodies in ``modified_bodies``."""
    faces = _faces(ctx, params)
    if not _given(params, "distance"):
        raise _invalid("missing required parameter 'distance'", param="distance")
    raw = params["distance"]
    if _number(raw) and raw == 0:
        raise _invalid("parameter 'distance' must not be 0", param="distance", value=raw)
    distance = _length(ctx, params, "distance")
    chain = _bool(ctx, params, "chain", True)
    component = target_component(faces[0])
    touched = _bodies_of(faces)

    if chain:
        faces = tangent_chain(faces)
        touched = _bodies_of(faces)

    before = _snapshot(component, None)
    offsets = component.features.offsetFacesFeatures
    # Live API (c15 3.2): createInput(faces: list, distance) - no chain argument; the chain is
    # expanded above.
    inp = offsets.createInput(list(faces), distance)
    feature = offsets.add(inp)
    return _finish(ctx, feature, before, None, touched)

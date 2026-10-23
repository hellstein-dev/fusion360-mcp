"""``inspect.*``: model tree, entity descriptions, bounding boxes, mass properties and
geometric queries (c03 D5-D9). Read-only: every handler is ``mutating=False``."""

from __future__ import annotations

from typing import Any

import adsk.core
import adsk.fusion

from fusion_mcp.protocol import units as unit_conv
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.query import parse_query, select

from ..core import geometry, refs
from ..core.describe import (
    check_detail,
    curve_geometry,
    curve_type,
    describe,
    surface_geometry,
    surface_type,
)
from ..core.dispatcher import to_bridge_error
from ..core.registry import handler

MAX_DESCRIBE_REFS = 200
DEFAULT_MAX_ITEMS = 500
TREE_INCLUDE = ("bodies", "sketches", "construction", "features", "occurrences")
# Timeline entities already listed elsewhere in the tree (D5).
NOT_FEATURES = (
    "Sketch",
    "ConstructionPlane",
    "ConstructionAxis",
    "ConstructionPoint",
    "Occurrence",
)
ACCURACY = {
    "low": "LowCalculationAccuracy",
    "medium": "MediumCalculationAccuracy",
    "high": "HighCalculationAccuracy",
    "very_high": "VeryHighCalculationAccuracy",
}


def _invalid(param: str, message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, {"param": param, **details})


def _ref_error(ref: Any, exc: Exception) -> dict[str, Any]:
    error = to_bridge_error(exc)
    return {"ref": ref, "code": error.code.value, "message": error.message}


def _ref_list(
    ctx: Any, params: dict[str, Any], name: str, required: bool, max_len: int | None = None
) -> list[Any] | None:
    value = params.get(name)
    if value is None:
        if required:
            raise _invalid(name, f"missing required parameter '{name}'")
        return None
    if not isinstance(value, list):
        raise _invalid(name, f"parameter '{name}' must be a list of refs")
    if not value or (max_len is not None and len(value) > max_len):
        limit = f"1..{max_len}" if max_len is not None else "at least 1"
        raise _invalid(name, f"parameter '{name}' must hold {limit} refs, got {len(value)}")
    return value


def _is_parametric(design: Any) -> bool:
    return bool(design.designType == adsk.fusion.DesignTypes.ParametricDesignType)


def _items(collection: Any) -> list[Any]:
    return [collection.item(i) for i in range(collection.count)]


def visible_bodies(root: Any) -> list[Any]:
    """Every visible body of the design: root bodies plus proxies of all occurrences."""
    bodies = _items(root.bRepBodies)
    for occurrence in _items(root.allOccurrences):
        bodies.extend(_items(occurrence.bRepBodies))
    return [b for b in bodies if b.isVisible]


def bodies_of(entity: Any) -> list[Any]:
    """Bodies of a body, component (with its sub-occurrences) or occurrence (recursive)."""
    kind = refs.type_name(entity)
    if kind == "BRepBody":
        return [entity]
    if kind == "Component":
        bodies = _items(entity.bRepBodies)
        for occurrence in _items(entity.allOccurrences):
            bodies.extend(_items(occurrence.bRepBodies))
        return bodies
    if kind == "Occurrence":
        bodies = _items(entity.bRepBodies)
        for child in _items(entity.childOccurrences):
            bodies.extend(bodies_of(child))
        return bodies
    return []


# --- inspect.describe --------------------------------------------------------------------


@handler("inspect.describe")
def inspect_describe(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    ref_list = _ref_list(ctx, params, "refs", True, MAX_DESCRIBE_REFS) or []
    detail = check_detail(params.get("detail") or "full")
    items, errors = [], []
    for ref in ref_list:
        try:
            items.append(describe(ctx.refs.resolve(ref), ctx.units, detail))
        except Exception as exc:
            errors.append(_ref_error(ref, exc))
    return {"items": items, "errors": errors}


# --- inspect.tree ------------------------------------------------------------------------


class _Budget:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.count = 0
        self.truncated = False

    def take(self) -> bool:
        if self.count >= self.limit:
            self.truncated = True
            return False
        self.count += 1
        return True


def _tree_params(ctx: Any, params: dict[str, Any]) -> tuple[Any, int | None, set[str], int]:
    ref = params.get("component")
    component = ctx.root if ref is None else ctx.refs.resolve(ref)
    if refs.type_name(component) != "Component":
        raise _invalid("component", f"{ref!r} is not a component", ref=ref)
    depth = params.get("depth")
    if depth is not None and (isinstance(depth, bool) or not isinstance(depth, int) or depth < 0):
        raise _invalid("depth", "parameter 'depth' must be an integer >= 0 or null")
    include = params.get("include")
    if include is None:
        chosen = set(TREE_INCLUDE)
    elif isinstance(include, list) and all(i in TREE_INCLUDE for i in include):
        chosen = set(include)
    else:
        raise _invalid("include", f"parameter 'include' must be a list from {TREE_INCLUDE}")
    max_items = ctx.require(params, "max_items", int, DEFAULT_MAX_ITEMS)
    if isinstance(max_items, bool) or max_items < 1:
        raise _invalid("max_items", "parameter 'max_items' must be an integer >= 1")
    return component, depth, chosen, max_items


def _describe_all(ctx: Any, entities: list[Any], budget: _Budget) -> list[dict[str, Any]]:
    out = []
    for entity in entities:
        if not budget.take():
            break
        out.append(describe(entity, ctx.units, "brief"))
    return out


def _features_by_component(ctx: Any, design: Any) -> dict[str | None, list[Any]]:
    """Timeline features grouped by the ref of their parent component (``None``: unknown)."""
    grouped: dict[str | None, list[Any]] = {}
    timeline = design.timeline
    for item in _items(timeline):
        if item.isGroup:
            continue
        entity = item.entity
        if entity is None or refs.type_name(entity) in NOT_FEATURES:
            continue
        try:
            owner = entity.parentComponent
        except Exception:
            owner = None
        key = refs.to_ref(owner) if owner is not None else None
        grouped.setdefault(key, []).append(entity)
    return grouped


def _construction(component: Any) -> list[Any]:
    found: list[Any] = []
    for collection in ("constructionPlanes", "constructionAxes", "constructionPoints"):
        found.extend(
            e for e in _items(getattr(component, collection)) if refs.origin_name(e) is None
        )
    return found


def _occurrence_node(
    ctx: Any, occurrence: Any, level: int, depth: int | None, include: set[str], budget: _Budget
) -> dict[str, Any]:
    node = describe(occurrence, ctx.units, "brief")
    if "bodies" in include:
        node["bodies"] = _describe_all(ctx, _items(occurrence.bRepBodies), budget)
    if "occurrences" in include:
        node["occurrences"] = _occurrence_nodes(
            ctx, _items(occurrence.childOccurrences), level + 1, depth, include, budget
        )
    return node


def _occurrence_nodes(
    ctx: Any,
    occurrences: list[Any],
    level: int,
    depth: int | None,
    include: set[str],
    budget: _Budget,
) -> list[dict[str, Any]]:
    if depth is not None and level > depth:
        return []
    nodes = []
    for occurrence in occurrences:
        if not budget.take():
            break
        nodes.append(_occurrence_node(ctx, occurrence, level, depth, include, budget))
    return nodes


@handler("inspect.tree")
def inspect_tree(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    design = ctx.design
    component, depth, include, max_items = _tree_params(ctx, params)
    parametric = _is_parametric(design)
    budget = _Budget(max_items)
    budget.take()  # the component node itself
    node = describe(component, ctx.units, "brief")
    if "bodies" in include:
        node["bodies"] = _describe_all(ctx, _items(component.bRepBodies), budget)
    if "sketches" in include:
        node["sketches"] = _describe_all(ctx, _items(component.sketches), budget)
    if "construction" in include:
        node["construction"] = _describe_all(ctx, _construction(component), budget)
    if "features" in include:
        features: list[Any] = []
        if parametric:
            grouped = _features_by_component(ctx, design)
            features = grouped.get(refs.to_ref(component), [])
            if component is ctx.root or refs.to_ref(component) == refs.to_ref(ctx.root):
                features = features + grouped.get(None, [])
        node["features"] = _describe_all(ctx, features, budget)
    if "occurrences" in include:
        node["occurrences"] = _occurrence_nodes(
            ctx, _items(component.occurrences), 1, depth, include, budget
        )
    return {
        "design_type": "parametric" if parametric else "direct",
        "root": node,
        "item_count": budget.count,
        "truncated": budget.truncated,
    }


# --- inspect.bounding_box ----------------------------------------------------------------


def _tight_box(ctx: Any, entity: Any) -> dict[str, Any] | None:
    """World-axis AABB from ``MeasureManager.getOrientedBoundingBox``; ``None`` if Fusion
    cannot compute one for this entity."""
    try:
        obb = ctx.app.measureManager.getOrientedBoundingBox(
            entity, adsk.core.Vector3D.create(1, 0, 0), adsk.core.Vector3D.create(0, 1, 0)
        )
    except Exception:
        return None
    if obb is None:
        return None
    center = obb.centerPoint
    half = (obb.length / 2, obb.width / 2, obb.height / 2)
    mid = (center.x, center.y, center.z)
    lo = [geometry.length(c - h, ctx.units) for c, h in zip(mid, half, strict=True)]
    hi = [geometry.length(c + h, ctx.units) for c, h in zip(mid, half, strict=True)]
    return geometry.box_dict(lo, hi)


def entity_box(ctx: Any, entity: Any) -> tuple[dict[str, Any], bool]:
    """``(box, tight)``: union of tight body boxes for components/occurrences, the entity's
    own tight box otherwise; ``entity.boundingBox`` with ``tight = False`` as fallback."""
    kind = refs.type_name(entity)
    if kind in ("Component", "Occurrence"):
        boxes = [_tight_box(ctx, b) for b in bodies_of(entity)]
        union = geometry.union_bbox(boxes)
        if union is not None:
            return union, all(b is not None for b in boxes)
    else:
        box = _tight_box(ctx, entity)
        if box is not None:
            return box, True
    return geometry.bbox(entity.boundingBox, ctx.units), False


@handler("inspect.bounding_box")
def inspect_bounding_box(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    ref_list = _ref_list(ctx, params, "refs", False)
    targets: list[tuple[Any, Any]] = []
    errors = []
    if ref_list is None:
        targets = [(refs.to_ref(b), b) for b in visible_bodies(ctx.root)]
    else:
        for ref in ref_list:
            try:
                targets.append((ref, ctx.refs.resolve(ref)))
            except Exception as exc:
                errors.append(_ref_error(ref, exc))
    items = []
    for ref, entity in targets:
        try:
            box, tight = entity_box(ctx, entity)
        except Exception as exc:
            errors.append(_ref_error(ref, exc))
            continue
        items.append({"ref": ref, **box, "tight": tight})
    return {"items": items, "union": geometry.union_bbox(items), "errors": errors}


# --- inspect.physical_properties ---------------------------------------------------------


def _moments(props: Any, units: Any) -> tuple[dict[str, float] | None, list[float] | None]:
    ok, *xyz = props.getXYZMomentsOfInertia()
    inertia = (
        dict(
            zip(
                ("xx", "yy", "zz", "xy", "yz", "xz"),
                map(unit_conv.inertia_to_user, xyz),
                strict=True,
            )
        )
        if ok
        else None
    )
    ok, *principal = props.getPrincipalMomentsOfInertia()
    moments = [unit_conv.inertia_to_user(v) for v in principal] if ok else None
    return inertia, moments


def _material_name(entity: Any) -> str | None:
    try:
        material = entity.material
    except Exception:
        return None
    return material.name if material is not None else None


def physical_item(ctx: Any, entity: Any, accuracy: int, accuracy_name: str) -> dict[str, Any]:
    kind = refs.type_name(entity)
    if kind in ("BRepBody", "Component"):
        props = entity.getPhysicalProperties(accuracy)
        center = props.centerOfMass
        material = _material_name(entity)
    elif kind == "Occurrence":
        component = entity.component
        props = component.getPhysicalProperties(accuracy)
        center = props.centerOfMass.copy()
        center.transformBy(entity.transform2)
        material = _material_name(component)
    else:
        raise _invalid("refs", f"{kind} has no physical properties", type=kind)
    inertia, principal = _moments(props, ctx.units)
    return {
        "ref": refs.to_ref(entity),
        "mass_kg": unit_conv.mass_from_internal(props.mass),
        "volume_mm3": round(ctx.units.volume_out(props.volume), 6),
        "area_mm2": round(ctx.units.area_out(props.area), 6),
        "density_kg_m3": unit_conv.density_to_user(props.density),
        "center_of_mass_mm": geometry.point(center, ctx.units),
        "inertia_kg_mm2": inertia,
        "principal_moments_kg_mm2": principal,
        "material": material,
        "accuracy": accuracy_name,
    }


@handler("inspect.physical_properties")
def inspect_physical_properties(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    ref_list = _ref_list(ctx, params, "refs", False)
    name = params.get("accuracy") or "high"
    if name not in ACCURACY:
        raise _invalid("accuracy", f"parameter 'accuracy' must be one of {', '.join(ACCURACY)}")
    accuracy = getattr(adsk.fusion.CalculationAccuracy, ACCURACY[name])
    items, errors = [], []
    targets = [(None, ctx.root)] if ref_list is None else [(r, None) for r in ref_list]
    for ref, entity in targets:
        try:
            target = entity if entity is not None else ctx.refs.resolve(ref)
            items.append(physical_item(ctx, target, accuracy, name))
        except Exception as exc:
            errors.append(_ref_error(ref, exc))
    return {"items": items, "errors": errors}


# --- inspect.query -----------------------------------------------------------------------

RADIUS_SURFACES = ("cylinder", "cone", "sphere", "torus")


def _children(entity: Any, kind: str) -> list[Any]:
    """Candidates of ``kind`` inside a scope entity (body, component, occurrence, face)."""
    scope_type = refs.type_name(entity)
    if scope_type == "BRepFace":
        if kind == "face":
            return [entity]
        if kind == "edge":
            return _items(entity.edges)
        if kind == "vertex":
            return _items(entity.vertices)
        return [entity.body]
    bodies = bodies_of(entity)
    if not bodies and scope_type != "BRepBody":
        raise _invalid("scope", f"{scope_type} cannot be a query scope", type=scope_type)
    if kind == "body":
        return bodies
    attr = {"face": "faces", "edge": "edges", "vertex": "vertices"}[kind]
    found = []
    for body in bodies:
        found.extend(_items(getattr(body, attr)))
    return found


def query_item(ctx: Any, entity: Any, kind: str) -> dict[str, Any]:
    """Brief description plus the values the query filters on (D9)."""
    item = describe(entity, ctx.units, "brief")
    if kind == "face":
        shape = surface_type(entity)
        if shape in RADIUS_SURFACES:
            geom = surface_geometry(entity, shape, ctx.units)
            # A torus is matched on its tube (minor) radius: the fillet radius.
            item["radius_mm"] = geom.get("radius_mm", geom.get("minor_radius_mm"))
            if "axis" in geom and shape in ("cylinder", "cone"):
                item["axis"] = geom["axis"]
    elif kind == "edge":
        shape = curve_type(entity)
        if shape in ("line", "arc", "circle"):
            geom = curve_geometry(entity, shape, ctx.units)
            if "radius_mm" in geom:
                item["radius_mm"] = geom["radius_mm"]
            if "direction" in geom:
                item["direction"] = geom["direction"]
    elif kind == "body":
        item["bbox"] = geometry.bbox(entity.boundingBox, ctx.units)
    return item


def _dedupe(entities: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    unique = []
    for entity in entities:
        try:
            key = entity.entityToken or id(entity)
        except Exception:
            key = id(entity)
        if key in seen:
            continue
        seen.add(key)
        unique.append(entity)
    return unique


@handler("inspect.query")
def inspect_query(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    spec = parse_query(params)
    if spec.scope is None:
        scopes = visible_bodies(ctx.root)
    else:
        scopes = [ctx.refs.resolve(ref) for ref in spec.scope]
    candidates: list[Any] = []
    for scope in scopes:
        candidates.extend(_children(scope, spec.kind))
    described = [query_item(ctx, e, spec.kind) for e in _dedupe(candidates)]
    items, total = select(spec, described)
    return {"items": items, "total": total, "truncated": total > len(items)}

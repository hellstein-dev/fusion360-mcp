"""``fusion_addin.core.features``: shared helpers of the ``feature.*`` handlers (c07 3.1-3.8,
design D3-D9)."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core import features as fx
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import Registry
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.sketch_refs import format_profile_ref
from fusion_mcp.protocol.wire import Request
from tests.fakes.brep import (
    FakeBody,
    FakeComponent,
    FakeFeature,
    add_face,
    fake_box_body,
    fake_cylinder_face,
)
from tests.fakes.design import FakeApp
from tests.fakes.fusion_features import FakeFeatureInput, fake_feature_design, make_ctx
from tests.fakes.fusion_sketch import auto_profiles, circle_curve, make_sketch, polygon_curves

adsk = fusion_addin.adsk
OPS = adsk.fusion.FeatureOperations
HS = adsk.fusion.FeatureHealthStates
DIRS = adsk.fusion.ExtentDirections

PLATE = [(0, 0), (10, 0), (10, 6), (0, 6)]  # cm: 100 x 60 mm
HOLE = (5, 3)  # cm, r = 1 cm


def _error(fn: Any, *args: Any, **kwargs: Any) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        fn(*args, **kwargs)
    return info.value


def _real(value_input: Any) -> float:
    assert value_input.kind == "real"
    return float(value_input.value)


def _plate_sketch(design: Any) -> tuple[Any, list[Any], Any]:
    """Sketch on XY: plate rectangle with a round hole -> two profiles (plate, disc)."""
    sketch = make_sketch(design, "XY")
    lines = polygon_curves(sketch, PLATE)
    hole = circle_curve(sketch, HOLE, 1.0)
    sketch.profile_builder = auto_profiles
    return sketch, lines, hole


class _Text:
    objectType = "adsk::fusion::SketchText"

    def __init__(self, sketch: Any) -> None:
        self.entityToken = "text-1"
        self.parentSketch = sketch


class _Proxy:
    """Assembly-context proxy: same ``objectType``, own token, ``nativeObject``."""

    def __init__(self, native: Any, token: str = "proxy-1") -> None:
        self.objectType = native.objectType
        self.entityToken = token
        self.nativeObject = native


# --- 3.1 values and units ------------------------------------------------------------------


def test_length_value_mm_to_cm_and_units() -> None:
    ctx = make_ctx()
    assert _real(fx.length_value(ctx, 15)) == pytest.approx(1.5)
    assert _real(fx.length_value(ctx, 1, "in")) == pytest.approx(2.54)
    inch = make_ctx(params={"units": "in"})
    assert _real(fx.length_value(inch, 1)) == pytest.approx(2.54)
    assert _real(fx.length_value(inch, 10, inch.units)) == pytest.approx(25.4)
    assert _real(fx.length_value(ctx, -5)) == pytest.approx(-0.5)


def test_length_value_expression_unchanged() -> None:
    value = fx.length_value(make_ctx(params={"units": "in"}), "wall/2")
    assert value.kind == "string"
    assert value.value == "wall/2"


def test_angle_value_degrees_to_radians_and_expression() -> None:
    ctx = make_ctx(params={"units": "in"})
    assert _real(fx.angle_value(ctx, 5)) == pytest.approx(math.radians(5))
    assert _real(fx.angle_value(ctx, 90)) == pytest.approx(math.pi / 2)
    value = fx.angle_value(ctx, "draft * 2")
    assert (value.kind, value.value) == ("string", "draft * 2")


def test_scalar_value_is_unitless() -> None:
    assert _real(fx.scalar_value(0.5)) == 0.5
    assert _real(fx.scalar_value(3)) == 3.0
    value = fx.scalar_value("count")
    assert (value.kind, value.value) == ("string", "count")


def test_point3d_in_cm() -> None:
    point = fx.point3d(make_ctx(), [10, 20, -30])
    assert (point.x, point.y, point.z) == pytest.approx((1.0, 2.0, -3.0))
    point = fx.point3d(make_ctx(params={"units": "in"}), (1, 0, 0))
    assert point.x == pytest.approx(2.54)


@pytest.mark.parametrize(
    ("call", "param"),
    [
        (lambda ctx: fx.length_value(ctx, True, param="distance"), "distance"),
        (lambda ctx: fx.length_value(ctx, None, param="distance"), "distance"),
        (lambda ctx: fx.length_value(ctx, " ", param="distance"), "distance"),
        (lambda ctx: fx.length_value(ctx, math.inf, param="distance"), "distance"),
        (lambda ctx: fx.angle_value(ctx, [1], param="taper1"), "taper1"),
        (lambda ctx: fx.scalar_value(math.nan, param="weight"), "weight"),
        (lambda ctx: fx.point3d(ctx, [1, 2], param="point"), "point"),
        (lambda ctx: fx.point3d(ctx, [1, 2, "3"], param="point"), "point"),
    ],
)
def test_bad_values_are_invalid_params(call: Any, param: str) -> None:
    error = _error(call, make_ctx())
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param


# --- 3.2 resolve_many / object_collection --------------------------------------------------


def test_resolve_many_edges() -> None:
    body = fake_box_body(1, 1, 1)
    ctx = make_ctx(fake_feature_design(body))
    e1, e2 = body.edges.item(0), body.edges.item(1)
    found = fx.resolve_many(
        ctx,
        [e1.entityToken, {"ref": e2.entityToken}, e1.entityToken],
        ("adsk::fusion::BRepEdge",),
        "edges",
    )
    assert found == [e1, e2]
    assert fx.resolve_many(ctx, [e1.entityToken], ("BRepEdge",), "edges") == [e1]


def test_resolve_many_accepts_proxies_as_is() -> None:
    body = fake_box_body(1, 1, 1)
    proxy = _Proxy(body.edges.item(0))
    ctx = make_ctx(fake_feature_design(body), refs={"proxy-1": proxy})
    assert fx.resolve_many(ctx, ["proxy-1"], ("adsk::fusion::BRepEdge",), "edges") == [proxy]


def test_resolve_many_empty_list() -> None:
    error = _error(fx.resolve_many, make_ctx(), [], ("adsk::fusion::BRepEdge",), "edges")
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == "edges"
    assert error.details["expected"] == ["BRepEdge"]
    assert error.details["reason"] == "empty"
    error = _error(fx.resolve_many, make_ctx(), "edge-1", ("BRepEdge",), "edges")
    assert error.code is ErrorCode.INVALID_PARAMS


def test_resolve_many_wrong_type() -> None:
    body = fake_box_body(1, 1, 1)
    ctx = make_ctx(fake_feature_design(body))
    face = body.faces.item(0)
    error = _error(fx.resolve_many, ctx, [face.entityToken], ("adsk::fusion::BRepEdge",), "edges")
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details == {
        "param": "edges",
        "ref": face.entityToken,
        "expected": ["BRepEdge"],
        "actual": "BRepFace (plane)",
        "index": 0,
    }


def test_resolve_many_passes_entity_not_found_through() -> None:
    error = _error(fx.resolve_many, make_ctx(), ["gone"], ("BRepEdge",), "edges")
    assert error.code is ErrorCode.ENTITY_NOT_FOUND
    assert error.details["ref"] == "gone"
    assert error.details["param"] == "edges"


def test_object_collection() -> None:
    a, b = object(), object()
    collection = fx.object_collection([a, b])
    assert isinstance(collection, adsk.core.ObjectCollection)
    assert collection.asArray() == [a, b]
    assert fx.object_collection([]).count == 0


# --- 3.3 feature_profiles ------------------------------------------------------------------


def _profile_by_inner(sketch: Any, has_inner: bool) -> Any:
    for profile in sketch.profiles:
        if any(not loop.isOuter for loop in profile.profileLoops) == has_inner:
            return profile
    raise AssertionError("no such profile")


def test_feature_profiles_profile_ref_gives_one_object() -> None:
    design = fake_feature_design()
    sketch, _, _ = _plate_sketch(design)
    ctx = make_ctx(design)
    ref = format_profile_ref(sketch.entityToken, 10, 10)  # mm: inside the plate, off the hole
    found = fx.feature_profiles(ctx, [ref])
    assert found is _profile_by_inner(sketch, True)
    assert fx.feature_profiles(ctx, ref) is found


def test_feature_profiles_all_and_dedup_give_collection() -> None:
    design = fake_feature_design()
    sketch, _, _ = _plate_sketch(design)
    ctx = make_ctx(design)
    disc = format_profile_ref(sketch.entityToken, 50, 30)
    found = fx.feature_profiles(ctx, [disc, {"sketch": sketch.entityToken, "all": True}, disc])
    assert isinstance(found, adsk.core.ObjectCollection)
    assert found.count == 2
    assert found.item(0) is _profile_by_inner(sketch, False)
    assert set(map(id, found)) == {id(p) for p in sketch.profiles}


def test_feature_profiles_planar_face() -> None:
    body = fake_box_body(1, 1, 1)
    design = fake_feature_design(body)
    ctx = make_ctx(design)
    top = body.faces.item(5)
    assert fx.feature_profiles(ctx, [top.entityToken], allow_faces=True) is top
    error = _error(fx.feature_profiles, ctx, [top.entityToken])
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == "profiles"
    assert error.details["expected"] == ["Profile"]
    assert error.details["actual"] == "BRepFace (plane)"


def test_feature_profiles_non_planar_face_is_invalid() -> None:
    body = fake_box_body(1, 1, 1)
    design = fake_feature_design(body)
    cylinder = add_face(body, fake_cylinder_face(1, 2, token="cyl-face"))
    design.add_entity(cylinder)
    error = _error(fx.feature_profiles, make_ctx(design), ["cyl-face"], allow_faces=True)
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["ref"] == "cyl-face"
    assert error.details["actual"] == "BRepFace (cylinder)"
    assert error.details["expected"] == ["Profile", "BRepFace (planar)"]


def test_feature_profiles_sketch_text_only_when_allowed() -> None:
    design = fake_feature_design()
    sketch, _, _ = _plate_sketch(design)
    text = _Text(sketch)
    ctx = make_ctx(design, refs={"text-1": text})
    assert fx.feature_profiles(ctx, ["text-1"], allow_text=True) is text
    mixed = fx.feature_profiles(
        ctx, ["text-1", format_profile_ref(sketch.entityToken, 50, 30)], allow_text=True
    )
    assert mixed.asArray() == [text, _profile_by_inner(sketch, False)]
    error = _error(fx.feature_profiles, ctx, ["text-1"], allow_faces=True)
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["actual"] == "SketchText"


def test_feature_profiles_stale_point_passes_details_profiles() -> None:
    design = fake_feature_design()
    sketch, _, _ = _plate_sketch(design)
    error = _error(
        fx.feature_profiles, make_ctx(design), [format_profile_ref(sketch.entityToken, 500, 500)]
    )
    assert error.code is ErrorCode.ENTITY_NOT_FOUND
    assert len(error.details["profiles"]) == 2


def test_feature_profiles_bad_specs() -> None:
    ctx = make_ctx()
    assert _error(fx.feature_profiles, ctx, []).code is ErrorCode.INVALID_PARAMS
    assert _error(fx.feature_profiles, ctx, [42]).details["index"] == 0
    assert _error(fx.feature_profiles, ctx, ["gone"]).code is ErrorCode.ENTITY_NOT_FOUND
    error = _error(fx.feature_profiles, ctx, [{"point": [1, 2]}])
    assert error.details["reason"] == "invalid_profile_spec"


# --- 3.4 operation and participants --------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("new_body", OPS.NewBodyFeatureOperation),
        ("join", OPS.JoinFeatureOperation),
        ("cut", OPS.CutFeatureOperation),
        ("intersect", OPS.IntersectFeatureOperation),
        ("new_component", OPS.NewComponentFeatureOperation),
    ],
)
def test_operation_enum(name: str, expected: int) -> None:
    assert fx.operation_enum(name) == expected


def test_operation_enum_unknown() -> None:
    error = _error(fx.operation_enum, "merge")
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == "operation"


def test_apply_participants_none_and_empty_leave_input_untouched() -> None:
    inp = FakeFeatureInput()
    assert fx.apply_participants(make_ctx(), inp, None, "cut") is None
    assert fx.apply_participants(make_ctx(), inp, [], "new_body") is None
    assert inp.log == []


def test_apply_participants_sets_python_list_of_bodies() -> None:
    a, b = fake_box_body(1, 1, 1, name="A"), fake_box_body(1, 1, 1, name="B")
    ctx = make_ctx(fake_feature_design(a, b))
    inp = FakeFeatureInput()
    assert fx.apply_participants(ctx, inp, [a.entityToken], "cut") == [a]
    assert inp.sets == {"participantBodies": [a]}
    assert type(inp.participantBodies) is list
    assert fx.apply_participants(ctx, FakeFeatureInput(), [a.entityToken, b.entityToken]) == [a, b]


@pytest.mark.parametrize("operation", ["new_body", "new_component"])
def test_apply_participants_rejected_for_new_bodies(operation: str) -> None:
    a = fake_box_body(1, 1, 1)
    inp = FakeFeatureInput()
    error = _error(
        fx.apply_participants, make_ctx(fake_feature_design(a)), inp, [a.entityToken], operation
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == "participant_bodies"
    assert inp.log == []


def test_participant_bodies_wrong_type() -> None:
    a = fake_box_body(1, 1, 1)
    ctx = make_ctx(fake_feature_design(a))
    error = _error(fx.participant_bodies, ctx, [a.faces.item(0).entityToken], "join")
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["expected"] == ["BRepBody"]
    assert (
        _error(fx.participant_bodies, ctx, a.entityToken, "join").code is ErrorCode.INVALID_PARAMS
    )


# --- 3.5 extents, starts, directions -------------------------------------------------------


def test_distance_extent_in_mm() -> None:
    definition = fx.extent_definition(make_ctx(), {"type": "distance", "distance": 15})
    assert isinstance(definition, adsk.fusion.DistanceExtentDefinition)
    assert _real(definition.distance) == pytest.approx(1.5)
    definition = fx.extent_definition(make_ctx(), {"type": "distance", "distance": 1}, "in")
    assert _real(definition.distance) == pytest.approx(2.54)


def test_to_entity_extent_with_expression_offset() -> None:
    body = fake_box_body(1, 1, 1)
    ctx = make_ctx(fake_feature_design(body))
    face = body.faces.item(5)
    definition = fx.extent_definition(
        ctx, {"type": "to_entity", "entity": face.entityToken, "offset": "wall/2"}
    )
    assert isinstance(definition, adsk.fusion.ToEntityExtentDefinition)
    assert definition.entity is face
    assert definition.isChained is True
    assert (definition.offset.kind, definition.offset.value) == ("string", "wall/2")
    definition = fx.extent_definition(
        ctx, {"type": "to_entity", "entity": "origin:XY", "chained": False}
    )
    assert definition.entity is ctx.root.xYConstructionPlane
    assert definition.isChained is False
    assert _real(definition.offset) == 0.0


def test_through_all_extent() -> None:
    definition = fx.extent_definition(make_ctx(), {"type": "through_all"})
    assert isinstance(definition, adsk.fusion.ThroughAllExtentDefinition)


@pytest.mark.parametrize(
    ("spec", "param"),
    [
        ("distance", "extent"),
        ({"type": "blind"}, "extent.type"),
        ({"type": "distance"}, "extent.distance"),
        ({"type": "to_entity"}, "extent.entity"),
        ({"type": "to_entity", "entity": "origin:XY", "chained": "yes"}, "extent.chained"),
        ({"type": "to_entity", "entity": "origin:Z"}, "extent.entity"),
    ],
)
def test_extent_validation(spec: Any, param: str) -> None:
    error = _error(fx.extent_definition, make_ctx(), spec)
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param


def test_start_definitions() -> None:
    body = fake_box_body(1, 1, 1)
    ctx = make_ctx(fake_feature_design(body))
    plane = fx.start_definition(ctx, {"type": "profile_plane"})
    assert isinstance(plane, adsk.fusion.ProfilePlaneStartDefinition)
    offset = fx.start_definition(ctx, {"type": "offset", "offset": 5})
    assert isinstance(offset, adsk.fusion.OffsetStartDefinition)
    assert _real(offset.offset) == pytest.approx(0.5)
    face = body.faces.item(5)
    entity = fx.start_definition(ctx, {"type": "entity", "entity": face.entityToken, "offset": 2})
    assert isinstance(entity, adsk.fusion.EntityStartDefinition)
    assert entity.entity is face
    assert _real(entity.offset) == pytest.approx(0.2)
    entity = fx.start_definition(ctx, {"type": "entity", "entity": face.entityToken}, "in")
    assert _real(entity.offset) == 0.0
    assert _error(fx.start_definition, ctx, {"type": "offset"}).details["param"] == "start.offset"
    assert _error(fx.start_definition, ctx, {"type": "x"}).details["param"] == "start.type"


def test_direction_enum() -> None:
    assert fx.direction_enum("positive") == DIRS.PositiveExtentDirection
    assert fx.direction_enum("negative") == DIRS.NegativeExtentDirection
    assert _error(fx.direction_enum, "symmetric").code is ErrorCode.INVALID_PARAMS


# --- 3.6 target_component ------------------------------------------------------------------


def test_target_component_of_sketch_entities() -> None:
    design = fake_feature_design()
    root = design.rootComponent
    sketch, lines, hole = _plate_sketch(design)
    assert fx.target_component(sketch.profiles.item(0)) is root
    assert fx.target_component(lines[0]) is root
    assert fx.target_component(hole) is root
    assert fx.target_component(sketch.originPoint) is root
    assert fx.target_component(_Text(sketch)) is root


def test_target_component_of_brep_and_construction() -> None:
    part = FakeComponent("Part")
    body = part.add_body(fake_box_body(1, 1, 1))
    assert fx.target_component(body.faces.item(0)) is part
    assert fx.target_component(body.edges.item(0)) is part
    assert fx.target_component(body.vertices.item(0)) is part
    assert fx.target_component(body) is part
    assert fx.target_component(part.zConstructionAxis) is part
    axis = part.xConstructionAxis
    axis.component = FakeComponent("Owner")
    assert fx.target_component(axis) is axis.component
    assert fx.target_component(FakeFeature(component=part)) is part


def test_target_component_of_proxy_uses_native_object() -> None:
    part = FakeComponent("Part")
    body = part.add_body(fake_box_body(1, 1, 1))
    proxy = _Proxy(body.edges.item(0))
    assert fx.target_component(proxy) is part


def test_target_component_unknown() -> None:
    error = _error(fx.target_component, FakeBody())  # a body outside any component
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["actual"] == "BRepBody"


# --- 3.7 / 3.8 snapshot, result, health ----------------------------------------------------


def _ref(entity: Any) -> dict[str, Any]:
    return {
        "ref": entity.entityToken,
        "type": entity.objectType.rsplit("::", 1)[-1],
        "name": entity.name,
    }


def test_snapshot_takes_unique_components() -> None:
    a, b = fake_box_body(1, 1, 1), fake_box_body(1, 1, 1)
    design = fake_feature_design(a, b)
    root = design.rootComponent
    snapshot = fx.BodySnapshot.take([root, root, None])
    assert snapshot.components == [root]
    assert snapshot.tokens() == {a.entityToken, b.entityToken}


def test_result_new_body() -> None:
    design = fake_feature_design()
    root = design.rootComponent
    ctx = make_ctx(design)
    extrudes = root.features.extrudeFeatures
    before = fx.BodySnapshot.take([root])
    new = FakeBody("Body1")
    feature = extrudes.will_create(new).add(extrudes.createInput(None, OPS.NewBodyFeatureOperation))
    result = fx.feature_result(ctx, feature, before)
    assert result == {
        "feature": {"ref": feature.entityToken, "type": "ExtrudeFeature", "name": "Extrude1"},
        "health": {"state": "ok", "message": ""},
        "created_bodies": [_ref(new)],
        "modified_bodies": [],
        "removed_bodies": [],
    }


def test_result_join_modifies_and_removes() -> None:
    kept, eaten = fake_box_body(1, 1, 1, name="Kept"), fake_box_body(1, 1, 1, name="Eaten")
    design = fake_feature_design(kept, eaten)
    root = design.rootComponent
    extrudes = root.features.extrudeFeatures
    before = fx.BodySnapshot.take([root])
    feature = extrudes.will_modify(kept).will_remove(eaten).add(extrudes.createInput(None, 0))
    result = fx.feature_result(make_ctx(design), feature, before)
    assert result["created_bodies"] == []
    assert result["modified_bodies"] == [_ref(kept)]
    assert result["removed_bodies"] == [eaten.entityToken]


def test_result_touched_bodies_are_modified() -> None:
    body = fake_box_body(1, 1, 1)
    other = fake_box_body(1, 1, 1, name="Other")
    design = fake_feature_design(body, other)
    root = design.rootComponent
    fillets = root.features.filletFeatures
    before = fx.BodySnapshot.take([root])
    feature = fillets.add(fillets.createInput())
    result = fx.feature_result(make_ctx(design), feature, before, touched=[body, body])
    assert result["modified_bodies"] == [_ref(body)]
    assert result["created_bodies"] == []
    assert result["removed_bodies"] == []


def test_result_modified_body_with_new_token_stays_modified() -> None:
    body = fake_box_body(1, 1, 1)
    design = fake_feature_design(body)
    root = design.rootComponent
    shells = root.features.shellFeatures
    before = fx.BodySnapshot.take([root])
    old = body.entityToken
    feature = shells.add(shells.createInput([], True))
    body.entityToken = "body-new-token"  # Fusion re-tokens a modified body (design R4)
    result = fx.feature_result(make_ctx(design), feature, before, touched=[body])
    assert result["modified_bodies"] == [_ref(body)]
    assert result["created_bodies"] == []
    assert old not in result["removed_bodies"]


def test_result_new_component_adds_component() -> None:
    design = fake_feature_design()
    root = design.rootComponent
    part = FakeComponent("Part")
    extrudes = root.features.extrudeFeatures
    before = fx.BodySnapshot.take([root])
    new = FakeBody("Body1")
    extrudes.will_create(new, component=part)
    feature = extrudes.add(extrudes.createInput(None, OPS.NewComponentFeatureOperation))
    result = fx.feature_result(make_ctx(design), feature, before, operation="new_component")
    assert result["created_bodies"] == [_ref(new)]
    assert result["component"] == {"ref": part.entityToken, "type": "Component", "name": "Part"}
    assert "component" not in fx.feature_result(make_ctx(design), feature, before)


def test_result_warning_is_kept_in_health() -> None:
    design = fake_feature_design()
    fillets = design.rootComponent.features.filletFeatures
    fillets.health = HS.WarningFeatureHealthState
    fillets.message = "small radius"
    before = fx.BodySnapshot.take([design.rootComponent])
    feature = fillets.add(fillets.createInput())
    result = fx.feature_result(make_ctx(design), feature, before)
    assert result["health"] == {"state": "warning", "message": "small radius"}


def test_result_error_feature_raises_fusion_api_error() -> None:
    design = fake_feature_design()
    fillets = design.rootComponent.features.filletFeatures
    fillets.health = HS.ErrorFeatureHealthState
    fillets.message = "radius too large"
    before = fx.BodySnapshot.take([design.rootComponent])
    feature = fillets.add(fillets.createInput())
    error = _error(fx.feature_result, make_ctx(design), feature, before)
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert "radius too large" in error.message
    assert error.details["health"]["state"] == "error"
    assert error.details["feature_type"] == "FilletFeature"


def test_result_without_feature() -> None:
    error = _error(fx.feature_result, make_ctx(), None, fx.BodySnapshot())
    assert error.code is ErrorCode.FUSION_API_ERROR


def test_error_feature_is_rolled_back_by_dispatcher() -> None:
    body = fake_box_body(1, 1, 1)
    design = fake_feature_design(body)
    root = design.rootComponent
    timeline_before = design.timeline.names()
    registry = Registry()

    def fillet(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        fillets = ctx.root.features.filletFeatures
        fillets.health = HS.ErrorFeatureHealthState
        fillets.message = "cannot fillet"
        before = fx.BodySnapshot.take([ctx.root])
        feature = fillets.add(fillets.createInput())
        return fx.feature_result(ctx, feature, before, touched=[body])

    registry.register("test.fillet", fillet, mutating=True)
    dispatcher = Dispatcher(
        "tok", registry, context_factory=lambda params: Context(FakeApp(design), params)
    )
    response = dispatcher.dispatch(Request("1", "tok", "test.fillet", {}))
    assert response.ok is False
    assert design.timeline.names() == timeline_before
    assert list(root.features.filletFeatures) == []


# --- 6.1 make_path -------------------------------------------------------------------------


def _path_setup(*bodies: Any) -> tuple[Any, Any, list[Any], list[Any]]:
    """Design, root, an open chain of two sketch lines and a separate line (all on XZ)."""
    design = fake_feature_design(*bodies)
    sketch = make_sketch(design, "XZ")
    chain = polygon_curves(sketch, [(0, 0), (0, 5), (3, 8)], closed=False)
    lone = polygon_curves(sketch, [(6, 0), (6, 4)], closed=False)
    return design, design.rootComponent, chain, lone


def _path_ctx(design: Any, curves: list[Any]) -> Any:
    return make_ctx(design, {}, {c.entityToken: c for c in curves})


def test_make_path_one_curve_with_chain_takes_the_connected_curves() -> None:
    design, root, chain, lone = _path_setup()
    ctx = _path_ctx(design, [*chain, *lone])
    path = fx.make_path(ctx, root, [chain[0].entityToken], chain=True)
    assert path is root.features.paths[-1]
    assert isinstance(path.curves, adsk.core.ObjectCollection)
    assert list(path.curves) == chain
    assert path.isChain is False
    single = fx.make_path(ctx, root, [chain[0].entityToken], chain=False)
    assert single.curves is chain[0] and single.isChain is False
    alone = fx.make_path(ctx, root, [lone[0].entityToken], chain=True)
    assert alone.curves is lone[0]


def test_make_path_several_curves_without_duplicates() -> None:
    design, root, chain, lone = _path_setup()
    ctx = _path_ctx(design, [*chain, *lone])
    refs = [chain[0].entityToken, chain[1].entityToken, chain[0].entityToken]
    path = fx.make_path(ctx, root, refs, chain=False)
    assert list(path.curves) == chain


def test_make_path_edges() -> None:
    box = fake_box_body(1, 1, 1)
    design, root, _, _ = _path_setup(box)
    ctx = make_ctx(design, {})
    edge = box.edges.item(0)
    path = fx.make_path(ctx, root, [edge.entityToken], chain=True)
    assert path.curves is edge and path.isChain is True
    path = fx.make_path(ctx, root, [edge.entityToken], chain=False)
    assert path.isChain is False
    two = [box.edges.item(0).entityToken, box.edges.item(1).entityToken]
    path = fx.make_path(ctx, root, two, chain=True)
    assert list(path.curves) == [box.edges.item(0), box.edges.item(1)]
    assert path.isChain is False


def test_make_path_mixing_curves_and_edges_is_invalid() -> None:
    box = fake_box_body(1, 1, 1)
    design, root, chain, _ = _path_setup(box)
    ctx = _path_ctx(design, chain)
    error = _error(
        fx.make_path, ctx, root, [chain[0].entityToken, box.edges.item(0).entityToken], True, "rail"
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == "rail"
    assert error.details["reason"] == "mixed_path"
    assert root.features.paths == []


def test_make_path_invalid_input() -> None:
    box = fake_box_body(1, 1, 1)
    design, root, chain, _ = _path_setup(box)
    ctx = _path_ctx(design, chain)
    bad: tuple[Any, ...] = ([], None, "x")
    for refs in bad:
        error = _error(fx.make_path, ctx, root, refs)
        assert error.code is ErrorCode.INVALID_PARAMS
        assert error.details["param"] == "path"
    error = _error(fx.make_path, ctx, root, [box.faces.item(0).entityToken])
    assert error.details == {
        "param": "path",
        "ref": box.faces.item(0).entityToken,
        "expected": list(fx.PATH_TYPES),
        "actual": "BRepFace (plane)",
        "index": 0,
    }
    error = _error(fx.make_path, ctx, root, ["gone"])
    assert error.code is ErrorCode.ENTITY_NOT_FOUND
    assert error.details["param"] == "path"
    assert root.features.paths == []


def test_make_path_fusion_failure_is_fusion_api_error() -> None:
    design, root, chain, _ = _path_setup()
    ctx = _path_ctx(design, chain)
    root.features.path_error = "gap between curves"
    error = _error(fx.make_path, ctx, root, [chain[0].entityToken, chain[1].entityToken])
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert "gap between curves" in error.message
    assert error.details == {"param": "path", "count": 2}

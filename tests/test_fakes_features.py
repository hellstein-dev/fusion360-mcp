"""``tests/fakes/fusion_features.py`` (c07 2.2, D21): feature collections, inputs, body
changes, rollback and the fake ref resolver."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import Registry
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.brep import FakeBody, FakeComponent, fake_box_body
from tests.fakes.design import FakeApp
from tests.fakes.fusion_features import (
    FEATURE_COLLECTIONS,
    THREAD_TABLE,
    FakeFeatureCollection,
    FakeFeatureInput,
    FakeRefs,
    FakeThreadDataQuery,
    cylinder_face,
    fake_feature_design,
    install_features,
    make_ctx,
)

adsk = fusion_addin.adsk
HS = adsk.fusion.FeatureHealthStates
OPS = adsk.fusion.FeatureOperations


def test_install_features_hangs_every_collection() -> None:
    component = install_features(FakeComponent("part"))
    collections = component.features.collections()
    assert set(collections) == set(FEATURE_COLLECTIONS)
    extrudes = component.features.extrudeFeatures
    assert isinstance(extrudes, FakeFeatureCollection)
    assert extrudes.kind == "ExtrudeFeature"
    assert extrudes.component is component
    holes = component.features.holeFeatures
    assert hasattr(holes, "createCounterboreInput")
    assert not hasattr(holes, "createInput")
    assert hasattr(component.features.chamferFeatures, "createInput2")


def test_omitted_collections_are_absent() -> None:
    component = install_features(FakeComponent(), omit=("ribFeatures", "webFeatures"))
    assert not hasattr(component.features, "ribFeatures")
    assert not hasattr(component.features, "webFeatures")
    assert hasattr(component.features, "extrudeFeatures")
    with pytest.raises(ValueError, match="unknown"):
        install_features(FakeComponent(), omit=("boxFeatures",))


def test_create_input_records_method_and_arguments() -> None:
    extrudes = install_features(FakeComponent()).features.extrudeFeatures
    profile = object()
    inp = extrudes.createInput(profile, OPS.CutFeatureOperation)
    assert isinstance(inp, FakeFeatureInput)
    assert inp.create_method == "createInput"
    assert inp.create_args == (profile, OPS.CutFeatureOperation)
    assert extrudes.inputs == [inp]
    assert extrudes.last_input is inp
    assert inp.objectType == "adsk::fusion::ExtrudeFeatureInput"


def test_removed_create_method_is_absent_and_create_error_raises() -> None:
    chamfers = install_features(FakeComponent()).features.chamferFeatures
    chamfers.create_methods.discard("createInput2")
    assert not hasattr(chamfers, "createInput2")
    chamfers.create_error = "bad input"
    with pytest.raises(RuntimeError, match="bad input"):
        chamfers.createInput([], True)


def test_input_records_sets_and_calls_in_order() -> None:
    inp = FakeFeatureInput()
    extent = adsk.fusion.DistanceExtentDefinition.create(adsk.core.ValueInput.createByReal(1.5))
    assert inp.setOneSideExtent(extent, 0, None) is True
    inp.participantBodies = ["b"]
    inp.isSolid = True
    inp.isSolid = False
    assert inp.isSolid is False
    assert inp.log == [
        ("call", "setOneSideExtent", (extent, 0, None)),
        ("set", "participantBodies", ["b"]),
        ("set", "isSolid", True),
        ("set", "isSolid", False),
    ]
    assert inp.sets == {"participantBodies": ["b"], "isSolid": False}
    assert inp.calls == [("setOneSideExtent", (extent, 0, None))]
    assert inp.call("setOneSideExtent") == (extent, 0, None)
    assert inp.called("setOneSideExtent")
    assert not inp.called("setTwoSidesExtent")
    with pytest.raises(KeyError, match="setTwoSidesExtent"):
        inp.call("setTwoSidesExtent")


def test_input_returns_provide_and_missing() -> None:
    inp = FakeFeatureInput()
    edge_sets = FakeFeatureInput()
    inp.provide("edgeSetInputs", edge_sets)
    assert inp.edgeSetInputs is edge_sets
    assert inp.log == []
    edge_set = FakeFeatureInput()
    edge_sets.returns["addVariableRadiusEdgeSet"] = edge_set
    assert inp.edgeSetInputs.addVariableRadiusEdgeSet("edges", 1, 2, True) is edge_set
    assert edge_sets.call("addVariableRadiusEdgeSet") == ("edges", 1, 2, True)
    inp.returns["setPositionBySketchPoints"] = lambda points: len(points) == 2
    assert inp.setPositionBySketchPoints([1, 2]) is True
    assert inp.setPositionBySketchPoints([1]) is False
    inp.missing.add("edgeSetInputs")
    assert not hasattr(inp, "edgeSetInputs")


def test_fillet_and_chamfer_inputs_have_edge_sets_and_on_input_hook() -> None:
    features = install_features(FakeComponent()).features
    fillet = features.filletFeatures.createInput()
    edge_set = fillet.edgeSetInputs.addVariableRadiusEdgeSet("edges", 1, 2, True)
    assert isinstance(edge_set, FakeFeatureInput)
    assert edge_set.create_method == "addVariableRadiusEdgeSet"
    assert edge_set.create_args == ("edges", 1, 2, True)
    assert fillet.edgeSetInputs.edge_sets == [edge_set]
    assert fillet.log == []
    chamfer = features.chamferFeatures.createInput2()
    chamfer_set = chamfer.chamferEdgeSets.addEqualDistanceChamferEdgeSet("edges", 1, True)
    assert chamfer.chamferEdgeSets.edge_sets == [chamfer_set]
    features.filletFeatures.on_input = lambda inp: inp.missing.add("edgeSetInputs")
    assert not hasattr(features.filletFeatures.createInput(), "edgeSetInputs")


def test_add_returns_c03_feature_with_health_and_timeline() -> None:
    design = fake_feature_design()
    root = design.rootComponent
    fillets = root.features.filletFeatures
    fillets.health = HS.WarningFeatureHealthState
    fillets.message = "small radius"
    inp = fillets.createInput()
    feature = fillets.add(inp)
    assert feature.objectType == "adsk::fusion::FilletFeature"
    assert feature.name == "Fillet1"
    assert feature.healthState == HS.WarningFeatureHealthState
    assert feature.errorOrWarningMessage == "small radius"
    assert feature.parentComponent is root
    assert feature.input is inp
    assert list(feature.bodies) == []
    assert fillets.added == [inp]
    assert list(fillets) == [feature]
    assert design.timeline.names()[-1] == "Fillet1"
    assert design.findEntityByToken(feature.entityToken) == [feature]
    assert fillets.add(fillets.createInput()).name == "Fillet2"


def test_add_errors() -> None:
    extrudes = install_features(FakeComponent()).features.extrudeFeatures
    with pytest.raises(RuntimeError, match="feature input"):
        extrudes.add(object())
    extrudes.add_error = "Profile is not closed"
    with pytest.raises(RuntimeError, match="Profile is not closed"):
        extrudes.add(extrudes.createInput(None, 0))
    assert list(extrudes) == []


def test_body_changes_apply_once_and_roll_back() -> None:
    kept = fake_box_body(1, 1, 1, name="Kept")
    eaten = fake_box_body(1, 1, 1, name="Eaten")
    design = fake_feature_design(kept, eaten)
    root = design.rootComponent
    timeline_before = design.timeline.names()
    extrudes = root.features.extrudeFeatures
    new = FakeBody("New")
    extrudes.will_create(new).will_modify(kept).will_remove(eaten)
    feature = extrudes.add(extrudes.createInput(None, OPS.JoinFeatureOperation))

    assert list(feature.bodies) == [new, kept]
    assert list(root.bRepBodies) == [kept, new]
    assert new.parentComponent is root
    assert design.findEntityByToken(new.entityToken) == [new]
    assert design.findEntityByToken(eaten.entityToken) == []
    # The next add starts without pending changes.
    assert list(extrudes.add(extrudes.createInput(None, 0)).bodies) == []

    assert feature.deleteMe() is True
    assert eaten in list(root.bRepBodies)
    assert new not in list(root.bRepBodies)
    assert design.findEntityByToken(eaten.entityToken) == [eaten]
    assert design.findEntityByToken(new.entityToken) == []
    assert design.findEntityByToken(feature.entityToken) == []
    assert feature not in list(extrudes)
    assert design.timeline.names() == [*timeline_before, "Extrude2"]


def test_new_bodies_may_go_to_another_component() -> None:
    design = fake_feature_design()
    other = FakeComponent("Part")
    extrudes = design.rootComponent.features.extrudeFeatures
    body = FakeBody("Body1")
    feature = extrudes.will_create(body, component=other).add(extrudes.createInput(None, 4))
    assert body.parentComponent is other
    assert list(other.bRepBodies) == [body]
    assert list(design.rootComponent.bRepBodies) == []
    assert list(feature.bodies) == [body]


def test_dispatcher_rollback_deletes_the_feature_and_restores_bodies() -> None:
    eaten = fake_box_body(1, 1, 1, name="Eaten")
    design = fake_feature_design(eaten)
    root = design.rootComponent
    before = design.timeline.names()
    registry = Registry()

    def make_cut(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        cuts = ctx.root.features.extrudeFeatures
        cuts.will_remove(eaten)
        cuts.add(cuts.createInput(None, OPS.CutFeatureOperation))
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "failed after add")

    registry.register("test.cut", make_cut, mutating=True)
    dispatcher = Dispatcher(
        "tok", registry, context_factory=lambda params: Context(FakeApp(design), params)
    )
    response = dispatcher.dispatch(Request("1", "tok", "test.cut", {}))
    assert response.ok is False
    assert design.timeline.names() == before
    assert list(root.bRepBodies) == [eaten]
    assert list(root.features.extrudeFeatures) == []


def test_make_ctx_defaults_and_params() -> None:
    ctx = make_ctx(params={"units": "in"})
    assert isinstance(ctx.refs, FakeRefs)
    assert hasattr(ctx.root.features, "extrudeFeatures")
    assert ctx.units.length(1) == pytest.approx(2.54)


def test_fake_refs_resolve_aliases_design_and_origin() -> None:
    body = fake_box_body(1, 2, 3)
    design = fake_feature_design(body)
    proxy = object()
    ctx = make_ctx(design, refs={"proxy-face": proxy})
    face = body.faces.item(0)
    assert ctx.refs.resolve("proxy-face") is proxy
    assert ctx.refs.resolve({"ref": face.entityToken, "type": "BRepFace"}) is face
    assert ctx.refs.resolve("origin:Z") is design.rootComponent.zConstructionAxis
    assert isinstance(ctx.refs, FakeRefs)
    assert ctx.refs.resolved == ["proxy-face", face.entityToken, "origin:Z"]
    assert ctx.refs.to_ref(proxy) == "proxy-face"
    assert ctx.refs.to_ref(face) == face.entityToken
    assert ctx.refs.to_ref(design.rootComponent.xYConstructionPlane) == "origin:XY"


def test_fake_refs_unknown_ref_is_entity_not_found() -> None:
    ctx = make_ctx()
    with pytest.raises(BridgeError) as info:
        ctx.refs.resolve("gone-token")
    assert info.value.code is ErrorCode.ENTITY_NOT_FOUND
    assert info.value.details == {"ref": "gone-token"}
    assert isinstance(ctx.refs, FakeRefs)
    assert ctx.refs.resolved == ["gone-token"]


def test_create_path_records_paths_and_can_fail() -> None:
    component = install_features(FakeComponent())
    features = component.features
    path = features.createPath("curve", True)
    assert features.paths == [path]
    assert path.objectType == "adsk::fusion::Path"
    assert path.entities == ["curve"] and path.isChain is True
    collection = adsk.core.ObjectCollection.createWithArray(["a", "b"])
    assert features.createPath(collection, False).entities == ["a", "b"]
    features.path_error = "gap"
    with pytest.raises(RuntimeError, match="gap"):
        features.createPath("curve", True)


def test_loft_input_has_sections_and_guides() -> None:
    component = install_features(FakeComponent())
    lofts = component.features.loftFeatures
    inp = lofts.createInput(OPS.NewBodyFeatureOperation)
    section = inp.loftSections.add("profile")
    assert section.create_args == ("profile",) and section.entity == "profile"
    section.setTangentEndCondition(2)
    assert section.calls == [("setTangentEndCondition", (2,))]
    assert list(inp.loftSections) == [section]
    inp.centerLineOrRails.addRail("rail")
    assert inp.centerLineOrRails.calls == [("addRail", ("rail",))]
    assert inp.sets == {}
    other = component.features.extrudeFeatures.createInput("p", OPS.NewBodyFeatureOperation)
    assert "loftSections" not in other.attrs


def test_will_set_applies_to_the_next_feature_only() -> None:
    holes = install_features(FakeComponent()).features.holeFeatures
    holes.will_set(sideFaces=["f1", "f2"])
    first = holes.add(holes.createSimpleInput("d"))
    assert first.sideFaces == ["f1", "f2"]
    second = holes.add(holes.createSimpleInput("d"))
    assert not hasattr(second, "sideFaces")


def test_thread_data_query() -> None:
    component = install_features(FakeComponent())
    query = component.features.threadFeatures.threadDataQuery
    assert isinstance(query, FakeThreadDataQuery)
    assert query.allThreadTypes == list(THREAD_TABLE)
    assert query.allSizes("ISO Metric profile") == ["5", "6", "8", "10"]
    assert query.allSizes("Whitworth") == []
    assert query.allDesignations("ISO Metric profile", "6") == ["M6x1", "M6x0.75"]
    assert query.allClasses(True, "ISO Metric profile", "M6x1") == ["6H", "4H", "5H"]
    assert query.allClasses(False, "ISO Metric profile", "M6x1") == ["6g", "4g6g", "6e"]
    assert query.allClasses(True, "ISO Metric profile", "M99x9") == []
    assert query.recommendThreadData(1.0, False, "ISO Metric profile") == (True, "M10x1.5", "6g")
    assert query.recommendThreadData(0.5, True, "ISO Metric profile") == (True, "M6x1", "6H")
    assert query.recommendThreadData(5.0, True, "ISO Metric profile") == (False, "", "")
    assert query.calls[0] == ("allThreadTypes", ())
    assert query.calls[-1] == ("recommendThreadData", (5.0, True, "ISO Metric profile"))
    omitted = install_features(FakeComponent(), omit=("threadFeatures",))
    assert not hasattr(omitted.features, "threadFeatures")


def test_cylinder_face_orientation() -> None:
    body = FakeBody("Shaft")
    outer = cylinder_face(0.5, 2.0, body=body)
    inner = cylinder_face(0.5, 2.0, internal=True, axis=(1, 0, 0))
    assert outer.body is body and list(body.faces) == [outer]
    assert outer.geometry.radius == 0.5
    p = outer.pointOnFace
    assert math.hypot(p.x, p.y) == pytest.approx(0.5) and p.z == pytest.approx(1.0)
    ok, normal = outer.evaluator.getNormalAtPoint(p)
    assert ok and normal.dotProduct(adsk.core.Vector3D.create(p.x, p.y, 0)) > 0
    q = inner.pointOnFace
    _, inward = inner.evaluator.getNormalAtPoint(q)
    assert q.x == pytest.approx(1.0)
    assert inward.dotProduct(adsk.core.Vector3D.create(0, q.y, q.z)) < 0

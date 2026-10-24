"""``feature.fillet`` / ``rule_fillet`` / ``chamfer`` / ``shell`` / ``draft`` / ``offset_faces``
handlers on feature fakes (c07 D17-D19; tasks 11.1-11.4, 11.7-11.8, 12.1-12.3)."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import feature_modify as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.brep import FakeFeature, fake_box_body
from tests.fakes.design import FakeCollection
from tests.fakes.fusion_features import (
    FakeFeatureInput,
    cylinder_face,
    fake_feature_design,
    install_features,
    make_ctx,
)

adsk = fusion_addin.adsk
HS = adsk.fusion.FeatureHealthStates
TOPO = adsk.fusion.RuleFilletTopologyTypes
TOKEN = "t" * 32


def _real(value_input: Any) -> float:
    assert value_input.kind == "real", value_input
    return float(value_input.value)


class Setup:
    """A 40 x 20 x 10 mm box ``box`` (faces ``top``/``bottom``/``px``/``nx``/``py``/``ny``,
    vertical edges ``v0``..``v3``, top edge ``tx``) and a second box ``other``."""

    def __init__(self, parametric: bool = True, omit: tuple[str, ...] = ()) -> None:
        self.box = fake_box_body(4, 2, 1, name="Box")
        self.other = fake_box_body(1, 1, 1, name="Other")
        self.design = fake_feature_design(self.box, self.other, parametric=parametric)
        self.root = self.design.rootComponent
        if omit:
            install_features(self.root, self.design, omit)
        self.features = self.root.features
        faces = {f.entityToken.rsplit("face", 1)[1]: f for f in self.box.faces}
        edges = {e.entityToken.rsplit("-e", 1)[1]: e for e in self.box.edges}
        self.top, self.bottom = faces["+z"], faces["-z"]
        self.px, self.nx, self.py, self.ny = faces["+x"], faces["-x"], faces["+y"], faces["-y"]
        self.vertical = [edges[k] for k in ("000001", "100101", "010011", "110111")]
        self.tx = edges["001101"]
        self.cyl = cylinder_face(0.5, body=self.box)
        feature: Any = FakeFeature("ExtrudeFeature", "Boss", component=self.root)
        feature.bodies = FakeCollection([self.box])
        self.feature = feature
        self.aliases: dict[str, Any] = {
            "top": self.top,
            "bottom": self.bottom,
            "px": self.px,
            "nx": self.nx,
            "py": self.py,
            "ny": self.ny,
            "tx": self.tx,
            "cyl": self.cyl,
            "box": self.box,
            "other": self.other,
            "other_top": next(f for f in self.other.faces if f.entityToken.endswith("face+z")),
            "boss": self.feature,
            **{f"v{i}": e for i, e in enumerate(self.vertical)},
        }

    def ctx(self, params: dict[str, Any]) -> Context:
        return make_ctx(self.design, params, self.aliases)

    def run(self, fn: Any, params: dict[str, Any]) -> Any:
        return fn(self.ctx(params), params)

    def error(self, fn: Any, params: dict[str, Any]) -> BridgeError:
        with pytest.raises(BridgeError) as info:
            fn(self.ctx(params), params)
        return info.value

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        dispatcher = Dispatcher(TOKEN, context_factory=lambda p: self.ctx(p))
        return dispatcher.dispatch(Request("1", TOKEN, method, params))


def _names(bodies: list[dict[str, Any]]) -> list[str]:
    return [b["name"] for b in bodies]


@pytest.mark.parametrize(
    "method",
    [
        "feature.fillet",
        "feature.rule_fillet",
        "feature.chamfer",
        "feature.shell",
        "feature.draft",
        "feature.offset_faces",
    ],
)
def test_handlers_are_registered_as_mutating(method: str) -> None:
    spec = REGISTRY.get(method)
    assert spec is not None and spec.mutating is True


# --- 11.1 fillet: constant -----------------------------------------------------------------


def test_constant_fillet_of_two_edges() -> None:
    s = Setup()
    result = s.run(h.feature_fillet, {"edges": ["v0", "v1"], "radius": 3})
    fillets = s.features.filletFeatures
    inp = fillets.last_input
    assert inp.create_method == "createInput" and inp.create_args == ()
    (edge_set,) = inp.edgeSetInputs.edge_sets
    assert edge_set.create_method == "addConstantRadiusEdgeSet"
    edges, radius, chain = edge_set.create_args
    assert isinstance(edges, adsk.core.ObjectCollection)
    assert list(edges) == s.vertical[:2]
    assert _real(radius) == pytest.approx(0.3)
    assert chain is True
    assert inp.sets["isRollingBallCorner"] is True
    assert inp.sets["isG2"] is False
    assert fillets.added == [inp]
    assert result["feature"]["type"] == "FilletFeature"
    assert _names(result["modified_bodies"]) == ["Box"]
    assert result["created_bodies"] == [] and result["removed_bodies"] == []
    assert result["health"] == {"state": "ok", "message": ""}


def test_fillet_options_units_and_expression() -> None:
    s = Setup()
    s.run(
        h.feature_fillet,
        {
            "edges": ["v0", "v0", "tx"],
            "radius": 0.25,
            "tangent_chain": False,
            "corner": "setback",
            "g2": True,
            "units": "in",
        },
    )
    inp = s.features.filletFeatures.last_input
    edges, radius, chain = inp.edgeSetInputs.edge_sets[0].create_args
    assert list(edges) == [s.vertical[0], s.tx]
    assert _real(radius) == pytest.approx(0.635)
    assert chain is False
    assert inp.sets["isRollingBallCorner"] is False and inp.sets["isG2"] is True
    s.run(h.feature_fillet, {"edges": ["v0"], "radius": "r_edge"})
    radius = s.features.filletFeatures.last_input.edgeSetInputs.edge_sets[0].create_args[1]
    assert radius.kind == "string" and radius.value == "r_edge"


def test_fillet_edges_of_two_bodies_are_both_modified() -> None:
    s = Setup()
    other_edge = next(iter(s.other.edges))
    s.aliases["oe"] = other_edge
    result = s.run(h.feature_fillet, {"edges": ["v0", "oe", "v1"], "radius": 1})
    assert _names(result["modified_bodies"]) == ["Box", "Other"]


def test_constant_fillet_falls_back_to_legacy_call() -> None:
    s = Setup()
    fillets = s.features.filletFeatures
    fillets.on_input = lambda inp: inp.missing.add("edgeSetInputs")
    result = s.run(h.feature_fillet, {"edges": ["v0"], "radius": 2})
    inp = fillets.last_input
    edges, radius, chain = inp.call("addConstantRadiusEdgeSet")
    assert list(edges) == [s.vertical[0]]
    assert _real(radius) == pytest.approx(0.2) and chain is True
    assert fillets.added == [inp]
    assert _names(result["modified_bodies"]) == ["Box"]


@pytest.mark.parametrize(
    "params",
    [
        {"mode": "variable", "start_radius": 1, "end_radius": 2},
        {"mode": "chord", "chord_length": 2},
    ],
)
def test_legacy_fillet_api_has_only_constant(params: dict[str, Any]) -> None:
    s = Setup()
    fillets = s.features.filletFeatures
    fillets.on_input = lambda inp: inp.missing.add("edgeSetInputs")
    error = s.error(h.feature_fillet, {"edges": ["v0"], **params})
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert "not available" in error.message
    assert error.details["feature_type"] == "FilletFeature"
    assert fillets.added == []


def test_legacy_fillet_without_any_edge_set_call() -> None:
    s = Setup()
    fillets = s.features.filletFeatures

    def strip(inp: FakeFeatureInput) -> None:
        inp.missing.update({"edgeSetInputs", "addConstantRadiusEdgeSet"})

    fillets.on_input = strip
    error = s.error(h.feature_fillet, {"edges": ["v0"], "radius": 2})
    assert error.code is ErrorCode.FUSION_API_ERROR and "not available" in error.message


def test_fillet_edge_set_refused_by_fusion() -> None:
    s = Setup()
    fillets = s.features.filletFeatures
    fillets.on_input = lambda inp: inp.edgeSetInputs.returns.update(
        {"addConstantRadiusEdgeSet": None}
    )
    error = s.error(h.feature_fillet, {"edges": ["v0"], "radius": 2})
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.details["setter"] == "addConstantRadiusEdgeSet"
    assert fillets.added == []


def test_fillet_error_health_is_rolled_back() -> None:
    s = Setup()
    fillets = s.features.filletFeatures
    fillets.health = HS.ErrorFeatureHealthState
    fillets.message = "radius too large"
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.fillet", {"edges": ["v0"], "radius": 50})
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert response.error.details["health"]["state"] == "error"
    assert s.design.timeline.names() == timeline
    assert list(fillets) == []


def test_fillet_error_in_direct_design_is_deleted() -> None:
    s = Setup(parametric=False)
    fillets = s.features.filletFeatures
    fillets.health = HS.ErrorFeatureHealthState
    error = s.error(h.feature_fillet, {"edges": ["v0"], "radius": 50})
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert list(fillets) == []


# --- 11.2 fillet: variable -----------------------------------------------------------------


def test_variable_fillet_with_mid_radius() -> None:
    s = Setup()
    s.run(
        h.feature_fillet,
        {
            "edges": ["tx"],
            "mode": "variable",
            "start_radius": 2,
            "end_radius": 5,
            "mid": [{"position": 0.5, "radius": 3}, {"position": 0.8, "radius": "r_mid"}],
        },
    )
    inp = s.features.filletFeatures.last_input
    (edge_set,) = inp.edgeSetInputs.edge_sets
    assert edge_set.create_method == "addVariableRadiusEdgeSet"
    edges, start, end, chain = edge_set.create_args
    assert list(edges) == [s.tx]
    assert _real(start) == pytest.approx(0.2) and _real(end) == pytest.approx(0.5)
    assert chain is True
    positions = list(edge_set.sets["midPositions"])
    radii = list(edge_set.sets["midRadii"])
    assert [_real(p) for p in positions] == pytest.approx([0.5, 0.8])
    assert _real(radii[0]) == pytest.approx(0.3)
    assert radii[1].kind == "string" and radii[1].value == "r_mid"


def test_variable_fillet_without_mid_sets_no_mid_lists() -> None:
    s = Setup()
    s.run(
        h.feature_fillet,
        {"edges": ["tx"], "mode": "variable", "start_radius": 1, "end_radius": 4, "mid": []},
    )
    edge_set = s.features.filletFeatures.last_input.edgeSetInputs.edge_sets[0]
    assert edge_set.sets == {}


def test_variable_fillet_mid_radii_not_available() -> None:
    s = Setup()
    fillets = s.features.filletFeatures

    def old_edge_set(*args: Any) -> FakeFeatureInput:
        edge_set = FakeFeatureInput(None, "addVariableRadiusEdgeSet", args)
        edge_set.missing.add("midPositions")
        return edge_set

    fillets.on_input = lambda inp: inp.edgeSetInputs.returns.update(
        {"addVariableRadiusEdgeSet": old_edge_set}
    )
    error = s.error(
        h.feature_fillet,
        {
            "edges": ["tx"],
            "mode": "variable",
            "start_radius": 1,
            "end_radius": 4,
            "mid": [{"position": 0.5, "radius": 2}],
        },
    )
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert "midPositions" in error.message and "not available" in error.message
    assert fillets.added == []


# --- 11.3 fillet: chord --------------------------------------------------------------------


def test_chord_length_fillet() -> None:
    s = Setup()
    s.run(h.feature_fillet, {"edges": ["v2"], "mode": "chord", "chord_length": 4})
    (edge_set,) = s.features.filletFeatures.last_input.edgeSetInputs.edge_sets
    assert edge_set.create_method == "addChordLengthEdgeSet"
    edges, chord, chain = edge_set.create_args
    assert list(edges) == [s.vertical[2]]
    assert _real(chord) == pytest.approx(0.4) and chain is True


def test_chord_fillet_not_available() -> None:
    s = Setup()
    fillets = s.features.filletFeatures
    fillets.on_input = lambda inp: inp.edgeSetInputs.missing.add("addChordLengthEdgeSet")
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.fillet", {"edges": ["v0"], "mode": "chord", "chord_length": 2})
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert "Chord length fillets are not available" in response.error.message
    assert fillets.added == []
    assert s.design.timeline.names() == timeline


# --- fillet validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({}, "radius"),
        ({"radius": 0}, "radius"),
        ({"radius": -2}, "radius"),
        ({"radius": True}, "radius"),
        ({"mode": "variable", "start_radius": 1}, "end_radius"),
        ({"mode": "variable", "start_radius": 1, "end_radius": 2, "radius": 3}, "radius"),
        ({"radius": 2, "mid": [{"position": 0.5, "radius": 1}]}, "mid"),
        ({"radius": 2, "chord_length": 1}, "chord_length"),
        ({"mode": "chord"}, "chord_length"),
        ({"mode": "round", "radius": 1}, "mode"),
        ({"radius": 1, "corner": "sharp"}, "corner"),
        ({"radius": 1, "g2": "yes"}, "g2"),
        ({"radius": 1, "tangent_chain": 1}, "tangent_chain"),
        ({"radius": 1, "edges": ["top"]}, "edges"),
        ({"radius": 1, "edges": []}, "edges"),
        ({"radius": 1, "edges": None}, "edges"),
    ],
)
def test_fillet_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    error = s.error(h.feature_fillet, {"edges": ["v0"], **params})
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.features.filletFeatures.inputs == []


@pytest.mark.parametrize(
    ("mid", "param"),
    [
        ({"position": 1.5, "radius": 1}, "mid[0].position"),
        ({"position": -0.1, "radius": 1}, "mid[0].position"),
        ({"position": "half", "radius": 1}, "mid[0].position"),
        ({"position": 0.5}, "mid[0].radius"),
        ({"position": 0.5, "radius": 0}, "mid[0].radius"),
        ({"position": 0.5, "radius": 1, "weight": 2}, "mid"),
        ("0.5", "mid"),
    ],
)
def test_variable_fillet_mid_validation(mid: Any, param: str) -> None:
    s = Setup()
    params = {"edges": ["tx"], "mode": "variable", "start_radius": 1, "end_radius": 2}
    error = s.error(h.feature_fillet, {**params, "mid": [mid]})
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    error = s.error(h.feature_fillet, {**params, "mid": {"position": 0.5}})
    assert error.details["param"] == "mid"
    assert s.features.filletFeatures.inputs == []


def test_mid_positions_at_both_ends_are_allowed() -> None:
    s = Setup()
    s.run(
        h.feature_fillet,
        {
            "edges": ["tx"],
            "mode": "variable",
            "start_radius": 1,
            "end_radius": 2,
            "mid": [{"position": 0, "radius": 1}, {"position": 1, "radius": 2}],
        },
    )
    edge_set = s.features.filletFeatures.last_input.edgeSetInputs.edge_sets[0]
    assert [_real(p) for p in edge_set.sets["midPositions"]] == [0.0, 1.0]


# --- 11.4 rule fillet ----------------------------------------------------------------------


def test_rule_fillet_all_edges_of_faces() -> None:
    s = Setup()
    result = s.run(h.feature_rule_fillet, {"faces": ["top"], "radius": 1})
    rules = s.features.ruleFilletFeatures
    inp = rules.last_input
    entities, radius, topology = inp.create_args
    assert list(entities) == [s.top]
    assert _real(radius) == pytest.approx(0.1)
    assert topology == TOPO.AllEdgesRuleFilletTopologyType
    assert h.RULE_FILLET_SECOND_SET not in inp.sets
    assert rules.added == [inp]
    assert result["feature"]["type"] == "RuleFilletFeature"
    assert _names(result["modified_bodies"]) == ["Box"]


def test_rule_fillet_of_a_feature() -> None:
    s = Setup()
    result = s.run(h.feature_rule_fillet, {"faces": ["boss", "top"], "radius": "r"})
    entities, radius, _ = s.features.ruleFilletFeatures.last_input.create_args
    assert list(entities) == [s.feature, s.top]
    assert radius.kind == "string"
    assert _names(result["modified_bodies"]) == ["Box"]


def test_rule_fillet_between_faces() -> None:
    s = Setup()
    s.run(
        h.feature_rule_fillet,
        {"faces": ["top"], "faces2": ["px", "py"], "rule": "between_faces", "radius": 2},
    )
    inp = s.features.ruleFilletFeatures.last_input
    assert inp.create_args[2] == TOPO.BetweenFacesRuleFilletTopologyType
    assert list(inp.sets[h.RULE_FILLET_SECOND_SET]) == [s.px, s.py]


def test_rule_fillet_not_available_without_collection() -> None:
    s = Setup(omit=("ruleFilletFeatures",))
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.rule_fillet", {"faces": ["top"], "radius": 1})
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert "Rule fillets are not available" in response.error.message
    assert response.error.details["feature_type"] == "RuleFilletFeature"
    assert s.design.timeline.names() == timeline


def test_rule_fillet_not_available_without_topology_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Setup()
    monkeypatch.delattr(adsk.fusion, "RuleFilletTopologyTypes")
    error = s.error(h.feature_rule_fillet, {"faces": ["top"], "radius": 1})
    assert error.code is ErrorCode.FUSION_API_ERROR and "not available" in error.message
    assert s.features.ruleFilletFeatures.inputs == []


def test_rule_fillet_second_set_not_available() -> None:
    s = Setup()
    rules = s.features.ruleFilletFeatures
    rules.on_input = lambda inp: inp.missing.add(h.RULE_FILLET_SECOND_SET)
    error = s.error(
        h.feature_rule_fillet,
        {"faces": ["top"], "faces2": ["px"], "rule": "between_faces", "radius": 2},
    )
    assert error.code is ErrorCode.FUSION_API_ERROR and "not available" in error.message
    assert rules.added == []


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"rule": "between_faces"}, "faces2"),
        ({"faces2": ["px"]}, "faces2"),
        ({"radius": None}, "radius"),
        ({"radius": 0}, "radius"),
        ({"rule": "some_edges"}, "rule"),
        ({"faces": ["v0"]}, "faces"),
        ({"faces": []}, "faces"),
        ({"rule": "between_faces", "faces2": ["box"]}, "faces2"),
    ],
)
def test_rule_fillet_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    error = s.error(h.feature_rule_fillet, {"faces": ["top"], "radius": 1, **params})
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.features.ruleFilletFeatures.inputs == []


# --- 11.7 / 11.8 chamfer -------------------------------------------------------------------


def test_equal_distance_chamfer() -> None:
    s = Setup()
    result = s.run(h.feature_chamfer, {"edges": ["tx"], "distance": 1})
    chamfers = s.features.chamferFeatures
    inp = chamfers.last_input
    assert inp.create_method == "createInput2" and inp.create_args == ()
    (edge_set,) = inp.chamferEdgeSets.edge_sets
    assert edge_set.create_method == "addEqualDistanceChamferEdgeSet"
    edges, distance, chain = edge_set.create_args
    assert list(edges) == [s.tx]
    assert _real(distance) == pytest.approx(0.1) and chain is True
    assert chamfers.added == [inp]
    assert result["feature"]["type"] == "ChamferFeature"
    assert _names(result["modified_bodies"]) == ["Box"]


def test_two_distances_chamfer_with_flip() -> None:
    s = Setup()
    s.run(
        h.feature_chamfer,
        {
            "edges": ["tx", "v0"],
            "mode": "two_distances",
            "distance": 2,
            "distance2": 3,
            "flip": True,
            "tangent_chain": False,
        },
    )
    (edge_set,) = s.features.chamferFeatures.last_input.chamferEdgeSets.edge_sets
    assert edge_set.create_method == "addTwoDistancesChamferEdgeSet"
    edges, d1, d2, flip, chain = edge_set.create_args
    assert list(edges) == [s.tx, s.vertical[0]]
    assert (_real(d1), _real(d2)) == pytest.approx((0.2, 0.3))
    assert flip is True and chain is False


def test_distance_and_angle_chamfer() -> None:
    s = Setup()
    s.run(
        h.feature_chamfer, {"edges": ["tx"], "mode": "distance_angle", "distance": 2, "angle": 30}
    )
    (edge_set,) = s.features.chamferFeatures.last_input.chamferEdgeSets.edge_sets
    assert edge_set.create_method == "addDistanceAndAngleChamferEdgeSet"
    _, distance, angle, flip, chain = edge_set.create_args
    assert _real(distance) == pytest.approx(0.2)
    assert _real(angle) == pytest.approx(math.radians(30))
    assert flip is False and chain is True
    s.run(h.feature_chamfer, {"edges": ["tx"], "mode": "distance_angle", "distance": 2})
    edge_set = s.features.chamferFeatures.last_input.chamferEdgeSets.edge_sets[0]
    assert _real(edge_set.create_args[2]) == pytest.approx(math.radians(45))


def test_chamfer_falls_back_to_create_input() -> None:
    s = Setup()
    chamfers = s.features.chamferFeatures
    chamfers.create_methods.discard("createInput2")
    result = s.run(h.feature_chamfer, {"edges": ["tx"], "distance": 1})
    inp = chamfers.last_input
    assert inp.create_method == "createInput"
    edges, chain = inp.create_args
    assert list(edges) == [s.tx] and chain is True
    (distance,) = inp.call("setToEqualDistance")
    assert _real(distance) == pytest.approx(0.1)
    assert chamfers.added == [inp]
    assert _names(result["modified_bodies"]) == ["Box"]


def test_legacy_two_distances_flip_swaps_distances() -> None:
    s = Setup()
    chamfers = s.features.chamferFeatures
    chamfers.create_methods.discard("createInput2")
    params = {"edges": ["tx"], "mode": "two_distances", "distance": 2, "distance2": 3}
    s.run(h.feature_chamfer, params)
    d1, d2 = chamfers.last_input.call("setToTwoDistances")
    assert (_real(d1), _real(d2)) == pytest.approx((0.2, 0.3))
    s.run(h.feature_chamfer, {**params, "flip": True})
    d1, d2 = chamfers.last_input.call("setToTwoDistances")
    assert (_real(d1), _real(d2)) == pytest.approx((0.3, 0.2))


def test_legacy_distance_angle_flip() -> None:
    s = Setup()
    chamfers = s.features.chamferFeatures
    chamfers.create_methods.discard("createInput2")
    params = {"edges": ["tx"], "mode": "distance_angle", "distance": 2, "angle": 30}
    s.run(h.feature_chamfer, {**params, "flip": True})
    inp = chamfers.last_input
    distance, angle = inp.call("setToDistanceAndAngle")
    assert _real(distance) == pytest.approx(0.2)
    assert _real(angle) == pytest.approx(math.radians(30))
    assert inp.sets["isFlipped"] is True
    chamfers.on_input = lambda i: i.missing.add("isFlipped")
    error = s.error(h.feature_chamfer, {**params, "flip": True})
    assert error.code is ErrorCode.FUSION_API_ERROR and "not available" in error.message


def test_legacy_chamfer_setter_refused() -> None:
    s = Setup()
    chamfers = s.features.chamferFeatures
    chamfers.create_methods.discard("createInput2")
    chamfers.on_input = lambda i: i.returns.update({"setToEqualDistance": False})
    error = s.error(h.feature_chamfer, {"edges": ["tx"], "distance": 1})
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.details["setter"] == "setToEqualDistance"
    assert chamfers.added == []


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"distance": None}, "distance"),
        ({"distance": 0}, "distance"),
        ({"distance2": 2}, "distance2"),
        ({"mode": "two_distances"}, "distance2"),
        ({"mode": "two_distances", "distance2": -1}, "distance2"),
        ({"angle": 30}, "angle"),
        ({"mode": "two_distances", "distance2": 2, "angle": 30}, "angle"),
        ({"mode": "distance_angle", "angle": 90}, "angle"),
        ({"mode": "distance_angle", "angle": 0}, "angle"),
        ({"flip": True}, "flip"),
        ({"mode": "bevel"}, "mode"),
        ({"edges": ["top"]}, "edges"),
    ],
)
def test_chamfer_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    error = s.error(h.feature_chamfer, {"edges": ["tx"], "distance": 1, **params})
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.features.chamferFeatures.inputs == []


# --- 12.1 shell ----------------------------------------------------------------------------


def test_open_top_shell() -> None:
    s = Setup()
    result = s.run(h.feature_shell, {"faces": ["top"], "inside_thickness": 2})
    shells = s.features.shellFeatures
    inp = shells.last_input
    entities, chain = inp.create_args
    assert list(entities) == [s.top] and chain is True
    assert _real(inp.sets["insideThickness"]) == pytest.approx(0.2)
    assert "outsideThickness" not in inp.sets
    assert shells.added == [inp]
    assert result["feature"]["type"] == "ShellFeature"
    assert _names(result["modified_bodies"]) == ["Box"]


def test_closed_shell_of_bodies_with_both_thicknesses() -> None:
    s = Setup()
    result = s.run(
        h.feature_shell,
        {
            "bodies": ["box", "other"],
            "inside_thickness": 1,
            "outside_thickness": "wall",
            "tangent_chain": False,
            "faces": [],
        },
    )
    inp = s.features.shellFeatures.last_input
    entities, chain = inp.create_args
    assert list(entities) == [s.box, s.other] and chain is False
    assert _real(inp.sets["insideThickness"]) == pytest.approx(0.1)
    assert inp.sets["outsideThickness"].value == "wall"
    assert _names(result["modified_bodies"]) == ["Box", "Other"]


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"inside_thickness": 2}, "faces"),
        ({"faces": ["top"], "bodies": ["box"], "inside_thickness": 2}, "bodies"),
        ({"faces": ["top"]}, "inside_thickness"),
        ({"faces": ["top"], "inside_thickness": 0}, "inside_thickness"),
        ({"faces": ["top"], "outside_thickness": -1}, "outside_thickness"),
        ({"faces": ["v0"], "inside_thickness": 2}, "faces"),
        ({"bodies": ["top"], "inside_thickness": 2}, "bodies"),
        ({"faces": "top", "inside_thickness": 2}, "faces"),
        ({"faces": ["top"], "inside_thickness": 2, "tangent_chain": "no"}, "tangent_chain"),
    ],
)
def test_shell_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    error = s.error(h.feature_shell, params)
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.features.shellFeatures.inputs == []


# --- 12.2 draft ----------------------------------------------------------------------------


def test_single_angle_draft() -> None:
    s = Setup()
    result = s.run(h.feature_draft, {"faces": ["px", "nx"], "plane": "origin:XY", "angle": 2})
    drafts = s.features.draftFeatures
    inp = drafts.last_input
    faces, plane, chain = inp.create_args
    assert faces == [s.px, s.nx]
    assert plane is s.root.xYConstructionPlane
    assert chain is True
    symmetric, angle = inp.call("setSingleAngle")
    assert symmetric is False
    assert _real(angle) == pytest.approx(math.radians(2))
    assert not inp.called("setTwoAngles")
    assert inp.sets["isDirectionFlipped"] is False
    assert drafts.added == [inp]
    assert result["feature"]["type"] == "DraftFeature"
    assert _names(result["modified_bodies"]) == ["Box"]


def test_symmetric_two_angle_and_flipped_drafts() -> None:
    s = Setup()
    s.run(
        h.feature_draft,
        {"faces": ["px"], "plane": "bottom", "angle": 3, "symmetric": True, "flip": True},
    )
    inp = s.features.draftFeatures.last_input
    assert inp.create_args[1] is s.bottom
    symmetric, angle = inp.call("setSingleAngle")
    assert symmetric is True and _real(angle) == pytest.approx(math.radians(3))
    assert inp.sets["isDirectionFlipped"] is True
    s.run(
        h.feature_draft,
        {
            "faces": ["px"],
            "plane": "origin:XY",
            "angle": 2,
            "angle2": "draft2",
            "tangent_chain": False,
        },
    )
    inp = s.features.draftFeatures.last_input
    a1, a2 = inp.call("setTwoAngles")
    assert _real(a1) == pytest.approx(math.radians(2)) and a2.value == "draft2"
    assert not inp.called("setSingleAngle")
    assert inp.create_args[2] is False


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"angle": None}, "angle"),
        ({"angle": 0}, "angle"),
        ({"angle": 90}, "angle"),
        ({"angle": -95}, "angle"),
        ({"angle2": 0}, "angle2"),
        ({"symmetric": True, "angle2": 3}, "angle2"),
        ({"plane": "cyl"}, "plane"),
        ({"plane": "v0"}, "plane"),
        ({"plane": None}, "plane"),
        ({"plane": "px"}, "plane"),
        ({"faces": ["v0"]}, "faces"),
        ({"flip": 1}, "flip"),
    ],
)
def test_draft_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    base = {"faces": ["px", "nx"], "plane": "origin:XY", "angle": 2}
    error = s.error(h.feature_draft, {**base, **params})
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.features.draftFeatures.inputs == []


def test_draft_setter_refused_by_fusion() -> None:
    s = Setup()
    drafts = s.features.draftFeatures
    drafts.on_input = lambda i: i.returns.update({"setSingleAngle": False})
    error = s.error(h.feature_draft, {"faces": ["px"], "plane": "origin:XY", "angle": 2})
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.details["setter"] == "setSingleAngle"
    assert drafts.added == []


# --- 12.3 offset faces ---------------------------------------------------------------------


def test_offset_face_outward() -> None:
    s = Setup()
    result = s.run(h.feature_offset_faces, {"faces": ["top"], "distance": 2})
    offsets = s.features.offsetFacesFeatures
    inp = offsets.last_input
    faces, distance = inp.create_args  # live createInput(faces, distance): no chain (c15 3.2)
    assert faces == [s.top]
    assert _real(distance) == pytest.approx(0.2)
    assert offsets.added == [inp]
    assert result["feature"]["type"] == "OffsetFacesFeature"
    assert _names(result["modified_bodies"]) == ["Box"]


def test_offset_faces_inward_without_chain_and_expression() -> None:
    s = Setup()
    params = {"faces": ["top", "px"], "distance": -0.1, "chain": False, "units": "in"}
    s.run(h.feature_offset_faces, params)
    faces, distance = s.features.offsetFacesFeatures.last_input.create_args
    assert faces == [s.top, s.px]
    assert _real(distance) == pytest.approx(-0.254)
    s.run(h.feature_offset_faces, {"faces": ["top"], "distance": "-pull"})
    distance = s.features.offsetFacesFeatures.last_input.create_args[1]
    assert distance.kind == "string" and distance.value == "-pull"


def test_offset_faces_chain_walks_tangent_faces() -> None:
    """The live input has no chain option (c15 3.2): ``chain`` adds the faces reached through
    ``tangentiallyConnectedFaces`` (direct neighbours only), walked to the whole chain."""
    s = Setup()
    s.top.tangentiallyConnectedFaces = [s.px]
    s.px.tangentiallyConnectedFaces = [s.top, s.py]
    s.py.tangentiallyConnectedFaces = [s.px]
    s.run(h.feature_offset_faces, {"faces": ["top"], "distance": 1})
    assert s.features.offsetFacesFeatures.last_input.create_args[0] == [s.top, s.px, s.py]
    s.run(h.feature_offset_faces, {"faces": ["top"], "distance": 1, "chain": False})
    assert s.features.offsetFacesFeatures.last_input.create_args[0] == [s.top]


def test_offset_faces_old_three_argument_call_is_refused() -> None:
    offsets = Setup().features.offsetFacesFeatures
    with pytest.raises(TypeError, match="takes 2 positional arguments"):
        offsets.createInput([], None, True)


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"distance": None}, "distance"),
        ({"distance": 0}, "distance"),
        ({"distance": True}, "distance"),
        ({"faces": ["v0"]}, "faces"),
        ({"faces": []}, "faces"),
        ({"chain": "yes"}, "chain"),
    ],
)
def test_offset_faces_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    error = s.error(h.feature_offset_faces, {"faces": ["top"], "distance": 2, **params})
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.features.offsetFacesFeatures.inputs == []


def test_offset_faces_warning_stays_with_health() -> None:
    s = Setup()
    offsets = s.features.offsetFacesFeatures
    offsets.health = HS.WarningFeatureHealthState
    offsets.message = "tiny sliver"
    result = s.run(h.feature_offset_faces, {"faces": ["top"], "distance": 2})
    assert result["health"] == {"state": "warning", "message": "tiny sliver"}
    assert len(list(offsets)) == 1

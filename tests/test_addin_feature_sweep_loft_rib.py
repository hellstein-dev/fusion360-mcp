"""``feature.sweep`` / ``feature.loft`` / ``feature.rib`` / ``feature.web`` handlers on feature
fakes (c07 D12-D14; tasks 6.2-6.3, 7.1-7.2, 8.1-8.2)."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import feature_sketch as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.sketch_refs import format_profile_ref
from fusion_mcp.protocol.wire import Request
from tests.fakes.brep import FakeBody, fake_box_body
from tests.fakes.design import FakeApp
from tests.fakes.fusion_features import fake_feature_design, install_features, make_ctx
from tests.fakes.fusion_sketch import auto_profiles, circle_curve, make_sketch, polygon_curves

adsk = fusion_addin.adsk
OPS = adsk.fusion.FeatureOperations
HS = adsk.fusion.FeatureHealthStates
ORIENT = adsk.fusion.SweepOrientationTypes
SCALING = adsk.fusion.SweepProfileScalingOptions
TOKEN = "t" * 32

RECT = [(0, 0), (4, 0), (4, 2), (0, 2)]  # cm: 40 x 20 mm
SMALL = [(1, 0.5), (3, 0.5), (3, 1.5), (1, 1.5)]  # cm, inside RECT on another sketch


def _real(value_input: Any) -> float:
    assert value_input.kind == "real", value_input
    return float(value_input.value)


class Setup:
    """Design with a 40 x 20 mm rectangle sketch on XY (``profile``) and an open two-line
    path sketch on XZ (``path``: sketch lines sharing an end point, ``rail``: a separate
    line)."""

    def __init__(self, *bodies: Any, parametric: bool = True) -> None:
        self.design = fake_feature_design(*bodies, parametric=parametric)
        self.root = self.design.rootComponent
        self.features = self.root.features
        self.sketch = make_sketch(self.design, "XY")
        polygon_curves(self.sketch, RECT)
        self.sketch.profile_builder = auto_profiles
        self.profile = format_profile_ref(self.sketch.entityToken, 20, 10)
        self.path_sketch = make_sketch(self.design, "XZ")
        self.path = polygon_curves(self.path_sketch, [(0, 0), (0, 5), (3, 8)], closed=False)
        self.rail = polygon_curves(self.path_sketch, [(4, 0), (4, 6)], closed=False)
        self.aliases: dict[str, Any] = {c.entityToken: c for c in (*self.path, *self.rail)}
        self.sweeps = self.features.sweepFeatures
        self.lofts = self.features.loftFeatures

    def ctx(self, params: dict[str, Any], refs: dict[str, Any] | None = None) -> Context:
        return make_ctx(self.design, params, {**self.aliases, **(refs or {})})

    def run(self, fn: Any, params: dict[str, Any], refs: dict[str, Any] | None = None) -> Any:
        return fn(self.ctx(params, refs), params)

    def error(
        self, fn: Any, params: dict[str, Any], refs: dict[str, Any] | None = None
    ) -> BridgeError:
        with pytest.raises(BridgeError) as info:
            fn(self.ctx(params, refs), params)
        return info.value

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        dispatcher = Dispatcher(TOKEN, context_factory=lambda p: Context(FakeApp(self.design), p))
        return dispatcher.dispatch(Request("1", TOKEN, method, params))


@pytest.mark.parametrize("method", ["feature.sweep", "feature.loft", "feature.rib", "feature.web"])
def test_handlers_are_registered_as_mutating(method: str) -> None:
    spec = REGISTRY.get(method)
    assert spec is not None and spec.mutating is True


# --- 6.2 sweep -----------------------------------------------------------------------------


def _sweep(s: Setup, **params: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"profiles": [s.profile], "path": [s.path[0].entityToken]}
    base.update(params)
    return base


def test_sweep_with_twist_builds_chained_path() -> None:
    s = Setup()
    s.sweeps.will_create(FakeBody("Body1"))
    result = s.run(
        h.feature_sweep,
        _sweep(s, path=[s.path[0].entityToken, s.path[1].entityToken], twist_angle=90),
    )
    path = s.features.paths[-1]
    assert isinstance(path.curves, adsk.core.ObjectCollection)
    assert list(path.curves) == s.path
    assert path.isChain is False  # the chain is already expanded by resolve_curves

    inp = s.sweeps.last_input
    profile, given_path, operation = inp.create_args
    assert profile is s.sketch.profiles.item(0)
    assert given_path is path
    assert operation == OPS.NewBodyFeatureOperation
    assert inp.sets["orientation"] == ORIENT.PerpendicularOrientationType
    assert _real(inp.sets["twistAngle"]) == pytest.approx(math.pi / 2)
    assert _real(inp.sets["taperAngle"]) == 0.0
    assert _real(inp.sets["distanceOne"]) == 1.0
    assert inp.sets["isDirectionFlipped"] is False
    assert "guideRail" not in inp.sets and "participantBodies" not in inp.sets
    assert s.sweeps.added == [inp]
    assert result["feature"]["type"] == "SweepFeature"
    assert [b["name"] for b in result["created_bodies"]] == ["Body1"]


def test_sweep_one_curve_with_chain_takes_connected_curves() -> None:
    s = Setup()
    s.run(h.feature_sweep, _sweep(s))
    path = s.features.paths[-1]
    assert list(path.curves) == s.path
    s.run(h.feature_sweep, _sweep(s, path_chain=False))
    path = s.features.paths[-1]
    assert path.curves is s.path[0] and path.isChain is False


def test_sweep_options_parallel_taper_extent_flip_cut() -> None:
    box = fake_box_body(4, 2, 1, name="Box")
    s = Setup(box)
    s.sweeps.will_modify(box)
    result = s.run(
        h.feature_sweep,
        _sweep(
            s,
            orientation="parallel",
            taper_angle="draft",
            path_extent=0.5,
            flip=True,
            operation="cut",
            participant_bodies=[box.entityToken],
        ),
    )
    inp = s.sweeps.last_input
    assert inp.create_args[2] == OPS.CutFeatureOperation
    assert inp.sets["orientation"] == ORIENT.ParallelOrientationType
    taper = inp.sets["taperAngle"]
    assert (taper.kind, taper.value) == ("string", "draft")
    assert _real(inp.sets["distanceOne"]) == pytest.approx(0.5)
    assert inp.sets["isDirectionFlipped"] is True
    assert inp.sets["participantBodies"] == [box]
    assert [b["name"] for b in result["modified_bodies"]] == ["Box"]


def test_sweep_along_edges() -> None:
    box = fake_box_body(4, 2, 1)
    s = Setup(box)
    edge = box.edges.item(0)
    s.run(h.feature_sweep, _sweep(s, path=[edge.entityToken]))
    path = s.features.paths[-1]
    assert path.curves is edge and path.isChain is True
    s.run(h.feature_sweep, _sweep(s, path=[edge.entityToken], path_chain=False))
    assert s.features.paths[-1].isChain is False
    edges = [box.edges.item(0).entityToken, box.edges.item(1).entityToken]
    s.run(h.feature_sweep, _sweep(s, path=edges))
    path = s.features.paths[-1]
    assert isinstance(path.curves, adsk.core.ObjectCollection) and path.curves.count == 2
    assert path.isChain is False


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"path": []}, "path"),
        ({"path": None}, "path"),
        ({"path": "x"}, "path"),
        ({"orientation": "normal"}, "orientation"),
        ({"twist_angle": True}, "twist_angle"),
        ({"path_extent": 0}, "path_extent"),
        ({"path_extent": 1.5}, "path_extent"),
        ({"path_extent": -0.2}, "path_extent"),
        ({"path_extent": "half"}, "path_extent"),
        ({"flip": "yes"}, "flip"),
        ({"path_chain": 1}, "path_chain"),
        ({"rail_scaling": "stretch"}, "rail_scaling"),
        ({"operation": "merge"}, "operation"),
        ({"profiles": []}, "profiles"),
        ({"participant_bodies": ["x"]}, "participant_bodies"),
    ],
)
def test_sweep_invalid_params_change_nothing(params: dict[str, Any], param: str) -> None:
    s = Setup()
    error = s.error(h.feature_sweep, _sweep(s, **params))
    assert error.code is ErrorCode.INVALID_PARAMS, error
    assert error.details["param"] == param
    assert s.sweeps.inputs == [] and s.features.paths == []


def test_sweep_path_of_wrong_kind_or_mixed_is_invalid() -> None:
    box = fake_box_body(4, 2, 1)
    s = Setup(box)
    face = box.faces.item(0)
    error = s.error(h.feature_sweep, _sweep(s, path=[face.entityToken]))
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == "path" and error.details["actual"].startswith("BRepFace")
    error = s.error(
        h.feature_sweep, _sweep(s, path=[s.path[0].entityToken, box.edges.item(0).entityToken])
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["reason"] == "mixed_path"
    assert s.sweeps.inputs == [] and s.features.paths == []


def test_sweep_unknown_path_ref_is_entity_not_found() -> None:
    s = Setup()
    error = s.error(h.feature_sweep, _sweep(s, path=["gone"]))
    assert error.code is ErrorCode.ENTITY_NOT_FOUND
    assert error.details["param"] == "path"
    assert s.sweeps.inputs == []


def test_sweep_broken_path_is_fusion_api_error() -> None:
    s = Setup()
    s.features.path_error = "curves are not connected"
    error = s.error(h.feature_sweep, _sweep(s))
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.details["param"] == "path"
    assert "not connected" in error.message
    assert s.sweeps.inputs == []


def test_sweep_error_feature_is_rolled_back() -> None:
    s = Setup()
    s.sweeps.health = HS.ErrorFeatureHealthState
    s.sweeps.message = "self-intersecting sweep"
    s.sweeps.will_create(FakeBody("Body1"))
    timeline = s.design.timeline.names()
    params = {"profiles": [s.profile], "path": [s.path[0].entityToken]}
    # The dispatcher context resolves sketch curves by token through the design.
    response = s.dispatch("feature.sweep", params)
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert response.error.details["feature_type"] == "SweepFeature"
    assert s.design.timeline.names() == timeline
    assert list(s.sweeps) == []
    assert s.root.bRepBodies.count == 0


def test_sweep_warning_is_kept() -> None:
    s = Setup()
    s.sweeps.health = HS.WarningFeatureHealthState
    s.sweeps.message = "tight bend"
    result = s.run(h.feature_sweep, _sweep(s))
    assert result["health"] == {"state": "warning", "message": "tight bend"}


# --- 6.3 sweep with a guide rail -----------------------------------------------------------


@pytest.mark.parametrize(
    ("scaling", "expected"),
    [
        (None, SCALING.SweepProfileScaleOption),
        ("scale", SCALING.SweepProfileScaleOption),
        ("stretch", SCALING.SweepProfileStretchOption),
        ("none", SCALING.SweepProfileNoScalingOption),
    ],
)
def test_sweep_guide_rail_and_scaling(scaling: str | None, expected: int) -> None:
    s = Setup()
    params = _sweep(s, guide_rail=[s.rail[0].entityToken], twist_angle=0, taper_angle=0)
    if scaling is not None:
        params["rail_scaling"] = scaling
    s.run(h.feature_sweep, params)
    path, rail = s.features.paths[-2:]
    inp = s.sweeps.last_input
    assert inp.create_args[1] is path
    assert inp.sets["guideRail"] is rail
    assert rail.curves is s.rail[0]
    assert inp.sets["profileScaling"] == expected
    assert "twistAngle" not in inp.sets and "taperAngle" not in inp.sets


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"twist_angle": 30}, "twist_angle"),
        ({"taper_angle": 2}, "taper_angle"),
        ({"taper_angle": "draft"}, "taper_angle"),
        ({"rail_scaling": "squash"}, "rail_scaling"),
    ],
)
def test_sweep_guide_rail_rules(params: dict[str, Any], param: str) -> None:
    s = Setup()
    error = s.error(h.feature_sweep, _sweep(s, guide_rail=[s.rail[0].entityToken], **params))
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.sweeps.inputs == [] and s.features.paths == []


def test_sweep_guide_rail_errors_name_the_rail() -> None:
    box = fake_box_body(4, 2, 1)
    s = Setup(box)
    error = s.error(h.feature_sweep, _sweep(s, guide_rail=[]))
    assert error.details["param"] == "guide_rail"
    error = s.error(h.feature_sweep, _sweep(s, guide_rail=[box.faces.item(0).entityToken]))
    assert error.details["param"] == "guide_rail"
    assert s.sweeps.inputs == []


# --- 7.1 loft: sections and end conditions -------------------------------------------------


class LoftSetup(Setup):
    """Adds a second rectangle sketch (``profile2``) and its first sketch point (``point``)."""

    def __init__(self, *bodies: Any) -> None:
        super().__init__(*bodies)
        self.sketch2 = make_sketch(self.design, "XY")
        polygon_curves(self.sketch2, SMALL)
        self.sketch2.profile_builder = auto_profiles
        self.profile2 = format_profile_ref(self.sketch2.entityToken, 20, 10)
        self.point = self.sketch2.sketchPoints.item(0)
        self.aliases[self.point.entityToken] = self.point


def _loft(s: LoftSetup, *sections: Any, **params: Any) -> dict[str, Any]:
    items = list(sections) or [{"entity": s.profile}, {"entity": s.profile2}]
    return {"sections": items, **params}


def test_loft_two_profiles_free_ends() -> None:
    s = LoftSetup()
    s.lofts.will_create(FakeBody("Body1"))
    result = s.run(h.feature_loft, _loft(s))
    inp = s.lofts.last_input
    assert inp.create_args == (OPS.NewBodyFeatureOperation,)
    sections = list(inp.attrs["loftSections"])
    assert [sec.create_args[0] for sec in sections] == [
        s.sketch.profiles.item(0),
        s.sketch2.profiles.item(0),
    ]
    for section in sections:
        assert section.calls == [("setFreeEndCondition", ())]
    assert inp.sets["isClosed"] is False
    assert inp.sets["isTangentEdgesMerged"] is True
    assert inp.attrs["centerLineOrRails"].calls == []
    assert result["feature"]["type"] == "LoftFeature"
    assert [b["name"] for b in result["created_bodies"]] == ["Body1"]


def test_loft_end_conditions_and_point_section() -> None:
    box = fake_box_body(1, 1, 1, name="Box")
    s2 = LoftSetup(box)
    face = box.faces.item(5)  # planar +Z face
    s2.run(
        h.feature_loft,
        _loft(
            s2,
            {"entity": face.entityToken, "end_condition": "direction", "angle": 10, "weight": 2},
            {"entity": s2.profile, "end_condition": "smooth", "weight": 0.5},
            {"entity": s2.point.entityToken, "end_condition": "point_tangent", "weight": 3},
        ),
    )
    first, middle, last = list(s2.lofts.last_input.attrs["loftSections"])
    assert first.create_args[0] is face
    angle, weight = first.call("setDirectionEndCondition")
    assert _real(angle) == pytest.approx(math.radians(10))
    assert _real(weight) == 2.0
    (weight,) = middle.call("setSmoothEndCondition")
    assert _real(weight) == 0.5
    assert last.create_args[0] is s2.point
    (weight,) = last.call("setPointTangentEndCondition")
    assert _real(weight) == 3.0


@pytest.mark.parametrize(
    ("condition", "setter", "has_weight"),
    [("tangent", "setTangentEndCondition", True), ("free", "setFreeEndCondition", False)],
)
def test_loft_simple_conditions(condition: str, setter: str, has_weight: bool) -> None:
    s = LoftSetup()
    s.run(
        h.feature_loft,
        _loft(s, {"entity": s.profile, "end_condition": condition}, {"entity": s.profile2}),
    )
    first = s.lofts.last_input.attrs["loftSections"].item(0)
    args = first.call(setter)
    assert len(args) == (1 if has_weight else 0)
    if has_weight:
        assert _real(args[0]) == 1.0


def test_loft_point_sharp_and_construction_point_and_vertex() -> None:
    box = fake_box_body(1, 1, 1)
    s = LoftSetup(box)
    vertex = box.vertices.item(7)
    s.run(
        h.feature_loft,
        _loft(
            s,
            {"entity": "origin:O", "end_condition": "point_sharp"},
            {"entity": s.profile},
            {"entity": vertex.entityToken},
        ),
    )
    first, _, last = list(s.lofts.last_input.attrs["loftSections"])
    assert first.create_args[0] is s.root.originConstructionPoint
    assert first.calls == [("setPointSharpEndCondition", ())]
    assert last.create_args[0] is vertex


def test_loft_inner_free_sections_keep_defaults() -> None:
    s = LoftSetup()
    s.run(
        h.feature_loft,
        _loft(
            s,
            {"entity": s.profile},
            {"entity": {"sketch": s.sketch2.entityToken, "all": True}},
            {"entity": s.profile},
        ),
    )
    first, middle, last = list(s.lofts.last_input.attrs["loftSections"])
    assert middle.calls == []
    assert first.calls == last.calls == [("setFreeEndCondition", ())]


def test_loft_spec_selecting_several_profiles_is_invalid() -> None:
    s = LoftSetup()
    circle_curve(s.sketch, (10, 10), 1.0)
    error = s.error(
        h.feature_loft,
        _loft(s, {"entity": {"sketch": s.sketch.entityToken, "all": True}}, {"entity": s.profile2}),
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == "sections[0].entity"
    assert error.details["reason"] == "several_profiles"
    assert s.lofts.inputs == []


def test_loft_section_setter_rejected_is_fusion_api_error() -> None:
    s = LoftSetup()
    original = type(s.lofts).__getattr__(s.lofts, "createInput")

    def create(*args: Any) -> Any:
        inp = original(*args)
        sections = inp.attrs["loftSections"]
        base_add = sections.add

        def add(entity: Any) -> Any:
            section = base_add(entity)
            section.returns["setFreeEndCondition"] = False
            return section

        sections.add = add
        return inp

    s.lofts.createInput = create
    error = s.error(h.feature_loft, _loft(s))
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.details["setter"] == "setFreeEndCondition"
    assert s.lofts.added == []


# --- 7.2 loft: rails, centerline, closed, merge, validation --------------------------------


def test_loft_with_rail() -> None:
    s = LoftSetup()
    s.run(h.feature_loft, _loft(s, rails=[s.rail[0].entityToken]))
    inp = s.lofts.last_input
    assert len(list(inp.attrs["loftSections"])) == 2
    assert inp.attrs["centerLineOrRails"].calls == [("addRail", (s.rail[0],))]


def test_loft_with_edge_centerline_closed_and_no_merge() -> None:
    box = fake_box_body(4, 2, 1, name="Box")
    s = LoftSetup(box)
    edge = box.edges.item(0)
    s.lofts.will_modify(box)
    result = s.run(
        h.feature_loft,
        _loft(
            s,
            {"entity": s.profile},
            {"entity": s.profile2},
            {"entity": box.faces.item(5).entityToken},
            centerline=edge.entityToken,
            closed=True,
            merge_tangent_edges=False,
            operation="join",
            participant_bodies=[box.entityToken],
        ),
    )
    inp = s.lofts.last_input
    assert inp.create_args == (OPS.JoinFeatureOperation,)
    assert inp.attrs["centerLineOrRails"].calls == [("addCenterLine", (edge,))]
    assert inp.sets["isClosed"] is True
    assert inp.sets["isTangentEdgesMerged"] is False
    assert inp.sets["participantBodies"] == [box]
    assert [b["name"] for b in result["modified_bodies"]] == ["Box"]


@pytest.mark.parametrize(
    ("build", "param", "reason"),
    [
        (lambda s: {"sections": [{"entity": s.profile}]}, "sections", "too_few_sections"),
        (lambda s: {"sections": []}, "sections", "too_few_sections"),
        (
            lambda s: _loft(s, rails=[s.rail[0].entityToken], centerline=s.path[0].entityToken),
            "centerline",
            "rails_and_centerline",
        ),
        (
            lambda s: _loft(
                s,
                {"entity": s.profile},
                {"entity": s.point.entityToken},
                {"entity": s.profile2},
            ),
            "sections[1].entity",
            "point_in_middle",
        ),
        (
            lambda s: _loft(
                s, {"entity": s.point.entityToken}, {"entity": s.profile2}, closed=True
            ),
            "closed",
            "closed_with_point",
        ),
    ],
)
def test_loft_validation_rules(build: Any, param: str, reason: str) -> None:
    s = LoftSetup()
    error = s.error(h.feature_loft, build(s))
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert error.details["reason"] == reason
    assert s.lofts.inputs == []


@pytest.mark.parametrize(
    ("first", "param"),
    [
        ({"end_condition": "point_sharp"}, "sections[0].end_condition"),
        ({"end_condition": "point_tangent"}, "sections[0].end_condition"),
        ({"end_condition": "spiky"}, "sections[0].end_condition"),
        ({"angle": 5}, "sections[0].angle"),
        ({"end_condition": "tangent", "angle": 5}, "sections[0].angle"),
        ({"weight": 2}, "sections[0].weight"),
        ({"end_condition": "tangent", "weight": 0}, "sections[0].weight"),
        ({"end_condition": "tangent", "weight": True}, "sections[0].weight"),
        ({"color": "red"}, "sections[0]"),
        ({"entity": None}, "sections[0].entity"),
        ({"entity": 5}, "sections[0].entity"),
    ],
)
def test_loft_section_rules(first: dict[str, Any], param: str) -> None:
    s = LoftSetup()
    section = {"entity": s.profile, **first}
    error = s.error(h.feature_loft, _loft(s, section, {"entity": s.profile2}))
    assert error.code is ErrorCode.INVALID_PARAMS, error
    assert error.details["param"] == param
    assert s.lofts.inputs == []


def test_loft_wrong_entity_kinds() -> None:
    box = fake_box_body(1, 1, 1)
    s = LoftSetup(box)
    error = s.error(h.feature_loft, _loft(s, {"entity": "origin:Z"}, {"entity": s.profile2}))
    assert error.details["param"] == "sections[0].entity"
    assert error.details["actual"] == "ConstructionAxis"
    error = s.error(h.feature_loft, _loft(s, {"entity": "origin:XY"}, {"entity": s.profile2}))
    assert error.details["actual"] == "ConstructionPlane"
    error = s.error(h.feature_loft, _loft(s, rails=[box.faces.item(0).entityToken]))
    assert error.details["param"] == "rails"
    error = s.error(h.feature_loft, _loft(s, centerline="origin:XY"))
    assert error.details["param"] == "centerline"
    error = s.error(h.feature_loft, _loft(s, {"entity": "gone"}, {"entity": s.profile2}))
    assert error.code is ErrorCode.ENTITY_NOT_FOUND
    assert error.details["param"] == "sections[0].entity"
    error = s.error(h.feature_loft, _loft(s, closed="yes"))
    assert error.details["param"] == "closed"
    error = s.error(h.feature_loft, _loft(s, participant_bodies=[box.entityToken]))
    assert error.details["param"] == "participant_bodies"
    assert s.lofts.inputs == []


def test_loft_error_feature_is_rolled_back() -> None:
    s = LoftSetup()
    s.lofts.health = HS.ErrorFeatureHealthState
    s.lofts.message = "sections cross"
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.loft", _loft(s))
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert "sections cross" in response.error.message
    assert s.design.timeline.names() == timeline
    assert list(s.lofts) == []


# --- 8.1 / 8.2 rib and web -----------------------------------------------------------------


class TouchMeasure:
    """``app.measureManager``: a curve is 0 cm from the bodies named in ``touching``, else
    5 cm (``target_body`` default, c15 3.1)."""

    def __init__(self, touching: tuple[str, ...]) -> None:
        self.touching = touching
        self.calls: list[tuple[Any, Any]] = []

    def measureMinimumDistance(self, a: Any, b: Any) -> Any:
        self.calls.append((a, b))
        return type("Result", (), {"value": 0.0 if b.name in self.touching else 5.0})()


class RibSetup:
    """A box body and an open sketch line on XZ inside it (``line``); with ``second`` also a
    body ``Other``. The curves touch the bodies named in ``touching``."""

    def __init__(
        self,
        omit: tuple[str, ...] = (),
        second: bool = False,
        touching: tuple[str, ...] = ("Box",),
    ) -> None:
        self.box = fake_box_body(4, 2, 3, name="Box")
        self.other = fake_box_body(1, 1, 1, name="Other")
        self.measure = TouchMeasure(touching)
        self.design = fake_feature_design(*((self.box, self.other) if second else (self.box,)))
        self.root = self.design.rootComponent
        if omit:
            install_features(self.root, self.design, omit=omit)
        self.sketch = make_sketch(self.design, "XZ")
        self.lines = polygon_curves(self.sketch, [(0, 0), (2, 2), (4, 0)], closed=False)
        self.circle = circle_curve(self.sketch, (2, 1), 0.5)
        self.aliases = {c.entityToken: c for c in (*self.lines, self.circle)}

    def ctx(self, params: dict[str, Any]) -> Context:
        ctx = make_ctx(self.design, params, self.aliases)
        ctx.app.measureManager = self.measure  # type: ignore[union-attr]
        return ctx

    def run(self, fn: Any, params: dict[str, Any]) -> Any:
        return fn(self.ctx(params), params)

    def error(self, fn: Any, params: dict[str, Any]) -> BridgeError:
        with pytest.raises(BridgeError) as info:
            fn(self.ctx(params), params)
        return info.value


RIB_CASES = [
    (h.feature_rib, "ribFeatures", "RibFeature", "Rib"),
    (h.feature_web, "webFeatures", "WebFeature", "Web"),
]


@pytest.mark.parametrize(("fn", "attr", "kind", "label"), RIB_CASES)
def test_rib_to_next_modifies_the_body(fn: Any, attr: str, kind: str, label: str) -> None:
    s = RibSetup()
    collection = getattr(s.root.features, attr)
    collection.will_modify(s.box)
    result = s.run(fn, {"curves": [s.lines[0].entityToken], "thickness": 3})
    inp = collection.last_input
    # Live API (c15 3.1): createInput(curves: list, thickness, targetBody).
    curves, thickness, target = inp.create_args
    assert curves == [s.lines[0]]
    assert _real(thickness) == pytest.approx(0.3)
    assert target is s.box  # the one body the curve touches
    assert inp.sets["isSymmetric"] is True
    assert inp.call("setToNextExtent") == ()
    assert inp.sets["isFlipped"] is False
    assert result["feature"]["type"] == kind
    assert [b["name"] for b in result["modified_bodies"]] == ["Box"]
    assert result["created_bodies"] == [] and result["removed_bodies"] == []


@pytest.mark.parametrize(("fn", "attr", "kind", "label"), RIB_CASES)
def test_rib_side_distance_flip_units(fn: Any, attr: str, kind: str, label: str) -> None:
    s = RibSetup()
    collection = getattr(s.root.features, attr)
    s.run(
        fn,
        {
            "curves": [c.entityToken for c in s.lines],
            "thickness": "wall",
            "thickness_location": "side1",
            "depth": {"type": "distance", "distance": 1},
            "flip": True,
            "units": "in",
        },
    )
    inp = collection.last_input
    curves, thickness, _target = inp.create_args
    assert curves == s.lines
    assert (thickness.kind, thickness.value) == ("string", "wall")
    assert inp.sets["isSymmetric"] is False
    (distance,) = inp.call("setDistanceExtent")
    assert _real(distance) == pytest.approx(2.54)
    assert inp.sets["isFlipped"] is True


@pytest.mark.parametrize(("fn", "attr", "kind", "label"), RIB_CASES)
def test_rib_side2_is_not_available(fn: Any, attr: str, kind: str, label: str) -> None:
    """The live input has no thickness-side flip (c15 3.1): side2 fails before ``add``."""
    s = RibSetup()
    collection = getattr(s.root.features, attr)
    error = s.error(
        fn, {"curves": [s.lines[0].entityToken], "thickness": 1, "thickness_location": "side2"}
    )
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert "isThicknessFlipped" in error.message and "not available" in error.message
    assert collection.added == []


@pytest.mark.parametrize(("fn", "attr", "kind", "label"), RIB_CASES)
def test_rib_explicit_target_body(fn: Any, attr: str, kind: str, label: str) -> None:
    s = RibSetup(second=True)
    collection = getattr(s.root.features, attr)
    s.run(
        fn,
        {"curves": [s.lines[0].entityToken], "thickness": 3, "target_body": s.other.entityToken},
    )
    assert collection.last_input.create_args[2] is s.other
    error = s.error(
        fn, {"curves": [s.lines[0].entityToken], "thickness": 3, "target_body": "origin:XY"}
    )
    assert error.code is ErrorCode.INVALID_PARAMS and error.details["param"] == "target_body"


@pytest.mark.parametrize(("fn", "attr", "kind", "label"), RIB_CASES)
@pytest.mark.parametrize(
    ("touching", "reason", "candidates"),
    [
        (("Box", "Other"), "ambiguous_target_body", ["Box", "Other"]),
        ((), "no_target_body", ["Box", "Other"]),
    ],
)
def test_rib_target_body_must_be_unique(
    fn: Any,
    attr: str,
    kind: str,
    label: str,
    touching: tuple[str, ...],
    reason: str,
    candidates: list[str],
) -> None:
    s = RibSetup(second=True, touching=touching)
    error = s.error(fn, {"curves": [s.lines[0].entityToken], "thickness": 3})
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == "target_body"
    assert error.details["reason"] == reason
    assert [c["name"] for c in error.details["candidates"]] == candidates
    assert getattr(s.root.features, attr).inputs == []


@pytest.mark.parametrize(("fn", "attr", "kind", "label"), RIB_CASES)
def test_rib_old_two_argument_call_is_refused(fn: Any, attr: str, kind: str, label: str) -> None:
    collection = getattr(RibSetup().root.features, attr)
    with pytest.raises(TypeError, match="takes 3 positional arguments"):
        collection.createInput([], None)
    inp = collection.createInput([], None, None)
    for retired in ("setDepthToNext", "setDepthDistance", "isDirectionFlipped"):
        assert not hasattr(inp, retired)


@pytest.mark.parametrize(("fn", "attr", "kind", "label"), RIB_CASES)
def test_rib_not_available_without_collection(fn: Any, attr: str, kind: str, label: str) -> None:
    s = RibSetup(omit=(attr,))
    timeline = s.design.timeline.names()
    error = s.error(fn, {"curves": [s.lines[0].entityToken], "thickness": 3})
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.message == f"{label} features are not available in this Fusion API version"
    assert error.details["feature_type"] == kind
    assert s.design.timeline.names() == timeline


@pytest.mark.parametrize(("fn", "attr", "kind", "label"), RIB_CASES)
def test_rib_missing_input_setter_is_not_available(
    fn: Any, attr: str, kind: str, label: str
) -> None:
    s = RibSetup()
    collection = getattr(s.root.features, attr)
    original = type(collection).__getattr__(collection, "createInput")

    def create(*args: Any) -> Any:
        inp = original(*args)
        inp.missing.add("setToNextExtent")
        return inp

    collection.createInput = create
    error = s.error(fn, {"curves": [s.lines[0].entityToken], "thickness": 3})
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert "setToNextExtent" in error.message and "not available" in error.message
    assert collection.added == []


@pytest.mark.parametrize(("fn", "attr", "kind", "label"), RIB_CASES)
@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"curves": []}, "curves"),
        ({"curves": None}, "curves"),
        ({"curves": ["origin:XY"]}, "curves"),
        ({"thickness": None}, "thickness"),
        ({"thickness": True}, "thickness"),
        ({"thickness_location": "middle"}, "thickness_location"),
        ({"depth": "next"}, "depth"),
        ({"depth": {"type": "blind"}}, "depth.type"),
        ({"depth": {"type": "distance"}}, "depth.distance"),
        ({"depth": {"type": "to_next", "distance": 3}}, "depth"),
        ({"flip": "no"}, "flip"),
    ],
)
def test_rib_invalid_params_change_nothing(
    fn: Any, attr: str, kind: str, label: str, params: dict[str, Any], param: str
) -> None:
    s = RibSetup()
    base: dict[str, Any] = {"curves": [s.lines[0].entityToken], "thickness": 3}
    base.update(params)
    error = s.error(fn, base)
    assert error.code is ErrorCode.INVALID_PARAMS, error
    assert error.details["param"] == param
    assert getattr(s.root.features, attr).inputs == []


@pytest.mark.parametrize(("fn", "attr", "kind", "label"), RIB_CASES)
def test_rib_closed_curve_is_invalid(fn: Any, attr: str, kind: str, label: str) -> None:
    s = RibSetup()
    error = s.error(fn, {"curves": [s.circle.entityToken], "thickness": 3})
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["reason"] == "closed_curve"
    assert getattr(s.root.features, attr).inputs == []


@pytest.mark.parametrize(("fn", "attr", "kind", "label"), RIB_CASES)
def test_rib_error_feature_is_rolled_back(fn: Any, attr: str, kind: str, label: str) -> None:
    s = RibSetup()
    collection = getattr(s.root.features, attr)
    collection.health = HS.ErrorFeatureHealthState
    collection.message = "rib does not meet the body"
    timeline = s.design.timeline.names()
    dispatcher = Dispatcher(TOKEN, context_factory=lambda p: s.ctx(p))
    method = "feature.rib" if kind == "RibFeature" else "feature.web"
    response = dispatcher.dispatch(
        Request("1", TOKEN, method, {"curves": [s.lines[0].entityToken], "thickness": 3})
    )
    assert response.ok is False and response.error is not None
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert response.error.details["feature_type"] == kind
    assert s.design.timeline.names() == timeline
    assert list(collection) == []

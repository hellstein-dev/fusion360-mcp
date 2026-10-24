"""``feature.extrude`` / ``feature.revolve`` handlers on feature fakes (c07 D10, D11; tasks
4.1-4.3, 5.1-5.2)."""

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
from tests.fakes.brep import (
    FakeBody,
    FakeEdge,
    P,
    add_face,
    circle_curve,
    fake_box_body,
    fake_cylinder_face,
)
from tests.fakes.design import FakeApp
from tests.fakes.fusion_features import fake_feature_design, make_ctx
from tests.fakes.fusion_sketch import auto_profiles, make_sketch, polygon_curves

adsk = fusion_addin.adsk
OPS = adsk.fusion.FeatureOperations
DIRS = adsk.fusion.ExtentDirections
THIN = adsk.fusion.ThinExtrudeWallLocation
HS = adsk.fusion.FeatureHealthStates
TOKEN = "t" * 32

RECT = [(0, 0), (4, 0), (4, 2), (0, 2)]  # cm: 40 x 20 mm


# --- helpers -------------------------------------------------------------------------------


def _real(value_input: Any) -> float:
    assert value_input.kind == "real", value_input
    return float(value_input.value)


def _error(fn: Any, ctx: Any, params: dict[str, Any]) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        fn(ctx, params)
    return info.value


class Setup:
    """Design with a 40 x 20 mm rectangle sketch on ``plane``; ``profile`` is its ref."""

    def __init__(self, *bodies: Any, plane: str = "XY", parametric: bool = True) -> None:
        self.design = fake_feature_design(*bodies, parametric=parametric)
        self.root = self.design.rootComponent
        self.sketch = make_sketch(self.design, plane)
        self.lines = polygon_curves(self.sketch, RECT)
        self.sketch.profile_builder = auto_profiles
        self.profile = format_profile_ref(self.sketch.entityToken, 20, 10)
        self.extrudes = self.root.features.extrudeFeatures
        self.revolves = self.root.features.revolveFeatures

    def ctx(self, params: dict[str, Any], refs: dict[str, Any] | None = None) -> Context:
        return make_ctx(self.design, params, refs)

    def run(self, fn: Any, params: dict[str, Any], refs: dict[str, Any] | None = None) -> Any:
        return fn(self.ctx(params, refs), params)

    def error(
        self, fn: Any, params: dict[str, Any], refs: dict[str, Any] | None = None
    ) -> BridgeError:
        return _error(fn, self.ctx(params, refs), params)

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        dispatcher = Dispatcher(TOKEN, context_factory=lambda p: Context(FakeApp(self.design), p))
        return dispatcher.dispatch(Request("1", TOKEN, method, params))


def _extrude(s: Setup, **params: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"profiles": [s.profile], "side1": {"type": "distance", "distance": 10}}
    base.update(params)
    return base


def _line_edge(token: str = "line-edge") -> FakeEdge:
    from tests.fakes.brep import line_curve

    return FakeEdge(line_curve(P(0, 0, 0), P(0, 0, 1)), 1.0, P(0, 0, 0.5), token=token)


def _circle_edge(token: str = "circle-edge") -> FakeEdge:
    from tests.fakes.brep import V

    return FakeEdge(circle_curve(P(0, 0, 0), V(0, 0, 1), 1.0), 2 * math.pi, P(1, 0, 0), token=token)


# --- registration --------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["feature.extrude", "feature.revolve"])
def test_handlers_are_registered_as_mutating(method: str) -> None:
    spec = REGISTRY.get(method)
    assert spec is not None and spec.mutating is True


# --- 4.1 extrude one_side ------------------------------------------------------------------


def test_extrude_one_side_distance_with_taper() -> None:
    s = Setup()
    new = FakeBody("Body1")
    s.extrudes.will_create(new)
    result = s.run(h.feature_extrude, _extrude(s, taper1=3))

    inp = s.extrudes.last_input
    profile, operation = inp.create_args
    assert profile is s.sketch.profiles.item(0)
    assert operation == OPS.NewBodyFeatureOperation
    extent, direction, taper = inp.call("setOneSideExtent")
    assert isinstance(extent, adsk.fusion.DistanceExtentDefinition)
    assert _real(extent.distance) == pytest.approx(1.0)
    assert direction == DIRS.PositiveExtentDirection
    assert _real(taper) == pytest.approx(math.radians(3))
    assert "participantBodies" not in inp.sets
    assert s.extrudes.added == [inp]

    assert result["feature"]["type"] == "ExtrudeFeature"
    assert result["feature"]["name"] == "Extrude1"
    assert result["health"] == {"state": "ok", "message": ""}
    assert [b["name"] for b in result["created_bodies"]] == ["Body1"]
    assert result["modified_bodies"] == [] and result["removed_bodies"] == []


def test_extrude_units_and_expressions() -> None:
    s = Setup()
    s.run(h.feature_extrude, _extrude(s, units="in", side1={"type": "distance", "distance": 1}))
    extent, _, taper = s.extrudes.last_input.call("setOneSideExtent")
    assert _real(extent.distance) == pytest.approx(2.54)
    assert _real(taper) == 0.0
    s.run(
        h.feature_extrude,
        _extrude(s, side1={"type": "distance", "distance": "height / 2"}, taper1="draft"),
    )
    extent, _, taper = s.extrudes.last_input.call("setOneSideExtent")
    assert (extent.distance.kind, extent.distance.value) == ("string", "height / 2")
    assert (taper.kind, taper.value) == ("string", "draft")


def test_extrude_through_all_cut_negative_with_participants() -> None:
    box = fake_box_body(4, 2, 1, name="Box")
    s = Setup(box)
    s.extrudes.will_modify(box)
    result = s.run(
        h.feature_extrude,
        _extrude(
            s,
            operation="cut",
            side1={"type": "through_all"},
            direction="negative",
            participant_bodies=[box.entityToken],
        ),
    )
    inp = s.extrudes.last_input
    assert inp.create_args[1] == OPS.CutFeatureOperation
    extent, direction, _ = inp.call("setOneSideExtent")
    assert isinstance(extent, adsk.fusion.ThroughAllExtentDefinition)
    assert direction == DIRS.NegativeExtentDirection
    assert inp.sets["participantBodies"] == [box]
    assert type(inp.sets["participantBodies"]) is list
    assert [b["name"] for b in result["modified_bodies"]] == ["Box"]


def test_extrude_to_entity_and_join_removes_body() -> None:
    box = fake_box_body(4, 2, 1, name="Box")
    other = fake_box_body(1, 1, 1, name="Other")
    s = Setup(box, other)
    top = box.faces.item(5)
    s.extrudes.will_modify(box).will_remove(other)
    result = s.run(
        h.feature_extrude,
        _extrude(
            s,
            operation="join",
            side1={"type": "to_entity", "entity": top.entityToken, "offset": 2, "chained": False},
        ),
    )
    extent, _, _ = s.extrudes.last_input.call("setOneSideExtent")
    assert isinstance(extent, adsk.fusion.ToEntityExtentDefinition)
    assert extent.entity is top
    assert extent.isChained is False
    assert _real(extent.offset) == pytest.approx(0.2)
    assert s.extrudes.last_input.create_args[1] == OPS.JoinFeatureOperation
    assert [b["name"] for b in result["modified_bodies"]] == ["Box"]
    assert result["removed_bodies"] == [other.entityToken]


def test_extrude_planar_face_profile() -> None:
    box = fake_box_body(4, 2, 1)
    s = Setup(box)
    top = box.faces.item(5)
    s.run(h.feature_extrude, _extrude(s, profiles=[top.entityToken], operation="join"))
    assert s.extrudes.last_input.create_args[0] is top


def test_extrude_several_profiles_give_collection() -> None:
    s = Setup()
    s.run(h.feature_extrude, _extrude(s, profiles=[{"sketch": s.sketch.entityToken, "all": True}]))
    assert s.extrudes.last_input.create_args[0] is s.sketch.profiles.item(0)
    circle = make_sketch(s.design, "XY")
    polygon_curves(circle, [(10, 10), (12, 10), (12, 12), (10, 12)])
    circle.profile_builder = auto_profiles
    s.run(
        h.feature_extrude,
        _extrude(s, profiles=[s.profile, format_profile_ref(circle.entityToken, 110, 110)]),
    )
    profiles = s.extrudes.last_input.create_args[0]
    assert isinstance(profiles, adsk.core.ObjectCollection)
    assert profiles.count == 2


def test_extrude_new_component_reports_component() -> None:
    from tests.fakes.brep import FakeComponent

    s = Setup()
    part = FakeComponent("Part")
    s.extrudes.will_create(FakeBody("Body1"), component=part)
    result = s.run(h.feature_extrude, _extrude(s, operation="new_component"))
    assert s.extrudes.last_input.create_args[1] == OPS.NewComponentFeatureOperation
    assert result["component"]["name"] == "Part"


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"operation": "merge"}, "operation"),
        ({"profiles": []}, "profiles"),
        ({"side1": None}, "side1"),
        ({"side1": {"type": "blind"}}, "side1.type"),
        ({"side1": {"type": "distance"}}, "side1.distance"),
        ({"direction": "up"}, "direction"),
        ({"taper1": True}, "taper1"),
        ({"mode": "both"}, "mode"),
        ({"participant_bodies": ["x"], "operation": "new_body"}, "participant_bodies"),
        ({"side2": {"type": "through_all"}}, "side2"),
        ({"taper2": 2}, "taper2"),
        ({"symmetric_full_length": True}, "symmetric_full_length"),
        ({"symmetric_full_length": "yes"}, "symmetric_full_length"),
        ({"start": {"type": "offset"}}, "start.offset"),
        ({"thin": {"location": "center"}}, "thin.thickness"),
        ({"thin": {"thickness": 2, "location": "middle"}}, "thin.location"),
        ({"thin": {"thickness": 2, "thickness2": 1}}, "thin.thickness2"),
        ({"thin": 2}, "thin"),
    ],
)
def test_extrude_invalid_params_change_nothing(params: dict[str, Any], param: str) -> None:
    s = Setup()
    error = s.error(h.feature_extrude, _extrude(s, **params))
    assert error.code is ErrorCode.INVALID_PARAMS, error
    assert error.details["param"] == param
    assert s.extrudes.inputs == [] and s.extrudes.added == []


def test_extrude_wrong_to_entity_kind_is_invalid() -> None:
    s = Setup()
    error = s.error(
        h.feature_extrude, _extrude(s, side1={"type": "to_entity", "entity": "origin:Z"})
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == "side1.entity"
    assert s.extrudes.inputs == []


def test_extrude_stale_profile_passes_details_profiles() -> None:
    s = Setup()
    stale = format_profile_ref(s.sketch.entityToken, 500, 500)
    error = s.error(h.feature_extrude, _extrude(s, profiles=[stale]))
    assert error.code is ErrorCode.ENTITY_NOT_FOUND
    assert len(error.details["profiles"]) == 1
    assert s.extrudes.inputs == []


def test_extrude_setter_rejected_is_fusion_api_error() -> None:
    s = Setup()

    def create(*args: Any) -> Any:
        inp = type(s.extrudes).__getattr__(s.extrudes, "createInput")(*args)
        inp.returns["setOneSideExtent"] = False
        return inp

    s.extrudes.createInput = create
    error = s.error(h.feature_extrude, _extrude(s))
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.details["setter"] == "setOneSideExtent"
    assert s.extrudes.added == []


def test_extrude_error_feature_is_rolled_back() -> None:
    s = Setup()
    s.extrudes.health = HS.ErrorFeatureHealthState
    s.extrudes.message = "profile is open"
    s.extrudes.will_create(FakeBody("Body1"))
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.extrude", _extrude(s))
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert "profile is open" in response.error.message
    assert response.error.details["health"]["state"] == "error"
    assert s.design.timeline.names() == timeline
    assert list(s.extrudes) == []
    assert s.root.bRepBodies.count == 0


def test_extrude_error_feature_in_direct_design_is_deleted() -> None:
    s = Setup(parametric=False)
    s.extrudes.health = HS.ErrorFeatureHealthState
    error = s.error(h.feature_extrude, _extrude(s))
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert list(s.extrudes) == []


def test_extrude_warning_is_kept() -> None:
    s = Setup()
    s.extrudes.health = HS.WarningFeatureHealthState
    s.extrudes.message = "thin wall"
    result = s.run(h.feature_extrude, _extrude(s))
    assert result["health"] == {"state": "warning", "message": "thin wall"}
    assert list(s.extrudes) != []


def test_extrude_add_runtime_error_is_rolled_back_by_dispatcher() -> None:
    s = Setup()
    s.extrudes.add_error = "Compute failed"
    response = s.dispatch("feature.extrude", _extrude(s))
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert "Compute failed" in response.error.message


# --- 4.2 extrude two_sides / symmetric -----------------------------------------------------


def test_extrude_two_sides() -> None:
    box = fake_box_body(4, 2, 1)
    s = Setup(box)
    top = box.faces.item(5)
    s.run(
        h.feature_extrude,
        _extrude(
            s,
            mode="two_sides",
            side1={"type": "distance", "distance": 5},
            side2={"type": "to_entity", "entity": top.entityToken},
            taper1=2,
            taper2=-4,
        ),
    )
    ext1, ext2, taper1, taper2 = s.extrudes.last_input.call("setTwoSidesExtent")
    assert isinstance(ext1, adsk.fusion.DistanceExtentDefinition)
    assert _real(ext1.distance) == pytest.approx(0.5)
    assert isinstance(ext2, adsk.fusion.ToEntityExtentDefinition)
    assert ext2.entity is top
    assert _real(taper1) == pytest.approx(math.radians(2))
    assert _real(taper2) == pytest.approx(math.radians(-4))
    assert not s.extrudes.last_input.called("setOneSideExtent")


def test_extrude_two_sides_needs_side2() -> None:
    s = Setup()
    error = s.error(h.feature_extrude, _extrude(s, mode="two_sides"))
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == "side2"
    assert s.extrudes.inputs == []


@pytest.mark.parametrize("full", [False, True])
def test_extrude_symmetric(full: bool) -> None:
    s = Setup()
    s.run(
        h.feature_extrude,
        _extrude(
            s,
            mode="symmetric",
            side1={"type": "distance", "distance": 30},
            symmetric_full_length=full,
            taper1=1,
        ),
    )
    distance, is_full, taper = s.extrudes.last_input.call("setSymmetricExtent")
    assert _real(distance) == pytest.approx(3.0)
    assert is_full is full
    assert _real(taper) == pytest.approx(math.radians(1))


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"side1": {"type": "through_all"}}, "side1.type"),
        ({"side1": {"type": "to_entity", "entity": "origin:XY"}}, "side1.type"),
        ({"direction": "negative"}, "direction"),
        ({"side2": {"type": "distance", "distance": 1}}, "side2"),
    ],
)
def test_extrude_symmetric_invalid(params: dict[str, Any], param: str) -> None:
    s = Setup()
    error = s.error(h.feature_extrude, _extrude(s, mode="symmetric", **params))
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.extrudes.inputs == []


# --- 4.3 extrude start and thin ------------------------------------------------------------


def test_extrude_thin_with_start_offset() -> None:
    s = Setup()
    s.run(
        h.feature_extrude,
        _extrude(
            s, thin={"thickness": 2, "location": "center"}, start={"type": "offset", "offset": 5}
        ),
    )
    inp = s.extrudes.last_input
    location, thickness = inp.call("setThinExtrude")
    assert location == THIN.Center
    assert _real(thickness) == pytest.approx(0.2)
    start = inp.sets["startExtent"]
    assert isinstance(start, adsk.fusion.OffsetStartDefinition)
    assert _real(start.offset) == pytest.approx(0.5)


def test_extrude_start_entity_and_profile_plane() -> None:
    box = fake_box_body(4, 2, 1)
    s = Setup(box)
    top = box.faces.item(5)
    s.run(h.feature_extrude, _extrude(s, start={"type": "entity", "entity": top.entityToken}))
    start = s.extrudes.last_input.sets["startExtent"]
    assert isinstance(start, adsk.fusion.EntityStartDefinition)
    assert start.entity is top
    s.run(h.feature_extrude, _extrude(s, start={"type": "profile_plane"}))
    start = s.extrudes.last_input.sets["startExtent"]
    assert isinstance(start, adsk.fusion.ProfilePlaneStartDefinition)
    s.run(h.feature_extrude, _extrude(s))
    assert "startExtent" not in s.extrudes.last_input.sets


def test_extrude_thin_default_location_and_second_thickness() -> None:
    s = Setup()
    s.run(h.feature_extrude, _extrude(s, thin={"thickness": "wall"}))
    location, thickness = s.extrudes.last_input.call("setThinExtrude")
    assert location == THIN.Side1
    assert (thickness.kind, thickness.value) == ("string", "wall")
    s.run(
        h.feature_extrude,
        _extrude(
            s,
            mode="two_sides",
            side2={"type": "distance", "distance": 4},
            thin={"thickness": 1, "location": "side2", "thickness2": 3},
        ),
    )
    loc1, t1, loc2, t2 = s.extrudes.last_input.call("setThinExtrude")
    assert loc1 == loc2 == THIN.Side2
    assert _real(t1) == pytest.approx(0.1)
    assert _real(t2) == pytest.approx(0.3)


def test_extrude_sets_extent_before_thin_and_participants_last() -> None:
    box = fake_box_body(4, 2, 1)
    s = Setup(box)
    s.run(
        h.feature_extrude,
        _extrude(
            s,
            operation="join",
            thin={"thickness": 1},
            start={"type": "profile_plane"},
            participant_bodies=[box.entityToken],
        ),
    )
    order = [name for _, name, _ in s.extrudes.last_input.log]
    assert order == ["setOneSideExtent", "startExtent", "setThinExtrude", "participantBodies"]


# --- 5.1 revolve: axis and angle extents ---------------------------------------------------


def test_revolve_full_around_origin_axis() -> None:
    s = Setup(plane="XZ")
    s.revolves.will_create(FakeBody("Body1"))
    result = s.run(h.feature_revolve, {"profiles": [s.profile], "axis": "origin:Z"})
    inp = s.revolves.last_input
    profile, axis, operation = inp.create_args
    assert profile is s.sketch.profiles.item(0)
    assert axis is s.root.zConstructionAxis
    assert operation == OPS.NewBodyFeatureOperation
    symmetric, angle = inp.call("setAngleExtent")
    assert symmetric is False
    assert (angle.kind, angle.value) == ("string", "360 deg")
    assert result["feature"]["type"] == "RevolveFeature"
    assert [b["name"] for b in result["created_bodies"]] == ["Body1"]


def test_revolve_symmetric_angle() -> None:
    s = Setup(plane="XZ")
    s.run(
        h.feature_revolve,
        {
            "profiles": [s.profile],
            "axis": "origin:Z",
            "extent": {"type": "angle", "angle": 90, "symmetric": True},
        },
    )
    symmetric, angle = s.revolves.last_input.call("setAngleExtent")
    assert symmetric is True
    assert _real(angle) == pytest.approx(math.pi / 2)


def test_revolve_angle_default_and_two_angles() -> None:
    s = Setup(plane="XZ")
    s.run(
        h.feature_revolve,
        {"profiles": [s.profile], "axis": "origin:Z", "extent": {"type": "angle"}},
    )
    symmetric, angle = s.revolves.last_input.call("setAngleExtent")
    assert symmetric is False and _real(angle) == pytest.approx(2 * math.pi)
    s.run(
        h.feature_revolve,
        {
            "profiles": [s.profile],
            "axis": "origin:Z",
            "extent": {"type": "two_angles", "angle1": 30, "angle2": "a2"},
        },
    )
    a1, a2 = s.revolves.last_input.call("setTwoSideAngleExtent")
    assert _real(a1) == pytest.approx(math.radians(30))
    assert (a2.kind, a2.value) == ("string", "a2")


def test_revolve_around_sketch_line_and_linear_edge_with_cut() -> None:
    box = fake_box_body(4, 2, 1, name="Box")
    s = Setup(box)
    line = s.lines[0]
    s.run(
        h.feature_revolve,
        {"profiles": [s.profile], "axis": line.entityToken, "operation": "cut"},
        refs={line.entityToken: line},
    )
    assert s.revolves.last_input.create_args[1] is line
    assert s.revolves.last_input.create_args[2] == OPS.CutFeatureOperation
    edge = _line_edge()
    s.run(
        h.feature_revolve,
        {
            "profiles": [s.profile],
            "axis": "line-edge",
            "operation": "intersect",
            "participant_bodies": [box.entityToken],
        },
        refs={"line-edge": edge},
    )
    inp = s.revolves.last_input
    assert inp.create_args[1] is edge
    assert inp.sets["participantBodies"] == [box]


def test_revolve_invalid_axis_kinds() -> None:
    s = Setup()
    error = s.error(
        h.feature_revolve,
        {"profiles": [s.profile], "axis": "circle-edge"},
        refs={"circle-edge": _circle_edge()},
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details == {"param": "axis", "expected": "linear", "got": "BRepEdge (circle)"}
    error = s.error(h.feature_revolve, {"profiles": [s.profile], "axis": "origin:XY"})
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["got"] == "ConstructionPlane"
    cylinder = add_face(fake_box_body(1, 1, 1), fake_cylinder_face(1, 2, token="cyl"))
    error = s.error(h.feature_revolve, {"profiles": [s.profile], "axis": "cyl"}, {"cyl": cylinder})
    assert error.details["got"] == "BRepFace (cylinder)"
    assert s.revolves.inputs == []


def test_revolve_missing_axis_and_unknown_ref() -> None:
    s = Setup()
    error = s.error(h.feature_revolve, {"profiles": [s.profile]})
    assert error.code is ErrorCode.INVALID_PARAMS and error.details["param"] == "axis"
    error = s.error(h.feature_revolve, {"profiles": [s.profile], "axis": "gone"})
    assert error.code is ErrorCode.ENTITY_NOT_FOUND and error.details["param"] == "axis"
    assert s.revolves.inputs == []


@pytest.mark.parametrize(
    ("extent", "param"),
    [
        ("full", "extent"),
        ({"type": "spin"}, "extent.type"),
        ({"type": "two_angles", "angle1": 10}, "extent.angle2"),
        ({"type": "angle", "symmetric": "yes"}, "extent.symmetric"),
        ({"type": "angle", "angle": True}, "extent.angle"),
        ({"type": "to_entity"}, "extent.entity"),
        ({"type": "to_entity", "entity": "origin:XY", "direction": "up"}, "extent.direction"),
        ({"type": "to_entity", "entity": "origin:Z"}, "extent.entity"),
    ],
)
def test_revolve_invalid_extent(extent: Any, param: str) -> None:
    s = Setup()
    error = s.error(
        h.feature_revolve, {"profiles": [s.profile], "axis": "origin:Y", "extent": extent}
    )
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.revolves.inputs == []


def test_revolve_participants_with_new_body_are_invalid() -> None:
    box = fake_box_body(1, 1, 1)
    s = Setup(box)
    error = s.error(
        h.feature_revolve,
        {"profiles": [s.profile], "axis": "origin:Y", "participant_bodies": [box.entityToken]},
    )
    assert error.details["param"] == "participant_bodies"
    assert s.revolves.inputs == []


def test_revolve_error_feature_is_rolled_back() -> None:
    s = Setup()
    s.revolves.health = HS.ErrorFeatureHealthState
    s.revolves.message = "profile crosses the axis"
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.revolve", {"profiles": [s.profile], "axis": "origin:Y"})
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert response.error.details["feature_type"] == "RevolveFeature"
    assert s.design.timeline.names() == timeline
    assert list(s.revolves) == []


# --- 5.2 revolve to_entity -----------------------------------------------------------------


@pytest.mark.parametrize(("direction", "sign"), [("positive", 1.0), ("negative", -1.0)])
def test_revolve_to_entity_direction_from_sketch_normal(direction: str, sign: float) -> None:
    box = fake_box_body(4, 2, 1)
    s = Setup(box)  # sketch on XY: normal +Z
    face = box.faces.item(5)
    s.run(
        h.feature_revolve,
        {
            "profiles": [s.profile],
            "axis": "origin:Y",
            "extent": {"type": "to_entity", "entity": face.entityToken, "direction": direction},
        },
    )
    entity, hint = s.revolves.last_input.call("setOneSideToExtent")
    assert entity is face
    assert hint.asArray() == pytest.approx([0.0, 0.0, sign])
    assert not s.revolves.last_input.called("setAngleExtent")


def test_revolve_to_entity_default_direction_on_xz_sketch() -> None:
    s = Setup(plane="XZ")
    s.run(
        h.feature_revolve,
        {
            "profiles": [s.profile],
            "axis": "origin:Z",
            "extent": {"type": "to_entity", "entity": "origin:YZ"},
        },
    )
    entity, hint = s.revolves.last_input.call("setOneSideToExtent")
    assert entity is s.root.yZConstructionPlane
    normal = s.sketch.xDirection.crossProduct(s.sketch.yDirection)
    normal.normalize()
    assert hint.asArray() == pytest.approx(normal.asArray())


def test_revolve_to_entity_from_planar_face_profile() -> None:
    box = fake_box_body(4, 2, 1)
    s = Setup(box)
    top = box.faces.item(5)
    s.run(
        h.feature_revolve,
        {
            "profiles": [top.entityToken],
            "axis": "origin:X",
            "operation": "join",
            "extent": {"type": "to_entity", "entity": "origin:XZ", "direction": "negative"},
        },
    )
    _, hint = s.revolves.last_input.call("setOneSideToExtent")
    expected = [-c for c in top.geometry.normal.asArray()]
    assert hint.asArray() == pytest.approx(expected)


def test_revolve_to_entity_not_available() -> None:
    s = Setup()

    def create(*args: Any) -> Any:
        inp = type(s.revolves).__getattr__(s.revolves, "createInput")(*args)
        inp.missing.add("setOneSideToExtent")
        return inp

    s.revolves.createInput = create
    error = s.error(
        h.feature_revolve,
        {
            "profiles": [s.profile],
            "axis": "origin:Y",
            "extent": {"type": "to_entity", "entity": "origin:YZ"},
        },
    )
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert "not available" in error.message
    assert s.revolves.added == []

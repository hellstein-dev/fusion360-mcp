"""``pattern.rectangular`` / ``circular`` / ``path`` / ``mirror`` handlers on feature fakes
(c08 D3-D7; tasks 3.1-3.6)."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import pattern as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.brep import (
    FakeBody,
    FakeComponent,
    FakeFeature,
    FakeOccurrence,
    add_occurrence,
    fake_box_body,
)
from tests.fakes.fusion_features import cylinder_face, fake_feature_design, make_ctx
from tests.fakes.fusion_sketch import make_sketch, polygon_curves

adsk = fusion_addin.adsk
PDT = adsk.fusion.PatternDistanceType
PCO = adsk.fusion.PatternComputeOptions
HS = adsk.fusion.FeatureHealthStates
TOKEN = "t" * 32


def real(value_input: Any) -> float:
    assert value_input.kind == "real", value_input
    return float(value_input.value)


class Setup:
    """Box ``box`` (40 x 20 x 10 mm) with an extrude ``boss``, a second body ``other``, a
    cylindrical face ``cyl``, an open sketch path ``path`` and an occurrence ``occ``."""

    def __init__(self, parametric: bool = True) -> None:
        self.box = fake_box_body(4, 2, 1, name="Box")
        self.other = fake_box_body(1, 1, 1, name="Other")
        self.design = fake_feature_design(self.box, self.other, parametric=parametric)
        self.root = self.design.rootComponent
        self.features = self.root.features
        faces = {f.entityToken.rsplit("face", 1)[1]: f for f in self.box.faces}
        edges = {e.entityToken.rsplit("-e", 1)[1]: e for e in self.box.edges}
        self.top, self.px = faces["+z"], faces["+x"]
        self.x_edge = edges["000100"]
        self.cyl = cylinder_face(0.5, body=self.box)
        boss: Any = FakeFeature("ExtrudeFeature", "Boss", component=self.root)
        self.boss = boss
        self.part = FakeComponent("part")
        self.occ = add_occurrence(self.root, FakeOccurrence(self.part, "Part:1"), self.root)
        sketch = make_sketch(self.design, "XZ")
        self.path = polygon_curves(sketch, [(0, 0), (0, 5), (3, 8)], closed=False)
        self.aliases: dict[str, Any] = {
            "box": self.box,
            "other": self.other,
            "top": self.top,
            "px": self.px,
            "xe": self.x_edge,
            "cyl": self.cyl,
            "boss": self.boss,
            "occ": self.occ,
            "c1": self.path[0],
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


def copies(n: int, prefix: str = "Box") -> list[FakeBody]:
    return [fake_box_body(4, 2, 1, name=f"{prefix} ({i + 2})") for i in range(n)]


def invalid(s: Setup, fn: Any, params: dict[str, Any], param: str) -> BridgeError:
    err = s.error(fn, params)
    assert err.code is ErrorCode.INVALID_PARAMS, err
    assert err.details.get("param") == param, err.details
    return err


@pytest.mark.parametrize(
    "method", ["pattern.rectangular", "pattern.circular", "pattern.path", "pattern.mirror"]
)
def test_handlers_are_registered_as_mutating(method: str) -> None:
    spec = REGISTRY.get(method)
    assert spec is not None and spec.mutating is True


# --- 3.1 rectangular: one direction --------------------------------------------------------


def test_rectangular_one_direction_of_bodies() -> None:
    s = Setup()
    rects = s.features.rectangularPatternFeatures
    rects.will_create(*copies(2))
    result = s.run(
        h.pattern_rectangular,
        {"entities": ["box"], "direction1": "origin:X", "quantity1": 3, "distance1": 30},
    )
    inp = rects.last_input
    entities, direction, quantity, distance, distance_type = inp.create_args
    assert list(entities) == [s.box]
    assert direction is s.root.xConstructionAxis
    assert real(quantity) == 3.0
    assert real(distance) == pytest.approx(3.0)
    assert distance_type == PDT.SpacingPatternDistanceType
    assert inp.sets["isSymmetricInDirectionOne"] is False
    assert "patternComputeOption" not in inp.sets  # bodies have no compute option
    assert not inp.called("setDirectionTwo")
    assert result["feature"]["type"] == "RectangularPatternFeature"
    assert [b["name"] for b in result["created_bodies"]] == ["Box (2)", "Box (3)"]
    assert result["modified_bodies"] == [] and result["removed_bodies"] == []
    assert "created_occurrences" not in result


def test_rectangular_extent_symmetric_edge_and_feature_compute() -> None:
    s = Setup()
    s.run(
        h.pattern_rectangular,
        {
            "entities": ["boss"],
            "direction1": "xe",
            "quantity1": "n",
            "distance1": "w * 2",
            "distance_type": "extent",
            "symmetric1": True,
            "compute": "identical",
            "units": "cm",
        },
    )
    inp = s.features.rectangularPatternFeatures.last_input
    _, direction, quantity, distance, distance_type = inp.create_args
    assert direction.entityToken == s.x_edge.entityToken
    assert (quantity.kind, quantity.value) == ("string", "n")
    assert (distance.kind, distance.value) == ("string", "w * 2")
    assert distance_type == PDT.ExtentPatternDistanceType
    assert inp.sets["isSymmetricInDirectionOne"] is True
    assert inp.sets["patternComputeOption"] == PCO.IdenticalPatternCompute


def test_rectangular_negative_distance_and_default_compute() -> None:
    s = Setup()
    s.run(
        h.pattern_rectangular,
        {"entities": ["boss"], "direction1": "origin:Y", "distance1": -10},
    )
    inp = s.features.rectangularPatternFeatures.last_input
    assert real(inp.create_args[2]) == 2.0
    assert real(inp.create_args[3]) == pytest.approx(-1.0)
    assert inp.sets["patternComputeOption"] == PCO.AdjustPatternCompute


def test_rectangular_face_pattern_reports_the_body() -> None:
    s = Setup()
    s.features.rectangularPatternFeatures.will_modify(s.box)
    result = s.run(
        h.pattern_rectangular,
        {"entities": ["cyl"], "direction1": "origin:X", "quantity1": 4, "distance1": 5},
    )
    assert [b["name"] for b in result["modified_bodies"]] == ["Box"]


@pytest.mark.parametrize(
    ("change", "param"),
    [
        ({"quantity1": 0}, "quantity1"),
        ({"quantity1": 2.5}, "quantity1"),
        ({"quantity1": True}, "quantity1"),
        ({"distance1": None}, "distance1"),
        ({"direction1": "top"}, "direction1"),
        ({"distance_type": "pitch"}, "distance_type"),
        ({"compute": "fast"}, "compute"),
        ({"entities": []}, "entities"),
        ({"symmetric1": "yes"}, "symmetric1"),
    ],
)
def test_rectangular_validation_changes_nothing(change: dict[str, Any], param: str) -> None:
    s = Setup()
    params = {"entities": ["box"], "direction1": "origin:X", "quantity1": 3, "distance1": 30}
    params.update(change)
    count = s.design.timeline.count
    invalid(s, h.pattern_rectangular, params, param)
    assert s.features.rectangularPatternFeatures.inputs == []
    assert s.design.timeline.count == count


def test_mixed_input_lists_kinds() -> None:
    s = Setup()
    err = invalid(
        s,
        h.pattern_rectangular,
        {"entities": ["box", "boss"], "direction1": "origin:X", "distance1": 30},
        "entities",
    )
    assert set(err.details["kinds"]) == {"body", "feature"}


# --- 3.2 rectangular: second direction -----------------------------------------------------


def test_rectangular_bodies_3_by_2() -> None:
    s = Setup()
    rects = s.features.rectangularPatternFeatures
    rects.will_create(*copies(5))
    result = s.run(
        h.pattern_rectangular,
        {
            "entities": ["box"],
            "direction1": "origin:X",
            "quantity1": 3,
            "distance1": 30,
            "direction2": "origin:Y",
            "quantity2": 2,
            "distance2": 25,
            "symmetric2": True,
        },
    )
    inp = rects.last_input
    assert real(inp.create_args[2]) == 3.0
    assert real(inp.create_args[3]) == pytest.approx(3.0)
    assert inp.create_args[4] == PDT.SpacingPatternDistanceType
    direction2, quantity2, distance2 = inp.call("setDirectionTwo")
    assert direction2 is s.root.yConstructionAxis
    assert real(quantity2) == 2.0
    assert real(distance2) == pytest.approx(2.5)
    assert inp.sets["isSymmetricInDirectionTwo"] is True
    assert len(result["created_bodies"]) == 5


@pytest.mark.parametrize(
    ("change", "param"),
    [
        ({"direction2": "origin:Y", "quantity2": 2}, "distance2"),
        ({"quantity2": 2, "distance2": 5}, "quantity2"),
        ({"distance2": 5}, "distance2"),
        ({"symmetric2": True}, "symmetric2"),
        ({"direction2": "px", "quantity2": 2, "distance2": 5}, "direction2"),
        ({"direction2": "origin:Y", "quantity2": 0, "distance2": 5}, "quantity2"),
    ],
)
def test_second_direction_validation(change: dict[str, Any], param: str) -> None:
    s = Setup()
    params = {"entities": ["box"], "direction1": "origin:X", "quantity1": 3, "distance1": 30}
    params.update(change)
    invalid(s, h.pattern_rectangular, params, param)
    assert s.features.rectangularPatternFeatures.inputs == []


def test_second_direction_with_one_instance_needs_no_distance() -> None:
    s = Setup()
    s.run(
        h.pattern_rectangular,
        {"entities": ["box"], "direction1": "origin:X", "distance1": 30, "direction2": "origin:Y"},
    )
    _, quantity2, distance2 = s.features.rectangularPatternFeatures.last_input.call(
        "setDirectionTwo"
    )
    assert real(quantity2) == 1.0 and real(distance2) == 0.0


def test_setter_refused_and_error_health_roll_back() -> None:
    s = Setup()
    s.features.rectangularPatternFeatures.on_input = lambda inp: inp.returns.update(
        setDirectionTwo=False
    )
    params = {
        "entities": ["box"],
        "direction1": "origin:X",
        "distance1": 30,
        "direction2": "origin:Y",
        "quantity2": 2,
        "distance2": 5,
    }
    err = s.error(h.pattern_rectangular, params)
    assert err.code is ErrorCode.FUSION_API_ERROR
    s2 = Setup()
    s2.features.rectangularPatternFeatures.health = HS.ErrorFeatureHealthState
    count = s2.design.timeline.count
    response = s2.dispatch(
        "pattern.rectangular", {"entities": ["box"], "direction1": "origin:X", "distance1": 30}
    )
    assert response.error is not None and response.error.code == ErrorCode.FUSION_API_ERROR.value
    assert s2.design.timeline.count == count


def test_direct_design_deletes_failed_pattern() -> None:
    s = Setup(parametric=False)
    rects = s.features.rectangularPatternFeatures
    rects.health = HS.ErrorFeatureHealthState
    s.error(h.pattern_rectangular, {"entities": ["box"], "direction1": "origin:X", "distance1": 3})
    assert list(rects) == []


# --- 3.3 circular --------------------------------------------------------------------------


def test_circular_six_around_z() -> None:
    s = Setup()
    s.run(h.pattern_circular, {"entities": ["boss"], "axis": "origin:Z", "quantity": 6})
    inp = s.features.circularPatternFeatures.last_input
    entities, axis = inp.create_args
    assert list(entities) == [s.boss]
    assert axis is s.root.zConstructionAxis
    assert real(inp.sets["quantity"]) == 6.0
    assert real(inp.sets["totalAngle"]) == pytest.approx(2 * math.pi)
    assert inp.sets["isSymmetric"] is False
    assert inp.sets["patternComputeOption"] == PCO.AdjustPatternCompute


def test_circular_cylinder_axis_angle_symmetric() -> None:
    s = Setup()
    s.run(
        h.pattern_circular,
        {
            "entities": ["other"],
            "axis": "cyl",
            "quantity": "k",
            "total_angle": 90,
            "symmetric": True,
        },
    )
    inp = s.features.circularPatternFeatures.last_input
    assert inp.create_args[1] is s.cyl
    assert inp.sets["quantity"].value == "k"
    assert real(inp.sets["totalAngle"]) == pytest.approx(math.pi / 2)
    assert inp.sets["isSymmetric"] is True


@pytest.mark.parametrize(
    ("change", "param"),
    [
        ({"quantity": None}, "quantity"),
        ({"quantity": 0}, "quantity"),
        ({"axis": "top"}, "axis"),
        ({"total_angle": True}, "total_angle"),
    ],
)
def test_circular_validation(change: dict[str, Any], param: str) -> None:
    s = Setup()
    params = {"entities": ["boss"], "axis": "origin:Z", "quantity": 6}
    params.update(change)
    invalid(s, h.pattern_circular, params, param)
    assert s.features.circularPatternFeatures.inputs == []


# --- 3.4 path ------------------------------------------------------------------------------


def test_path_pattern_along_a_chained_path() -> None:
    s = Setup()
    s.run(
        h.pattern_path,
        {
            "entities": ["box"],
            "path": ["c1"],
            "quantity": 4,
            "distance": 100,
            "orientation_along_path": True,
        },
    )
    path = s.features.paths[-1]
    assert list(path.curves) == s.path  # chained through the connected line
    inp = s.features.pathPatternFeatures.last_input
    entities, given_path, quantity, distance, distance_type = inp.create_args
    assert list(entities) == [s.box]
    assert given_path is path
    assert real(quantity) == 4.0
    assert real(distance) == pytest.approx(10.0)
    assert distance_type == PDT.ExtentPatternDistanceType
    assert inp.sets["isOrientationAlongPath"] is True
    assert inp.sets["isFlipDirection"] is False
    assert inp.sets["isSymmetric"] is False
    assert "startPoint" not in inp.sets


def test_path_pattern_start_flip_spacing_no_chain() -> None:
    s = Setup()
    s.run(
        h.pattern_path,
        {
            "entities": ["boss"],
            "path": ["c1"],
            "path_chain": False,
            "quantity": 3,
            "distance": 10,
            "distance_type": "spacing",
            "start": 0.25,
            "flip": True,
            "symmetric": True,
            "compute": "optimized",
        },
    )
    assert s.features.paths[-1].curves is s.path[0]
    inp = s.features.pathPatternFeatures.last_input
    assert inp.create_args[4] == PDT.SpacingPatternDistanceType
    assert real(inp.sets["startPoint"]) == 0.25
    assert inp.sets["isFlipDirection"] is True
    assert inp.sets["isSymmetric"] is True
    assert inp.sets["patternComputeOption"] == PCO.OptimizedPatternCompute


@pytest.mark.parametrize(
    ("change", "param"),
    [
        ({"start": 1.5}, "start"),
        ({"start": "0.5"}, "start"),
        ({"distance": None}, "distance"),
        ({"path": []}, "path"),
        ({"path": ["top"]}, "path"),
        ({"quantity": -1}, "quantity"),
    ],
)
def test_path_validation(change: dict[str, Any], param: str) -> None:
    s = Setup()
    params = {"entities": ["box"], "path": ["c1"], "quantity": 4, "distance": 100}
    params.update(change)
    invalid(s, h.pattern_path, params, param)
    assert s.features.pathPatternFeatures.inputs == [] and s.features.paths == []


def test_path_start_not_available() -> None:
    s = Setup()
    s.features.pathPatternFeatures.on_input = lambda inp: inp.missing.add("startPoint")
    err = s.error(
        h.pattern_path,
        {"entities": ["box"], "path": ["c1"], "quantity": 4, "distance": 100, "start": 0.5},
    )
    assert err.code is ErrorCode.FUSION_API_ERROR and "not available" in err.message


# --- 3.5 mirror ----------------------------------------------------------------------------


def test_mirror_body_and_combine() -> None:
    s = Setup()
    s.features.mirrorFeatures.will_modify(s.box)
    result = s.run(h.pattern_mirror, {"entities": ["box"], "plane": "origin:YZ", "combine": True})
    inp = s.features.mirrorFeatures.last_input
    entities, plane = inp.create_args
    assert list(entities) == [s.box]
    assert plane is s.root.yZConstructionPlane
    assert inp.sets["isCombine"] is True
    assert "patternComputeOption" not in inp.sets
    assert [b["name"] for b in result["modified_bodies"]] == ["Box"]


def test_mirror_feature_across_face_without_combine() -> None:
    s = Setup()
    s.run(h.pattern_mirror, {"entities": ["boss"], "plane": "px", "compute": "identical"})
    inp = s.features.mirrorFeatures.last_input
    assert inp.create_args[1] is s.px
    assert "isCombine" not in inp.sets
    assert inp.sets["patternComputeOption"] == PCO.IdenticalPatternCompute


def test_mirror_body_without_combine_creates_copy() -> None:
    s = Setup()
    s.features.mirrorFeatures.will_create(FakeBody("Box (mirror)"))
    result = s.run(h.pattern_mirror, {"entities": ["box"], "plane": "origin:XZ"})
    assert s.features.mirrorFeatures.last_input.sets["isCombine"] is False
    assert [b["name"] for b in result["created_bodies"]] == ["Box (mirror)"]
    assert result["modified_bodies"] == []


@pytest.mark.parametrize(
    ("change", "param"),
    [
        ({"entities": ["boss"], "combine": True}, "combine"),
        ({"entities": ["top"], "combine": True}, "combine"),
        ({"plane": "xe"}, "plane"),
        ({"plane": "cyl"}, "plane"),
        ({"plane": None}, "plane"),
    ],
)
def test_mirror_validation(change: dict[str, Any], param: str) -> None:
    s = Setup()
    params = {"entities": ["box"], "plane": "origin:YZ"}
    params.update(change)
    invalid(s, h.pattern_mirror, params, param)
    assert s.features.mirrorFeatures.inputs == []


# --- 3.6 occurrence patterns ---------------------------------------------------------------


def test_rectangular_pattern_of_occurrences_reports_created_occurrences() -> None:
    s = Setup()
    rects = s.features.rectangularPatternFeatures
    new = [FakeOccurrence(s.part, f"Part:{i}") for i in (2, 3)]

    def place(feature: Any) -> None:
        for occurrence in new:
            add_occurrence(s.root, occurrence, s.root)

    rects.on_add = place
    result = s.run(
        h.pattern_rectangular,
        {"entities": ["occ"], "direction1": "origin:X", "quantity1": 3, "distance1": 50},
    )
    inp = rects.last_input
    assert list(inp.create_args[0]) == [s.occ]
    assert "patternComputeOption" not in inp.sets
    assert [o["full_path_name"] for o in result["created_occurrences"]] == ["Part:2", "Part:3"]
    assert [o["ref"] for o in result["created_occurrences"]] == [o.entityToken for o in new]
    assert all(o["type"] == "Occurrence" for o in result["created_occurrences"])

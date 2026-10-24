"""``feature.hole`` / ``feature.thread`` / ``feature.thread_options`` handlers on feature fakes
(c07 D15, D16; tasks 9.1-9.3, 10.1-10.4)."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import feature_hole as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.brep import (
    FakeBody,
    FakeEdge,
    circle_curve,
    cone_surface,
    fake_box_body,
    fake_cylinder_face,
)
from tests.fakes.design import FakeCollection
from tests.fakes.fusion_features import cylinder_face, fake_feature_design, make_ctx
from tests.fakes.fusion_sketch import make_sketch

adsk = fusion_addin.adsk
HS = adsk.fusion.FeatureHealthStates
DIR = adsk.fusion.ExtentDirections
LOC = adsk.fusion.ThreadLocations
P = adsk.core.Point3D
V = adsk.core.Vector3D
TOKEN = "t" * 32


def _real(value_input: Any) -> float:
    assert value_input.kind == "real", value_input
    return float(value_input.value)


class Setup:
    """A 40 x 20 x 10 mm box (``box``, top face ``top``, edges ``ex``/``ey`` along X/Y) and a
    sketch on XY with four standalone points (``points``)."""

    def __init__(self, parametric: bool = True) -> None:
        self.box = fake_box_body(4, 2, 1, name="Box")
        self.design = fake_feature_design(self.box, parametric=parametric)
        self.root = self.design.rootComponent
        self.features = self.root.features
        self.holes = self.features.holeFeatures
        self.threads = self.features.threadFeatures
        self.query = self.threads.threadDataQuery
        self.top = next(f for f in self.box.faces if f.entityToken.endswith("face+z"))
        self.side = next(f for f in self.box.faces if f.entityToken.endswith("face+x"))
        base = self.box.entityToken
        self.ex = next(e for e in self.box.edges if e.entityToken == f"{base}-e001101")
        self.ey = next(e for e in self.box.edges if e.entityToken == f"{base}-e001011")
        self.sketch = make_sketch(self.design, "XY")
        self.points = [
            self.sketch.sketchPoints.add(P(x, y, 0)) for x, y in ((1, 0.5), (3, 0.5), (3, 1.5))
        ]
        self.points.append(self.sketch.sketchPoints.add(P(1, 1.5, 0)))
        self.aliases: dict[str, Any] = {
            "top": self.top,
            "side": self.side,
            "ex": self.ex,
            "ey": self.ey,
            "box": self.box,
            **{f"p{i}": p for i, p in enumerate(self.points)},
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

    def bore(self, radius: float = 0.25, *extra: Any) -> list[Any]:
        """Hole side faces of the next hole: a bore of ``radius`` cm plus ``extra`` faces."""
        faces = [cylinder_face(radius, 1.0, internal=True, body=self.box), *extra]
        self.holes.will_set(sideFaces=FakeCollection(faces))
        return faces


def _hole(**params: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "placement": {"type": "point", "face": "top", "point": [10, 10, 10]},
        "diameter": 5,
        "extent": {"type": "through_all"},
    }
    base.update(params)
    return base


@pytest.mark.parametrize(
    ("method", "mutating"),
    [("feature.hole", True), ("feature.thread", True), ("feature.thread_options", False)],
)
def test_handlers_are_registered(method: str, mutating: bool) -> None:
    spec = REGISTRY.get(method)
    assert spec is not None and spec.mutating is mutating


# --- 9.1 hole kinds ------------------------------------------------------------------------


def test_simple_hole_by_point_through_all() -> None:
    s = Setup()
    s.holes.will_modify(s.box)
    result = s.run(h.feature_hole, _hole())
    inp = s.holes.last_input
    assert inp.create_method == "createSimpleInput"
    assert _real(inp.create_args[0]) == pytest.approx(0.5)
    assert _real(inp.sets["tipAngle"]) == pytest.approx(math.radians(118))
    face, point = inp.call("setPositionByPoint")
    assert face is s.top
    assert (point.x, point.y, point.z) == pytest.approx((1.0, 1.0, 1.0))
    assert inp.call("setAllExtent") == (DIR.PositiveExtentDirection,)
    assert inp.sets["isDefaultDirection"] is True
    assert "participantBodies" not in inp.sets
    assert s.holes.added == [inp]
    assert result["feature"]["type"] == "HoleFeature"
    assert result["holes"] == 1
    assert [b["name"] for b in result["modified_bodies"]] == ["Box"]
    assert result["created_bodies"] == [] and result["removed_bodies"] == []
    assert "thread" not in result


def test_counterbore_holes_on_four_sketch_points() -> None:
    s = Setup()
    result = s.run(
        h.feature_hole,
        _hole(
            kind="counterbore",
            diameter=5.5,
            cb_diameter=10,
            cb_depth=5.5,
            placement={"type": "sketch_points", "points": ["p0", "p1", "p2", "p3"]},
        ),
    )
    inp = s.holes.last_input
    assert inp.create_method == "createCounterboreInput"
    assert [_real(v) for v in inp.create_args] == pytest.approx([0.55, 1.0, 0.55])
    (collection,) = inp.call("setPositionBySketchPoints")
    assert isinstance(collection, adsk.core.ObjectCollection)
    assert list(collection) == s.points
    assert inp.called("setAllExtent")
    assert result["holes"] == 4


def test_countersink_with_angles_and_expressions() -> None:
    s = Setup()
    s.run(
        h.feature_hole,
        _hole(kind="countersink", diameter="bolt", cs_diameter=9, cs_angle=82, tip_angle=180),
    )
    inp = s.holes.last_input
    assert inp.create_method == "createCountersinkInput"
    diameter, cs_diameter, cs_angle = inp.create_args
    assert diameter.kind == "string" and diameter.value == "bolt"
    assert _real(cs_diameter) == pytest.approx(0.9)
    assert _real(cs_angle) == pytest.approx(math.radians(82))
    assert _real(inp.sets["tipAngle"]) == pytest.approx(math.pi)
    s.run(h.feature_hole, _hole(kind="countersink", cs_diameter=9))
    assert _real(s.holes.last_input.create_args[2]) == pytest.approx(math.radians(90))


def test_hole_inch_units() -> None:
    s = Setup()
    s.run(h.feature_hole, _hole(diameter=0.25, units="in"))
    assert _real(s.holes.last_input.create_args[0]) == pytest.approx(0.635)


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"kind": "counterbore", "cb_diameter": 10}, "cb_depth"),
        ({"kind": "counterbore", "cb_depth": 3}, "cb_diameter"),
        ({"kind": "countersink"}, "cs_diameter"),
        ({"kind": "simple", "cb_depth": 3}, "cb_depth"),
        ({"kind": "counterbore", "cb_diameter": 10, "cb_depth": 3, "cs_angle": 90}, "cs_angle"),
        ({"kind": "drilled"}, "kind"),
        ({"diameter": None}, "diameter"),
        ({"diameter": 0}, "diameter"),
        ({"diameter": True}, "diameter"),
        ({"kind": "counterbore", "cb_diameter": 4, "cb_depth": 3}, "cb_diameter"),
        ({"kind": "countersink", "cs_diameter": 9, "cs_angle": 180}, "cs_angle"),
        ({"tip_angle": 0}, "tip_angle"),
        ({"tip_angle": 200}, "tip_angle"),
        ({"flip": "yes"}, "flip"),
    ],
)
def test_hole_kind_validation_fails_before_any_change(params: dict[str, Any], param: str) -> None:
    s = Setup()
    error = s.error(h.feature_hole, _hole(**params))
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.holes.inputs == [] and s.holes.added == []


# --- 9.2 placements ------------------------------------------------------------------------


def test_one_sketch_point_uses_single_setter() -> None:
    s = Setup()
    result = s.run(h.feature_hole, _hole(placement={"type": "sketch_points", "points": ["p2"]}))
    inp = s.holes.last_input
    assert inp.call("setPositionBySketchPoint") == (s.points[2],)
    assert not inp.called("setPositionBySketchPoints")
    assert result["holes"] == 1


def test_duplicate_sketch_points_count_once() -> None:
    s = Setup()
    result = s.run(
        h.feature_hole, _hole(placement={"type": "sketch_points", "points": ["p0", "p0", "p1"]})
    )
    assert result["holes"] == 2


def test_sketch_points_from_two_sketches_are_refused() -> None:
    s = Setup()
    other = make_sketch(s.design, "XZ").sketchPoints.add(P(1, 1, 0))
    s.aliases["q"] = other
    error = s.error(
        h.feature_hole, _hole(placement={"type": "sketch_points", "points": ["p0", "q"]})
    )
    assert error.details["reason"] == "several_sketches"
    assert s.holes.inputs == []


def test_face_offsets_placement() -> None:
    s = Setup()
    s.run(
        h.feature_hole,
        _hole(
            placement={
                "type": "face_offsets",
                "face": "top",
                "point": [10, 15, 10],
                "edge1": "ex",
                "offset1": 10,
                "edge2": "ey",
                "offset2": 15,
            }
        ),
    )
    face, point, edge1, offset1, edge2, offset2 = s.holes.last_input.call(
        "setPositionByPlaneAndOffsets"
    )
    assert face is s.top and edge1 is s.ex and edge2 is s.ey
    assert (point.x, point.y, point.z) == pytest.approx((1.0, 1.5, 1.0))
    assert _real(offset1) == pytest.approx(1.0)
    assert _real(offset2) == pytest.approx(1.5)


def test_center_placement_on_circular_edge() -> None:
    s = Setup()
    edge = FakeEdge(circle_curve(P(1, 1, 1), V(0, 0, 1), 0.5), math.pi, P(1.5, 1, 1), body=s.box)
    s.aliases["rim"] = edge
    s.run(h.feature_hole, _hole(placement={"type": "center", "face": "top", "edge": "rim"}))
    assert s.holes.last_input.call("setPositionAtCenter") == (s.top, edge)


@pytest.mark.parametrize(
    ("placement", "param"),
    [
        ({"type": "center", "face": "top", "edge": "ex"}, "placement.edge"),
        ({"type": "point", "face": "box", "point": [0, 0, 0]}, "placement.face"),
        ({"type": "point", "face": "top", "point": [0, 0]}, "placement.point"),
        ({"type": "point", "face": "top"}, "placement.point"),
        ({"type": "point", "face": "top", "point": [0, 0, 0], "edge": "ex"}, "placement"),
        ({"type": "axis", "face": "top"}, "placement.type"),
        (
            {
                "type": "face_offsets",
                "face": "top",
                "point": [0, 0, 0],
                "edge1": "ex",
                "offset1": 1,
                "edge2": "ex",
                "offset2": 2,
            },
            "placement.edge2",
        ),
        ({"type": "sketch_points", "points": ["top"]}, "placement.points"),
        ({"type": "sketch_points", "points": []}, "placement.points"),
    ],
)
def test_bad_placements(placement: dict[str, Any], param: str) -> None:
    s = Setup()
    error = s.error(h.feature_hole, _hole(placement=placement))
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.holes.inputs == []


def test_curved_face_is_not_planar() -> None:
    s = Setup()
    s.aliases["cyl"] = cylinder_face(1.0, body=s.box)
    error = s.error(
        h.feature_hole, _hole(placement={"type": "point", "face": "cyl", "point": [0, 0, 0]})
    )
    assert error.details["param"] == "placement.face"
    assert "cylinder" in error.details["got"]


# --- 9.3 extents, flip, participants --------------------------------------------------------


def test_distance_extent_flip_and_participants() -> None:
    s = Setup()
    s.run(
        h.feature_hole,
        _hole(
            extent={"type": "distance", "depth": 12},
            flip=True,
            participant_bodies=["box"],
        ),
    )
    inp = s.holes.last_input
    (depth,) = inp.call("setDistanceExtent")
    assert _real(depth) == pytest.approx(1.2)
    assert inp.sets["isDefaultDirection"] is False
    assert inp.sets["participantBodies"] == [s.box]


def test_to_entity_extent() -> None:
    s = Setup()
    s.run(h.feature_hole, _hole(extent={"type": "to_entity", "entity": "side"}))
    assert s.holes.last_input.call("setOneSideToExtent") == (s.side, False)


@pytest.mark.parametrize(
    ("extent", "param"),
    [
        (None, "extent"),
        ({"type": "blind"}, "extent.type"),
        ({"type": "distance"}, "extent.depth"),
        ({"type": "distance", "depth": -1}, "extent.depth"),
        ({"type": "to_entity", "entity": "ex"}, "extent.entity"),
        ({"type": "through_all", "depth": 3}, "extent"),
    ],
)
def test_bad_extents(extent: Any, param: str) -> None:
    s = Setup()
    error = s.error(h.feature_hole, _hole(extent=extent))
    assert error.details["param"] == param
    assert s.holes.inputs == []


def test_participants_must_be_bodies() -> None:
    s = Setup()
    error = s.error(h.feature_hole, _hole(participant_bodies=["top"]))
    assert error.details["param"] == "participant_bodies"


def test_setter_refused_by_fusion() -> None:
    s = Setup()
    original = s.holes.createSimpleInput

    def create(*args: Any) -> Any:
        inp = original(*args)
        inp.returns["setPositionByPoint"] = False
        return inp

    s.holes.createSimpleInput = create
    error = s.error(h.feature_hole, _hole())
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.details["setter"] == "setPositionByPoint"
    assert s.holes.added == []


def test_hole_error_health_is_rolled_back() -> None:
    s = Setup()
    s.holes.health = HS.ErrorFeatureHealthState
    s.holes.message = "hole does not hit the body"
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.hole", _hole())
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert response.error.details["health"]["state"] == "error"
    assert s.design.timeline.names() == timeline
    assert list(s.holes) == []


def test_hole_error_in_direct_design_is_deleted() -> None:
    s = Setup(parametric=False)
    s.holes.health = HS.ErrorFeatureHealthState
    error = s.error(h.feature_hole, _hole())
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert list(s.holes) == []


# --- 10.1 thread options -------------------------------------------------------------------


def test_thread_options_levels() -> None:
    s = Setup()
    assert s.run(h.feature_thread_options, {}) == {
        "thread_types": ["ISO Metric profile", "ANSI Unified Screw Threads"]
    }
    sizes = s.run(h.feature_thread_options, {"thread_type": "ISO Metric profile"})
    assert sizes == {"thread_type": "ISO Metric profile", "sizes": ["5", "6", "8", "10"]}
    assert ("allSizes", ("ISO Metric profile",)) in s.query.calls
    designations = s.run(
        h.feature_thread_options, {"thread_type": "ISO Metric profile", "size": "6"}
    )
    assert designations["designations"] == ["M6x1", "M6x0.75"]
    classes = s.run(h.feature_thread_options, {"designation": "M6x1", "internal": True})
    assert classes == {
        "thread_type": "ISO Metric profile",
        "size": "6",
        "designation": "M6x1",
        "internal": True,
        "classes": ["6H", "4H", "5H"],
    }
    external = s.run(
        h.feature_thread_options,
        {"thread_type": "ISO Metric profile", "size": "6", "designation": "M6x1"},
    )
    assert external["classes"] == ["6g", "4g6g", "6e"]


def test_thread_options_recommendation_by_diameter() -> None:
    s = Setup()
    result = s.run(
        h.feature_thread_options,
        {"thread_type": "ISO Metric profile", "diameter": 8, "internal": False},
    )
    assert result["recommended"] == {"designation": "M8x1.25", "class": "6g"}
    assert result["diameter"] == pytest.approx(8.0) and result["units"] == "mm"
    assert s.query.calls[-1] == ("recommendThreadData", (0.8, False, "ISO Metric profile"))
    hole = s.run(h.feature_thread_options, {"diameter": 5, "internal": True})
    assert hole["recommended"] == {"designation": "M6x1", "class": "6H"}
    by_expression = s.run(h.feature_thread_options, {"diameter": "10 mm"})
    assert by_expression["recommended"]["designation"] == "M10x1.5"
    none = s.run(h.feature_thread_options, {"diameter": 50})
    assert none["recommended"] is None


def test_thread_options_never_change_the_design() -> None:
    s = Setup()
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.thread_options", {"thread_type": "ISO Metric profile"})
    assert response.ok is True
    assert s.design.timeline.names() == timeline
    assert s.threads.inputs == [] and s.threads.added == []


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"thread_type": "Whitworth"}, "thread_type"),
        ({"size": "7"}, "size"),
        ({"designation": "M99x9"}, "designation"),
        ({"size": "6", "designation": "M8x1"}, "designation"),
        ({"diameter": 8, "size": "8"}, "diameter"),
        ({"diameter": -1}, "diameter"),
        ({"diameter": "nope"}, "diameter"),
        ({"thread_type": ""}, "thread_type"),
    ],
)
def test_thread_options_errors(params: dict[str, Any], param: str) -> None:
    s = Setup()
    error = s.error(h.feature_thread_options, params)
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param


def test_unknown_designation_lists_available() -> None:
    s = Setup()
    error = s.error(h.feature_thread_options, {"designation": "M99x9"})
    assert "M6x1" in error.details["available"]
    assert len(error.details["available"]) <= h.MAX_AVAILABLE


# --- 10.2 internal / external faces --------------------------------------------------------


def test_is_internal_face() -> None:
    assert h.is_internal_face(cylinder_face(0.5, internal=True)) is True
    assert h.is_internal_face(cylinder_face(0.5, internal=False)) is False
    tilted = cylinder_face(0.3, internal=True, origin=(1, 2, 3), axis=(1, 0, 0))
    assert h.is_internal_face(tilted) is True


def test_non_cylindrical_faces_are_refused() -> None:
    s = Setup()
    with pytest.raises(BridgeError) as info:
        h.is_internal_face(s.top)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["actual"] == "BRepFace (plane)"
    cone = cylinder_face(0.5)
    cone.geometry = cone_surface(P(0, 0, 0), V(0, 0, 1), 0.5, 0.3)
    with pytest.raises(BridgeError) as info:
        h.check_cylinder(cone, "faces", 2)
    assert info.value.details == {
        "param": "faces",
        "expected": ["BRepFace (cylinder)"],
        "actual": "BRepFace (cone)",
        "index": 2,
    }


def test_face_without_normal_needs_explicit_internal() -> None:
    with pytest.raises(BridgeError) as info:
        h.is_internal_face(fake_cylinder_face(0.5, 1.0))
    assert info.value.details["reason"] == "orientation_unknown"


# --- 10.3 thread ---------------------------------------------------------------------------


def test_recommended_external_thread_on_shaft() -> None:
    s = Setup()
    shaft = cylinder_face(0.5, 2.0, internal=False, body=s.box)
    s.aliases["shaft"] = shaft
    result = s.run(h.feature_thread, {"faces": ["shaft"]})
    assert ("recommendThreadData", (1.0, False, "ISO Metric profile")) in s.query.calls
    info = s.threads.inputs[0]
    assert info.create_method == "createThreadInfo"
    assert info.create_args == (False, "ISO Metric profile", "M10x1.5", "6g")
    inp = s.threads.last_input
    faces, given_info = inp.create_args
    assert list(faces) == [shaft] and given_info is info
    assert inp.sets["isModeled"] is False
    assert inp.sets["isFullLength"] is True
    assert inp.sets["threadLocation"] == LOC.HighEndThreadLocation
    assert "threadLength" not in inp.sets and "threadOffset" not in inp.sets
    assert result["feature"]["type"] == "ThreadFeature"
    assert result["thread"] == {
        "type": "ISO Metric profile",
        "designation": "M10x1.5",
        "class": "6g",
        "internal": False,
        "modeled": False,
    }


def test_explicit_modeled_partial_thread() -> None:
    s = Setup()
    s.aliases["shaft"] = cylinder_face(0.4, 2.0, body=s.box)
    s.threads.will_modify(s.box)
    result = s.run(
        h.feature_thread,
        {
            "faces": ["shaft"],
            "designation": "M8x1.25",
            "thread_class": "4g6g",
            "modeled": True,
            "full_length": False,
            "length": 12,
            "offset": "1 mm",
            "location": "low",
        },
    )
    assert s.threads.inputs[0].create_args == (False, "ISO Metric profile", "M8x1.25", "4g6g")
    inp = s.threads.last_input
    assert inp.sets["isModeled"] is True and inp.sets["isFullLength"] is False
    assert _real(inp.sets["threadLength"]) == pytest.approx(1.2)
    assert inp.sets["threadOffset"].value == "1 mm"
    assert inp.sets["threadLocation"] == LOC.LowEndThreadLocation
    assert not any(name == "recommendThreadData" for name, _ in s.query.calls)
    assert [b["name"] for b in result["modified_bodies"]] == ["Box"]


def test_internal_detected_and_overridden() -> None:
    s = Setup()
    s.aliases["wall"] = cylinder_face(0.25, body=s.box, internal=True)
    result = s.run(h.feature_thread, {"faces": ["wall"]})
    assert result["thread"]["internal"] is True
    assert result["thread"]["designation"] == "M6x1"
    assert result["thread"]["class"] == "6H"
    forced = s.run(h.feature_thread, {"faces": ["wall"], "designation": "M6x1", "internal": False})
    assert forced["thread"]["internal"] is False and forced["thread"]["class"] == "6g"
    # A class without a designation is checked against the recommended designation.
    classed = s.run(h.feature_thread, {"faces": ["wall"], "thread_class": "5H"})
    assert classed["thread"]["designation"] == "M6x1" and classed["thread"]["class"] == "5H"


def test_unknown_designation_fails_with_available() -> None:
    s = Setup()
    s.aliases["shaft"] = cylinder_face(0.5, body=s.box)
    error = s.error(h.feature_thread, {"faces": ["shaft"], "designation": "M99x9"})
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == "designation"
    assert "M10x1.5" in error.details["available"]
    assert s.threads.inputs == [] and s.threads.added == []


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"faces": ["top"]}, "faces"),
        ({"thread_type": "Whitworth"}, "thread_type"),
        ({"designation": "M10x1.5", "thread_class": "9z"}, "thread_class"),
        ({"thread_class": "6H"}, "thread_class"),
        ({"length": 5}, "length"),
        ({"offset": 1}, "offset"),
        ({"full_length": False}, "length"),
        ({"location": "middle"}, "location"),
        ({"modeled": "yes"}, "modeled"),
        ({"internal": "no"}, "internal"),
        ({"faces": []}, "faces"),
    ],
)
def test_thread_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.aliases["shaft"] = cylinder_face(0.5, body=s.box)
    error = s.error(h.feature_thread, {"faces": ["shaft"], **params})
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.threads.inputs == []


def test_thread_face_set_rules() -> None:
    s = Setup()
    s.aliases["hole"] = cylinder_face(0.25, body=s.box, internal=True)
    s.aliases["shaft"] = cylinder_face(0.25, body=s.box)
    s.aliases["big"] = cylinder_face(0.5, body=s.box)
    mixed = s.error(h.feature_thread, {"faces": ["hole", "shaft"]})
    assert mixed.details["reason"] == "mixed_internal_external"
    sizes = s.error(h.feature_thread, {"faces": ["shaft", "big"]})
    assert sizes.details["reason"] == "different_diameters"
    s.run(h.feature_thread, {"faces": ["shaft", "big"], "designation": "M5x0.8"})
    assert list(s.threads.last_input.create_args[0]) == [s.aliases["shaft"], s.aliases["big"]]


def test_no_recommendation_for_odd_diameter() -> None:
    s = Setup()
    s.aliases["shaft"] = cylinder_face(3.0, body=s.box)
    error = s.error(h.feature_thread, {"faces": ["shaft"]})
    assert error.details["reason"] == "no_recommendation"
    assert error.details["diameter"] == pytest.approx(60.0) and error.details["units"] == "mm"


def test_thread_error_health_is_rolled_back() -> None:
    s = Setup()
    s.aliases["shaft"] = cylinder_face(0.5, body=s.box)
    s.threads.health = HS.ErrorFeatureHealthState
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.thread", {"faces": ["shaft"]})
    assert response.ok is False
    assert response.error.details["health"]["state"] == "error"
    assert s.design.timeline.names() == timeline
    assert list(s.threads) == []


# --- 10.4 tapped hole ----------------------------------------------------------------------


def test_m6_tapped_hole() -> None:
    s = Setup()
    s.holes.will_modify(s.box)
    bore = s.bore(0.25, cone_face := cylinder_face(0.3, body=s.box))
    cone_face.geometry = cone_surface(P(0, 0, 0), V(0, 0, 1), 0.3, 0.5)
    result = s.run(h.feature_hole, _hole(thread={"designation": "M6x1"}))
    assert result["feature"]["type"] == "HoleFeature"
    assert s.threads.inputs[0].create_args == (True, "ISO Metric profile", "M6x1", "6H")
    inp = s.threads.last_input
    assert list(inp.create_args[0]) == [bore[0]]
    thread = result["thread"]
    assert thread["designation"] == "M6x1" and thread["internal"] is True
    assert thread["class"] == "6H" and thread["modeled"] is False
    assert thread["feature"]["type"] == "ThreadFeature"
    assert thread["health"]["state"] == "ok" and thread["faces"] == 1
    assert s.design.timeline.names()[-2:] == ["Hole1", "Thread1"]


def test_tapped_counterbore_threads_the_bore_of_every_hole() -> None:
    s = Setup()
    walls = [cylinder_face(0.25, internal=True, body=s.box) for _ in range(2)]
    counterbores = [cylinder_face(0.5, internal=True, body=s.box) for _ in range(2)]
    s.holes.will_set(sideFaces=FakeCollection([*counterbores, *walls]))
    result = s.run(
        h.feature_hole,
        _hole(
            kind="counterbore",
            diameter=5,
            cb_diameter=10,
            cb_depth=3,
            placement={"type": "sketch_points", "points": ["p0", "p1"]},
            thread={"full_length": False, "length": 8, "modeled": True},
        ),
    )
    # No designation: recommended from the bore (5 mm -> M6x1).
    assert ("recommendThreadData", (0.5, True, "ISO Metric profile")) in s.query.calls
    assert list(s.threads.last_input.create_args[0]) == walls
    assert _real(s.threads.last_input.sets["threadLength"]) == pytest.approx(0.8)
    assert result["thread"]["designation"] == "M6x1" and result["thread"]["faces"] == 2
    assert result["holes"] == 2


def test_unknown_hole_thread_designation_fails_before_the_hole() -> None:
    s = Setup()
    s.bore()
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.hole", _hole(thread={"designation": "M99x9"}))
    assert response.ok is False
    assert response.error.code is ErrorCode.INVALID_PARAMS
    assert response.error.details["param"] == "thread.designation"
    assert "M6x1" in response.error.details["available"]
    assert s.holes.added == [] and s.threads.added == []
    assert s.design.timeline.names() == timeline


@pytest.mark.parametrize(
    ("thread", "param"),
    [
        ({"pitch": 1}, "thread"),
        ({"thread_type": "Whitworth"}, "thread.thread_type"),
        ({"designation": "M6x1", "thread_class": "6g"}, "thread.thread_class"),
        ({"designation": "M6x1", "offset": 2}, "thread.offset"),
        ({"length": 5}, "thread.length"),
        ("M6x1", "thread"),
    ],
)
def test_hole_thread_spec_validation(thread: Any, param: str) -> None:
    s = Setup()
    error = s.error(h.feature_hole, _hole(thread=thread))
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["param"] == param
    assert s.holes.inputs == []


def _assert_both_rolled_back(s: Setup, response: Any, timeline: list[str]) -> None:
    assert response.ok is False
    assert s.design.timeline.names() == timeline
    assert list(s.holes) == [] and list(s.threads) == []


def test_thread_failure_after_hole_rolls_back_both() -> None:
    s = Setup()
    s.bore()
    s.threads.add_error = "Thread compute failed"
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.hole", _hole(thread={"designation": "M6x1"}))
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert "Thread compute failed" in response.error.message
    assert len(s.holes.added) == 1  # the hole was added, then rolled back
    _assert_both_rolled_back(s, response, timeline)


def test_thread_error_health_after_hole_rolls_back_both() -> None:
    s = Setup()
    s.bore()
    s.threads.health = HS.ErrorFeatureHealthState
    s.threads.message = "thread does not fit"
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.hole", _hole(thread={"designation": "M6x1"}))
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert response.error.details["health"]["state"] == "error"
    assert len(s.threads.added) == 1
    _assert_both_rolled_back(s, response, timeline)


def test_bore_without_recommendation_rolls_back_the_hole() -> None:
    s = Setup()
    s.bore(2.0)  # 40 mm bore: no ISO thread recommended
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.hole", _hole(diameter=40, thread={}))
    assert response.error.code is ErrorCode.INVALID_PARAMS
    assert response.error.details["reason"] == "no_recommendation"
    assert len(s.holes.added) == 1
    _assert_both_rolled_back(s, response, timeline)


def test_class_not_offered_for_recommended_bore_thread_rolls_back() -> None:
    s = Setup()
    s.bore()
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.hole", _hole(thread={"thread_class": "6g"}))
    assert response.error.code is ErrorCode.INVALID_PARAMS
    assert response.error.details["param"] == "thread.thread_class"
    assert response.error.details["available"] == ["6H", "4H", "5H"]
    _assert_both_rolled_back(s, response, timeline)


def test_hole_without_cylindrical_side_face_rolls_back() -> None:
    s = Setup()
    s.holes.will_set(sideFaces=FakeCollection([]))
    timeline = s.design.timeline.names()
    response = s.dispatch("feature.hole", _hole(thread={"designation": "M6x1"}))
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    _assert_both_rolled_back(s, response, timeline)


def test_tapped_hole_failure_in_direct_design_deletes_both() -> None:
    s = Setup(parametric=False)
    s.bore()
    s.threads.health = HS.ErrorFeatureHealthState
    error = s.error(h.feature_hole, _hole(thread={"designation": "M6x1"}))
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert list(s.holes) == [] and list(s.threads) == []


def test_body_snapshot_of_new_body_is_reported() -> None:
    # A hole whose feature reports a new body (split) lists it as created.
    s = Setup()
    s.holes.will_create(FakeBody("Split"))
    result = s.run(h.feature_hole, _hole())
    assert [b["name"] for b in result["created_bodies"]] == ["Split"]

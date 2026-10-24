"""``body.combine`` / ``split`` / ``move`` / ``scale`` / ``thicken`` / ``boundary_fill`` /
``rename`` / ``set_visibility`` / ``remove`` handlers on feature fakes (c08 D8-D11, D13;
tasks 4.1-4.2, 5.1-5.3, 6.1-6.2, 7.1-7.3)."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import body as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.brep import FakeBody, FakeConstructionPlane, fake_box_body
from tests.fakes.fusion_features import (
    FakeCellBody,
    fake_feature_design,
    install_features,
    make_ctx,
)

adsk = fusion_addin.adsk
OPS = adsk.fusion.FeatureOperations
HS = adsk.fusion.FeatureHealthStates
TOKEN = "t" * 32


def real(value_input: Any) -> float:
    assert value_input.kind == "real", value_input
    return float(value_input.value)


class VisibleBody(FakeBody):
    """A body with light bulb and opacity; ``fail_on`` makes that assignment raise."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.__dict__["isLightBulbOn"] = True
        self.__dict__["opacity"] = 1.0
        self.__dict__["fail_on"] = None

    def __setattr__(self, name: str, value: Any) -> None:
        if name == self.__dict__.get("fail_on"):
            raise RuntimeError(f"cannot set {name}")
        super().__setattr__(name, value)


class Setup:
    """Boxes ``a`` (40 x 20 x 10 mm), ``b``, ``c``; ``top`` face of ``a``; plane ``p``."""

    def __init__(self, parametric: bool = True, omit: tuple[str, ...] = ()) -> None:
        self.a = fake_box_body(4, 2, 1, name="A")
        self.b = fake_box_body(1, 1, 1, name="B")
        self.c = fake_box_body(1, 1, 1, name="C")
        self.v1, self.v2 = VisibleBody("V1"), VisibleBody("V2")
        self.design = fake_feature_design(
            self.a, self.b, self.c, self.v1, self.v2, parametric=parametric
        )
        self.root = self.design.rootComponent
        if omit:
            install_features(self.root, self.design, omit)
        self.features = self.root.features
        faces = {f.entityToken.rsplit("face", 1)[1]: f for f in self.a.faces}
        self.top, self.px = faces["+z"], faces["+x"]
        self.plane = FakeConstructionPlane(
            "P", adsk.core.Point3D.create(0, 0, 0.5), adsk.core.Vector3D.create(0, 0, 1)
        )
        self.surface = FakeBody("Patch", is_solid=False)
        self.aliases: dict[str, Any] = {
            "a": self.a,
            "b": self.b,
            "c": self.c,
            "v1": self.v1,
            "v2": self.v2,
            "top": self.top,
            "px": self.px,
            "p": self.plane,
            "edge": self.a.edges.item(0),
            "vertex": self.a.vertices.item(7),
        }

    def ctx(self, params: dict[str, Any]) -> Context:
        return make_ctx(self.design, params, self.aliases)

    def run(self, fn: Any, params: dict[str, Any]) -> Any:
        return fn(self.ctx(params), params)

    def error(self, fn: Any, params: dict[str, Any]) -> BridgeError:
        with pytest.raises(BridgeError) as info:
            fn(self.ctx(params), params)
        return info.value

    def invalid(self, fn: Any, params: dict[str, Any], param: str) -> BridgeError:
        err = self.error(fn, params)
        assert err.code is ErrorCode.INVALID_PARAMS, err
        assert err.details.get("param") == param, err.details
        return err

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        dispatcher = Dispatcher(TOKEN, context_factory=lambda p: self.ctx(p))
        return dispatcher.dispatch(Request("1", TOKEN, method, params))


def names(bodies: list[dict[str, Any]]) -> list[str]:
    return [b["name"] for b in bodies]


@pytest.mark.parametrize(
    "method",
    [
        "body.combine",
        "body.split",
        "body.move",
        "body.scale",
        "body.thicken",
        "body.boundary_fill",
        "body.rename",
        "body.set_visibility",
        "body.remove",
    ],
)
def test_handlers_are_registered_as_mutating(method: str) -> None:
    spec = REGISTRY.get(method)
    assert spec is not None and spec.mutating is True


# --- 4.1 combine ---------------------------------------------------------------------------


def test_join_consumes_tools() -> None:
    s = Setup()
    combines = s.features.combineFeatures
    combines.will_modify(s.a).will_remove(s.b, s.c)
    tokens = [s.b.entityToken, s.c.entityToken]
    result = s.run(h.body_combine, {"target": "a", "tools": ["b", "c"]})
    inp = combines.last_input
    target, tools = inp.create_args
    assert target is s.a and list(tools) == [s.b, s.c]
    assert inp.sets == {
        "operation": OPS.JoinFeatureOperation,
        "isKeepToolBodies": False,
        "isNewComponent": False,
    }
    assert names(result["modified_bodies"]) == ["A"]
    assert sorted(result["removed_bodies"]) == sorted(tokens)


def test_cut_keeping_tools() -> None:
    s = Setup()
    result = s.run(
        h.body_combine, {"target": "a", "tools": ["b"], "operation": "cut", "keep_tools": True}
    )
    inp = s.features.combineFeatures.last_input
    assert inp.sets["operation"] == OPS.CutFeatureOperation
    assert inp.sets["isKeepToolBodies"] is True
    assert result["removed_bodies"] == []
    assert names(result["modified_bodies"]) == ["A"]


def test_intersect_into_new_component() -> None:
    s = Setup()
    from tests.fakes.brep import FakeComponent

    part = FakeComponent("Combined")
    s.features.combineFeatures.will_create(FakeBody("A (1)"), component=part)
    result = s.run(
        h.body_combine,
        {"target": "a", "tools": ["b"], "operation": "intersect", "new_component": True},
    )
    inp = s.features.combineFeatures.last_input
    assert inp.sets["operation"] == OPS.IntersectFeatureOperation
    assert inp.sets["isNewComponent"] is True
    assert names(result["created_bodies"]) == ["A (1)"]
    assert result["component"]["name"] == "Combined"


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"target": "a", "tools": ["b", "a"]}, "tools"),
        ({"target": "a", "tools": []}, "tools"),
        ({"target": "top", "tools": ["b"]}, "target"),
        ({"target": "a", "tools": ["b"], "operation": "new_body"}, "operation"),
        ({"target": "a", "tools": ["b"], "keep_tools": 1}, "keep_tools"),
    ],
)
def test_combine_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.body_combine, params, param)
    assert s.features.combineFeatures.inputs == []


# --- 4.2 split body ------------------------------------------------------------------------


def test_split_by_origin_plane() -> None:
    s = Setup()
    s.features.splitBodyFeatures.will_modify(s.a).will_create(FakeBody("A (2)"))
    result = s.run(h.body_split, {"bodies": ["a"], "tool": "origin:XZ"})
    inp = s.features.splitBodyFeatures.last_input
    body, tool, extend = inp.create_args
    assert body is s.a
    assert tool is s.root.xZConstructionPlane
    assert extend is True
    assert names(result["created_bodies"]) == ["A (2)"]
    assert names(result["modified_bodies"]) == ["A"]


def test_split_several_bodies_by_face_body_or_plane() -> None:
    s = Setup()
    s.run(h.body_split, {"bodies": ["a", "b"], "tool": "p", "extend_tool": False})
    bodies, tool, extend = s.features.splitBodyFeatures.last_input.create_args
    assert list(bodies) == [s.a, s.b] and tool is s.plane and extend is False
    s.run(h.body_split, {"bodies": ["b"], "tool": "top"})
    assert s.features.splitBodyFeatures.last_input.create_args[1] is s.top
    s.run(h.body_split, {"bodies": ["b"], "tool": "a"})
    assert s.features.splitBodyFeatures.last_input.create_args[1] is s.a


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"bodies": ["a"], "tool": "a"}, "tool"),
        ({"bodies": ["a"], "tool": "edge"}, "tool"),
        ({"bodies": ["a"], "tool": "origin:Z"}, "tool"),
        ({"bodies": ["top"], "tool": "p"}, "bodies"),
        ({"bodies": ["a"]}, "tool"),
    ],
)
def test_split_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.body_split, params, param)
    assert s.features.splitBodyFeatures.inputs == []


# --- 5.1 move without copy -----------------------------------------------------------------


def test_move_translate_with_expression() -> None:
    s = Setup()
    result = s.run(
        h.body_move, {"bodies": ["a"], "transform": {"type": "translate", "x": 10, "z": "gap"}}
    )
    moves = s.features.moveFeatures
    inp = moves.last_input
    assert inp.create_method == "createInput2"
    assert list(inp.create_args[0]) == [s.a]
    x, y, z, design_space = inp.call("defineAsTranslateXYZ")
    assert real(x) == pytest.approx(1.0) and real(y) == 0.0
    assert (z.kind, z.value) == ("string", "gap")
    assert design_space is True
    assert moves.added == [inp] and len(moves.inputs) == 1
    assert names(result["modified_bodies"]) == ["A"]
    assert result["created_bodies"] == [] and "copy" not in result


@pytest.mark.parametrize(
    ("transform", "method"),
    [
        (
            {"type": "translate_along", "entity": "edge", "distance": 5},
            "defineAsTranslateAlongEntity",
        ),
        ({"type": "rotate", "axis": "origin:Z", "angle": 90}, "defineAsRotate"),
        (
            {"type": "rotate_about", "point": [0, 0, 0], "direction": [0, 0, 1], "angle": 45},
            "defineAsFreeMove",
        ),
        ({"type": "align", "from": "vertex", "to": "origin:O"}, "defineAsPointToPoint"),
        (
            {"type": "matrix", "matrix": [1, 0, 0, 5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]},
            "defineAsFreeMove",
        ),
    ],
)
def test_move_each_transform_type(transform: dict[str, Any], method: str) -> None:
    s = Setup()
    result = s.run(h.body_move, {"bodies": ["a", "b"], "transform": transform})
    inp = s.features.moveFeatures.last_input
    assert inp.called(method)
    assert names(result["modified_bodies"]) == ["A", "B"]


def test_move_rotate_angle_in_radians() -> None:
    s = Setup()
    s.run(
        h.body_move,
        {"bodies": ["a"], "transform": {"type": "rotate", "axis": "origin:Z", "angle": 90}},
    )
    axis, angle = s.features.moveFeatures.last_input.call("defineAsRotate")
    assert axis is s.root.zConstructionAxis
    assert real(angle) == pytest.approx(math.pi / 2)


def test_move_non_rigid_matrix_suggests_scale() -> None:
    s = Setup()
    matrix = [2, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    err = s.invalid(
        h.body_move,
        {"bodies": ["a"], "transform": {"type": "matrix", "matrix": matrix}},
        "transform.matrix",
    )
    assert "body_scale" in err.message
    assert s.features.moveFeatures.inputs == []


def test_move_fallback_without_create_input2() -> None:
    s = Setup()
    s.features.moveFeatures.create_methods.discard("createInput2")
    s.run(h.body_move, {"bodies": ["a"], "transform": {"type": "translate", "x": 10, "z": 50}})
    inp = s.features.moveFeatures.last_input
    assert inp.create_method == "createInput"
    entities, matrix = inp.create_args
    assert list(entities) == [s.a]
    assert matrix.translation.asArray() == pytest.approx([1.0, 0.0, 5.0])


def test_move_fallback_when_define_method_missing() -> None:
    s = Setup()
    s.features.moveFeatures.on_input = lambda inp: inp.missing.add("defineAsPointToPoint")
    s.run(
        h.body_move,
        {"bodies": ["a"], "transform": {"type": "align", "from": "vertex", "to": "origin:O"}},
    )
    inp = s.features.moveFeatures.last_input
    assert inp.create_method == "createInput"
    assert inp.create_args[1].translation.asArray() == pytest.approx([-4.0, -2.0, -1.0])


def test_move_fallback_expression_not_available_changes_nothing() -> None:
    s = Setup()
    s.features.moveFeatures.create_methods.discard("createInput2")
    err = s.error(h.body_move, {"bodies": ["a"], "transform": {"type": "translate", "x": "d"}})
    assert err.code is ErrorCode.FUSION_API_ERROR and "not available" in err.message
    assert s.features.moveFeatures.added == []


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"bodies": ["a"]}, "transform"),
        ({"bodies": ["a"], "transform": {"type": "spin"}}, "transform.type"),
        ({"bodies": ["top"], "transform": {"type": "translate"}}, "bodies"),
        (
            {"bodies": ["a"], "transform": {"type": "translate"}, "keep_original": "y"},
            "keep_original",
        ),
    ],
)
def test_move_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.body_move, params, param)
    assert s.features.moveFeatures.added == [] and s.features.copyPasteBodies.added == []


# --- 5.2 move a copy -----------------------------------------------------------------------


def test_copy_and_rotate() -> None:
    s = Setup()
    copy_a, copy_b = FakeBody("A (1)"), FakeBody("B (1)")
    s.features.copyPasteBodies.will_create(copy_a, copy_b)
    result = s.run(
        h.body_move,
        {
            "bodies": ["a", "b"],
            "transform": {"type": "rotate", "axis": "origin:Z", "angle": 90},
            "keep_original": True,
        },
    )
    pasted = s.features.copyPasteBodies.added[-1]
    assert list(pasted.create_args[0]) == [s.a, s.b]
    moves = s.features.moveFeatures
    inp = moves.last_input
    assert list(inp.create_args[0]) == [copy_a, copy_b]
    assert real(inp.call("defineAsRotate")[1]) == pytest.approx(math.pi / 2)
    assert moves.added == [inp]
    assert names(result["created_bodies"]) == ["A (1)", "B (1)"]
    assert result["modified_bodies"] == []
    assert result["copy"]["method"] == "copyPasteBodies"
    assert result["copy"]["feature"]["type"] == "CopyPasteBody"


def test_copy_fallback_copy_to_component() -> None:
    s = Setup(omit=("copyPasteBodies",))
    copy = FakeBody("A (copy)")

    def copy_to(component: Any) -> FakeBody:
        component.add_body(copy)
        return copy

    s.a.copyToComponent = copy_to  # type: ignore[attr-defined]
    result = s.run(
        h.body_move,
        {"bodies": ["a"], "transform": {"type": "translate", "z": 50}, "keep_original": True},
    )
    assert list(s.features.moveFeatures.last_input.create_args[0]) == [copy]
    assert result["copy"] == {"feature": None, "method": "copyToComponent"}
    assert names(result["created_bodies"]) == ["A (copy)"]


def test_copy_count_mismatch_is_an_error() -> None:
    s = Setup()
    s.features.copyPasteBodies.will_create(FakeBody("only one"))
    err = s.error(
        h.body_move,
        {"bodies": ["a", "b"], "transform": {"type": "translate", "x": 1}, "keep_original": True},
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert s.features.moveFeatures.added == []


def test_direct_design_deletes_copies_when_the_move_fails() -> None:
    s = Setup(parametric=False)
    s.features.copyPasteBodies.will_create(FakeBody("A (1)"))
    s.features.moveFeatures.add_error = "cannot move"
    with pytest.raises(RuntimeError):
        s.run(
            h.body_move,
            {"bodies": ["a"], "transform": {"type": "translate", "x": 1}, "keep_original": True},
        )
    assert [b.name for b in s.root.bRepBodies] == ["A", "B", "C", "V1", "V2"]
    assert list(s.features.copyPasteBodies) == []


def test_direct_design_deletes_partial_copies() -> None:
    s = Setup(parametric=False)
    s.features.copyPasteBodies.will_create(FakeBody("only one"))
    with pytest.raises(BridgeError):
        s.run(
            h.body_move,
            {
                "bodies": ["a", "b"],
                "transform": {"type": "translate", "x": 1},
                "keep_original": True,
            },
        )
    assert list(s.features.copyPasteBodies) == []
    assert [b.name for b in s.root.bRepBodies] == ["A", "B", "C", "V1", "V2"]

    old = Setup(parametric=False, omit=("copyPasteBodies",))
    deleted: list[str] = []
    copy = FakeBody("A (copy)")
    copy.deleteMe = lambda: bool(deleted.append(copy.name)) or True  # type: ignore[attr-defined,func-returns-value]
    old.a.copyToComponent = lambda component: copy  # type: ignore[attr-defined]
    old.b.copyToComponent = lambda component: None  # type: ignore[attr-defined]
    err = old.error(
        h.body_move,
        {"bodies": ["a", "b"], "transform": {"type": "translate", "x": 1}, "keep_original": True},
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert deleted == ["A (copy)"]


def test_copy_rolled_back_by_dispatcher_on_error_health() -> None:
    s = Setup()
    s.features.copyPasteBodies.will_create(FakeBody("A (1)"))
    s.features.moveFeatures.health = HS.ErrorFeatureHealthState
    count = s.design.timeline.count
    response = s.dispatch(
        "body.move",
        {"bodies": ["a"], "transform": {"type": "translate", "x": 1}, "keep_original": True},
    )
    assert response.error is not None
    assert s.design.timeline.count == count
    assert [b.name for b in s.root.bRepBodies] == ["A", "B", "C", "V1", "V2"]


# --- 5.3 scale -----------------------------------------------------------------------------


def test_uniform_scale_about_origin() -> None:
    s = Setup()
    result = s.run(h.body_scale, {"bodies": ["a"], "factor": 2})
    inp = s.features.scaleFeatures.last_input
    entities, point, factor = inp.create_args
    assert list(entities) == [s.a]
    assert point is s.root.originConstructionPoint
    assert real(factor) == 2.0
    assert not inp.called("setToNonUniform")
    assert names(result["modified_bodies"]) == ["A"]


def test_non_uniform_scale_without_unit_conversion() -> None:
    s = Setup()
    s.run(
        h.body_scale,
        {"bodies": ["a"], "factors": {"x": 1, "y": 2, "z": 0.5}, "point": "vertex", "units": "in"},
    )
    inp = s.features.scaleFeatures.last_input
    assert inp.create_args[1] is s.a.vertices.item(7)
    assert real(inp.create_args[2]) == 1.0
    assert [real(v) for v in inp.call("setToNonUniform")] == [1.0, 2.0, 0.5]


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"factor": 0}, "factor"),
        ({"factor": -2}, "factor"),
        ({"factors": {"x": 1, "y": 0, "z": 1}}, "factors.y"),
        ({"factors": {"x": 1, "y": 2}}, "factors"),
        ({"factor": 2, "factors": {"x": 1, "y": 2, "z": 1}}, "factors"),
        ({"point": "top"}, "point"),
    ],
)
def test_scale_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.body_scale, {"bodies": ["a"], **params}, param)
    assert s.features.scaleFeatures.inputs == []


# --- 6.1 thicken ---------------------------------------------------------------------------


def test_thicken_surface_patch() -> None:
    s = Setup()
    s.features.thickenFeatures.will_create(FakeBody("Thick"))
    result = s.run(h.body_thicken, {"faces": ["top"], "thickness": 2})
    faces, thickness, symmetric, operation, chain = (
        s.features.thickenFeatures.last_input.create_args
    )
    assert list(faces) == [s.top]
    assert real(thickness) == pytest.approx(0.2)
    assert symmetric is False
    assert operation == OPS.NewBodyFeatureOperation
    assert chain is True
    assert names(result["created_bodies"]) == ["Thick"]


def test_thicken_options_and_validation() -> None:
    s = Setup()
    s.run(
        h.body_thicken,
        {
            "faces": ["top", "px"],
            "thickness": "-t",
            "symmetric": True,
            "operation": "join",
            "chain": False,
        },
    )
    _, thickness, symmetric, operation, chain = s.features.thickenFeatures.last_input.create_args
    assert thickness.value == "-t" and symmetric is True and chain is False
    assert operation == OPS.JoinFeatureOperation
    s.invalid(h.body_thicken, {"faces": ["top"], "thickness": 0}, "thickness")
    s.invalid(h.body_thicken, {"faces": ["top"]}, "thickness")
    s.invalid(h.body_thicken, {"faces": ["a"], "thickness": 1}, "faces")
    s.invalid(h.body_thicken, {"faces": ["top"], "thickness": 1, "operation": "x"}, "operation")


# --- 6.2 boundary fill ---------------------------------------------------------------------


def fill_setup() -> Setup:
    s = Setup()
    s.features.boundaryFillFeatures.cells = [
        FakeCellBody((0, 0, 0), (1, 1, 1), "Inner"),
        FakeCellBody((2, 0, 0), (3, 1, 1), "Outer"),
    ]
    return s


def test_boundary_fill_all_cells() -> None:
    s = fill_setup()
    s.features.boundaryFillFeatures.will_create(FakeBody("Fill"))
    result = s.run(h.body_boundary_fill, {"tools": ["a", "p", "top"]})
    inp = s.features.boundaryFillFeatures.last_input
    tools, operation = inp.create_args
    assert list(tools) == [s.a, s.plane, s.top]
    assert operation == OPS.NewBodyFeatureOperation
    assert all(cell.isSelected for cell in inp.bRepCells)
    assert inp.sets["isRemoveTools"] is False
    assert not inp.called("cancel")
    assert names(result["created_bodies"]) == ["Fill"]


def test_boundary_fill_select_cell_by_point() -> None:
    s = fill_setup()
    s.features.boundaryFillFeatures.will_create(FakeBody("Fill"))
    result = s.run(
        h.body_boundary_fill,
        {"tools": ["a"], "cell_points": [[5, 5, 5]], "remove_tools": True, "operation": "join"},
    )
    inp = s.features.boundaryFillFeatures.last_input
    inner, outer = list(inp.bRepCells)
    assert inner.isSelected is True and outer.isSelected is False
    assert inner.cellBody.queries[0].asArray() == pytest.approx([0.5, 0.5, 0.5])
    assert inp.create_args[1] == OPS.JoinFeatureOperation
    assert inp.sets["isRemoveTools"] is True
    assert names(result["created_bodies"]) == ["Fill"]


def test_boundary_fill_point_outside_all_cells_cancels() -> None:
    s = fill_setup()
    err = s.invalid(
        h.body_boundary_fill,
        {"tools": ["a"], "cell_points": [[5, 5, 5], [15, 50, 5]]},
        "cell_points",
    )
    inp = s.features.boundaryFillFeatures.last_input
    assert inp.called("cancel")
    assert s.features.boundaryFillFeatures.added == []
    assert err.details["missing"] == [1]
    assert err.details["cells"] == [
        {"center": [5.0, 5.0, 5.0], "volume": 1000.0},
        {"center": [25.0, 5.0, 5.0], "volume": 1000.0},
    ]


def test_boundary_fill_validation_before_input() -> None:
    s = fill_setup()
    s.invalid(h.body_boundary_fill, {"tools": ["edge"]}, "tools")
    s.invalid(h.body_boundary_fill, {"tools": ["a"], "cell_points": [[1, 2]]}, "cell_points[0]")
    s.invalid(h.body_boundary_fill, {"tools": ["a"], "cell_points": []}, "cell_points")
    s.invalid(h.body_boundary_fill, {"tools": ["a"], "operation": "merge"}, "operation")
    assert s.features.boundaryFillFeatures.inputs == []
    empty = Setup()
    err = empty.invalid(h.body_boundary_fill, {"tools": ["a"]}, "tools")
    assert err.details["reason"] == "no_cells"
    assert empty.features.boundaryFillFeatures.last_input.called("cancel")


# --- 7.1 rename ----------------------------------------------------------------------------


def test_rename() -> None:
    s = Setup()
    result = s.run(h.body_rename, {"body": "b", "name": "  Lid "})
    assert s.b.name == "Lid"
    assert result == {
        "body": {"ref": "b", "type": "BRepBody", "name": "Lid"},  # FakeRefs answers aliases
        "previous_name": "B",
    }


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"body": "b", "name": ""}, "name"),
        ({"body": "b", "name": "   "}, "name"),
        ({"body": "b", "name": 3}, "name"),
        ({"body": "top", "name": "x"}, "body"),
    ],
)
def test_rename_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.body_rename, params, param)
    assert s.b.name == "B"


# --- 7.2 visibility ------------------------------------------------------------------------


def test_hide_and_fade() -> None:
    s = Setup()
    result = s.run(
        h.body_set_visibility, {"bodies": ["v1", "v2"], "visible": False, "opacity": 0.3}
    )
    for body in (s.v1, s.v2):
        assert body.isLightBulbOn is False  # type: ignore[attr-defined]
        assert body.opacity == 0.3  # type: ignore[attr-defined]
    assert result["bodies"][0] == {
        "ref": "v1",
        "type": "BRepBody",
        "name": "V1",
        "light_bulb": False,
        "is_visible": True,
        "opacity": 0.3,
    }


def test_only_opacity_keeps_light_bulb() -> None:
    s = Setup()
    s.run(h.body_set_visibility, {"bodies": ["v1"], "opacity": 1})
    assert s.v1.isLightBulbOn is True  # type: ignore[attr-defined]
    assert s.v1.opacity == 1.0  # type: ignore[attr-defined]


def test_failure_on_second_body_restores_values() -> None:
    s = Setup()
    s.v2.__dict__["fail_on"] = "opacity"
    with pytest.raises(RuntimeError):
        s.run(h.body_set_visibility, {"bodies": ["v1", "v2"], "visible": False, "opacity": 0.3})
    assert s.v1.isLightBulbOn is True and s.v1.opacity == 1.0  # type: ignore[attr-defined]
    assert s.v2.isLightBulbOn is True  # type: ignore[attr-defined]
    response = s.dispatch("body.set_visibility", {"bodies": ["v1", "v2"], "visible": False})
    assert response.error is None
    response = s.dispatch("body.set_visibility", {"bodies": ["v1", "v2"], "opacity": 0.5})
    assert response.error is not None
    assert s.v1.opacity == 1.0  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({}, "visible"),
        ({"opacity": 1.5}, "opacity"),
        ({"opacity": -0.1}, "opacity"),
        ({"opacity": True}, "opacity"),
        ({"visible": "no"}, "visible"),
    ],
)
def test_visibility_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.body_set_visibility, {"bodies": ["v1"], **params}, param)
    assert s.v1.isLightBulbOn is True and s.v1.opacity == 1.0  # type: ignore[attr-defined]


# --- 7.3 remove ----------------------------------------------------------------------------


def test_parametric_remove() -> None:
    s = Setup()
    token = "b"  # the ref taken before removal (FakeRefs answers aliases)
    result = s.run(h.body_remove, {"bodies": ["b"]})
    removed = s.features.removeFeatures.added[-1]
    assert removed.create_args == (s.b,)
    assert s.b not in list(s.root.bRepBodies)
    assert result["mode_used"] == "remove"
    assert result["removed"] == [token]
    assert len(result["features"]) == 1 and result["features"][0]["type"] == "RemoveFeature"


def test_delete_mode_and_direct_design() -> None:
    s = Setup()
    deleted: list[str] = []

    def deleter(name: str) -> Any:
        def delete_me() -> bool:
            deleted.append(name)
            return True

        return delete_me

    s.b.deleteMe = deleter("b")  # type: ignore[attr-defined]
    result = s.run(h.body_remove, {"bodies": ["b"], "mode": "delete"})
    assert deleted == ["b"] and result["mode_used"] == "delete" and result["features"] == []
    direct = Setup(parametric=False)
    direct.c.deleteMe = deleter("c")  # type: ignore[attr-defined]
    result = direct.run(h.body_remove, {"bodies": ["c"], "mode": "remove"})
    assert deleted == ["b", "c"]
    assert result["mode_used"] == "delete"
    assert direct.features.removeFeatures.added == []


def test_remove_validation_and_failures() -> None:
    s = Setup()
    s.invalid(h.body_remove, {"bodies": ["b"], "mode": "erase"}, "mode")
    s.invalid(h.body_remove, {"bodies": ["b", "top"]}, "bodies")
    assert s.features.removeFeatures.added == []
    s.b.deleteMe = lambda: False  # type: ignore[attr-defined]
    err = s.error(h.body_remove, {"bodies": ["b"], "mode": "delete"})
    assert err.code is ErrorCode.FUSION_API_ERROR
    old = Setup(omit=("removeFeatures",))
    err = old.error(h.body_remove, {"bodies": ["b"]})
    assert err.code is ErrorCode.FUSION_API_ERROR and "not available" in err.message

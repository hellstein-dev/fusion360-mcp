"""``form.*`` handlers on form fakes (c14 D2-D9; tasks 2.1-2.10)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.handlers import form as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.design import FakeCollection, FakeEntity
from tests.fakes.fusion_features import make_ctx
from tests.fakes.fusion_form import add_box_form, fake_form_design

adsk = fusion_addin.adsk
F = adsk.fusion
TOKEN = "t" * 32
TOP_FACE = [5.0, 5.0, 20.0]  # centre of a top face of the 20 mm box (mm)
TOP_FACE2 = [15.0, 15.0, 20.0]
TOP_EDGE = [5.0, 0.0, 20.0]  # midpoint of a top edge
TOP_EDGE2 = [15.0, 0.0, 20.0]


class Setup:
    """A form ``form`` with a 20 mm box T-spline body ``body`` (with its B-Rep)."""

    def __init__(self, parametric: bool = True, insert_edge: bool = True) -> None:
        self.design = fake_form_design(parametric=parametric, insert_edge=insert_edge)
        self.root = self.design.rootComponent
        self.forms = self.root.features.formFeatures
        self.form, self.body = add_box_form(self.design)
        self.aliases: dict[str, Any] = {"form": self.form, "body": self.body}

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

    def positions(self) -> list[list[float]]:
        return [list(v.p) for v in self.body.vertices]

    def last_form(self) -> Any:
        return self.forms.item(self.forms.count - 1)


def face_at(body: Any, xyz_cm: tuple[float, float, float]) -> Any:
    from fusion_addin.core.form import element_position

    return next(f for f in body.faces if element_position(f, "face") == pytest.approx(xyz_cm))


# --- form.list (2.1) -------------------------------------------------------------------------


def test_list_empty() -> None:
    design = fake_form_design()
    ctx = make_ctx(design, {})
    assert h.form_list(ctx, {}) == {"forms": [], "count": 0}


def test_list_one_form() -> None:
    s = Setup()
    out = s.run(h.form_list, {})
    assert out["count"] == 1
    (entry,) = out["forms"]
    assert entry["feature"] == "form" and entry["component"] == "root"
    assert entry["health"]["state"] == "ok"
    (body,) = entry["bodies"]
    assert body["ref"] == "body" and body["brep_body"] is not None
    assert body["vertex_count"] == 26


def test_list_filtered_by_component() -> None:
    s = Setup()
    other = FakeEntity(
        "adsk::fusion::Component",
        "Other",
        token="comp-other",
        features=FakeEntity("adsk::fusion::Features", formFeatures=FakeCollection()),
    )
    s.design.allComponents.add(other)
    s.aliases["other"] = other
    assert s.run(h.form_list, {"component": "other"})["count"] == 0
    assert s.run(h.form_list, {"component": "root"})["count"] == 1
    assert s.run(h.form_list, {})["count"] == 1


# --- form.create_primitive: box, plane (2.2) -------------------------------------------------


def test_box_40_20_10_new_form() -> None:
    s = Setup()
    out = s.run(h.form_create_primitive, {"shape": "box", "x_size": 40, "y_size": 20, "z_size": 10})
    form = s.last_form()
    assert s.forms.count == 2 and out["created_form"] is True
    inp = form.tSplineBodies.inputs[-1]
    assert inp.method == "createBoxInput"
    assert inp.args[3:] == pytest.approx((4.0, 2.0, 1.0))
    origin, x_dir, y_dir = inp.args[:3]
    assert (origin.x, origin.y, origin.z) == (0, 0, 0)
    assert (x_dir.x, y_dir.y) == (1.0, 1.0)
    assert out["body"]["brep_body"] is not None and out["form"] == form.entityToken
    assert form.log[:2] == ["finishEdit", "updateBRep"]
    assert out["body"]["type"] == "TSplineBody"


def test_box_options() -> None:
    s = Setup()
    out = s.run(
        h.form_create_primitive,
        {
            "shape": "box",
            "x_size": 10,
            "y_size": 10,
            "z_size": 10,
            "origin": [10, 0, 0],
            "x_direction": [0, 2, 0],
            "y_direction": [-3, 0, 0],
            "x_faces": 3,
            "z_faces": 1,
            "symmetry": {"x": True},
            "name": "Handle",
        },
    )
    inp = s.last_form().tSplineBodies.inputs[-1]
    assert inp.args[0].x == pytest.approx(1.0)
    assert (inp.args[1].y, inp.args[2].x) == (1.0, -1.0)
    assert (inp.xFaceCount, inp.zFaceCount) == (3, 1) and not hasattr(inp, "yFaceCount")
    assert (inp.isXSymmetric, inp.isYSymmetric, inp.isZSymmetric) == (True, False, False)
    assert out["body"]["name"] == "Handle"


def test_plane_in_existing_form() -> None:
    s = Setup()
    out = s.run(
        h.form_create_primitive, {"shape": "plane", "x_size": 10, "y_size": 20, "form": "form"}
    )
    inp = s.form.tSplineBodies.inputs[-1]
    assert inp.method == "createPlaneInput" and inp.args[3:] == pytest.approx((1.0, 2.0))
    assert out["created_form"] is False and out["form"] == "form"
    assert s.forms.count == 1 and s.form.tSplineBodies.count == 2


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"shape": "box", "x_size": "width", "y_size": 1, "z_size": 1}, "x_size"),
        ({"shape": "box", "x_size": 1, "y_size": 1}, "z_size"),
        ({"shape": "box", "x_size": 0, "y_size": 1, "z_size": 1}, "x_size"),
        ({"shape": "plane", "x_size": 1, "y_size": 1, "z_size": 1}, "z_size"),
        ({"shape": "plane", "x_size": 1, "y_size": 1, "x_faces": 0}, "x_faces"),
        ({"shape": "plane", "x_size": 1, "y_size": 1, "x_direction": [0, 0, 0]}, "x_direction"),
        ({"shape": "plane", "x_size": 1, "y_size": 1, "y_direction": [1, 1, 0]}, "y_direction"),
        ({"shape": "plane", "x_size": 1, "y_size": 1, "symmetry": {"w": True}}, "symmetry"),
        ({"shape": "plane", "x_size": 1, "y_size": 1, "symmetry": "circular"}, "symmetry"),
        ({"shape": "plane", "x_size": 1, "y_size": 1, "name": ""}, "name"),
        ({"shape": "cube"}, "shape"),
        (
            {
                "shape": "box",
                "x_size": 1,
                "y_size": 1,
                "z_size": 1,
                "form": "form",
                "component": "root",
            },
            "component",
        ),
    ],
)
def test_primitive_invalid_creates_nothing(params: dict[str, Any], param: str) -> None:
    s = Setup()
    err = s.invalid(h.form_create_primitive, params, param)
    if param == "x_size" and isinstance(params["x_size"], str):
        assert err.details["reason"] == "not_parametric"
        assert "not parametric" in err.message
    assert s.forms.count == 1 and s.form.tSplineBodies.count == 1


def test_primitive_in_existing_form_failing_is_undone() -> None:
    s = Setup()
    s.form.brep_mode = "never"
    err = s.error(
        h.form_create_primitive,
        {"shape": "box", "x_size": 1, "y_size": 1, "z_size": 1, "form": "form"},
    )
    assert err.code is ErrorCode.FUSION_API_ERROR and err.details["reason"] == "no_brep"
    assert s.form.tSplineBodies.count == 1
    assert s.form.log[-2:] == ["finishEdit", "updateBRep"]
    s.form.brep_mode = "ok"
    s.form.tSplineBodies.add_none = True
    err = s.error(h.form_create_primitive, {"shape": "sphere", "radius": 5, "form": "form"})
    assert err.code is ErrorCode.FUSION_API_ERROR


def test_primitive_new_form_rolled_back_by_dispatcher() -> None:
    s = Setup()
    s.forms.brep_mode = "never"
    names = s.design.timeline.names()
    response = s.dispatch("form.create_primitive", {"shape": "sphere", "radius": 5})
    assert response.ok is False and response.error.code is ErrorCode.FUSION_API_ERROR
    assert s.design.timeline.names() == names and s.forms.count == 1


def test_missing_shape_api_is_unavailable() -> None:
    s = Setup()
    s.form.tSplineBodies.missing.add("createQuadballInput")
    err = s.error(h.form_create_primitive, {"shape": "quadball", "radius": 5, "form": "form"})
    assert err.details == {"reason": "api_unavailable", "api": "TSplineBodies.createQuadballInput"}


# --- cylinder, sphere, torus, quadball (2.3) -------------------------------------------------


def test_cylinder_circular() -> None:
    s = Setup()
    s.run(
        h.form_create_primitive,
        {
            "shape": "cylinder",
            "radius": 10,
            "height": 30,
            "radial_faces": 8,
            "length_faces": 2,
            "symmetry": "circular",
            "symmetric_faces": 4,
        },
    )
    inp = s.last_form().tSplineBodies.inputs[-1]
    assert inp.method == "createCylinderInput" and inp.args[3:] == pytest.approx((1.0, 3.0))
    assert (inp.radialFaceCount, inp.lengthFaceCount) == (8, 2)
    assert inp.isCircularSymmetry is True and inp.symmetricFaceCount == 4


def test_sphere_torus_quadball() -> None:
    s = Setup()
    s.run(h.form_create_primitive, {"shape": "sphere", "radius": 5, "latitude_faces": 6})
    inp = s.last_form().tSplineBodies.inputs[-1]
    assert inp.method == "createSphereInput" and inp.args[3:] == pytest.approx((0.5,))
    assert inp.latitudeFaceCount == 6
    s.run(
        h.form_create_primitive,
        {"shape": "torus", "major_radius": 20, "minor_radius": 5, "major_faces": 8},
    )
    inp = s.last_form().tSplineBodies.inputs[-1]
    assert inp.args[3:] == pytest.approx((2.0, 0.5)) and inp.majorFaceCount == 8
    s.run(h.form_create_primitive, {"shape": "quadball", "radius": 1, "faces": 2, "units": "cm"})
    inp = s.last_form().tSplineBodies.inputs[-1]
    assert inp.args[3:] == pytest.approx((1.0,)) and inp.faceCount == 2
    assert s.forms.count == 4


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"shape": "sphere", "radius": 5, "height": 10}, "height"),
        ({"shape": "sphere"}, "radius"),
        ({"shape": "torus", "major_radius": 5, "minor_radius": 5}, "minor_radius"),
        ({"shape": "torus", "major_radius": 5, "minor_radius": 1, "faces": 3}, "faces"),
        ({"shape": "quadball", "radius": 5, "symmetry": "circular"}, "symmetry"),
        ({"shape": "cylinder", "radius": 5, "height": 5, "symmetric_faces": 4}, "symmetric_faces"),
        ({"shape": "cylinder", "radius": "r", "height": 5}, "radius"),
    ],
)
def test_round_shapes_invalid(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.form_create_primitive, params, param)
    assert s.forms.count == 1


# --- TSM (2.4, 2.5) --------------------------------------------------------------------------


def test_create_from_tsm_text_and_file(tmp_path: Path) -> None:
    s = Setup()
    out = s.run(h.form_create_from_tsm, {"tsm": "FAKE-TSM vertices=26", "name": "Copy"})
    assert out["created_form"] is True and out["body"]["name"] == "Copy"
    assert s.last_form().tSplineBodies.tsm_texts == ["FAKE-TSM vertices=26"]
    path = tmp_path / "shape.TSM"
    path.write_text("x", encoding="utf-8")
    out = s.run(h.form_create_from_tsm, {"path": str(path), "form": "form"})
    assert out["created_form"] is False
    assert s.form.tSplineBodies.tsm_files == [str(path)]


@pytest.mark.parametrize("params", [{}, {"tsm": "a", "path": "C:/x.tsm"}, {"tsm": ""}])
def test_create_from_tsm_needs_one_input(params: dict[str, Any]) -> None:
    Setup().invalid(h.form_create_from_tsm, params, "tsm")


def test_create_from_tsm_path_rules(tmp_path: Path) -> None:
    s = Setup()
    wrong = tmp_path / "a.txt"
    wrong.write_text("x", encoding="utf-8")
    err = s.invalid(h.form_create_from_tsm, {"path": str(wrong)}, "path")
    assert err.details["reason"] == "bad_extension"
    err = s.invalid(h.form_create_from_tsm, {"path": str(tmp_path / "none.tsm")}, "path")
    assert err.details["reason"] == "not_found"
    err = s.invalid(h.form_create_from_tsm, {"path": "relative.tsm"}, "path")
    assert err.details["reason"] == "not_absolute"
    assert s.forms.count == 1


def test_create_from_tsm_limit_and_invalid_text() -> None:
    s = Setup()
    err = s.invalid(h.form_create_from_tsm, {"tsm": "a" * (8 * 1024 * 1024 + 1)}, "tsm")
    assert err.details["reason"] == "too_large"
    names = s.design.timeline.names()
    response = s.dispatch("form.create_from_tsm", {"tsm": "invalid TSM"})
    assert response.ok is False and response.error.code is ErrorCode.FUSION_API_ERROR
    assert "invalid_tsm" in str(response.error.details)
    assert s.design.timeline.names() == names and s.forms.count == 1


def test_get_tsm_text_and_limit() -> None:
    s = Setup()
    out = s.run(h.form_get_tsm, {"body": "body"})
    assert out == {"body": "body", "tsm": "FAKE-TSM vertices=26", "size_bytes": 20}
    s.body.tsm = "x" * (1024 * 1024 + 1)
    err = s.invalid(h.form_get_tsm, {"body": "body"}, "path")
    assert err.details["reason"] == "too_large" and "use path" in err.message


def test_get_tsm_to_file(tmp_path: Path) -> None:
    s = Setup()
    target = tmp_path / "out.tsm"
    out = s.run(h.form_get_tsm, {"body": "body", "path": str(target)})
    assert out["files"] == [{"path": str(target), "size_bytes": 20}]
    assert target.read_text(encoding="utf-8") == "FAKE-TSM vertices=26"
    err = s.invalid(h.form_get_tsm, {"body": "body", "path": str(target)}, "path")
    assert err.details["reason"] == "exists"
    s.body.tsm = "new"
    s.run(h.form_get_tsm, {"body": "body", "path": str(target), "overwrite": True})
    assert target.read_text(encoding="utf-8") == "new"
    err = s.invalid(h.form_get_tsm, {"body": "body", "path": str(tmp_path / "a.obj")}, "path")
    assert err.details["reason"] == "bad_extension"


def test_tsm_needs_a_tspline_body() -> None:
    s = Setup()
    err = s.invalid(h.form_get_tsm, {"body": "form"}, "body")
    assert err.details["expected"] == ["TSplineBody"]


# --- inspect (2.6) ---------------------------------------------------------------------------


def test_inspect_vertices() -> None:
    s = Setup()
    out = s.run(h.form_inspect, {"body": "body", "include": ["vertices"]})
    assert len(out["vertices"]) == 26 and out["truncated"] is False
    assert out["vertices_truncated"] is False and "edges" not in out
    assert {"position": [20.0, 20.0, 20.0], "smooth_position": [19.0, 19.0, 19.0]} in out[
        "vertices"
    ]
    assert out["body"]["ref"] == "body"


def test_inspect_all_with_limit() -> None:
    s = Setup()
    s.body.edges.item(0).isCreased = True
    out = s.run(h.form_inspect, {"body": "body", "limit": 30})
    assert len(out["vertices"]) == 26 and len(out["edges"]) == 30 and len(out["faces"]) == 24
    assert out["edges_truncated"] is True and out["truncated"] is True
    edge = out["edges"][0]
    assert set(edge) == {"midpoint", "start", "end", "is_creased", "is_degenerate"}
    assert edge["is_creased"] is True
    pairs = zip(edge["start"], edge["end"], strict=True)
    assert edge["midpoint"] == [(a + b) / 2 for a, b in pairs]
    assert {"center": TOP_FACE, "vertex_count": 4} in out["faces"]


@pytest.mark.parametrize(
    ("params", "param"),
    [({"include": ["normals"]}, "include"), ({"include": []}, "include"), ({"limit": 0}, "limit")],
)
def test_inspect_invalid(params: dict[str, Any], param: str) -> None:
    Setup().invalid(h.form_inspect, {"body": "body", **params}, param)


# --- move (2.7) ------------------------------------------------------------------------------


def test_move_face_up_5_mm() -> None:
    s = Setup()
    face = face_at(s.body, (0.5, 0.5, 2.0))
    out = s.run(
        h.form_move, {"body": "body", "kind": "face", "near": [TOP_FACE], "vector": [0, 0, 5]}
    )
    assert face.moves == [pytest.approx((0, 0, 0.5))]
    assert out["changed"] == 1 and out["body"]["ref"] == "body"
    assert s.form.log == ["finishEdit", "updateBRep"]


def test_move_vertex_in_inches() -> None:
    s = Setup()
    out = s.run(
        h.form_move,
        {
            "body": "body",
            "kind": "vertex",
            "near": [[0.787402, 0.787402, 0.787402]],
            "vector": [0, 0, 1],
            "units": "in",
            "tolerance": 0.01,
        },
    )
    assert out["changed"] == 1
    moved = [v for v in s.body.vertices if v.moves]
    assert len(moved) == 1 and moved[0].moves == [pytest.approx((0, 0, 2.54))]


def test_move_second_face_fails_first_is_moved_back() -> None:
    s = Setup()
    start = s.positions()
    first = face_at(s.body, (0.5, 0.5, 2.0))
    second = face_at(s.body, (1.5, 1.5, 2.0))
    second.move_error = "Fusion refused the move"
    err = s.error(
        h.form_move,
        {"body": "body", "kind": "face", "near": [TOP_FACE, TOP_FACE2], "vector": [0, 0, 5]},
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert first.moves == [pytest.approx((0, 0, 0.5)), pytest.approx((0, 0, -0.5))]
    assert s.positions() == [pytest.approx(p) for p in start]
    assert s.form.log == ["finishEdit", "updateBRep"]


def test_move_body_transforms() -> None:
    s = Setup()
    out = s.run(
        h.form_move, {"body": "body", "kind": "body", "vector": [10, 0, 0], "near": [[999, 0, 0]]}
    )
    assert out["changed"] == 1 and len(s.body.transforms) == 1
    assert s.body.transforms[0][3] == pytest.approx(1.0)
    assert out["body"]["bbox"]["min"] == [10.0, 0.0, 0.0]


def test_move_far_point_changes_nothing() -> None:
    s = Setup()
    start = s.positions()
    err = s.invalid(
        h.form_move,
        {"body": "body", "kind": "vertex", "near": [[100, 100, 100]], "vector": [0, 0, 5]},
        "near",
    )
    assert err.details["reason"] == "no_match" and err.details["nearest"]
    assert s.positions() == start and s.form.log == []


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"kind": "face", "near": [TOP_FACE], "vector": [0, 0, 0]}, "vector"),
        ({"kind": "edge", "near": [TOP_FACE], "vector": [0, 0, 1]}, "kind"),
        ({"kind": "face", "near": [], "vector": [0, 0, 1]}, "near"),
        ({"kind": "face", "vector": [0, 0, 1]}, "near"),
        ({"kind": "face", "near": [TOP_FACE], "vector": [0, 0, 1], "tolerance": -1}, "tolerance"),
    ],
)
def test_move_invalid(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.form_move, {"body": "body", **params}, param)
    assert s.form.log == []


# --- crease (2.8) ----------------------------------------------------------------------------


def test_crease_and_uncrease() -> None:
    s = Setup()
    out = s.run(h.form_crease, {"body": "body", "near": [TOP_EDGE, TOP_EDGE2]})
    creased = [e for e in s.body.edges if e.isCreased]
    assert out["changed"] == 2 and len(creased) == 2
    assert s.run(h.form_crease, {"body": "body", "near": [TOP_EDGE]})["changed"] == 0
    out = s.run(h.form_crease, {"body": "body", "near": [TOP_EDGE], "creased": False})
    assert out["changed"] == 1 and sum(e.isCreased for e in s.body.edges) == 1
    assert s.form.log.count("updateBRep") == 3


def test_crease_error_restores_flags() -> None:
    s = Setup()
    from fusion_addin.core.form import select

    first, second = select(s.body, "edge", [(0.5, 0.0, 2.0), (1.5, 0.0, 2.0)], 0.05)
    second.crease_error = "nope"
    s.error(h.form_crease, {"body": "body", "near": [TOP_EDGE, TOP_EDGE2]})
    assert first.isCreased is False and first.crease_sets == [True, False]
    assert s.form.log == ["finishEdit", "updateBRep"]


# --- insert edge (2.9) -----------------------------------------------------------------------


def test_insert_edge_unavailable() -> None:
    s = Setup(insert_edge=False)
    err = s.error(h.form_insert_edge, {"body": "body", "near": [TOP_EDGE]})
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert err.details == {"reason": "api_unavailable", "api": "FormFeature.insertEdge"}


def test_insert_edge() -> None:
    s = Setup()
    out = s.run(
        h.form_insert_edge,
        {
            "body": "body",
            "near": [TOP_EDGE],
            "position": -0.25,
            "mode": "exact",
            "both_sides": True,
        },
    )
    (inp,) = s.form.inserted
    assert inp.distance == -0.25 and len(inp.edges) == 1
    assert inp.insertionMode == F.FormInsertionModes.ExactFormInsertionMode
    assert inp.insertionSide == F.FormEdgeInsertionSides.BothFormEdgeInsertionSide
    assert out["changed"] == 1 and out["new_edges"] == 2
    assert s.form.log == ["insertEdge", "finishEdit", "updateBRep"]
    s.run(h.form_insert_edge, {"body": "body", "near": [TOP_EDGE2]})
    inp = s.form.inserted[-1]
    assert inp.distance == 0.5
    assert inp.insertionMode == F.FormInsertionModes.SimpleFormInsertionMode
    assert inp.insertionSide == F.FormEdgeInsertionSides.SingleFormEdgeInsertionSide


@pytest.mark.parametrize(
    ("params", "param"),
    [({"position": 1.5}, "position"), ({"position": "half"}, "position"), ({"mode": "x"}, "mode")],
)
def test_insert_edge_invalid(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.form_insert_edge, {"body": "body", "near": [TOP_EDGE], **params}, param)
    assert s.form.inserted == []


def test_insert_edge_fusion_error() -> None:
    s = Setup()
    s.form.insert_error = "edges are not in one loop"
    err = s.error(h.form_insert_edge, {"body": "body", "near": [TOP_EDGE]})
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert s.form.log == ["insertEdge", "finishEdit", "updateBRep"]


# --- convert to B-Rep (2.10) -----------------------------------------------------------------


def test_convert_parametric() -> None:
    s = Setup()
    source = s.body.bRepBody
    out = s.run(h.form_convert_to_brep, {"body": "body", "name": "Shell"})
    (base,) = list(s.root.features.baseFeatures)
    assert base.log == ["startEdit", "finishEdit"] and len(base.bodies) == 1
    new = base.bodies[0]
    assert new in list(s.root.bRepBodies) and new is not source
    assert F.TemporaryBRepManager.get().copies == [source]
    assert out["body"] == s.ctx({}).refs.to_ref(new) and out["source"] == "body"
    assert out["base_feature"] is not None and out["is_solid"] is True
    assert out["name"] == "Shell" and new.name == "Shell"
    assert s.body.isLightBulbOn is False and source.isLightBulbOn is False
    assert "Base Feature1" in s.design.timeline.names()


def test_convert_keep_visible_and_direct() -> None:
    s = Setup()
    s.run(h.form_convert_to_brep, {"body": "body", "hide_form": False})
    assert s.body.isLightBulbOn is True
    d = Setup(parametric=False)
    out = d.run(h.form_convert_to_brep, {"body": "body"})
    assert out["base_feature"] is None and d.root.features.baseFeatures.count == 0
    (_added, base) = d.root.bRepBodies.added[-1]
    assert base is None and out["is_solid"] is True


def test_convert_without_brep_leaves_no_base_feature() -> None:
    s = Setup()
    s.form.brep_mode = "never"
    s.body.bRepBody = None
    names = s.design.timeline.names()
    response = s.dispatch("form.convert_to_brep", {"body": "body"})
    assert response.ok is False and response.error.code is ErrorCode.FUSION_API_ERROR
    assert s.root.features.baseFeatures.count == 0 and s.design.timeline.names() == names
    assert s.body.isLightBulbOn is True


def test_convert_copy_failure_keeps_source_visible() -> None:
    s = Setup()
    F.TemporaryBRepManager.get().copy_none = True
    err = s.error(h.form_convert_to_brep, {"body": "body"})
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert s.body.isLightBulbOn is True and s.root.features.baseFeatures.count == 0


def test_convert_add_failure_shows_source_again() -> None:
    s = Setup()
    s.root.bRepBodies.add_error = "cannot add"
    response = s.dispatch("form.convert_to_brep", {"body": "body"})
    assert response.ok is False
    assert s.root.features.baseFeatures.count == 0 and s.body.isLightBulbOn is True

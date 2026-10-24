"""``core/form.py``: selectors, form lifecycle, body description, undo (c14 D2-D4, D8; tasks
1.4, 1.5)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core import form as core
from fusion_addin.core.compensate import Compensation
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.fusion_features import make_ctx
from tests.fakes.fusion_form import add_box_form, fake_form_design

adsk = fusion_addin.adsk
V = adsk.core.Vector3D
TOL = 0.05  # 0.5 mm in cm


def box() -> tuple[Any, Any, Any]:
    design = fake_form_design()
    form, body = add_box_form(design)
    return design, form, body


# --- element_position / select (1.4) ---------------------------------------------------------


def test_element_positions() -> None:
    _, _, body = box()
    edge = body.edges.item(0)
    a, b = edge.startVertex.geometry, edge.endVertex.geometry
    assert core.element_position(edge, "edge") == pytest.approx(
        ((a.x + b.x) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2)
    )
    face = body.faces.item(0)
    pts = [v.geometry for v in face.vertices]
    assert core.element_position(face, "face") == pytest.approx(
        tuple(sum(getattr(p, c) for p in pts) / 4 for c in "xyz")
    )


def test_select_vertex_edge_face() -> None:
    _, _, body = box()
    vertex = core.select(body, "vertex", [(2.0, 2.0, 2.0)], TOL)
    assert len(vertex) == 1 and (vertex[0].geometry.x, vertex[0].geometry.z) == (2.0, 2.0)
    edge = core.select(body, "edge", [(0.5, 0.0, 2.0)], TOL)[0]
    assert core.element_position(edge, "edge") == pytest.approx((0.5, 0.0, 2.0))
    faces = core.select(body, "face", [(0.5, 0.5, 2.0), (0.52, 0.5, 2.0), (1.5, 1.5, 2.0)], TOL)
    assert len(faces) == 2  # the near-duplicate point picks the same face once
    assert core.element_position(faces[1], "face") == pytest.approx((1.5, 1.5, 2.0))


def test_select_far_point_lists_nearest() -> None:
    _, _, body = box()
    with pytest.raises(BridgeError) as info:
        core.select(body, "vertex", [(0.0, 0.0, 0.0), (10.0, 10.0, 10.0)], TOL)
    err = info.value
    assert err.code is ErrorCode.INVALID_PARAMS
    assert err.details["reason"] == "no_match" and err.details["index"] == 1
    assert err.details["param"] == "near" and err.details["point"] == [100.0, 100.0, 100.0]
    nearest = err.details["nearest"]
    assert len(nearest) == 5 and nearest[0]["position"] == [20.0, 20.0, 20.0]
    assert nearest[0]["distance_mm"] <= nearest[1]["distance_mm"]


def test_select_ambiguous() -> None:
    _, _, body = box()
    with pytest.raises(BridgeError) as info:
        core.select(body, "face", [(1.0, 0.5, 2.0)], 1.0)  # between two top faces
    assert info.value.details["reason"] == "ambiguous"
    assert len(info.value.details["nearest"]) >= 2


# --- lifecycle, description, undo (1.5) ------------------------------------------------------


def test_get_or_create_form() -> None:
    design, form, _ = box()
    ctx = make_ctx(design, {}, {"form": form})
    assert core.get_or_create_form(ctx, "form", None) == (form, False)
    new, created = core.get_or_create_form(ctx, None, None)
    assert created and new is not form and new.parentComponent is design.rootComponent
    with pytest.raises(BridgeError) as info:
        core.get_or_create_form(ctx, "form", "root")
    assert info.value.details["param"] == "component"


def test_finish_regenerates_and_falls_back() -> None:
    _design, form, body = box()
    body.bRepBody = None
    core.finish(form, [body])
    assert form.log == ["finishEdit", "updateBRep"] and body.bRepBody is not None

    form.brep_mode = "after_edit"
    body.bRepBody = None
    form.log.clear()
    core.finish(form, [body])
    assert form.log == ["finishEdit", "updateBRep", "startEdit", "finishEdit"]
    assert body.bRepBody is not None

    form.brep_mode = "never"
    body.bRepBody = None
    with pytest.raises(BridgeError) as info:
        core.finish(form, [body])
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert info.value.details["reason"] == "no_brep"
    assert "B-Rep was not generated" in info.value.message


def test_regenerate_on_error() -> None:
    _, form, _ = box()
    with pytest.raises(BridgeError) as info, core.regenerate_on_error(form):
        raise RuntimeError("boom")
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert form.log == ["finishEdit", "updateBRep"]
    form.update_error = "stuck"
    with pytest.raises(BridgeError) as info, core.regenerate_on_error(form):
        raise BridgeError(ErrorCode.INVALID_PARAMS, "bad", {})
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["finish_error"] == "stuck"


def test_describe_tsbody() -> None:
    design, form, body = box()
    ctx = make_ctx(design, {}, {"body": body, "form": form, "brep": body.bRepBody})
    out = core.describe_tsbody(ctx, body)
    assert out == {
        "ref": "body",
        "type": "TSplineBody",
        "name": body.name,
        "form": "form",
        "vertex_count": 26,
        "edge_count": 48,
        "face_count": 24,
        "bbox": {"min": [0.0, 0.0, 0.0], "max": [20.0, 20.0, 20.0]},
        "brep_body": "brep",
        "is_visible": True,
    }
    body.bRepBody = None
    assert core.describe_tsbody(ctx, body)["brep_body"] is None


def test_compensation_reverts_moves_transforms_creases() -> None:
    _, _, body = box()
    face, other = body.faces.item(0), body.faces.item(1)
    edge = body.edges.item(0)
    start = [list(v.p) for v in body.vertices]
    other.move_error = "refused"
    with pytest.raises(BridgeError), Compensation() as comp:
        core.move_element(comp, face, V(0, 0, 0.5), "face")
        assert core.set_crease(comp, edge, True) is True
        assert core.set_crease(comp, edge, True) is False
        matrix = adsk.core.Matrix3D.create()
        matrix.translation = V(1, 0, 0)
        core.transform_body(comp, body, matrix)
        core.move_element(comp, other, V(0, 0, 0.5), "other")
    assert [list(v.p) for v in body.vertices] == [pytest.approx(p) for p in start]
    assert face.moves == [(0, 0, 0.5), (-0.0, -0.0, -0.5)]
    assert other.moves == [] and edge.isCreased is False
    assert len(body.transforms) == 2

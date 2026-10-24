"""Form / T-spline fakes behave like the Fusion API the handlers use (c14; task 1.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

import fusion_addin
from tests.fakes.fusion_form import (
    FakeTSplineBody,
    add_box_form,
    build_box_mesh,
    fake_form_design,
)

adsk = fusion_addin.adsk
V = adsk.core.Vector3D


def test_box_mesh_counts_and_positions() -> None:
    body = build_box_mesh(FakeTSplineBody(None))
    assert (body.vertices.count, body.edges.count, body.faces.count) == (26, 48, 24)
    box = body.boundingBox
    assert (box.minPoint.x, box.maxPoint.z) == (0.0, 2.0)
    assert all(not hasattr(e, "entityToken") for e in [*body.vertices, *body.edges, *body.faces])
    assert all(f.vertices.count == 4 and f.edges.count == 4 for f in body.faces)


def test_form_lifecycle_and_brep_modes() -> None:
    design = fake_form_design()
    forms = design.rootComponent.features.formFeatures
    form, body = add_box_form(design, brep=False)
    assert design.timeline.names()[-1] == "Form1"
    assert body.bRepBody is None and body.parentFormFeature is form
    form.updateBRep()
    assert body.bRepBody is not None and design.by_token[body.bRepBody.entityToken]

    forms.brep_mode = "after_edit"
    form2, body2 = add_box_form(design)
    assert body2.bRepBody is None
    form2.startEdit()
    form2.finishEdit()
    assert body2.bRepBody is not None
    assert form2.log == ["startEdit", "finishEdit"]

    form2.deleteMe()
    assert "Form2" not in design.timeline.names() and form2.isValid is False


def test_shape_inputs_and_tsm() -> None:
    design = fake_form_design()
    form = design.rootComponent.features.formFeatures.add()
    bodies = form.tSplineBodies
    inp = bodies.createBoxInput(1, 2, 3)
    inp.xFaceCount = 3
    assert inp.objectType == "adsk::fusion::TSplineBoxInput" and inp.args == (1, 2, 3)
    assert bodies.addByShapeInput(inp) is not None
    assert bodies.addByTSMDescription("invalid") is None
    assert bodies.addByTSMDescription("good").vertices.count == 26
    bodies.missing.add("createTorusInput")
    with pytest.raises(AttributeError):
        _ = bodies.createTorusInput
    bodies.add_none = True
    assert bodies.addByShapeInput(inp) is None


def test_moves_transform_crease(tmp_path: Path) -> None:
    design = fake_form_design()
    _form, body = add_box_form(design)
    face = body.faces.item(0)
    face.move(V(0, 0, 0.5))
    assert face.moves == [(0, 0, 0.5)]
    vertex = body.vertices.item(0)
    before = vertex.geometry.z
    vertex.move(V(0, 0, 1))
    assert vertex.geometry.z == before + 1
    vertex.move_error = "nope"
    with pytest.raises(RuntimeError):
        vertex.move(V(1, 0, 0))
    matrix = adsk.core.Matrix3D.create()
    matrix.translation = V(1, 0, 0)
    body.transform(matrix)
    assert body.transforms and body.boundingBox.minPoint.x == 1.0
    edge = body.edges.item(0)
    edge.isCreased = True
    assert edge.isCreased and edge.crease_sets == [True]
    path = tmp_path / "a.tsm"
    body.saveAsTSMFile(str(path))
    assert path.read_text() == body.getTSMDescription()


def test_insert_edge_flag_and_base_features() -> None:
    design = fake_form_design(insert_edge=False)
    form, body = add_box_form(design)
    assert not hasattr(form, "insertEdge")
    design = fake_form_design()
    form, body = add_box_form(design)
    edges = [body.edges.item(0)]
    inp = adsk.fusion.FormInsertEdgeInput.create(edges, 0.5)
    assert form.insertEdge(inp) is True and body.edges.count == 49

    root = design.rootComponent
    base = root.features.baseFeatures.add()
    copy = adsk.fusion.TemporaryBRepManager.get().copy(body.bRepBody)
    added = root.bRepBodies.add(copy, base)
    assert added in list(root.bRepBodies) and base.bodies == [added]
    base.deleteMe()
    assert added not in list(root.bRepBodies)
    assert "Base Feature1" not in design.timeline.names()

"""Surface feature fakes (c11 D1; task 1.2)."""

from __future__ import annotations

import pytest

import fusion_addin
from tests.fakes.brep import FakeBody, fake_box_body
from tests.fakes.fusion_surfaces import (
    SURFACE_COLLECTIONS,
    FakeCellMeasure,
    fake_surface_design,
    surface_cell,
)

adsk = fusion_addin.adsk
P = adsk.core.Point3D.create


def test_all_collections_installed_next_to_c07_ones() -> None:
    design = fake_surface_design()
    features = design.rootComponent.features
    for attr, (kind, _, _) in SURFACE_COLLECTIONS.items():
        assert getattr(features, attr).kind == kind
    assert features.extrudeFeatures.kind == "ExtrudeFeature"


def test_create_input_and_add_are_recorded() -> None:
    design = fake_surface_design()
    patches = design.rootComponent.features.patchFeatures
    patches.will_create(FakeBody("Patch", is_solid=False))
    inp = patches.createInput("curve", 3)
    inp.groupContinuity = 1
    feature = patches.add(inp)
    assert inp.create_method == "createInput" and inp.create_args == ("curve", 3)
    assert inp.sets == {"groupContinuity": 1}
    assert patches.added == [inp]
    assert [b.name for b in feature.bodies] == ["Patch"]
    assert design.timeline.count == 1
    feature.deleteMe()
    assert design.timeline.count == 0


def test_untrim_has_create_input_from_faces_only() -> None:
    untrims = fake_surface_design().rootComponent.features.untrimFeatures
    inp = untrims.createInputFromFaces(["f"], 2, None)
    assert inp.create_args == (["f"], 2, None)
    with pytest.raises(AttributeError):
        untrims.createInputFromLoops  # noqa: B018


def test_trim_input_has_fresh_cells_and_records_cancel() -> None:
    trims = fake_surface_design().rootComponent.features.trimFeatures
    trims.cells = [surface_cell((0, 0, 0), (1, 1, 0), 1.0), surface_cell((1, 0, 0), (2, 1, 0))]
    first = trims.createInput("tool")
    cells = list(first.bRepCells)
    assert [c.cellBody for c in cells] == trims.cells
    cells[0].isSelected = True
    first.cancel()
    assert first.called("cancel")
    second = trims.createInput("tool")
    assert not any(c.isSelected for c in second.bRepCells)
    assert trims.cells[0].isSolid is False and trims.cells[0].area == 1.0


def test_unstitch_add_takes_entities() -> None:
    design = fake_surface_design()
    unstitches = design.rootComponent.features.unstitchFeatures
    feature = unstitches.add("faces", False)
    assert feature.input.create_args == ("faces", False)
    assert unstitches.added == [feature.input]


def test_open_profiles_are_recorded() -> None:
    root = fake_surface_design().rootComponent
    profile = root.createOpenProfile(["c1", "c2"], True)
    edge_profile = root.createBRepEdgeProfile(["e"], False)
    assert root.open_profiles == [profile, edge_profile]
    assert profile.method == "createOpenProfile" and profile.chain is True
    assert edge_profile.method == "createBRepEdgeProfile" and edge_profile.entities == ["e"]
    assert profile.objectType == "adsk::fusion::Profile"
    root.profile_error = "no chain"
    with pytest.raises(RuntimeError):
        root.createOpenProfile(["c"], True)


def test_cell_measure_distance_to_box() -> None:
    measure = FakeCellMeasure()
    cell = surface_cell((0, 0, 0), (1, 1, 1))
    assert measure.measureMinimumDistance(cell, P(0.5, 0.5, 0.5)).value == 0.0
    assert measure.measureMinimumDistance(cell, P(4, 1, 1)).value == pytest.approx(3.0)
    measure.error = "temporary body"
    with pytest.raises(RuntimeError):
        measure.measureMinimumDistance(cell, P(0, 0, 0))
    assert len(measure.calls) == 3


def test_bodies_still_work() -> None:
    box = fake_box_body(1, 1, 1, name="Box")
    design = fake_surface_design(box)
    assert box.parentComponent is design.rootComponent

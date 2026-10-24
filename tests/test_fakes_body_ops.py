"""c08 additions to ``tests/fakes/fusion_features.py`` (D15, task 1.5): pattern/body
collections, direct-add collections, boundary fill cells and the ``on_add`` hook."""

from __future__ import annotations

from typing import Any

import fusion_addin
from tests.fakes.brep import FakeBody, FakeComponent, fake_box_body
from tests.fakes.fusion_features import (
    DIRECT_COLLECTIONS,
    FakeBRepCell,
    FakeCellBody,
    FakeDirectCollection,
    FakeFeatureCollection,
    fake_feature_design,
    install_features,
)

adsk = fusion_addin.adsk
PC = adsk.fusion.PointContainment

C08 = {
    "rectangularPatternFeatures": "RectangularPatternFeature",
    "circularPatternFeatures": "CircularPatternFeature",
    "pathPatternFeatures": "PathPatternFeature",
    "mirrorFeatures": "MirrorFeature",
    "combineFeatures": "CombineFeature",
    "splitBodyFeatures": "SplitBodyFeature",
    "splitFaceFeatures": "SplitFaceFeature",
    "moveFeatures": "MoveFeature",
    "copyPasteBodies": "CopyPasteBody",
    "scaleFeatures": "ScaleFeature",
    "thickenFeatures": "ThickenFeature",
    "boundaryFillFeatures": "BoundaryFillFeature",
    "deleteFaceFeatures": "DeleteFaceFeature",
    "replaceFaceFeatures": "ReplaceFaceFeature",
    "removeFeatures": "RemoveFeature",
}


def test_c08_collections_are_installed() -> None:
    features = install_features(FakeComponent("part")).features
    for attr, kind in C08.items():
        collection = getattr(features, attr)
        assert isinstance(collection, FakeFeatureCollection)
        assert collection.kind == kind
        assert isinstance(collection, FakeDirectCollection) == (attr in DIRECT_COLLECTIONS)
    assert hasattr(features.moveFeatures, "createInput2")
    assert not hasattr(features.removeFeatures, "createInput")


def test_direct_collection_keeps_entities_and_rolls_back() -> None:
    box = fake_box_body(1, 1, 1, name="Box")
    design = fake_feature_design(box)
    root = design.rootComponent
    faces = adsk.core.ObjectCollection.create()
    faces.add(box.faces.item(0))
    deleted = root.features.deleteFaceFeatures.will_modify(box).add(faces)
    assert deleted.input.create_args == (faces,)
    assert list(deleted.bodies) == [box]
    assert deleted.objectType == "adsk::fusion::DeleteFaceFeature"

    removed = root.features.removeFeatures.add(box)
    assert box not in list(root.bRepBodies)
    assert removed.input.create_args == (box,)
    assert design.timeline.count == 2
    removed.deleteMe()
    assert box in list(root.bRepBodies)


def test_copy_paste_creates_copies() -> None:
    box = fake_box_body(1, 1, 1, name="Box")
    design = fake_feature_design(box)
    root = design.rootComponent
    copy = FakeBody("Box (1)")
    feature = root.features.copyPasteBodies.will_create(copy).add(box)
    assert list(feature.bodies) == [copy]
    assert copy in list(root.bRepBodies)
    assert design.findEntityByToken(copy.entityToken) == [copy]


def test_boundary_fill_input_has_cells_answering_point_containment() -> None:
    design = fake_feature_design()
    fills = design.rootComponent.features.boundaryFillFeatures
    inner = FakeCellBody((0, 0, 0), (1, 1, 1), name="Inner")
    outer = FakeCellBody((2, 0, 0), (3, 1, 1), name="Outer")
    fills.cells = [inner, outer]
    inp = fills.createInput("tools", 3)
    cells = inp.bRepCells
    assert cells.count == 2
    assert all(isinstance(c, FakeBRepCell) and c.isSelected is False for c in cells)
    assert cells.item(0).cellBody is inner
    p = adsk.core.Point3D.create(0.5, 0.5, 0.5)
    assert inner.pointContainment(p) == PC.PointInsidePointContainment
    assert outer.pointContainment(p) == PC.PointOutsidePointContainment
    assert inner.queries == [p]
    assert inner.volume == 1
    inp.cancel()
    assert inp.called("cancel")
    assert ("bRepCells", cells) not in [(n, v) for n, v in inp.sets.items()]


def test_on_add_hook_runs_with_the_feature() -> None:
    design = fake_feature_design()
    splits = design.rootComponent.features.splitFaceFeatures
    seen: list[Any] = []
    splits.on_add = seen.append
    feature = splits.add(splits.createInput("faces", "tool", True))
    assert seen == [feature]

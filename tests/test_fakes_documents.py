"""Structure of the document and parameter fakes (c04 task 1.2)."""

import math
from typing import Any

import pytest

import fusion_addin
from tests.fakes.design import FakeDesign
from tests.fakes.documents import FakeDataFolder, FakeDocsApp
from tests.fakes.parameters import attach_parameters

adsk = fusion_addin.adsk


def test_stub_enums() -> None:
    assert adsk.core.DocumentTypes.FusionDesignDocumentType == 0
    units = adsk.fusion.DistanceUnits
    assert [
        units.MillimeterDistanceUnits,
        units.CentimeterDistanceUnits,
        units.MeterDistanceUnits,
        units.InchDistanceUnits,
        units.FootDistanceUnits,
    ] == [0, 1, 2, 3, 4]
    assert adsk.fusion.DesignTypes.ParametricDesignType != adsk.fusion.DesignTypes.DirectDesignType


def test_documents_track_the_active_one() -> None:
    app = FakeDocsApp()
    assert app.activeDocument is None and app.activeProduct is None
    first = app.open_document("A", file_id="urn:a")
    second = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    assert app.documents.count == 2
    assert app.activeDocument is second
    assert app.activeProduct is second.design
    assert isinstance(adsk.fusion.Design.cast(app.activeProduct), FakeDesign)
    assert second.design.parentDocument is second
    first.activate()
    assert app.activeDocument is first
    first.close(False)
    assert app.activeDocument is second
    assert list(app.documents) == [second]


def test_saved_and_unsaved_documents() -> None:
    app = FakeDocsApp()
    doc = app.open_document("Part", modified=True)
    assert doc.isSaved is False and doc.dataFile is None
    doc.data_file_raises = True
    with pytest.raises(RuntimeError):
        _ = doc.dataFile
    doc.data_file_raises = False
    folder = app.data.activeProject.rootFolder
    assert doc.saveAs("Bracket", folder, "first", "") is True
    assert doc.isSaved and doc.name == "Bracket" and doc.isModified is False
    assert doc.dataFile is not None and doc.dataFile.parentFolder is folder
    assert doc.save("again") is True
    assert doc.dataFile.versionNumber == 2
    doc.save_result = False
    assert doc.save("x") is False


def test_data_folders_and_find_file() -> None:
    app = FakeDocsApp()
    app.data.activeProject.rootFolder = FakeDataFolder("root", [FakeDataFolder("A")])
    assert app.data.activeProject.rootFolder.dataFolders.itemByName("A").name == "A"
    assert app.data.activeProject.rootFolder.dataFolders.itemByName("B") is None
    data_file = app.data.add_file("urn:x", "X")
    assert app.data.findFileById("urn:x") is data_file
    assert app.data.findFileById("urn:none") is None
    doc = app.documents.open(data_file, True)
    assert doc.dataFile is data_file and app.activeDocument is doc


def test_import_manager() -> None:
    app = FakeDocsApp()
    options = app.importManager.createFusionArchiveImportOptions("C:/parts/hinge.f3d")
    doc = app.importManager.importToNewDocument(options)
    assert doc.name == "hinge" and app.activeDocument is doc
    assert app.importManager.imported == ["C:/parts/hinge.f3d"]


def test_non_design_document() -> None:
    app = FakeDocsApp()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDrawingDocumentType)
    assert doc.products.itemByProductType("DesignProductType") is None
    assert app.activeProduct is None


def test_parameters_evaluate_and_depend() -> None:
    design: Any = FakeDesign()
    params = attach_parameters(design)
    vi = adsk.core.ValueInput
    width = design.userParameters.add("width", vi.createByString("40 mm"), "mm", "")
    half = design.userParameters.add("half", vi.createByString("width / 2"), "mm", "c")
    angle = design.userParameters.add("tilt", vi.createByString("30 deg"), "deg", "")
    plain = design.userParameters.add("n", vi.createByString("3"), "", "")
    inches = design.userParameters.add("w_in", vi.createByString("1 in"), "in", "")
    assert width.value == pytest.approx(4.0)
    assert half.value == pytest.approx(2.0)
    assert angle.value == pytest.approx(math.pi / 6)
    assert plain.value == 3.0
    assert inches.value == pytest.approx(2.54)
    assert [p.name for p in width.dependentParameters] == ["half"]
    assert width.isDeletable is False and half.isDeletable is True
    assert design.allParameters.itemByName("half") is half
    assert design.userParameters.count == 5
    with pytest.raises(RuntimeError):
        design.userParameters.add("bad", vi.createByString("nope * 2"), "mm", "")
    with pytest.raises(RuntimeError):
        design.userParameters.add("width", vi.createByString("1 mm"), "mm", "")
    width.name = "w"
    assert half.expression == "w / 2"
    assert width.deleteMe() is False
    assert half.deleteMe() is True and half.deleted
    assert params.get("half") is None


def test_model_parameters_and_units_manager() -> None:
    design: Any = FakeDesign()
    params = attach_parameters(design)
    root = design.rootComponent
    extrude = object()
    d1 = params.add_model("d1", "10 mm", created_by=extrude, role="AlongDistance", component=root)
    assert root.modelParameters.itemByName("d1") is d1
    assert d1.isDeletable is False
    with pytest.raises(RuntimeError):
        d1.deleteMe()
    assert design.userParameters.count == 0 and design.allParameters.count == 1
    manager = design.unitsManager
    assert manager is design.fusionUnitsManager
    assert manager.formatInternalValue(4.0, "mm", True) == "40 mm"
    manager.distanceDisplayUnits = adsk.fusion.DistanceUnits.InchDistanceUnits
    assert manager.defaultLengthUnits == "in"
    manager.unformattable.add("kg")
    with pytest.raises(RuntimeError):
        manager.formatInternalValue(1.0, "kg", True)


def test_on_change_and_fail_on() -> None:
    design: Any = FakeDesign()
    params = attach_parameters(design)
    seen: list[int] = []
    params.on_change = lambda p: seen.append(len(p.params))
    params.fail_on.add("bad")
    vi = adsk.core.ValueInput
    design.userParameters.add("a", vi.createByString("1 mm"), "mm", "")
    with pytest.raises(RuntimeError):
        design.userParameters.add("bad", vi.createByString("1 mm"), "mm", "")
    assert seen == [1]

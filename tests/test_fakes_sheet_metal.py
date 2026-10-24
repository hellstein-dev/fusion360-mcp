"""Sheet metal fakes behave like the Fusion API they stand in for (c13 D1; task 1.3)."""

from __future__ import annotations

import math

import pytest

import fusion_addin
from tests.fakes.brep import fake_box_body
from tests.fakes.design import FakeApp
from tests.fakes.fusion_sheet_metal import (
    FakeFlatPatternProduct,
    fake_sheet_metal_design,
    install_sheet_metal_app,
    make_sheet_metal,
)

adsk = fusion_addin.adsk


def test_rules_library_and_copy() -> None:
    design = fake_sheet_metal_design()
    library = design.librarySheetMetalRules
    assert library.names == ["Steel", "Aluminum"]
    steel = library.itemByName("Steel")
    assert steel.isDefault and steel.thickness.value == pytest.approx(0.1)
    assert steel.bendRadius.expression == "Thickness"
    copy = design.designSheetMetalRules.addByCopy(steel, "Mine")
    assert design.designSheetMetalRules.names == ["Mine"]
    assert copy.isDefault is False and copy.thickness.value == pytest.approx(0.1)
    assert copy.deleteMe() is True and design.designSheetMetalRules.names == []


def test_rule_values_evaluate_expressions() -> None:
    design = fake_sheet_metal_design()
    rule = design.designSheetMetalRules.addByCopy(design.librarySheetMetalRules.item(0), "R")
    rule.thickness.value = 0.2
    assert rule.thickness.expression == "2 mm"
    rule.bendRadius.expression = "Thickness * 1.5"
    assert rule.bendRadius.value == pytest.approx(0.3)
    rule.gap.expression = "3 mm"
    assert rule.gap.value == pytest.approx(0.3)
    rule.set_error = "boom"
    with pytest.raises(RuntimeError):
        rule.gap.value = 1.0
    rule.isUsed = True
    with pytest.raises(RuntimeError):
        rule.deleteMe()


def test_copy_error() -> None:
    design = fake_sheet_metal_design()
    design.designSheetMetalRules.copy_error = "nope"
    with pytest.raises(RuntimeError):
        design.designSheetMetalRules.addByCopy(design.librarySheetMetalRules.item(0), "X")


def test_flange_inputs_and_edge_definitions() -> None:
    design = fake_sheet_metal_design()
    flanges = design.rootComponent.features.flangeFeatures
    inp = flanges.createEdgeFlangeInput([], None)
    definition = inp.edgeDefinitions.addSymmetricEdge("edge", "w")
    assert definition.create_method == "addSymmetricEdge" and definition.create_args == (
        "edge",
        "w",
    )
    inp.bendRadiusOverride.setOverride("r")
    assert inp.bendRadiusOverride.call("setOverride") == ("r",)
    feature = flanges.add(inp)
    assert feature.objectType == "adsk::fusion::FlangeFeature"
    assert hasattr(design.rootComponent.features.unfoldFeatures, "createInput")
    assert hasattr(design.rootComponent.features.refoldFeatures, "createInput")


def test_without_preview_api() -> None:
    design = fake_sheet_metal_design(preview=False)
    features = design.rootComponent.features
    assert not hasattr(features.flangeFeatures, "createContourFlangeInput")
    assert not hasattr(features.unfoldFeatures, "createInput")
    assert not hasattr(features.refoldFeatures, "createInput")
    assert not hasattr(design.exportManager, "createSTEPExportOptionsForFlatPattern")
    assert hasattr(design.exportManager, "createDXFFlatPatternExportOptions")
    body = make_sheet_metal(fake_box_body(1, 1, 1), is_sheet_metal=False, preview=False)
    assert body.isSheetMetal is False
    assert not hasattr(body, "convertToSheetMetal") and not hasattr(body, "getBendFaces")


def test_body_conversion_and_bend_faces() -> None:
    body = make_sheet_metal(fake_box_body(4, 2, 0.1), is_sheet_metal=False, bends=2, radius=0.1)
    face = body.faces.item(0)
    assert body.convertToSheetMetal(face, "rule") is True
    assert body.isSheetMetal is True and body.convert_calls == [(face, "rule")]
    ok, inner, outer = body.getBendFaces()
    assert ok and len(inner) == len(outer) == 2
    assert inner[0].geometry.radius == pytest.approx(0.1)
    assert outer[0].geometry.radius == pytest.approx(0.2)
    body.convert_result = False
    assert body.convertToSheetMetal(face, None) is False


def test_flat_pattern_switches_product_and_back() -> None:
    body = make_sheet_metal(fake_box_body(4, 2, 0.1))
    design = fake_sheet_metal_design(body)
    app = install_sheet_metal_app(FakeApp(design), design)
    root = design.rootComponent
    root.flat_bends = [(True, math.pi / 2), (False, math.pi / 4)]
    flat = root.createFlatPattern(body.faces.item(0))
    assert root.flatPattern is flat
    assert isinstance(app.activeProduct, FakeFlatPatternProduct)
    edges = flat.bendLinesBody.edges
    assert edges.count == 2
    assert flat.getBendInfo(edges.item(1)) == (True, False, math.pi / 4)
    app.userInterface.workspaces.itemById("FusionSolidEnvironment").activate()
    assert app.activeProduct is design
    assert flat.deleteMe() is True and root.flatPattern is None


def test_dxf_flat_options() -> None:
    design = fake_sheet_metal_design()
    options = design.exportManager.createDXFFlatPatternExportOptions("C:/x.dxf", "fp")
    assert options.kind == "dxf" and options.geometry == "fp"
    assert options.units == adsk.fusion.DistanceUnits.CentimeterDistanceUnits

"""Preview API wrappers and sheet metal helpers (c13 D1, D3, D7; tasks 1.4, 1.5)."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core import sheet_metal as sm
from fusion_addin.core import sheet_metal_api as api
from fusion_addin.core.compensate import Compensation
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import fake_box_body
from tests.fakes.fusion_features import make_ctx
from tests.fakes.fusion_sheet_metal import fake_sheet_metal_design, make_sheet_metal

adsk = fusion_addin.adsk


class Probe:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def thing(self, *args: Any) -> str:
        self.calls.append(args)
        return "ok"


# --- 1.4 require_api and wrappers ------------------------------------------------------------


def test_require_api_passes_the_call_through() -> None:
    probe = Probe()
    assert api.require_api(probe, "thing", "Probe.thing")(1, 2) == "ok"
    assert probe.calls == [(1, 2)]
    assert api.has_api(probe, "thing") is True


@pytest.mark.parametrize("obj", [Probe(), None])
def test_require_api_missing(obj: Any) -> None:
    with pytest.raises(BridgeError) as info:
        api.require_api(obj, "other", "Probe.other")
    err = info.value
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert err.details == {"reason": "api_unavailable", "api": "Probe.other"}
    assert "Probe.other" in err.message
    assert api.has_api(obj, "other") is False


def test_wrappers_call_fusion() -> None:
    body = make_sheet_metal(fake_box_body(2, 2, 0.1), is_sheet_metal=False, bends=1)
    design = fake_sheet_metal_design(body)
    root = design.rootComponent
    inp = api.create_contour_flange_input(root, "profile", "dist")
    assert inp.create_method == "createContourFlangeInput" and inp.create_args == (
        "profile",
        "dist",
    )
    assert api.create_base_flange_input(root, ["p"]).create_args == (["p"],)
    assert api.create_edge_flange_input(root, [], None).create_args == ([], None)
    assert api.create_unfold_input(root, "face").create_args == ("face",)
    assert api.create_refold_input(root, "unfold").create_args == ("unfold",)
    assert api.convert_to_sheet_metal(body, "face", "rule") is True
    inner, outer = api.get_bend_faces(body)
    assert len(inner) == len(outer) == 1
    options = api.create_flat_pattern_step_options(design.exportManager, "C:/a.step", "fp")
    assert options.kind == "step" and options.geometry == "fp"


@pytest.mark.parametrize(
    ("call", "name"),
    [
        (
            lambda root, body, d: api.create_base_flange_input(root, []),
            "FlangeFeatures.createBaseFlangeInput",
        ),
        (
            lambda root, body, d: api.create_contour_flange_input(root, [], 1),
            "FlangeFeatures.createContourFlangeInput",
        ),
        (
            lambda root, body, d: api.create_edge_flange_input(root, [], 1),
            "FlangeFeatures.createEdgeFlangeInput",
        ),
        (lambda root, body, d: api.create_unfold_input(root, "f"), "UnfoldFeatures.createInput"),
        (lambda root, body, d: api.create_refold_input(root, "u"), "RefoldFeatures.createInput"),
        (
            lambda root, body, d: api.convert_to_sheet_metal(body, "f", None),
            "BRepBody.convertToSheetMetal",
        ),
        (lambda root, body, d: api.get_bend_faces(body), "BRepBody.getBendFaces"),
        (
            lambda root, body, d: api.create_flat_pattern_step_options(d.exportManager, "p", "fp"),
            "ExportManager.createSTEPExportOptionsForFlatPattern",
        ),
    ],
)
def test_wrappers_without_preview_api(call: Any, name: str) -> None:
    body = make_sheet_metal(fake_box_body(2, 2, 0.1), preview=False)
    design = fake_sheet_metal_design(body, preview=False)
    with pytest.raises(BridgeError) as info:
        call(design.rootComponent, body, design)
    assert info.value.details == {"reason": "api_unavailable", "api": name}


def test_bend_faces_failure() -> None:
    body = make_sheet_metal(fake_box_body(2, 2, 0.1))
    body.getBendFaces = lambda: (False, [], [])
    with pytest.raises(BridgeError) as info:
        api.get_bend_faces(body)
    assert info.value.code is ErrorCode.FUSION_API_ERROR


# --- 1.5 rules and flat pattern helpers ------------------------------------------------------


def ctx_for(design: Any) -> Any:
    return make_ctx(design, {})


def test_describe_rule_in_mm() -> None:
    design = fake_sheet_metal_design()
    rule = design.librarySheetMetalRules.itemByName("Steel")
    data = sm.describe_rule(rule, "library")
    assert data["name"] == "Steel" and data["source"] == "library" and data["units"] == "mm"
    assert data["thickness"] == {"mm": 1.0, "expression": "1 mm"}
    assert data["bend_radius"] == {"mm": 1.0, "expression": "Thickness"}
    assert data["relief_depth"]["mm"] == pytest.approx(0.5)
    assert data["k_factor"] == pytest.approx(0.44)
    assert data["relief_shape"] == "round"
    assert data["is_default"] is True and "is_used" not in data
    assert "is_default" not in sm.describe_rule(rule, "design")


def test_find_rule_design_first_then_library() -> None:
    design = fake_sheet_metal_design()
    ctx = ctx_for(design)
    copy = design.designSheetMetalRules.addByCopy(design.librarySheetMetalRules.item(0), "Steel")
    assert sm.find_rule(ctx, "Steel") == (copy, "design")
    assert sm.find_rule(ctx, "Steel", "library")[1] == "library"
    assert sm.find_rule(ctx, "Aluminum")[1] == "library"
    with pytest.raises(BridgeError) as info:
        sm.find_rule(ctx, "Brass")
    assert info.value.code is ErrorCode.ENTITY_NOT_FOUND
    assert info.value.details["known"] == ["Steel", "Steel", "Aluminum"]
    with pytest.raises(BridgeError) as bad:
        sm.find_rule(ctx, "Steel", "cloud")
    assert bad.value.code is ErrorCode.INVALID_PARAMS


def test_set_rule_value_number_and_expression() -> None:
    design = fake_sheet_metal_design()
    rule = design.designSheetMetalRules.addByCopy(design.librarySheetMetalRules.item(0), "R")
    sm.set_rule_value(rule.thickness, 2, "mm", "thickness")
    assert rule.thickness.sets[-1] == ("value", pytest.approx(0.2))
    sm.set_rule_value(rule.gap, 0.1, "in", "gap")
    assert rule.gap.value == pytest.approx(0.254)
    sm.set_rule_value(rule.bendRadius, "Thickness * 1.5", "mm", "bend_radius")
    assert rule.bendRadius.sets[-1] == ("expression", "Thickness * 1.5")
    for bad in (-1, True, "  ", math.inf):
        with pytest.raises(BridgeError) as info:
            sm.set_rule_value(rule.gap, bad, "mm", "gap")
        assert info.value.details["param"] == "gap"


def test_k_factor_range() -> None:
    assert sm.check_k_factor(0.44) == pytest.approx(0.44)
    with pytest.raises(BridgeError) as info:
        sm.check_k_factor(1.5)
    assert info.value.details["param"] == "k_factor"


def test_apply_library_rule_copies_and_compensates() -> None:
    design = fake_sheet_metal_design()
    root = design.rootComponent
    old = design.designSheetMetalRules.addByCopy(design.librarySheetMetalRules.item(1), "Old")
    root.activeSheetMetalRule = old
    with pytest.raises(BridgeError), Compensation() as comp:
        result = sm.apply_rule(ctx_for(design), root, "Steel", "all", comp)
        assert result["copied_from_library"] is True and result["previous"] == "Old"
        assert root.activeSheetMetalRule.name == "Steel"
        raise RuntimeError("later failure")
    assert root.activeSheetMetalRule is old
    assert design.designSheetMetalRules.names == ["Old"]


def test_apply_design_rule_keeps_it_on_success() -> None:
    design = fake_sheet_metal_design()
    root = design.rootComponent
    with Compensation() as comp:
        result = sm.apply_rule(ctx_for(design), root, "Aluminum", "library", comp)
    assert result["rule"]["name"] == "Aluminum" and result["previous"] is None
    assert design.designSheetMetalRules.names == ["Aluminum"]
    with Compensation() as comp:
        again = sm.apply_rule(ctx_for(design), root, "Aluminum", "library", comp)
    assert again["copied_from_library"] is False and again["previous"] == "Aluminum"
    assert design.designSheetMetalRules.names == ["Aluminum"]


def test_enum_mapping() -> None:
    assert sm.sheet_metal_enum("relief_shape", "relief_shape", "tear") == (
        adsk.fusion.BendReliefShapes.TearBendReliefShape
    )
    assert (
        sm.enum_name("bend_position", adsk.fusion.BendPositionTypes.InsideBendPositionType)
        == "inside"
    )
    with pytest.raises(BridgeError) as info:
        sm.sheet_metal_enum("height_datum", "height_datum", "middle")
    assert info.value.code is ErrorCode.INVALID_PARAMS


def test_describe_flat_pattern() -> None:
    body = make_sheet_metal(fake_box_body(8, 3, 0.1))
    design = fake_sheet_metal_design(body)
    root = design.rootComponent
    root.flat_bends = [(True, math.pi / 2), (False, math.pi / 6)]
    root.activeSheetMetalRule = design.librarySheetMetalRules.item(1)
    flat = root.createFlatPattern(body.faces.item(0))
    data = sm.describe_flat_pattern(ctx_for(design), root, flat)
    assert data["component"] == "root" and data["exists"] is True
    assert data["size_mm"] == [80.0, 30.0]
    assert data["thickness_mm"] == pytest.approx(2.0)
    assert data["bend_count"] == 2
    assert data["bends"] == [
        {"angle_deg": pytest.approx(90.0), "is_up": True},
        {"angle_deg": pytest.approx(30.0), "is_up": False},
    ]

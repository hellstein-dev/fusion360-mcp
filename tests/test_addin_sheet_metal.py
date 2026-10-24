"""``sheet_metal.*`` handlers on sheet metal fakes (c13 D3-D8; tasks 2.1-4.6)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core import sheet_metal as core_sm
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import sheet_metal as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.sketch_refs import format_profile_ref
from fusion_mcp.protocol.wire import Request
from tests.fakes.brep import FakeBody, fake_box_body
from tests.fakes.fusion_features import make_ctx
from tests.fakes.fusion_sheet_metal import (
    fake_sheet_metal_design,
    install_sheet_metal_app,
    make_sheet_metal,
)
from tests.fakes.fusion_sketch import auto_profiles, make_sketch, polygon_curves
from tests.fakes.fusion_surfaces import install_surfaces

adsk = fusion_addin.adsk
F = adsk.fusion
OPS = F.FeatureOperations
TOKEN = "t" * 32


def real(value_input: Any) -> float:
    assert value_input.kind == "real", value_input
    return float(value_input.value)


class Setup:
    """Sheet metal body ``sheet`` (4 x 2 x 0.1 cm, one bend; faces ``top``/``side``, edge
    ``edge`` along X), a second sheet ``sheet2``, a plain plate ``plate``, a 40 x 20 mm
    rectangle sketch (``profile``) and an open L sketch (``line1``, ``line2``)."""

    def __init__(self, preview: bool = True) -> None:
        self.sheet = make_sheet_metal(
            fake_box_body(4, 2, 0.1, name="Sheet"), bends=1, preview=preview
        )
        self.sheet2 = make_sheet_metal(fake_box_body(1, 1, 0.1, name="Sheet2"), preview=preview)
        self.plate = make_sheet_metal(
            fake_box_body(4, 2, 0.2, name="Plate"), is_sheet_metal=False, preview=preview
        )
        self.design = fake_sheet_metal_design(self.sheet, self.sheet2, self.plate, preview=preview)
        self.root = self.design.rootComponent
        self.features = self.root.features
        self.flanges = self.features.flangeFeatures
        faces = {
            f.entityToken.rsplit("face", 1)[1]: f
            for f in self.sheet.faces
            if "face" in f.entityToken
        }
        self.top, self.side = faces["+z"], faces["+x"]
        self.plate_top = next(f for f in self.plate.faces if f.entityToken.endswith("face+z"))
        self.edge = next(e for e in self.sheet.edges if e.entityToken.endswith("e000100"))
        self.edge2 = next(e for e in self.sheet.edges if e.entityToken.endswith("e010110"))
        self.sketch = make_sketch(self.design, "XY")
        polygon_curves(self.sketch, [(0, 0), (4, 0), (4, 2), (0, 2)])
        self.sketch.profile_builder = auto_profiles
        self.profile = format_profile_ref(self.sketch.entityToken, 20, 10)
        self.open_sketch = make_sketch(self.design, "XZ")
        self.line1, self.line2 = polygon_curves(
            self.open_sketch, [(0, 0), (4, 0), (4, 3)], closed=False
        )
        install_surfaces(self.root, self.design)  # createOpenProfile (c11)
        self.timeline_start = self.design.timeline.count
        self.aliases: dict[str, Any] = {
            "sheet": self.sheet,
            "sheet2": self.sheet2,
            "plate": self.plate,
            "top": self.top,
            "side": self.side,
            "plate_top": self.plate_top,
            "edge": self.edge,
            "edge2": self.edge2,
            "edge_other": self.sheet2.edges.item(0),
            "plate_edge": self.plate.edges.item(0),
            "line1": self.line1,
            "line2": self.line2,
        }
        self.app: Any = None

    def ctx(self, params: dict[str, Any]) -> Context:
        ctx = make_ctx(self.design, params, self.aliases)
        self.app = install_sheet_metal_app(ctx.app, self.design)
        return ctx

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

    def unavailable(self, fn: Any, params: dict[str, Any], api: str) -> BridgeError:
        err = self.error(fn, params)
        assert err.code is ErrorCode.FUSION_API_ERROR, err
        assert err.details == {"reason": "api_unavailable", "api": api}
        return err

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        dispatcher = Dispatcher(TOKEN, context_factory=lambda p: self.ctx(p))
        return dispatcher.dispatch(Request("1", TOKEN, method, params))

    def design_rule(self, name: str = "Old") -> Any:
        return self.design.designSheetMetalRules.addByCopy(
            self.design.librarySheetMetalRules.item(1), name
        )


@pytest.mark.parametrize(
    ("method", "mutating"),
    [
        ("sheet_metal.capabilities", False),
        ("sheet_metal.list_rules", False),
        ("sheet_metal.create_rule", True),
        ("sheet_metal.assign_rule", True),
        ("sheet_metal.convert", True),
        ("sheet_metal.base_flange", True),
        ("sheet_metal.contour_flange", True),
        ("sheet_metal.edge_flange", True),
        ("sheet_metal.list_bends", False),
        ("sheet_metal.unfold", True),
        ("sheet_metal.refold", True),
        ("sheet_metal.create_flat_pattern", True),
        ("sheet_metal.get_flat_pattern", False),
        ("sheet_metal.delete_flat_pattern", True),
        ("sheet_metal.export_flat_pattern", False),
    ],
)
def test_handlers_are_registered(method: str, mutating: bool) -> None:
    spec = REGISTRY.get(method)
    assert spec is not None and spec.mutating is mutating


# --- 2.1 capabilities --------------------------------------------------------------------------


def test_capabilities_all_available() -> None:
    caps = Setup().run(h.sheet_metal_capabilities, {})
    for key in (
        "rules",
        "flat_pattern",
        "flat_pattern_dxf",
        "flat_pattern_step",
        "convert",
        "base_flange",
        "contour_flange",
        "edge_flange",
        "list_bends",
        "unfold",
        "refold",
    ):
        assert caps[key] is True, key
    assert caps["sketch_bend"] is False
    assert "FlangeFeatures.createContourFlangeInput" in caps["preview_apis"]
    assert caps["fusion_version"] == "2.0.21000"
    assert any("sketch" in note for note in caps["notes"])


def test_capabilities_without_preview_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(F.BRepBody, "convertToSheetMetal")
    monkeypatch.delattr(F.BRepBody, "getBendFaces")
    caps = Setup(preview=False).run(h.sheet_metal_capabilities, {})
    assert caps["rules"] is True and caps["flat_pattern"] is True
    assert caps["flat_pattern_dxf"] is True
    for key in ("contour_flange", "base_flange", "edge_flange", "unfold", "refold", "convert"):
        assert caps[key] is False, key
    assert caps["list_bends"] is False and caps["flat_pattern_step"] is False
    assert caps["sketch_bend"] is False


def test_capabilities_without_rules() -> None:
    s = Setup()
    del s.design.designSheetMetalRules
    assert s.run(h.sheet_metal_capabilities, {})["rules"] is False


# --- 2.2 list_rules ----------------------------------------------------------------------------


def test_list_rules_sources() -> None:
    s = Setup()
    s.design_rule("Mine")
    every = s.run(h.sheet_metal_list_rules, {})
    assert [(r["name"], r["source"]) for r in every["rules"]] == [
        ("Mine", "design"),
        ("Steel", "library"),
        ("Aluminum", "library"),
    ]
    assert every["count"] == 3
    assert every["rules"][1]["thickness"] == {"mm": 1.0, "expression": "1 mm"}
    design = s.run(h.sheet_metal_list_rules, {"source": "design"})
    assert [r["name"] for r in design["rules"]] == ["Mine"]
    library = s.run(h.sheet_metal_list_rules, {"source": "library"})
    assert [r["is_default"] for r in library["rules"]] == [True, False]
    s.invalid(h.sheet_metal_list_rules, {"source": "cloud"}, "source")


# --- 2.3 create_rule ---------------------------------------------------------------------------


def test_create_rule_spec_scenario() -> None:
    s = Setup()
    result = s.run(
        h.sheet_metal_create_rule,
        {
            "name": "Steel 2mm",
            "thickness": 2,
            "bend_radius": "Thickness * 1",
            "k_factor": 0.44,
            "relief_shape": "round",
        },
    )
    rule = s.design.designSheetMetalRules.itemByName("Steel 2mm")
    assert rule is not None
    assert rule.thickness.value == pytest.approx(0.2)
    assert rule.bendRadius.expression == "Thickness * 1"
    assert rule.kFactor == pytest.approx(0.44)
    assert rule.reliefShape == F.BendReliefShapes.RoundBendReliefShape
    assert result["rule"]["thickness"]["mm"] == pytest.approx(2.0)
    assert result["rule"]["bend_radius"] == {
        "mm": pytest.approx(2.0),
        "expression": "Thickness * 1",
    }
    assert result["base"] == {"name": "Steel", "source": "library"}


def test_create_rule_from_base_in_inches() -> None:
    s = Setup()
    result = s.run(
        h.sheet_metal_create_rule,
        {"name": "Al", "base_rule": "Aluminum", "gap": 0.1, "units": "in"},
    )
    assert result["base"] == {"name": "Aluminum", "source": "library"}
    assert s.design.designSheetMetalRules.itemByName("Al").gap.value == pytest.approx(0.254)


def test_create_rule_bad_k_factor_leaves_no_rule() -> None:
    s = Setup()
    s.invalid(h.sheet_metal_create_rule, {"name": "X", "k_factor": 1.5}, "k_factor")
    assert s.design.designSheetMetalRules.names == []


def test_create_rule_name_taken() -> None:
    s = Setup()
    s.design_rule("X")
    err = s.invalid(h.sheet_metal_create_rule, {"name": "X"}, "name")
    assert err.details["reason"] == "exists"


def test_create_rule_bad_value_deletes_copy() -> None:
    s = Setup()
    s.invalid(h.sheet_metal_create_rule, {"name": "X", "gap": -1}, "gap")
    assert s.design.designSheetMetalRules.names == []


def test_create_rule_fusion_error_deletes_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Setup()
    rules = s.design.designSheetMetalRules
    original = rules.addByCopy

    def failing_copy(rule: Any, name: str) -> Any:
        copy = original(rule, name)
        copy.set_error = "value rejected"
        return copy

    monkeypatch.setattr(rules, "addByCopy", failing_copy)
    err = s.error(h.sheet_metal_create_rule, {"name": "X", "thickness": 3})
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert rules.names == []


def test_create_rule_unknown_base() -> None:
    err = Setup().error(h.sheet_metal_create_rule, {"name": "X", "base_rule": "Brass"})
    assert err.code is ErrorCode.ENTITY_NOT_FOUND


# --- 2.4 assign_rule ---------------------------------------------------------------------------


def test_assign_library_rule_copies_it() -> None:
    s = Setup()
    result = s.run(h.sheet_metal_assign_rule, {"rule": "Steel"})
    assert result["component"] == "root"
    assert result["copied_from_library"] is True and result["previous"] is None
    assert result["rule"]["name"] == "Steel" and result["rule"]["source"] == "design"
    assert s.root.activeSheetMetalRule is s.design.designSheetMetalRules.itemByName("Steel")


def test_assign_design_rule() -> None:
    s = Setup()
    old = s.design_rule("Old")
    mine = s.design_rule("Mine")
    s.root.activeSheetMetalRule = old
    result = s.run(h.sheet_metal_assign_rule, {"rule": "Mine", "source": "design"})
    assert result["copied_from_library"] is False and result["previous"] == "Old"
    assert s.root.activeSheetMetalRule is mine


def test_assign_rule_restores_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Setup()
    old = s.design_rule("Old")
    s.root.activeSheetMetalRule = old

    def boom(rule: Any, source: str) -> Any:
        raise RuntimeError("describe failed")

    monkeypatch.setattr(core_sm, "describe_rule", boom)
    err = s.error(h.sheet_metal_assign_rule, {"rule": "Steel"})
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert s.root.activeSheetMetalRule is old
    assert s.design.designSheetMetalRules.names == ["Old"]


def test_assign_rule_needs_rule() -> None:
    Setup().invalid(h.sheet_metal_assign_rule, {}, "rule")


# --- 3.1 convert -------------------------------------------------------------------------------


def test_convert_plate() -> None:
    s = Setup()
    result = s.run(h.sheet_metal_convert, {"body": "plate", "base_face": "plate_top"})
    assert result["is_sheet_metal"] is True and result["body"] == "plate"
    assert result["component"] == "root"
    face, rule = s.plate.convert_calls[0]
    assert face is s.plate_top and rule.name == "Steel"
    assert result["rule"]["name"] == "Steel"


def test_convert_with_rule() -> None:
    s = Setup()
    result = s.run(
        h.sheet_metal_convert, {"body": "plate", "base_face": "plate_top", "rule": "Aluminum"}
    )
    assert s.plate.convert_calls[0][1].name == "Aluminum"
    assert result["rule"]["name"] == "Aluminum"


def test_convert_already_sheet_metal() -> None:
    err = Setup().invalid(h.sheet_metal_convert, {"body": "sheet", "base_face": "top"}, "body")
    assert err.details["reason"] == "sheet_metal"


def test_convert_face_of_other_body() -> None:
    Setup().invalid(h.sheet_metal_convert, {"body": "plate", "base_face": "top"}, "base_face")


def test_convert_false_is_api_error_and_restores_rule() -> None:
    s = Setup()
    s.plate.convert_result = False
    err = s.error(
        h.sheet_metal_convert, {"body": "plate", "base_face": "plate_top", "rule": "Aluminum"}
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert s.root.activeSheetMetalRule is None
    assert s.design.designSheetMetalRules.names == []


def test_convert_without_api() -> None:
    s = Setup(preview=False)
    s.unavailable(
        h.sheet_metal_convert,
        {"body": "plate", "base_face": "plate_top"},
        "BRepBody.convertToSheetMetal",
    )


# --- 3.2 base flange ---------------------------------------------------------------------------


def test_base_flange_from_profile() -> None:
    s = Setup()
    s.flanges.will_create(make_sheet_metal(FakeBody("Base")))
    result = s.run(h.sheet_metal_base_flange, {"profiles": [s.profile], "rule": "Aluminum"})
    inp = s.flanges.last_input
    assert inp.create_method == "createBaseFlangeInput"
    assert inp.create_args == ([s.sketch.profiles.item(0)],)
    assert inp.sets == {
        "orientation": F.FlangeOrientations.SideOneFlangeOrientation,
        "operation": OPS.NewBodyFeatureOperation,
    }
    assert result["feature"]["type"] == "FlangeFeature"
    assert [(b["name"], b["is_sheet_metal"]) for b in result["created_bodies"]] == [("Base", True)]
    assert result["rule"] == "Aluminum"


def test_base_flange_options_and_bad_operation() -> None:
    s = Setup()
    s.run(
        h.sheet_metal_base_flange,
        {"profiles": [s.profile], "orientation": "centered", "operation": "new_component"},
    )
    assert (
        s.flanges.last_input.sets["orientation"] == F.FlangeOrientations.CenteredFlangeOrientation
    )
    assert s.flanges.last_input.sets["operation"] == OPS.NewComponentFeatureOperation
    s.invalid(
        h.sheet_metal_base_flange, {"profiles": [s.profile], "operation": "join"}, "operation"
    )


def test_base_flange_without_api() -> None:
    s = Setup(preview=False)
    s.unavailable(
        h.sheet_metal_base_flange, {"profiles": [s.profile]}, "FlangeFeatures.createBaseFlangeInput"
    )


# --- 3.3 contour flange ------------------------------------------------------------------------


def test_contour_flange_one_side_40mm() -> None:
    s = Setup()
    s.flanges.will_create(make_sheet_metal(FakeBody("Contour")))
    result = s.run(h.sheet_metal_contour_flange, {"profiles": ["line1", "line2"], "distance": 40})
    inp = s.flanges.last_input
    profile, distance = inp.create_args
    assert profile.method == "createOpenProfile" and profile.entities == [s.line1, s.line2]
    assert real(distance) == pytest.approx(4.0)
    (one_side,) = inp.call("setOneSideDirection")
    assert real(one_side) == pytest.approx(4.0)
    assert result["created_bodies"][0]["is_sheet_metal"] is True


def test_contour_flange_symmetric_two_sides_and_radius() -> None:
    s = Setup()
    s.run(
        h.sheet_metal_contour_flange,
        {"profiles": ["line1"], "distance": 30, "direction": "symmetric", "bend_radius": 3},
    )
    inp = s.flanges.last_input
    assert real(inp.call("setSymmetricDirection")[0]) == pytest.approx(3.0)
    assert real(inp.bendRadiusOverride.call("setOverride")[0]) == pytest.approx(0.3)
    s.run(
        h.sheet_metal_contour_flange,
        {"profiles": ["line1"], "distance": 10, "distance_two": 20, "direction": "two_sides"},
    )
    one, two = s.flanges.last_input.call("setTwoSidesDirection")
    assert [real(one), real(two)] == pytest.approx([1.0, 2.0])


def test_contour_flange_two_sides_needs_distance_two() -> None:
    s = Setup()
    s.invalid(
        h.sheet_metal_contour_flange,
        {"profiles": ["line1"], "distance": 10, "direction": "two_sides"},
        "distance_two",
    )
    s.invalid(
        h.sheet_metal_contour_flange,
        {"profiles": ["line1"], "distance": 10, "distance_two": 5},
        "distance_two",
    )
    assert s.flanges.inputs == []


def test_contour_flange_error_restores_rule() -> None:
    s = Setup()
    old = s.design_rule("Old")
    s.root.activeSheetMetalRule = old
    s.flanges.add_error = "flange failed"
    response = s.dispatch(
        "sheet_metal.contour_flange",
        {"profiles": ["line1"], "distance": 40, "rule": "Steel"},
    )
    assert response.ok is False
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert s.root.activeSheetMetalRule is old
    assert s.design.designSheetMetalRules.names == ["Old"]
    assert s.design.timeline.count == s.timeline_start


def test_contour_flange_without_api_changes_nothing() -> None:
    s = Setup(preview=False)
    s.unavailable(
        h.sheet_metal_contour_flange,
        {"profiles": ["line1"], "distance": 40, "rule": "Steel"},
        "FlangeFeatures.createContourFlangeInput",
    )
    assert s.design.designSheetMetalRules.names == []


# --- 3.4 edge flange: extents and width --------------------------------------------------------


def test_edge_flange_20mm_at_90() -> None:
    s = Setup()
    result = s.run(h.sheet_metal_edge_flange, {"edges": ["edge"], "height": 20})
    inp = s.flanges.last_input
    edges, distance = inp.create_args
    assert edges == [] and real(distance) == pytest.approx(2.0)
    (definition,) = inp.edgeDefinitions.made
    assert definition.create_method == "addFullLengthEdge" and definition.create_args == (s.edge,)
    assert real(inp.call("setDistanceExtent")[0]) == pytest.approx(2.0)
    assert real(inp.sets["angle"]) == pytest.approx(math.pi / 2)
    assert result["feature"]["type"] == "FlangeFeature"
    assert s.flanges.added == [inp]


@pytest.mark.parametrize(
    ("width", "method", "values"),
    [
        ({"type": "symmetric", "width": 30}, "addSymmetricEdge", [3.0]),
        ({"type": "two_sided", "width1": 10, "width2": 20}, "addTwoSidedEdge", [1.0, 2.0]),
        ({"type": "two_offsets", "offset1": 5, "offset2": 15}, "addTwoOffsetsEdge", [0.5, 1.5]),
    ],
)
def test_edge_flange_width_types(width: dict[str, Any], method: str, values: list[float]) -> None:
    s = Setup()
    s.run(h.sheet_metal_edge_flange, {"edges": ["edge", "edge2"], "height": 10, "width": width})
    made = s.flanges.last_input.edgeDefinitions.made
    assert [d.create_method for d in made] == [method, method]
    assert [d.create_args[0] for d in made] == [s.edge, s.edge2]
    assert [real(v) for v in made[0].create_args[1:]] == pytest.approx(values)


def test_edge_flange_bad_width() -> None:
    s = Setup()
    s.invalid(
        h.sheet_metal_edge_flange,
        {"edges": ["edge"], "height": 10, "width": {"type": "symmetric"}},
        "width.width",
    )
    s.invalid(
        h.sheet_metal_edge_flange,
        {"edges": ["edge"], "height": 10, "width": {"type": "full", "width": 3}},
        "width",
    )


def test_edge_flange_to_entity() -> None:
    s = Setup()
    s.run(
        h.sheet_metal_edge_flange,
        {"edges": ["edge"], "to_entity": "side", "to_offset": 2},
    )
    inp = s.flanges.last_input
    assert inp.create_args[1] is None
    entity, offset = inp.call("setToObjectExtent")
    assert entity is s.side and real(offset) == pytest.approx(0.2)
    assert not inp.called("setDistanceExtent")


def test_edge_flange_extent_rules() -> None:
    s = Setup()
    s.invalid(
        h.sheet_metal_edge_flange, {"edges": ["edge"], "height": 10, "to_entity": "side"}, "height"
    )
    s.invalid(h.sheet_metal_edge_flange, {"edges": ["edge"]}, "height")
    s.invalid(
        h.sheet_metal_edge_flange, {"edges": ["edge", "edge2"], "to_entity": "side"}, "to_entity"
    )
    s.invalid(
        h.sheet_metal_edge_flange, {"edges": ["edge"], "height": 5, "to_offset": 1}, "to_offset"
    )
    assert s.flanges.inputs == []


# --- 3.5 edge flange: properties ---------------------------------------------------------------


def test_edge_flange_properties() -> None:
    s = Setup()
    s.run(
        h.sheet_metal_edge_flange,
        {
            "edges": ["edge"],
            "height": 10,
            "angle": 45,
            "height_datum": "inner",
            "bend_position": "tangent_to_side",
            "mitered_corners": False,
            "flip": True,
            "bend_radius": 2,
        },
    )
    inp = s.flanges.last_input
    sets = inp.sets
    assert real(sets["angle"]) == pytest.approx(math.pi / 4)
    assert sets["heightDatumType"] == F.FlangeHeightDatumTypes.InnerFacesFlangeHeightDatumType
    assert sets["bendPositionType"] == F.BendPositionTypes.TangentToSideBendPositionType
    assert sets["isMiteredCorners"] is False and sets["isFlipped"] is True
    assert real(inp.bendRadiusOverride.call("setOverride")[0]) == pytest.approx(0.2)


def test_edge_flange_defaults() -> None:
    s = Setup()
    s.run(h.sheet_metal_edge_flange, {"edges": ["edge"], "height": 10})
    sets = s.flanges.last_input.sets
    assert sets["heightDatumType"] == F.FlangeHeightDatumTypes.OuterFacesFlangeHeightDatumType
    assert sets["bendPositionType"] == F.BendPositionTypes.StartEdgeBendPositionType
    assert sets["isMiteredCorners"] is True and sets["isFlipped"] is False
    assert not s.flanges.last_input.bendRadiusOverride.called("setOverride")


def test_edge_flange_edge_checks() -> None:
    s = Setup()
    err = s.invalid(h.sheet_metal_edge_flange, {"edges": ["plate_edge"], "height": 10}, "edges")
    assert err.details["reason"] == "not_sheet_metal"
    err = s.invalid(
        h.sheet_metal_edge_flange, {"edges": ["edge", "edge_other"], "height": 10}, "edges"
    )
    assert err.details["reason"] == "several_bodies"
    s.invalid(
        h.sheet_metal_edge_flange,
        {"edges": ["edge"], "height": 10, "height_datum": "x"},
        "height_datum",
    )
    assert s.flanges.inputs == []


# --- 4.1 list_bends ----------------------------------------------------------------------------


def test_list_bends_pairs() -> None:
    s = Setup()
    result = s.run(h.sheet_metal_list_bends, {"body": "sheet"})
    inner, outer = s.sheet.bend_pairs[0]
    assert result["count"] == 1
    assert result["bends"] == [
        {"inner": inner.entityToken, "outer": outer.entityToken, "radius_mm": pytest.approx(1.0)}
    ]


def test_list_bends_non_sheet_metal() -> None:
    err = Setup().invalid(h.sheet_metal_list_bends, {"body": "plate"}, "body")
    assert err.details["reason"] == "not_sheet_metal"


def test_list_bends_without_api() -> None:
    Setup(preview=False).unavailable(
        h.sheet_metal_list_bends, {"body": "sheet"}, "BRepBody.getBendFaces"
    )


# --- 4.2 unfold --------------------------------------------------------------------------------


def test_unfold_all() -> None:
    s = Setup()
    unfolds = s.features.unfoldFeatures
    result = s.run(h.sheet_metal_unfold, {"stationary_face": "top"})
    inp = unfolds.last_input
    assert inp.create_args == (s.top,)
    assert inp.sets == {"isUnfoldAllBends": True}
    assert result["feature"]["type"] == "UnfoldFeature"


def test_unfold_selected_bends() -> None:
    s = Setup()
    inner, _ = s.sheet.bend_pairs[0]
    s.run(h.sheet_metal_unfold, {"stationary_face": "top", "bend_faces": [inner.entityToken]})
    sets = s.features.unfoldFeatures.last_input.sets
    assert sets == {"isUnfoldAllBends": False, "bendFaces": [inner]}


def test_unfold_checks() -> None:
    s = Setup()
    s.invalid(h.sheet_metal_unfold, {"stationary_face": "plate_top"}, "stationary_face")
    s.invalid(
        h.sheet_metal_unfold, {"stationary_face": "top", "bend_faces": ["plate_top"]}, "bend_faces"
    )


def test_unfold_without_api_leaves_timeline() -> None:
    s = Setup(preview=False)
    response = s.dispatch("sheet_metal.unfold", {"stationary_face": "top"})
    assert response.ok is False
    assert response.error.details == {
        "reason": "api_unavailable",
        "api": "UnfoldFeatures.createInput",
    }
    assert s.design.timeline.count == s.timeline_start


# --- 4.3 refold --------------------------------------------------------------------------------


def test_refold_unfold_feature() -> None:
    s = Setup()
    unfold = s.run(h.sheet_metal_unfold, {"stationary_face": "top"})
    result = s.run(h.sheet_metal_refold, {"unfold": unfold["feature"]["ref"]})
    inp = s.features.refoldFeatures.last_input
    (target,) = inp.create_args
    assert target.objectType == "adsk::fusion::UnfoldFeature"
    assert result["feature"]["type"] == "RefoldFeature"


def test_refold_needs_unfold_feature() -> None:
    Setup().invalid(h.sheet_metal_refold, {"unfold": "sheet"}, "unfold")


# --- 4.4 create_flat_pattern -------------------------------------------------------------------


def test_create_flat_pattern() -> None:
    s = Setup()
    result = s.run(h.sheet_metal_create_flat_pattern, {"stationary_face": "top"})
    assert s.root.flat_pattern_calls == [s.top]
    assert result["component"] == "root" and result["exists"] is True
    assert result["size_mm"] == [40.0, 20.0]
    assert result["bend_count"] == 1
    assert result["bends"] == [{"angle_deg": pytest.approx(90.0), "is_up": True}]
    assert result["replaced"] is False
    assert s.app.activeProduct is s.design
    assert s.app.userInterface.workspaces.itemById("FusionSolidEnvironment").activations == 1


def test_create_flat_pattern_existing() -> None:
    s = Setup()
    s.run(h.sheet_metal_create_flat_pattern, {"stationary_face": "top"})
    first = s.root.flatPattern
    err = s.invalid(h.sheet_metal_create_flat_pattern, {"stationary_face": "top"}, "replace")
    assert err.details["reason"] == "exists"
    assert s.root.flatPattern is first and not first.deleted
    result = s.run(h.sheet_metal_create_flat_pattern, {"stationary_face": "top", "replace": True})
    assert result["replaced"] is True and first.deleted
    assert s.root.flatPattern is not first


def test_create_flat_pattern_non_sheet_metal() -> None:
    s = Setup()
    s.invalid(
        h.sheet_metal_create_flat_pattern, {"stationary_face": "plate_top"}, "stationary_face"
    )
    assert s.root.flat_pattern_calls == []


def test_create_flat_pattern_compensates(monkeypatch: pytest.MonkeyPatch) -> None:
    s = Setup()

    def boom(*args: Any) -> Any:
        raise RuntimeError("describe failed")

    monkeypatch.setattr(h, "describe_flat_pattern", boom)
    err = s.error(h.sheet_metal_create_flat_pattern, {"stationary_face": "top"})
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert s.root.flatPattern is None
    assert s.app.activeProduct is s.design


# --- 4.5 get / delete flat pattern -------------------------------------------------------------


def test_get_and_delete_flat_pattern() -> None:
    s = Setup()
    assert s.run(h.sheet_metal_get_flat_pattern, {}) == {"component": "root", "exists": False}
    s.run(h.sheet_metal_create_flat_pattern, {"stationary_face": "top"})
    got = s.run(h.sheet_metal_get_flat_pattern, {"component": "root"})
    assert got["exists"] is True and got["bend_count"] == 1
    assert s.run(h.sheet_metal_delete_flat_pattern, {}) == {"component": "root", "deleted": True}
    assert s.root.flatPattern is None
    err = s.error(h.sheet_metal_delete_flat_pattern, {})
    assert err.code is ErrorCode.ENTITY_NOT_FOUND


# --- 4.6 export_flat_pattern -------------------------------------------------------------------


def _with_flat(s: Setup) -> None:
    s.run(h.sheet_metal_create_flat_pattern, {"stationary_face": "top"})


def test_export_dxf_mm_with_bend_lines(tmp_path: Path) -> None:
    s = Setup()
    _with_flat(s)
    path = str(tmp_path / "bracket.dxf")
    result = s.run(h.sheet_metal_export_flat_pattern, {"path": path})
    (options,) = s.design.exportManager.executed
    assert options.geometry is s.root.flatPattern
    assert options.units == F.DistanceUnits.MillimeterDistanceUnits
    assert options.isCenterLinesExported is True and options.isExtentLinesExported is True
    assert options.isSplineConvertedToPolyline is False
    assert options.convertToPolylineTolerance == pytest.approx(0.01)
    assert result["format"] == "dxf" and result["scope"] == "root"
    assert result["files"][0]["size_bytes"] > 0


def test_export_dxf_options(tmp_path: Path) -> None:
    s = Setup()
    _with_flat(s)
    s.run(
        h.sheet_metal_export_flat_pattern,
        {
            "path": str(tmp_path / "a.dxf"),
            "file_units": "in",
            "bend_lines": False,
            "extent_lines": False,
            "splines_to_polylines": True,
            "polyline_tolerance": 0.1,
        },
    )
    options = s.design.exportManager.executed[-1]
    assert options.units == F.DistanceUnits.InchDistanceUnits
    assert options.isCenterLinesExported is False and options.isExtentLinesExported is False
    assert options.isSplineConvertedToPolyline is True
    assert options.convertToPolylineTolerance == pytest.approx(0.01)


def test_export_step(tmp_path: Path) -> None:
    s = Setup()
    _with_flat(s)
    result = s.run(h.sheet_metal_export_flat_pattern, {"path": str(tmp_path / "a.step")})
    assert result["format"] == "step"
    assert s.design.exportManager.executed[-1].geometry is s.root.flatPattern


def test_export_step_without_api(tmp_path: Path) -> None:
    s = Setup(preview=False)
    _with_flat(s)
    s.unavailable(
        h.sheet_metal_export_flat_pattern,
        {"path": str(tmp_path / "a.stp")},
        "ExportManager.createSTEPExportOptionsForFlatPattern",
    )


def test_export_without_flat_pattern(tmp_path: Path) -> None:
    err = Setup().error(h.sheet_metal_export_flat_pattern, {"path": str(tmp_path / "a.dxf")})
    assert err.code is ErrorCode.ENTITY_NOT_FOUND


def test_export_bad_path(tmp_path: Path) -> None:
    s = Setup()
    _with_flat(s)
    s.invalid(h.sheet_metal_export_flat_pattern, {"path": str(tmp_path / "a.stl")}, "format")
    s.invalid(
        h.sheet_metal_export_flat_pattern,
        {"path": str(tmp_path / "a.dxf"), "polyline_tolerance": 0},
        "polyline_tolerance",
    )

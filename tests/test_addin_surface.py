"""``surface.*`` handlers on surface fakes (c11 D4-D11; tasks 2.1-2.9)."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import surface as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.sketch_refs import format_profile_ref
from fusion_mcp.protocol.wire import Request
from tests.fakes.brep import FakeBody, FakeConstructionPlane, fake_box_body
from tests.fakes.fusion_features import make_ctx
from tests.fakes.fusion_sketch import auto_profiles, make_sketch, polygon_curves
from tests.fakes.fusion_surfaces import FakeCellMeasure, fake_surface_design, surface_cell

adsk = fusion_addin.adsk
F = adsk.fusion
OPS = F.FeatureOperations
HS = F.FeatureHealthStates
TOKEN = "t" * 32


def real(value_input: Any) -> float:
    assert value_input.kind == "real", value_input
    return float(value_input.value)


class Setup:
    """Surface body ``sheet`` (a box shell with ``isSolid = False``: faces ``top``/``side``,
    edge ``edge``), solid box ``box`` (face ``solid_face``), a 40 x 20 mm rectangle sketch
    (``profile``), an open two-line sketch (``line1``, ``line2``) and plane ``p``."""

    def __init__(self, parametric: bool = True) -> None:
        self.sheet = fake_box_body(4, 2, 1, name="Sheet")
        self.sheet.isSolid = False
        self.box = fake_box_body(1, 1, 1, name="Box")
        self.design = fake_surface_design(self.sheet, self.box, parametric=parametric)
        self.root = self.design.rootComponent
        self.features = self.root.features
        faces = {f.entityToken.rsplit("face", 1)[1]: f for f in self.sheet.faces}
        self.top, self.side = faces["+z"], faces["+x"]
        self.sketch = make_sketch(self.design, "XY")
        polygon_curves(self.sketch, [(0, 0), (4, 0), (4, 2), (0, 2)])
        self.sketch.profile_builder = auto_profiles
        self.profile = format_profile_ref(self.sketch.entityToken, 20, 10)
        self.open_sketch = make_sketch(self.design, "XZ")
        self.line1, self.line2 = polygon_curves(
            self.open_sketch, [(0, 0), (0, 5), (3, 8)], closed=False
        )
        self.plane = FakeConstructionPlane(
            "P",
            adsk.core.Point3D.create(0, 0, 0.5),
            adsk.core.Vector3D.create(0, 0, 1),
            parentComponent=self.root,
        )
        self.measure = FakeCellMeasure()
        self.aliases: dict[str, Any] = {
            "sheet": self.sheet,
            "box": self.box,
            "top": self.top,
            "side": self.side,
            "edge": self.sheet.edges.item(0),
            "edge2": self.sheet.edges.item(1),
            "solid_face": self.box.faces.item(0),
            "line1": self.line1,
            "line2": self.line2,
            "p": self.plane,
            "z_edge": self.box.edges.item(0),
        }

    def ctx(self, params: dict[str, Any]) -> Context:
        ctx = make_ctx(self.design, params, self.aliases)
        app: Any = ctx.app
        app.measureManager = self.measure
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

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        dispatcher = Dispatcher(TOKEN, context_factory=lambda p: self.ctx(p))
        return dispatcher.dispatch(Request("1", TOKEN, method, params))


def names(bodies: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    return [(b["name"], b["is_solid"]) for b in bodies]


@pytest.mark.parametrize(
    ("method", "mutating"),
    [
        ("surface.patch", True),
        ("surface.extend", True),
        ("surface.trim_cells", False),
        ("surface.trim", True),
        ("surface.untrim", True),
        ("surface.stitch", True),
        ("surface.unstitch", True),
        ("surface.ruled", True),
        ("surface.offset", True),
    ],
)
def test_handlers_are_registered(method: str, mutating: bool) -> None:
    spec = REGISTRY.get(method)
    assert spec is not None and spec.mutating is mutating


# --- 2.1 patch -----------------------------------------------------------------------------


def test_patch_closed_profile() -> None:
    s = Setup()
    patches = s.features.patchFeatures
    patches.will_create(FakeBody("Patch", is_solid=False))
    result = s.run(h.surface_patch, {"boundary": [s.profile]})
    inp = patches.last_input
    boundary, operation = inp.create_args
    assert boundary is s.sketch.profiles.item(0)
    assert operation == OPS.NewBodyFeatureOperation
    assert inp.sets == {
        "isGroupEdges": True,
        "groupContinuity": F.SurfaceContinuityTypes.ConnectedSurfaceContinuityType,
        "groupWeight": 0.5,
        "groupIsContinuityDirectionFlipped": False,
    }
    assert names(result["created_bodies"]) == [("Patch", False)]
    assert result["feature"]["type"] == "PatchFeature"


def test_patch_edges_with_tangent_continuity() -> None:
    s = Setup()
    patches = s.features.patchFeatures
    s.run(
        h.surface_patch,
        {
            "boundary": ["edge", "edge2", "edge"],
            "continuity": "tangent",
            "weight": 0.8,
            "flip_continuity_direction": True,
            "operation": "new_component",
        },
    )
    inp = patches.last_input
    boundary, operation = inp.create_args
    assert isinstance(boundary, adsk.core.ObjectCollection)
    assert list(boundary) == [s.aliases["edge"], s.aliases["edge2"]]
    assert operation == OPS.NewComponentFeatureOperation
    assert inp.sets["groupContinuity"] == F.SurfaceContinuityTypes.TangentSurfaceContinuityType
    assert inp.sets["groupWeight"] == 0.8
    assert inp.sets["groupIsContinuityDirectionFlipped"] is True


def test_patch_single_sketch_curve_passed_as_is() -> None:
    s = Setup()
    s.run(h.surface_patch, {"boundary": ["line1"]})
    assert s.features.patchFeatures.last_input.create_args[0] is s.line1


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"boundary": ["edge"], "operation": "join"}, "operation"),
        ({"boundary": ["edge"], "weight": 1.5}, "weight"),
        ({"boundary": ["edge"], "weight": -0.1}, "weight"),
        ({"boundary": ["edge"], "weight": "x"}, "weight"),
        ({"boundary": ["edge"], "continuity": "smooth"}, "continuity"),
        ({"boundary": []}, "boundary"),
        ({"boundary": ["box"]}, "boundary"),
    ],
)
def test_patch_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.surface_patch, params, param)
    assert s.features.patchFeatures.inputs == []


def test_patch_error_health_rolls_back() -> None:
    s = Setup()
    s.features.patchFeatures.health = HS.ErrorFeatureHealthState
    s.features.patchFeatures.message = "cannot patch"
    s.features.patchFeatures.will_create(FakeBody("Patch", is_solid=False))
    count = s.design.timeline.count
    response = s.dispatch("surface.patch", {"boundary": ["edge"]})
    assert response.ok is False and response.error.code is ErrorCode.FUSION_API_ERROR
    assert "cannot patch" in response.error.message
    assert s.design.timeline.count == count
    assert [b.name for b in s.root.bRepBodies] == ["Sheet", "Box"]


# --- 2.2 extend ----------------------------------------------------------------------------


def test_extend_natural_5mm() -> None:
    s = Setup()
    extends = s.features.extendFeatures
    extends.will_modify(s.sheet)
    result = s.run(h.surface_extend, {"edges": ["edge"], "distance": 5})
    inp = extends.last_input
    edges, distance, extend_type, chain = inp.create_args
    assert list(edges) == [s.aliases["edge"]]
    assert real(distance) == pytest.approx(0.5)
    assert extend_type == F.SurfaceExtendTypes.NaturalSurfaceExtendType
    assert chain is True
    assert inp.sets["extendAlignment"] == F.SurfaceExtendAlignment.FreeEdges
    assert names(result["modified_bodies"]) == [("Sheet", False)]


def test_extend_expression_type_chain_alignment() -> None:
    s = Setup()
    s.run(
        h.surface_extend,
        {
            "edges": ["edge"],
            "distance": "ext * 2",
            "extend_type": "tangent",
            "chain": False,
            "alignment": "align",
        },
    )
    inp = s.features.extendFeatures.last_input
    _, distance, extend_type, chain = inp.create_args
    assert distance.kind == "string" and distance.value == "ext * 2"
    assert extend_type == F.SurfaceExtendTypes.TangentSurfaceExtendType and chain is False
    assert inp.sets["extendAlignment"] == F.SurfaceExtendAlignment.AlignEdges


def test_extend_align_without_attribute_is_not_available() -> None:
    s = Setup()
    extends = s.features.extendFeatures
    extends.on_input = lambda inp: inp.missing.add("extendAlignment")
    err = s.error(h.surface_extend, {"edges": ["edge"], "distance": 5, "alignment": "align"})
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert "extendAlignment not available" in err.message
    assert extends.added == []
    s.run(h.surface_extend, {"edges": ["edge"], "distance": 5})
    assert "extendAlignment" not in extends.last_input.sets
    assert len(extends.added) == 1


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"edges": ["edge"]}, "distance"),
        ({"edges": ["edge"], "distance": 0}, "distance"),
        ({"edges": ["top"], "distance": 1}, "edges"),
        ({"edges": ["edge"], "distance": 1, "extend_type": "x"}, "extend_type"),
        ({"edges": ["edge"], "distance": 1, "alignment": "x"}, "alignment"),
    ],
)
def test_extend_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.surface_extend, params, param)
    assert s.features.extendFeatures.inputs == []


# --- 2.3 trim cells ------------------------------------------------------------------------


def trim_setup() -> Setup:
    s = Setup()
    s.features.trimFeatures.cells = [
        surface_cell((0, 0, 0), (4, 2, 0), area=8.0, name="Low"),
        surface_cell((0, 0, 1), (4, 2, 3), area=2.5, name="High"),
    ]
    return s


def test_trim_cells_lists_cells_in_mm_and_cancels() -> None:
    s = trim_setup()
    result = s.run(h.surface_trim_cells, {"trim_tool": "p"})
    inp = s.features.trimFeatures.last_input
    assert inp.create_args == (s.plane,)
    assert inp.called("cancel")
    assert s.features.trimFeatures.added == []
    assert result["count"] == 2
    assert result["cells"][0] == {
        "center": [20.0, 10.0, 0.0],
        "bbox": {"min": [0.0, 0.0, 0.0], "max": [40.0, 20.0, 0.0]},
        "area_mm2": 800.0,
        "is_solid": False,
    }
    assert result["cells"][1]["center"] == [20.0, 10.0, 20.0]


def test_trim_cells_cancel_on_exception() -> None:
    class Broken:
        @property
        def cellBody(self) -> Any:
            raise RuntimeError("cell failed")

    s = trim_setup()
    trims = s.features.trimFeatures
    original = trims.__getattr__("createInput")

    def broken_input(*args: Any) -> Any:
        inp = original(*args)
        inp.provide("bRepCells", [Broken()])
        return inp

    trims.createInput = broken_input
    with pytest.raises(RuntimeError, match="cell failed"):
        s.run(h.surface_trim_cells, {"trim_tool": "sheet"})
    assert trims.last_input.called("cancel")


def test_trim_cells_origin_plane_and_validation() -> None:
    s = trim_setup()
    s.run(h.surface_trim_cells, {"trim_tool": "origin:XY"})
    assert s.features.trimFeatures.last_input.create_args == (s.root.xYConstructionPlane,)
    s.invalid(h.surface_trim_cells, {"trim_tool": "edge"}, "trim_tool")


# --- 2.4 trim ------------------------------------------------------------------------------


def test_trim_one_point_removes_one_cell() -> None:
    s = trim_setup()
    trims = s.features.trimFeatures
    trims.will_modify(s.sheet)
    result = s.run(h.surface_trim, {"trim_tool": "p", "remove_points": [[0, 0, 20]]})
    inp = trims.last_input
    low, high = list(inp.bRepCells)
    assert low.isSelected is False and high.isSelected is True
    assert trims.added == [inp]
    assert not inp.called("cancel")
    assert result["removed_cells"] == 1
    assert names(result["modified_bodies"]) == [("Sheet", False)]
    assert s.measure.calls[0][1].asArray() == pytest.approx([0, 0, 2])


def test_trim_two_points_in_one_cell() -> None:
    s = trim_setup()
    result = s.run(
        h.surface_trim, {"trim_tool": "p", "remove_points": [[10, 10, 20], [30, 10, 25]]}
    )
    assert result["removed_cells"] == 1


def test_trim_point_too_far_cancels_with_cells() -> None:
    s = trim_setup()
    trims = s.features.trimFeatures
    err = s.invalid(
        h.surface_trim,
        {"trim_tool": "p", "remove_points": [[0, 0, 20], [0, 0, 100]], "max_distance": 2},
        "remove_points",
    )
    assert err.details["missing"] == [1]
    assert [c["center"] for c in err.details["cells"]] == [[20.0, 10.0, 0.0], [20.0, 10.0, 20.0]]
    assert trims.last_input.called("cancel")
    assert trims.added == []


def test_trim_measure_failure_falls_back_to_bbox() -> None:
    s = trim_setup()
    s.measure.error = "temporary body"
    result = s.run(h.surface_trim, {"trim_tool": "p", "remove_points": [[0, 0, 0]]})
    low, high = list(s.features.trimFeatures.last_input.bRepCells)
    assert low.isSelected is True and high.isSelected is False
    assert result["removed_cells"] == 1


def test_trim_max_distance_in_units() -> None:
    s = trim_setup()
    # 0.5 in = 12.7 mm: the point is 15 mm above the high cell.
    s.invalid(
        h.surface_trim,
        {"trim_tool": "p", "remove_points": [[0, 0, 1.77]], "max_distance": 0.5, "units": "in"},
        "remove_points",
    )
    result = s.run(
        h.surface_trim,
        {"trim_tool": "p", "remove_points": [[0, 0, 1.77]], "max_distance": 1, "units": "in"},
    )
    assert result["removed_cells"] == 1


def test_trim_add_failure_cancels() -> None:
    s = trim_setup()
    s.features.trimFeatures.add_error = "boom"
    with pytest.raises(RuntimeError):
        s.run(h.surface_trim, {"trim_tool": "p", "remove_points": [[0, 0, 0]]})
    assert s.features.trimFeatures.last_input.called("cancel")


def test_trim_validation() -> None:
    s = trim_setup()
    s.invalid(h.surface_trim, {"trim_tool": "p", "remove_points": []}, "remove_points")
    s.invalid(h.surface_trim, {"trim_tool": "p", "remove_points": [[1, 2]]}, "remove_points[0]")
    s.invalid(
        h.surface_trim,
        {"trim_tool": "p", "remove_points": [[0, 0, 0]], "max_distance": -1},
        "max_distance",
    )
    assert s.features.trimFeatures.inputs == []
    empty = Setup()
    err = empty.invalid(
        h.surface_trim, {"trim_tool": "p", "remove_points": [[0, 0, 0]]}, "trim_tool"
    )
    assert err.details["reason"] == "no_cells"
    assert empty.features.trimFeatures.last_input.called("cancel")


# --- 2.5 untrim ----------------------------------------------------------------------------


def test_untrim_internal_loops() -> None:
    s = Setup()
    untrims = s.features.untrimFeatures
    untrims.will_modify(s.sheet)
    result = s.run(h.surface_untrim, {"faces": ["top"], "loop_type": "internal"})
    inp = untrims.last_input
    assert inp.create_method == "createInputFromFaces"
    faces, loop_type = inp.create_args
    assert faces == [s.top]
    assert loop_type == F.UntrimLoopTypes.InternalLoopsUntrimType
    assert names(result["modified_bodies"]) == [("Sheet", False)]


def test_untrim_all_with_extension() -> None:
    s = Setup()
    s.run(h.surface_untrim, {"faces": ["top", "side"], "extension_distance": 3})
    faces, loop_type, extension = s.features.untrimFeatures.last_input.create_args
    assert faces == [s.top, s.side]
    assert loop_type == F.UntrimLoopTypes.AllLoopsUntrimType
    assert real(extension) == pytest.approx(0.3)


def test_untrim_validation() -> None:
    s = Setup()
    err = s.invalid(h.surface_untrim, {"faces": ["solid_face"]}, "faces")
    assert err.details["reason"] == "solid_face"
    s.invalid(h.surface_untrim, {"faces": ["top"], "loop_type": "manual"}, "loop_type")
    s.invalid(
        h.surface_untrim,
        {"faces": ["top"], "loop_type": "internal", "extension_distance": 1},
        "extension_distance",
    )
    s.invalid(h.surface_untrim, {"faces": ["edge"]}, "faces")
    assert s.features.untrimFeatures.inputs == []


# --- 2.6 stitch ----------------------------------------------------------------------------


def test_stitch_closed_set_is_solid() -> None:
    s = Setup()
    other = FakeBody("Other", is_solid=False)
    s.root.add_body(other)
    s.design.add_entity(other)
    s.aliases["other"] = other
    stitches = s.features.stitchFeatures
    stitches.will_create(FakeBody("Stitched", is_solid=True))
    result = s.run(h.surface_stitch, {"surfaces": ["sheet", "other"]})
    surfaces, tolerance, operation = stitches.last_input.create_args
    assert list(surfaces) == [s.sheet, other]
    assert real(tolerance) == pytest.approx(0.01)
    assert operation == OPS.NewBodyFeatureOperation
    assert result["is_solid"] is True
    assert names(result["created_bodies"]) == [("Stitched", True)]


def test_stitch_faces_tolerance_in_units_open_result() -> None:
    s = Setup()
    stitches = s.features.stitchFeatures
    stitches.will_create(FakeBody("Quilt", is_solid=False))
    result = s.run(
        h.surface_stitch,
        {
            "surfaces": ["top", "side"],
            "tolerance": 0.01,
            "units": "in",
            "operation": "new_component",
        },
    )
    _, tolerance, operation = stitches.last_input.create_args
    assert real(tolerance) == pytest.approx(0.0254)
    assert operation == OPS.NewComponentFeatureOperation
    assert result["is_solid"] is False


def test_stitch_validation() -> None:
    s = Setup()
    err = s.invalid(h.surface_stitch, {"surfaces": ["sheet"]}, "surfaces")
    assert err.details["reason"] == "too_few_surfaces"
    s.invalid(h.surface_stitch, {"surfaces": ["sheet", "sheet"]}, "surfaces")
    s.invalid(h.surface_stitch, {"surfaces": ["sheet", "edge"]}, "surfaces")
    s.invalid(h.surface_stitch, {"surfaces": ["sheet", "box"], "operation": "join"}, "operation")
    assert s.features.stitchFeatures.inputs == []


# --- 2.7 unstitch --------------------------------------------------------------------------


def test_unstitch_face_without_chain() -> None:
    s = Setup()
    unstitches = s.features.unstitchFeatures
    unstitches.will_create(FakeBody("Face1", is_solid=False)).will_modify(s.box)
    result = s.run(h.surface_unstitch, {"entities": ["solid_face"], "chain": False})
    entities, chain = unstitches.last_input.create_args
    assert list(entities) == [s.box.faces.item(0)]
    assert chain is False
    assert names(result["created_bodies"]) == [("Face1", False)]
    assert names(result["modified_bodies"]) == [("Box", True)]


def test_unstitch_body_default_chain() -> None:
    s = Setup()
    s.run(h.surface_unstitch, {"entities": ["box"]})
    entities, chain = s.features.unstitchFeatures.last_input.create_args
    assert list(entities) == [s.box] and chain is True
    s.invalid(h.surface_unstitch, {"entities": ["edge"]}, "entities")
    s.invalid(h.surface_unstitch, {"entities": []}, "entities")


# --- 2.8 ruled -----------------------------------------------------------------------------


def test_ruled_normal_from_edge() -> None:
    s = Setup()
    ruled = s.features.ruledSurfaceFeatures
    ruled.will_create(FakeBody("Ruled", is_solid=False))
    result = s.run(h.surface_ruled, {"edges": ["edge"], "distance": 10, "angle": 0})
    (profile,) = s.root.open_profiles
    assert profile.method == "createBRepEdgeProfile" and profile.entities == [s.aliases["edge"]]
    assert profile.chain is True
    inp = ruled.last_input
    given, distance, angle, ruled_type = inp.create_args
    assert given is profile
    assert real(distance) == pytest.approx(1.0)
    assert real(angle) == 0.0
    assert ruled_type == F.RuledSurfaceTypes.NormalRuledSurfaceType
    assert inp.sets == {
        "cornerType": F.RuledSurfaceCornerTypes.RoundedRuledSurfaceCornerType,
        "alternateFace": False,
        "operation": OPS.NewBodyFeatureOperation,
    }
    assert names(result["created_bodies"]) == [("Ruled", False)]


def test_ruled_direction_from_curves() -> None:
    s = Setup()
    s.run(
        h.surface_ruled,
        {
            "edges": ["line1", "line2"],
            "distance": 5,
            "angle": 30,
            "ruled_type": "direction",
            "direction": "origin:Z",
            "corner": "mitered",
            "alternate_face": True,
            "chain": False,
        },
    )
    (profile,) = s.root.open_profiles
    assert profile.method == "createOpenProfile" and profile.chain is False
    inp = s.features.ruledSurfaceFeatures.last_input
    _, _, angle, ruled_type, direction = inp.create_args
    assert real(angle) == pytest.approx(math.pi / 6)
    assert ruled_type == F.RuledSurfaceTypes.DirectionRuledSurfaceType
    assert direction is s.root.zConstructionAxis
    assert inp.sets["cornerType"] == F.RuledSurfaceCornerTypes.MiteredRuledSurfaceCornerType
    assert inp.sets["alternateFace"] is True


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"edges": ["edge"], "distance": 1, "ruled_type": "direction"}, "direction"),
        ({"edges": ["edge"], "distance": 1, "direction": "origin:Z"}, "direction"),
        ({"edges": ["edge", "line1"], "distance": 1}, "edges"),
        ({"edges": ["edge"]}, "distance"),
        ({"edges": ["edge"], "distance": 1, "operation": "cut"}, "operation"),
        ({"edges": ["edge"], "distance": 1, "corner": "x"}, "corner"),
        ({"edges": [], "distance": 1}, "edges"),
    ],
)
def test_ruled_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.surface_ruled, params, param)
    assert s.features.ruledSurfaceFeatures.inputs == []
    assert s.root.open_profiles == []


# --- 2.9 offset ----------------------------------------------------------------------------


def test_offset_faces_2mm() -> None:
    s = Setup()
    offsets = s.features.offsetFeatures
    offsets.will_create(FakeBody("Offset", is_solid=False))
    result = s.run(h.surface_offset, {"faces": ["solid_face"], "distance": 2})
    inp = offsets.last_input
    faces, distance, operation, chain = inp.create_args
    assert list(faces) == [s.box.faces.item(0)]
    assert real(distance) == pytest.approx(0.2)
    assert operation == OPS.NewBodyFeatureOperation
    assert chain is True
    assert inp.sets["offsetType"] == F.OffsetTypes.SharpOffsetType
    assert names(result["created_bodies"]) == [("Offset", False)]


def test_offset_negative_rounded_no_chain() -> None:
    s = Setup()
    s.run(
        h.surface_offset,
        {"faces": ["top"], "distance": -1.5, "chain": False, "offset_type": "rounded"},
    )
    inp = s.features.offsetFeatures.last_input
    _, distance, _, chain = inp.create_args
    assert real(distance) == pytest.approx(-0.15) and chain is False
    assert inp.sets["offsetType"] == F.OffsetTypes.RoundedOffsetType


def test_offset_type_not_available() -> None:
    s = Setup()
    offsets = s.features.offsetFeatures
    offsets.on_input = lambda inp: inp.missing.add("offsetType")
    err = s.error(h.surface_offset, {"faces": ["top"], "distance": 1, "offset_type": "rounded"})
    assert err.code is ErrorCode.FUSION_API_ERROR and "not available" in err.message
    assert offsets.added == []
    s.run(h.surface_offset, {"faces": ["top"], "distance": 1})
    assert len(offsets.added) == 1


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"faces": ["top"]}, "distance"),
        ({"faces": ["top"], "distance": 0}, "distance"),
        ({"faces": ["edge"], "distance": 1}, "faces"),
        ({"faces": ["top"], "distance": 1, "operation": "join"}, "operation"),
        ({"faces": ["top"], "distance": 1, "offset_type": "x"}, "offset_type"),
    ],
)
def test_offset_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.surface_offset, params, param)
    assert s.features.offsetFeatures.inputs == []

"""``fusion_addin.core.surfaces`` helpers on fakes (c11 D2, D3, D6, D12; task 1.3)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core import surfaces as s
from fusion_addin.core.features import BodySnapshot
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import FakeBody, FakeBoundingBox, fake_box_body
from tests.fakes.fusion_features import make_ctx
from tests.fakes.fusion_sketch import make_sketch, polygon_curves
from tests.fakes.fusion_surfaces import fake_surface_design

adsk = fusion_addin.adsk
F = adsk.fusion
P = adsk.core.Point3D.create


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("continuity", "connected", F.SurfaceContinuityTypes.ConnectedSurfaceContinuityType),
        ("continuity", "curvature", F.SurfaceContinuityTypes.CurvatureSurfaceContinuityType),
        ("extend_type", "perpendicular", F.SurfaceExtendTypes.PerpendicularSurfaceExtendType),
        ("alignment", "align", F.SurfaceExtendAlignment.AlignEdges),
        ("loop_type", "internal", F.UntrimLoopTypes.InternalLoopsUntrimType),
        ("ruled_type", "direction", F.RuledSurfaceTypes.DirectionRuledSurfaceType),
        ("corner", "mitered", F.RuledSurfaceCornerTypes.MiteredRuledSurfaceCornerType),
        ("offset_type", "rounded", F.OffsetTypes.RoundedOffsetType),
    ],
)
def test_surface_enum_maps(kind: str, value: str, expected: int) -> None:
    assert s.surface_enum(kind, "p", value) == expected


def test_surface_enum_unknown_value_is_invalid() -> None:
    with pytest.raises(BridgeError) as info:
        s.surface_enum("continuity", "continuity", "smooth")
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["param"] == "continuity"


def test_surface_enum_missing_in_api_is_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(F, "OffsetTypes")
    with pytest.raises(BridgeError) as info:
        s.surface_enum("offset_type", "offset_type", "sharp")
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert "not available" in info.value.message


def test_surface_operation_only_new_body_or_component() -> None:
    assert s.surface_operation("new_body") == F.FeatureOperations.NewBodyFeatureOperation
    assert s.surface_operation("new_component") == F.FeatureOperations.NewComponentFeatureOperation
    for bad in ("join", "cut", "intersect", None):
        with pytest.raises(BridgeError) as info:
            s.surface_operation(bad)
        assert info.value.code is ErrorCode.INVALID_PARAMS
        assert info.value.details["param"] == "operation"


def _added(design: Any, *bodies: FakeBody, health: Any = None) -> tuple[Any, BodySnapshot]:
    root = design.rootComponent
    patches = root.features.patchFeatures
    if health is not None:
        patches.health = health
    before = BodySnapshot.take([root])
    patches.will_create(*bodies)
    return patches.add(patches.createInput("c", 3)), before


def test_surface_result_adds_is_solid() -> None:
    old = fake_box_body(1, 1, 1, name="Box")
    design = fake_surface_design(old)
    patches = design.rootComponent.features.patchFeatures
    before = BodySnapshot.take([design.rootComponent])
    patches.will_create(FakeBody("Patch", is_solid=False)).will_modify(old)
    feature = patches.add(patches.createInput("c", 3))
    result = s.surface_result(make_ctx(design), feature, before)
    assert [(b["name"], b["is_solid"]) for b in result["created_bodies"]] == [("Patch", False)]
    assert [(b["name"], b["is_solid"]) for b in result["modified_bodies"]] == [("Box", True)]
    assert result["feature"]["type"] == "PatchFeature"
    assert s.first_body_is_solid(feature) is False


def test_surface_result_error_health_raises_and_direct_design_deletes() -> None:
    design = fake_surface_design(parametric=False)
    feature, before = _added(
        design, FakeBody("P", is_solid=False), health=F.FeatureHealthStates.ErrorFeatureHealthState
    )
    with pytest.raises(BridgeError) as info:
        s.surface_result(make_ctx(design), feature, before)
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert design.rootComponent.features.patchFeatures.count == 0
    assert design.rootComponent.bRepBodies.count == 0


def test_open_profile_from_curves_and_edges() -> None:
    box = fake_box_body(1, 1, 1, name="Box")
    design = fake_surface_design(box)
    sketch = make_sketch(design, "XY")
    line, line2 = polygon_curves(sketch, [(0, 0), (1, 0), (1, 1)], closed=False)
    edge = box.edges.item(0)
    ctx = make_ctx(design, {}, {"l1": line, "l2": line2, "e": edge})
    profile = s.open_profile(ctx, ["l1", "l2", "l1"])
    assert profile.method == "createOpenProfile" and profile.entities == [line, line2]
    assert profile.chain is True
    edge_profile = s.open_profile(ctx, ["e"], chain=False)
    assert edge_profile.method == "createBRepEdgeProfile" and edge_profile.chain is False


def test_open_profile_mixed_empty_wrong_kind_and_fusion_error() -> None:
    box = fake_box_body(1, 1, 1, name="Box")
    design = fake_surface_design(box)
    sketch = make_sketch(design, "XY")
    (line,) = polygon_curves(sketch, [(0, 0), (1, 0)], closed=False)
    ctx = make_ctx(design, {}, {"l": line, "e": box.edges.item(0), "b": box})
    for refs, reason in ((["l", "e"], "mixed_profile"), ([], "empty")):
        with pytest.raises(BridgeError) as info:
            s.open_profile(ctx, refs)
        assert info.value.code is ErrorCode.INVALID_PARAMS
        assert info.value.details["reason"] == reason
    with pytest.raises(BridgeError) as info:
        s.open_profile(ctx, ["b"])
    assert info.value.code is ErrorCode.INVALID_PARAMS
    design.rootComponent.profile_error = "gap"
    with pytest.raises(BridgeError) as info:
        s.open_profile(ctx, ["l"])
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert design.rootComponent.open_profiles == []


def test_bbox_distance() -> None:
    box = FakeBoundingBox((0, 0, 0), (1, 1, 1))
    assert s.bbox_distance(box, P(0.5, 0.5, 0.5)) == 0.0
    assert s.bbox_distance(box, P(1, 1, 3)) == pytest.approx(2.0)
    assert s.bbox_distance(box, P(-3, -4, 0.5)) == pytest.approx(5.0)

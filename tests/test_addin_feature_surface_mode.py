"""Surface mode (``is_solid = false``) of ``feature.extrude`` / ``revolve`` / ``sweep`` /
``loft`` on feature fakes (c11 D12; tasks 4.1-4.4)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.handlers import feature_sketch as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.sketch_refs import format_profile_ref
from tests.fakes.brep import FakeBody, fake_box_body
from tests.fakes.fusion_features import make_ctx
from tests.fakes.fusion_sketch import auto_profiles, make_sketch, polygon_curves
from tests.fakes.fusion_surfaces import FakeOpenProfile, fake_surface_design

adsk = fusion_addin.adsk
OPS = adsk.fusion.FeatureOperations

DIST = {"type": "distance", "distance": 10}


class Setup:
    """A 40 x 20 mm rectangle sketch on XY (``profile``), a second one on XZ (``profile2``),
    open lines ``line1``/``line2`` and a box ``box`` (edge ``edge``)."""

    def __init__(self) -> None:
        self.box = fake_box_body(1, 1, 1, name="Box")
        self.design = fake_surface_design(self.box)
        self.root = self.design.rootComponent
        self.features = self.root.features
        self.sketch = make_sketch(self.design, "XY")
        polygon_curves(self.sketch, [(0, 0), (4, 0), (4, 2), (0, 2)])
        self.sketch.profile_builder = auto_profiles
        self.profile = format_profile_ref(self.sketch.entityToken, 20, 10)
        self.sketch2 = make_sketch(self.design, "XZ")
        polygon_curves(self.sketch2, [(0, 0), (2, 0), (2, 2), (0, 2)])
        self.sketch2.profile_builder = auto_profiles
        self.profile2 = format_profile_ref(self.sketch2.entityToken, 10, 10)
        self.open = make_sketch(self.design, "YZ")
        self.line1, self.line2 = polygon_curves(self.open, [(0, 0), (3, 0), (3, 3)], closed=False)
        self.aliases: dict[str, Any] = {
            "line1": self.line1,
            "line2": self.line2,
            "edge": self.box.edges.item(0),
            "face": self.box.faces.item(0),
        }

    def ctx(self, params: dict[str, Any]) -> Context:
        return make_ctx(self.design, params, self.aliases)

    def run(self, fn: Any, params: dict[str, Any]) -> Any:
        return fn(self.ctx(params), params)

    def invalid(self, fn: Any, params: dict[str, Any], param: str) -> BridgeError:
        with pytest.raises(BridgeError) as info:
            fn(self.ctx(params), params)
        err = info.value
        assert err.code is ErrorCode.INVALID_PARAMS, err
        assert err.details.get("param") == param, err.details
        return err


# --- 4.1 extrude ---------------------------------------------------------------------------


def test_surface_extrude_of_open_curve() -> None:
    s = Setup()
    extrudes = s.features.extrudeFeatures
    extrudes.will_create(FakeBody("Wall", is_solid=False))
    result = s.run(h.feature_extrude, {"profiles": ["line1"], "side1": DIST, "is_solid": False})
    (profile,) = s.root.open_profiles
    assert profile.method == "createOpenProfile" and profile.entities == [s.line1]
    assert profile.chain is True
    inp = extrudes.last_input
    assert inp.create_args == (profile, OPS.NewBodyFeatureOperation)
    assert inp.sets["isSolid"] is False
    assert inp.calls[0][0] == "setOneSideExtent"
    assert [b["name"] for b in result["created_bodies"]] == ["Wall"]


def test_surface_extrude_of_edges_and_of_closed_profile() -> None:
    s = Setup()
    s.run(h.feature_extrude, {"profiles": ["edge"], "side1": DIST, "is_solid": False})
    (profile,) = s.root.open_profiles
    assert isinstance(profile, FakeOpenProfile) and profile.method == "createBRepEdgeProfile"
    s.run(
        h.feature_extrude,
        {"profiles": [s.profile], "side1": DIST, "is_solid": False, "operation": "new_component"},
    )
    inp = s.features.extrudeFeatures.last_input
    assert inp.create_args == (s.sketch.profiles.item(0), OPS.NewComponentFeatureOperation)
    assert inp.sets["isSolid"] is False
    assert len(s.root.open_profiles) == 1


def test_solid_extrude_unchanged() -> None:
    s = Setup()
    s.run(h.feature_extrude, {"profiles": [s.profile], "side1": DIST})
    inp = s.features.extrudeFeatures.last_input
    assert "isSolid" not in inp.sets
    s.invalid(h.feature_extrude, {"profiles": ["line1"], "side1": DIST}, "profiles")
    assert s.root.open_profiles == []


@pytest.mark.parametrize("operation", ["cut", "join", "intersect"])
def test_surface_extrude_refuses_boolean_operations(operation: str) -> None:
    s = Setup()
    s.invalid(
        h.feature_extrude,
        {"profiles": ["line1"], "side1": DIST, "is_solid": False, "operation": operation},
        "operation",
    )
    assert s.features.extrudeFeatures.inputs == []


def test_surface_extrude_refuses_thin_and_mixed_profiles() -> None:
    s = Setup()
    s.invalid(
        h.feature_extrude,
        {"profiles": ["line1"], "side1": DIST, "is_solid": False, "thin": {"thickness": 1}},
        "thin",
    )
    err = s.invalid(
        h.feature_extrude,
        {"profiles": ["line1", s.profile], "side1": DIST, "is_solid": False},
        "profiles",
    )
    assert err.details["reason"] == "mixed_profile"
    err = s.invalid(
        h.feature_extrude,
        {"profiles": ["line1", "edge"], "side1": DIST, "is_solid": False},
        "profiles",
    )
    assert err.details["reason"] == "mixed_profile"
    s.invalid(h.feature_extrude, {"profiles": ["line1"], "side1": DIST, "is_solid": 0}, "is_solid")
    assert s.features.extrudeFeatures.inputs == []
    assert s.root.open_profiles == []


# --- 4.2 revolve ---------------------------------------------------------------------------


def test_surface_revolve() -> None:
    s = Setup()
    s.run(h.feature_revolve, {"profiles": [s.profile], "axis": "origin:X", "is_solid": False})
    assert s.features.revolveFeatures.last_input.sets["isSolid"] is False
    s.run(h.feature_revolve, {"profiles": [s.profile], "axis": "origin:X"})
    assert "isSolid" not in s.features.revolveFeatures.last_input.sets
    s.invalid(
        h.feature_revolve,
        {"profiles": [s.profile], "axis": "origin:X", "is_solid": False, "operation": "cut"},
        "operation",
    )


# --- 4.3 sweep -----------------------------------------------------------------------------


def test_surface_sweep_of_open_curve() -> None:
    s = Setup()
    path = polygon_curves(make_sketch(s.design, "XY"), [(5, 0), (5, 5)], closed=False)
    s.aliases["path"] = path[0]
    s.run(
        h.feature_sweep,
        {"profiles": ["line1"], "path": ["path"], "is_solid": False},
    )
    (profile,) = s.root.open_profiles
    inp = s.features.sweepFeatures.last_input
    assert inp.create_args[0] is profile
    assert inp.sets["isSolid"] is False
    s.run(h.feature_sweep, {"profiles": [s.profile], "path": ["path"]})
    assert "isSolid" not in s.features.sweepFeatures.last_input.sets
    s.invalid(
        h.feature_sweep,
        {"profiles": ["line1"], "path": ["path"], "is_solid": False, "operation": "join"},
        "operation",
    )


# --- 4.4 loft ------------------------------------------------------------------------------


def test_surface_loft() -> None:
    s = Setup()
    sections = [{"entity": s.profile}, {"entity": s.profile2}]
    s.run(h.feature_loft, {"sections": sections, "is_solid": False})
    assert s.features.loftFeatures.last_input.sets["isSolid"] is False
    s.run(h.feature_loft, {"sections": sections})
    assert "isSolid" not in s.features.loftFeatures.last_input.sets
    s.invalid(
        h.feature_loft,
        {"sections": sections, "is_solid": False, "operation": "intersect"},
        "operation",
    )

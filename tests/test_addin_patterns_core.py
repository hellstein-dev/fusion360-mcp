"""``fusion_addin/core/patterns.py`` (c08 D3; tasks 2.1-2.2): pattern input kinds, owner
component, compute / distance enums, occurrence snapshot and the pattern result."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core import patterns as p
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import (
    FakeComponent,
    FakeFeature,
    FakeOccurrence,
    add_occurrence,
    fake_box_body,
)
from tests.fakes.fusion_features import FakeFeatureInput, fake_feature_design, make_ctx

adsk = fusion_addin.adsk
PCO = adsk.fusion.PatternComputeOptions
PDT = adsk.fusion.PatternDistanceType


class Setup:
    def __init__(self) -> None:
        self.box = fake_box_body(1, 1, 1, name="Box")
        self.other = fake_box_body(1, 1, 1, name="Other")
        self.design = fake_feature_design(self.box, self.other)
        self.root = self.design.rootComponent
        self.feature = FakeFeature("ExtrudeFeature", "Boss", component=self.root)
        self.part = FakeComponent("part")
        self.occ = add_occurrence(self.root, FakeOccurrence(self.part, "Part:1"), self.root)
        self.top = next(f for f in self.box.faces if f.entityToken.endswith("face+z"))
        self.aliases: dict[str, Any] = {
            "box": self.box,
            "other": self.other,
            "boss": self.feature,
            "top": self.top,
            "occ": self.occ,
            "edge": self.box.edges.item(0),
        }

    def ctx(self) -> Any:
        return make_ctx(self.design, {}, self.aliases)


def error(fn: Any, *args: Any) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        fn(*args)
    return info.value


# --- 2.1 pattern_inputs --------------------------------------------------------------------


def test_feature_input_uses_parent_component() -> None:
    s = Setup()
    pattern = p.pattern_inputs(s.ctx(), ["boss", "boss"])
    assert pattern.kind == "feature"
    assert pattern.entities == [s.feature]
    assert list(pattern.collection) == [s.feature]
    assert pattern.component is s.root
    assert pattern.bodies == []


def test_body_and_face_inputs() -> None:
    s = Setup()
    bodies = p.pattern_inputs(s.ctx(), ["box", "other"])
    assert (bodies.kind, bodies.component) == ("body", s.root)
    assert bodies.bodies == [s.box, s.other]
    assert isinstance(bodies.collection, adsk.core.ObjectCollection)
    faces = p.pattern_inputs(s.ctx(), ["top"])
    assert faces.kind == "face"
    assert faces.bodies == [s.box]
    assert faces.component is s.root


def test_occurrence_input_owner_is_root_or_assembly_context() -> None:
    s = Setup()
    pattern = p.pattern_inputs(s.ctx(), ["occ"])
    assert pattern.kind == "occurrence"
    assert pattern.component is s.root
    parent = FakeOccurrence(FakeComponent("asm"), "Asm:1")
    s.occ.assemblyContext = parent  # type: ignore[attr-defined]
    assert p.pattern_inputs(s.ctx(), ["occ"]).component is parent.component


def test_mixed_kinds_are_refused() -> None:
    s = Setup()
    err = error(p.pattern_inputs, s.ctx(), ["box", "top", "boss"])
    assert err.code is ErrorCode.INVALID_PARAMS
    assert err.details["kinds"] == ["feature", "body", "face"]
    assert err.details["reason"] == "mixed_kinds"


def test_foreign_kind_and_empty_list() -> None:
    s = Setup()
    err = error(p.pattern_inputs, s.ctx(), ["box", "edge"])
    assert err.details["index"] == 1
    assert err.details["actual"].startswith("BRepEdge")
    assert error(p.pattern_inputs, s.ctx(), []).details["reason"] == "empty"
    missing = error(p.pattern_inputs, s.ctx(), ["nope"])
    assert missing.code is ErrorCode.ENTITY_NOT_FOUND
    assert missing.details["param"] == "entities"


# --- 2.2 enums, compute, snapshot, result --------------------------------------------------


def test_enums() -> None:
    assert p.compute_option_enum("optimized") == PCO.OptimizedPatternCompute
    assert p.compute_option_enum("identical") == PCO.IdenticalPatternCompute
    assert p.compute_option_enum("adjust") == PCO.AdjustPatternCompute
    assert p.distance_type_enum("extent") == PDT.ExtentPatternDistanceType
    assert p.distance_type_enum("spacing") == PDT.SpacingPatternDistanceType
    assert error(p.compute_option_enum, "fast").details["param"] == "compute"
    assert error(p.distance_type_enum, "pitch").details["param"] == "distance_type"


def test_apply_compute_only_for_features() -> None:
    inp = FakeFeatureInput()
    p.apply_compute(inp, "feature", "identical", "RectangularPatternFeature")
    assert inp.sets == {"patternComputeOption": PCO.IdenticalPatternCompute}
    other = FakeFeatureInput()
    p.apply_compute(other, "body", "identical", "RectangularPatternFeature")
    assert other.sets == {}
    with pytest.raises(BridgeError):
        p.apply_compute(other, "body", "bogus", "RectangularPatternFeature")
    old = FakeFeatureInput()
    old.missing.add("patternComputeOption")
    err = error(p.apply_compute, old, "feature", "adjust", "RectangularPatternFeature")
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert "not available" in err.message


def test_resolve_axis_kinds() -> None:
    s = Setup()
    ctx = s.ctx()
    assert p.resolve_axis(ctx, "origin:Z", s.part) is s.part.zConstructionAxis
    assert p.resolve_axis(ctx, "origin:Z", None) is s.root.zConstructionAxis
    assert p.resolve_axis(ctx, "edge", None) is s.box.edges.item(0)
    err = error(p.resolve_axis, ctx, "top", None)
    assert err.details == {"param": "axis", "expected": "axis", "got": "BRepFace (plane)"}


def test_pattern_result_reports_new_bodies_and_occurrences() -> None:
    s = Setup()
    pattern = p.pattern_inputs(s.ctx(), ["occ"])
    before = p.pattern_snapshot(pattern)
    assert before.occurrences is not None and before.occurrences.paths == {"Part:1"}
    patterns = s.root.features.rectangularPatternFeatures
    new = FakeOccurrence(s.part, "Part:2")
    patterns.on_add = lambda feature: add_occurrence(s.root, new, s.root)
    feature = patterns.add(patterns.createInput(pattern.collection))
    result = p.pattern_result(s.ctx(), feature, before)
    assert result["created_occurrences"] == [
        {
            "ref": new.entityToken,
            "type": "Occurrence",
            "name": "Part:2",
            "full_path_name": "Part:2",
        }
    ]
    assert result["health"]["state"] == "ok"


def test_body_pattern_result_has_no_occurrences() -> None:
    s = Setup()
    pattern = p.pattern_inputs(s.ctx(), ["box"])
    before = p.pattern_snapshot(pattern)
    assert before.occurrences is None
    copy = fake_box_body(1, 1, 1, name="Box (2)")
    patterns = s.root.features.rectangularPatternFeatures
    feature = patterns.will_create(copy).add(patterns.createInput(pattern.collection))
    result = p.pattern_result(s.ctx(), feature, before)
    assert "created_occurrences" not in result
    assert [b["name"] for b in result["created_bodies"]] == ["Box (2)"]


def test_error_pattern_raises() -> None:
    s = Setup()
    pattern = p.pattern_inputs(s.ctx(), ["box"])
    before = p.pattern_snapshot(pattern)
    patterns = s.root.features.rectangularPatternFeatures
    patterns.health = adsk.fusion.FeatureHealthStates.ErrorFeatureHealthState
    feature = patterns.add(patterns.createInput(pattern.collection))
    err = error(p.pattern_result, s.ctx(), feature, before)
    assert err.code is ErrorCode.FUSION_API_ERROR

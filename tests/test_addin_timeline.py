"""``timeline.*`` handlers on fakes (c09 tasks 2.2, 3.x, 4.x, 5.x, 6.x, 7.x)."""

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import timeline as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.design import FakeApp, FakeDesign, FakeEntity
from tests.fakes.documents import FakeDocsApp
from tests.fakes.parameters import FakeParameterSet
from tests.fakes.timeline import FakeTimeline

adsk = fusion_addin.adsk
STATES = adsk.fusion.FeatureHealthStates


class Model:
    """Sketch1 -> Extrude1 (d1 = 20 mm) -> Fillet1 (r1 = 2 mm) in a parametric design."""

    def __init__(self) -> None:
        self.app = FakeDocsApp()
        self.design: Any = self.app.open_document("Part").design
        self.params: FakeParameterSet = self.design.parameter_set
        self.timeline = FakeTimeline()
        self.design._timeline = self.timeline
        add = self.timeline.add_feature
        self.sketch = add("Sketch1", "adsk::fusion::Sketch", "sk-1", design=self.design)
        self.extrude = add(
            "Extrude1", "adsk::fusion::ExtrudeFeature", "ext-1", [self.sketch], self.design
        )
        self.fillet = add(
            "Fillet1", "adsk::fusion::FilletFeature", "fil-1", [self.extrude], self.design
        )
        root = self.design.rootComponent
        for item in (self.sketch, self.extrude, self.fillet):
            item.entity.parentComponent = root
        ext = self.extrude.entity
        self.d1 = self.params.add_model("d1", "20 mm", created_by=ext, role="AlongDistance")
        self.taper = self.params.add_model("d2", "0 deg", "deg", created_by=ext, role="Taper")
        ext.extentOne = FakeEntity("adsk::fusion::DistanceExtentDefinition", distance=self.d1)
        ext.extentTwo = None
        ext.taperAngleOne = self.taper
        fil = self.fillet.entity
        self.r1 = self.params.add_model("r1", "2 mm", created_by=fil, role="Radius")
        fil.edgeSets = FakeListOf(
            FakeEntity("adsk::fusion::ConstantRadiusFilletEdgeSet", radius=self.r1)
        )

    def ctx(self, params: dict[str, Any]) -> Context:
        return Context(self.app, params)

    def run(self, fn: Any, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        return fn(self.ctx(params), params)

    def fails(self, fn: Any, params: dict[str, Any], code: ErrorCode) -> BridgeError:
        with pytest.raises(BridgeError) as info:
            self.run(fn, params)
        assert info.value.code is code, info.value
        return info.value

    def fillet_fails_when(self, predicate: Any) -> None:
        """Recompute: the fillet falls into error when ``predicate(params)`` holds."""

        def recompute(params: FakeParameterSet) -> None:
            bad = predicate(params)
            self.fillet.healthState = (
                STATES.ErrorFeatureHealthState if bad else STATES.HealthyFeatureHealthState
            )
            self.fillet.errorOrWarningMessage = "fillet radius too large" if bad else ""

        self.params.on_change = recompute

    def feature(self, name: str, object_type: str, token: str, **attrs: Any) -> Any:
        item = self.timeline.add_feature(name, object_type, token, design=self.design)
        for key, value in attrs.items():
            setattr(item.entity, key, value)
        return item


class FakeListOf:
    def __init__(self, *items: Any) -> None:
        self._items = list(items)

    @property
    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> Any:
        return self._items[index]


MUTATING = {
    "timeline.compute_all",
    "timeline.roll_to",
    "timeline.set_suppressed",
    "timeline.delete",
    "timeline.rename",
    "timeline.set_model_parameter",
    "timeline.edit_extrude",
    "timeline.edit_revolve",
    "timeline.edit_fillet",
    "timeline.edit_chamfer",
    "timeline.edit_hole",
    "timeline.edit_shell",
    "timeline.create_group",
    "timeline.set_group_collapsed",
    "timeline.ungroup",
}
READING = {"timeline.list", "timeline.health", "timeline.feature_parameters"}


def test_registration() -> None:
    names = {n for n in REGISTRY.names() if n.startswith("timeline.")}
    assert names == MUTATING | READING
    for name in names:
        assert REGISTRY.get(name).mutating is (name in MUTATING)  # type: ignore[union-attr]


# --- 2.2 shared helpers --------------------------------------------------------------------------


@pytest.mark.parametrize("method", sorted(MUTATING | READING))
def test_direct_design_is_rejected(method: str) -> None:
    app = FakeApp(FakeDesign(parametric=False))
    spec = REGISTRY.get(method)
    assert spec is not None
    params = {
        "item": "ext-1",
        "items": ["ext-1"],
        "feature": "ext-1",
        "name": "d1",
        "value": 1,
        "position": "start",
        "first": "a",
        "last": "b",
        "group": "timeline:G",
        "collapsed": True,
        "distance": 1,
        "radius": 1,
    }
    with pytest.raises(BridgeError) as info:
        spec.fn(Context(app, params), params)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert "parametric design" in info.value.message


def test_entity_without_timeline_item() -> None:
    m = Model()
    m.design.add_entity(FakeEntity("adsk::fusion::BRepBody", "Body1", token="body-1"))
    error = m.fails(
        h.timeline_roll_to, {"position": "before", "item": "body-1"}, ErrorCode.INVALID_PARAMS
    )
    assert "no timeline item" in error.message


def test_item_serialisation_of_feature_group_and_member() -> None:
    m = Model()
    m.fillet.healthState = STATES.WarningFeatureHealthState
    m.fillet.errorOrWarningMessage = "tiny edge"
    group = m.timeline.timelineGroups.add(0, 1)
    assert group is not None
    group.name = "Base"
    ctx = m.ctx({})
    assert h._item(ctx, group) == {
        "index": 0,
        "name": "Base",
        "type": "TimelineGroup",
        "ref": "timeline:Base",
        "is_group": True,
        "group": None,
        "is_suppressed": False,
        "is_rolled_back": False,
        "health": "ok",
        "message": None,
        "component": None,
    }
    member = h._item(ctx, m.extrude)
    assert member["ref"] == "ext-1"
    assert member["type"] == "ExtrudeFeature"
    assert member["group"] == "timeline:Base"
    assert member["component"] == "component:root"
    fillet = h._item(ctx, m.fillet)
    assert (fillet["health"], fillet["message"], fillet["group"]) == ("warning", "tiny edge", None)
    assert h._timeline_object(ctx, "fil-1") is m.fillet
    assert h._timeline_object(ctx, "timeline:Base") is group


# --- 3.1 list ------------------------------------------------------------------------------------


def test_list_in_order_with_marker() -> None:
    m = Model()
    result = m.run(h.timeline_list)
    assert (result["count"], result["marker_position"], result["total"]) == (3, 3, 3)
    assert result["truncated"] is False
    assert [(i["name"], i["ref"], i["health"]) for i in result["items"]] == [
        ("Sketch1", "sk-1", "ok"),
        ("Extrude1", "ext-1", "ok"),
        ("Fillet1", "fil-1", "ok"),
    ]


def test_list_only_problems() -> None:
    m = Model()
    m.fillet.healthState = STATES.ErrorFeatureHealthState
    m.fillet.errorOrWarningMessage = "radius too large"
    items = m.run(h.timeline_list, {"only_problems": True})["items"]
    assert [(i["name"], i["message"]) for i in items] == [("Fillet1", "radius too large")]


def test_list_pagination_and_limits() -> None:
    m = Model()
    result = m.run(h.timeline_list, {"offset": 1, "limit": 1})
    assert [i["name"] for i in result["items"]] == ["Extrude1"]
    assert (result["total"], result["truncated"]) == (3, True)
    m.fails(h.timeline_list, {"limit": 0}, ErrorCode.INVALID_PARAMS)
    m.fails(h.timeline_list, {"limit": 1001}, ErrorCode.INVALID_PARAMS)
    m.fails(h.timeline_list, {"offset": -1}, ErrorCode.INVALID_PARAMS)


def test_list_group_members_follow_group() -> None:
    m = Model()
    m.timeline.timelineGroups.add(1, 2)
    items = m.run(h.timeline_list)["items"]
    assert [(i["name"], i["group"]) for i in items] == [
        ("Sketch1", None),
        ("Group1", None),
        ("Extrude1", "timeline:Group1"),
        ("Fillet1", "timeline:Group1"),
    ]


# --- 3.3 health / 3.5 compute_all ----------------------------------------------------------------


def test_health_ok() -> None:
    result = Model().run(h.timeline_health)
    assert result["ok"] is True
    assert (result["errors"], result["warnings"]) == ([], [])
    assert (result["suppressed_count"], result["rolled_back_count"]) == (0, 0)


def test_health_counts_problems() -> None:
    m = Model()
    m.sketch.healthState = STATES.WarningFeatureHealthState
    m.extrude.isSuppressed = True  # the fillet loses its reference
    m.timeline.markerPosition = 2
    result = m.run(h.timeline_health)
    assert result["ok"] is False
    assert [i["name"] for i in result["errors"]] == ["Fillet1"]
    assert [i["name"] for i in result["warnings"]] == ["Sketch1"]
    assert (result["suppressed_count"], result["rolled_back_count"]) == (1, 1)


def test_compute_all_reveals_error() -> None:
    m = Model()

    def recompute(design: Any) -> None:
        m.extrude.healthState = STATES.ErrorFeatureHealthState
        m.extrude.errorOrWarningMessage = "profile lost"

    m.design.on_compute = recompute
    result = m.run(h.timeline_compute_all)
    assert m.design.compute_calls == 1
    assert result["ok"] is False
    first = result["errors"][0]
    assert (first["name"], first["message"]) == ("Extrude1", "profile lost")
    assert [i["name"] for i in result["errors"]] == ["Extrude1", "Fillet1"]


# --- 4.1 roll_to ---------------------------------------------------------------------------------


def test_roll_before_and_after() -> None:
    m = Model()
    result = m.run(h.timeline_roll_to, {"position": "before", "item": "ext-1"})
    assert result == {"marker_position": 1, "count": 3, "rolled_back_count": 2}
    assert m.extrude.isRolledBack and m.fillet.isRolledBack
    assert m.run(h.timeline_roll_to, {"position": "after", "item": "ext-1"})["marker_position"] == 2
    assert m.run(h.timeline_roll_to, {"position": "start"})["marker_position"] == 0
    assert m.run(h.timeline_roll_to, {"position": "end"})["marker_position"] == 3


def test_roll_needs_item_and_valid_position() -> None:
    m = Model()
    m.fails(h.timeline_roll_to, {"position": "after"}, ErrorCode.INVALID_PARAMS)
    m.fails(h.timeline_roll_to, {"position": "middle"}, ErrorCode.INVALID_PARAMS)


def test_roll_into_collapsed_group_expands_it() -> None:
    m = Model()
    group = m.timeline.timelineGroups.add(1, 2)
    assert group is not None
    group.isCollapsed = True
    m.run(h.timeline_roll_to, {"position": "before", "item": "fil-1"})
    assert group.isCollapsed is False


def test_roll_failure_restores_marker() -> None:
    m = Model()
    m.timeline.markerPosition = 2
    m.timeline.fail_roll = True
    m.fails(h.timeline_roll_to, {"position": "before", "item": "sk-1"}, ErrorCode.FUSION_API_ERROR)
    assert m.timeline.markerPosition == 2


# --- 4.3 set_suppressed --------------------------------------------------------------------------


def test_suppress_fillet() -> None:
    m = Model()
    result = m.run(h.timeline_set_suppressed, {"items": ["fil-1"]})
    assert [i["is_suppressed"] for i in result["items"]] == [True]
    assert (result["marker_position"], result["count"]) == (3, 3)
    m.run(h.timeline_set_suppressed, {"items": ["fil-1"], "suppressed": False})
    assert m.fillet.isSuppressed is False


def test_failure_on_second_item_restores_first() -> None:
    m = Model()
    m.timeline.fail_suppress.add("Sketch1")
    error = m.fails(
        h.timeline_set_suppressed, {"items": ["fil-1", "sk-1"]}, ErrorCode.FUSION_API_ERROR
    )
    assert m.fillet.isSuppressed is False
    assert "Sketch1" in error.message


def test_unsuppress_into_error_is_reverted() -> None:
    m = Model()
    m.fillet.isSuppressed = True
    m.fillet.healthState = STATES.ErrorFeatureHealthState  # shows once unsuppressed
    m.fillet.errorOrWarningMessage = "edges gone"
    error = m.fails(
        h.timeline_set_suppressed,
        {"items": ["fil-1"], "suppressed": False},
        ErrorCode.FUSION_API_ERROR,
    )
    assert m.fillet.isSuppressed is True
    assert error.message == "Fillet1: edges gone"
    assert error.details["reverted"] is True
    kept = m.run(
        h.timeline_set_suppressed, {"items": ["fil-1"], "suppressed": False, "allow_errors": True}
    )
    assert kept["items"][0]["health"] == "error"


def test_suppress_breaking_dependents_is_reverted() -> None:
    m = Model()
    error = m.fails(h.timeline_set_suppressed, {"items": ["ext-1"]}, ErrorCode.FUSION_API_ERROR)
    assert m.extrude.isSuppressed is False
    assert [i["name"] for i in error.details["health"]] == ["Fillet1"]


def test_suppress_validation() -> None:
    m = Model()
    m.fails(h.timeline_set_suppressed, {"items": []}, ErrorCode.INVALID_PARAMS)
    m.fails(h.timeline_set_suppressed, {"items": ["x"] * 101}, ErrorCode.INVALID_PARAMS)
    m.sketch.canSuppress = False
    m.fails(h.timeline_set_suppressed, {"items": ["sk-1"]}, ErrorCode.INVALID_PARAMS)


# --- 4.5 delete ----------------------------------------------------------------------------------


def test_delete_independent_feature() -> None:
    m = Model()
    result = m.run(h.timeline_delete, {"item": "fil-1"})
    assert result["deleted"] == {"name": "Fillet1", "type": "FilletFeature"}
    assert result["dependents_checked"] is True
    assert result["problems_after"] == []
    assert (result["count"], m.timeline.names()) == (2, ["Sketch1", "Extrude1"])


def test_delete_would_break_dependents() -> None:
    m = Model()
    error = m.fails(h.timeline_delete, {"item": "ext-1"}, ErrorCode.INVALID_PARAMS)
    assert [d["name"] for d in error.details["dependents"]] == ["Fillet1"]
    assert m.timeline.names() == ["Sketch1", "Extrude1", "Fillet1"]
    assert m.extrude.isSuppressed is False


def test_force_delete_reports_problems() -> None:
    m = Model()
    result = m.run(h.timeline_delete, {"item": "ext-1", "force": True})
    assert result["dependents_checked"] is False
    assert [p["name"] for p in result["problems_after"]] == ["Fillet1"]
    assert m.timeline.names() == ["Sketch1", "Fillet1"]


def test_delete_without_suppression_support() -> None:
    m = Model()
    m.fillet.canSuppress = False
    assert m.run(h.timeline_delete, {"item": "fil-1"})["dependents_checked"] is False


def test_delete_refused_by_fusion() -> None:
    m = Model()
    m.fillet.entity.delete_result = False
    m.fails(h.timeline_delete, {"item": "fil-1"}, ErrorCode.FUSION_API_ERROR)
    assert m.fillet.isSuppressed is False


def test_delete_group_with_contents() -> None:
    m = Model()
    group = m.timeline.timelineGroups.add(2, 2)
    assert group is not None
    result = m.run(h.timeline_delete, {"item": "timeline:Group1"})
    assert result["deleted"] == {"name": "Group1", "type": "TimelineGroup"}
    assert m.timeline.names() == ["Sketch1", "Extrude1"]
    assert m.timeline.group_deletions == [("Group1", True)]


# --- 4.7 rename ----------------------------------------------------------------------------------


def test_rename_keeps_ref() -> None:
    m = Model()
    item = m.run(h.timeline_rename, {"item": "ext-1", "name": "Base"})["item"]
    assert (item["name"], item["ref"]) == ("Base", "ext-1")
    assert m.run(h.timeline_list)["items"][1]["name"] == "Base"


def test_rename_group() -> None:
    m = Model()
    m.timeline.timelineGroups.add(0, 1)
    item = m.run(h.timeline_rename, {"item": "timeline:Group1", "name": "Base"})["item"]
    assert item["ref"] == "timeline:Base"


def test_rename_rejections() -> None:
    m = Model()
    m.fails(h.timeline_rename, {"item": "ext-1", "name": "  "}, ErrorCode.INVALID_PARAMS)
    m.design.add_entity(FakeEntity("adsk::fusion::BRepBody", "Body1", token="body-1"))
    error = m.fails(h.timeline_rename, {"item": "body-1", "name": "B"}, ErrorCode.INVALID_PARAMS)
    assert "body_" in error.message
    m.extrude.entity.rename_error = "name is read-only"
    error = m.fails(h.timeline_rename, {"item": "ext-1", "name": "B"}, ErrorCode.FUSION_API_ERROR)
    assert error.message == "name is read-only"
    assert m.extrude.name == "Extrude1"


# --- 5.1 feature_parameters ----------------------------------------------------------------------


def test_feature_parameters() -> None:
    m = Model()
    m.params.add_model("d9", "3", "N", created_by=m.extrude.entity, role="Other")
    result = m.run(h.timeline_feature_parameters, {"feature": "ext-1"})
    assert result["feature"] == "ext-1"
    assert result["editable_keys"] == ["distance", "taper_angle"]
    d1, d2, d9 = result["parameters"]
    assert d1 == {
        "name": "d1",
        "expression": "20 mm",
        "value": pytest.approx(20.0),
        "value_unit": "mm",
        "role": "AlongDistance",
        "comment": "",
    }
    assert (d2["value"], d2["value_unit"]) == (pytest.approx(0.0), "deg")
    assert d9["value_unit"] == "fusion_internal"
    assert m.run(h.timeline_feature_parameters, {"feature": "timeline:Fillet1"})[
        "editable_keys"
    ] == ["radius"]


# --- 5.3 _apply_expressions ----------------------------------------------------------------------


def test_apply_expressions_success_and_revert() -> None:
    m = Model()
    m.fillet_fails_when(lambda p: p["r1"].value > 0.5)
    ctx = m.ctx({})
    result = h._apply_expressions(ctx, m.fillet.entity, [("radius", m.r1, 3)], "mm", False)
    assert result["changed"] == [
        {
            "key": "radius",
            "parameter": "r1",
            "old_expression": "2 mm",
            "new_expression": "3 mm",
            "value": pytest.approx(3.0),
            "value_unit": "mm",
        }
    ]
    assert result["feature"]["ref"] == "fil-1"
    with pytest.raises(BridgeError) as info:
        h._apply_expressions(ctx, m.fillet.entity, [("radius", m.r1, 6)], "mm", False)
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert info.value.message == "Fillet1: fillet radius too large"
    assert info.value.details["reverted"] is True
    assert m.r1.expression == "3 mm"
    kept = h._apply_expressions(ctx, m.fillet.entity, [("radius", m.r1, 6)], "mm", True)
    assert kept["feature"]["health"] == "error"
    assert m.r1.expression == "6 mm"


# --- 5.4 set_model_parameter ---------------------------------------------------------------------


def test_set_model_parameter_number_and_string() -> None:
    m = Model()
    result = m.run(h.timeline_set_model_parameter, {"name": "d1", "value": 30})
    assert result["parameter"]["expression"] == "30 mm"
    assert result["parameter"]["old_expression"] == "20 mm"
    assert result["parameter"]["value"] == pytest.approx(30.0)
    assert result["feature"]["ref"] == "ext-1"
    m.run(h.timeline_set_model_parameter, {"name": "d1", "value": 2, "units": "in"})
    assert m.d1.expression == "2 in"
    m.run(h.timeline_set_model_parameter, {"name": "d1", "value": "d2 * 0 + 5 mm"})
    assert m.d1.expression == "d2 * 0 + 5 mm"
    m.run(h.timeline_set_model_parameter, {"name": "d2", "value": 5})
    assert m.taper.expression == "5 deg"


def test_set_model_parameter_rejections() -> None:
    m = Model()
    m.params.add_user("width", "40 mm", "mm", "")
    error = m.fails(
        h.timeline_set_model_parameter, {"name": "width", "value": 1}, ErrorCode.INVALID_PARAMS
    )
    assert "parameter_update" in error.message
    m.fails(h.timeline_set_model_parameter, {"name": "zz", "value": 1}, ErrorCode.ENTITY_NOT_FOUND)
    m.params.add_model("f1", "3", "N", created_by=m.extrude.entity)
    m.fails(h.timeline_set_model_parameter, {"name": "f1", "value": 4}, ErrorCode.INVALID_PARAMS)
    m.fails(h.timeline_set_model_parameter, {"name": "d1", "value": ""}, ErrorCode.INVALID_PARAMS)


def test_set_model_parameter_into_error_is_reverted() -> None:
    m = Model()
    m.fillet_fails_when(lambda p: p["d1"].value < 0.5)
    error = m.fails(
        h.timeline_set_model_parameter, {"name": "d1", "value": 1}, ErrorCode.FUSION_API_ERROR
    )
    assert m.d1.expression == "20 mm"
    assert error.details["reverted"] is True
    assert "fillet radius too large" in error.message


# --- 6.1 _edit_feature ---------------------------------------------------------------------------


def test_edit_wrong_type_nothing_to_change_and_unknown_key() -> None:
    m = Model()
    error = m.fails(
        h.timeline_edit_fillet, {"feature": "ext-1", "radius": 1}, ErrorCode.INVALID_PARAMS
    )
    assert error.details == {"expected": "FilletFeature", "actual": "ExtrudeFeature"}
    error = m.fails(h.timeline_edit_extrude, {"feature": "ext-1"}, ErrorCode.INVALID_PARAMS)
    assert "nothing to change" in error.message
    error = m.fails(
        h.timeline_edit_extrude, {"feature": "ext-1", "distance_two": 5}, ErrorCode.INVALID_PARAMS
    )
    assert error.details["available_keys"] == ["distance", "taper_angle"]
    m.fails(
        h.timeline_edit_extrude, {"feature": "ext-1", "distance": True}, ErrorCode.INVALID_PARAMS
    )


# --- 6.2 extrude ---------------------------------------------------------------------------------


def test_edit_extrude_distance_and_taper_atomically() -> None:
    m = Model()
    result = m.run(h.timeline_edit_extrude, {"feature": "ext-1", "distance": 30, "taper_angle": 5})
    assert [(c["key"], c["old_expression"], c["new_expression"]) for c in result["changed"]] == [
        ("distance", "20 mm", "30 mm"),
        ("taper_angle", "0 deg", "5 deg"),
    ]
    m.fillet_fails_when(lambda p: p["d2"].value > 0.2)
    m.fails(
        h.timeline_edit_extrude,
        {"feature": "ext-1", "distance": 40, "taper_angle": 20},
        ErrorCode.FUSION_API_ERROR,
    )
    assert (m.d1.expression, m.taper.expression) == ("30 mm", "5 deg")


def test_edit_extrude_two_sided() -> None:
    m = Model()
    d3 = m.params.add_model("d3", "4 mm", created_by=m.extrude.entity)
    m.extrude.entity.extentTwo = FakeEntity("adsk::fusion::DistanceExtentDefinition", distance=d3)
    m.run(h.timeline_edit_extrude, {"feature": "ext-1", "distance_two": "6 mm"})
    assert d3.expression == "6 mm"


def test_edit_extrude_to_object_extent_unchanged() -> None:
    m = Model()
    m.extrude.entity.extentOne = FakeEntity("adsk::fusion::ToEntityExtentDefinition")
    m.fails(h.timeline_edit_extrude, {"feature": "ext-1", "distance": 30}, ErrorCode.INVALID_PARAMS)
    assert m.d1.expression == "20 mm"


# --- 6.4 revolve ---------------------------------------------------------------------------------


def test_edit_revolve() -> None:
    m = Model()
    a1 = m.params.add_model("a1", "90 deg", "deg")
    rev = m.feature(
        "Revolve1",
        "adsk::fusion::RevolveFeature",
        "rev-1",
        extentDefinition=FakeEntity("adsk::fusion::AngleExtentDefinition", angle=a1),
    )
    a1.createdBy = rev.entity
    result = m.run(h.timeline_edit_revolve, {"feature": "rev-1", "angle": 180})
    assert result["changed"][0]["new_expression"] == "180 deg"
    assert result["changed"][0]["value"] == pytest.approx(180.0)
    m.fails(
        h.timeline_edit_revolve, {"feature": "rev-1", "angle_two": 10}, ErrorCode.INVALID_PARAMS
    )
    a2 = m.params.add_model("a2", "10 deg", "deg", created_by=rev.entity)
    rev.entity.extentDefinition = FakeEntity(
        "adsk::fusion::TwoSidesAngleExtentDefinition", angleOne=a1, angleTwo=a2
    )
    m.run(h.timeline_edit_revolve, {"feature": "rev-1", "angle": 45, "angle_two": 30})
    assert (a1.expression, a2.expression) == ("45 deg", "30 deg")


# --- 6.6 fillet ----------------------------------------------------------------------------------


def test_edit_fillet_all_constant_sets() -> None:
    m = Model()
    r2 = m.params.add_model("r2", "2 mm", created_by=m.fillet.entity)
    m.fillet.entity.edgeSets = FakeListOf(
        FakeEntity("adsk::fusion::ConstantRadiusFilletEdgeSet", radius=m.r1),
        FakeEntity("adsk::fusion::VariableRadiusFilletEdgeSet", startRadius=object()),
        FakeEntity("adsk::fusion::ConstantRadiusFilletEdgeSet", radius=r2),
    )
    result = m.run(h.timeline_edit_fillet, {"feature": "fil-1", "radius": 3})
    assert [c["parameter"] for c in result["changed"]] == ["r1", "r2"]
    assert (m.r1.expression, r2.expression) == ("3 mm", "3 mm")


def test_edit_fillet_role_fallback_and_missing() -> None:
    m = Model()
    del m.fillet.entity.edgeSets
    m.run(h.timeline_edit_fillet, {"feature": "fil-1", "radius": 4})
    assert m.r1.expression == "4 mm"
    m.r1.role = "Other"
    error = m.fails(
        h.timeline_edit_fillet, {"feature": "fil-1", "radius": 4}, ErrorCode.INVALID_PARAMS
    )
    assert "timeline_set_model_parameter" in error.message


# --- 6.8 chamfer ---------------------------------------------------------------------------------


def test_edit_chamfer() -> None:
    m = Model()
    c1 = m.params.add_model("c1", "1 mm")
    m.feature(
        "Chamfer1",
        "adsk::fusion::ChamferFeature",
        "ch-1",
        edgeSets=FakeListOf(
            FakeEntity("adsk::fusion::EqualDistanceChamferEdgeSet", distance=c1),
            FakeEntity("adsk::fusion::TwoDistancesChamferEdgeSet", distanceOne=object()),
        ),
    )
    result = m.run(h.timeline_edit_chamfer, {"feature": "ch-1", "distance": 1.5})
    assert [c["new_expression"] for c in result["changed"]] == ["1.5 mm"]
    m.fails(h.timeline_edit_chamfer, {"feature": "ch-1"}, ErrorCode.INVALID_PARAMS)


# --- 6.10 hole -----------------------------------------------------------------------------------


def test_edit_hole() -> None:
    m = Model()
    dia = m.params.add_model("h1", "5 mm")
    depth = m.params.add_model("h2", "10 mm")
    tip = m.params.add_model("h3", "118 deg", "deg")
    m.feature(
        "Hole1",
        "adsk::fusion::HoleFeature",
        "hole-1",
        holeDiameter=dia,
        extentDefinition=FakeEntity("adsk::fusion::DistanceExtentDefinition", distance=depth),
        tipAngle=tip,
        counterboreDiameter=None,
        counterboreDepth=None,
        countersinkDiameter=None,
        countersinkAngle=None,
    )
    result = m.run(
        h.timeline_edit_hole, {"feature": "hole-1", "diameter": 6.6, "depth": 12, "tip_angle": 90}
    )
    assert [c["new_expression"] for c in result["changed"]] == ["6.6 mm", "12 mm", "90 deg"]
    error = m.fails(
        h.timeline_edit_hole,
        {"feature": "hole-1", "counterbore_diameter": 11},
        ErrorCode.INVALID_PARAMS,
    )
    assert error.details["available_keys"] == ["diameter", "depth", "tip_angle"]


# --- 6.12 shell ----------------------------------------------------------------------------------


def test_edit_shell() -> None:
    m = Model()
    t1 = m.params.add_model("t1", "1 mm")
    m.feature(
        "Shell1", "adsk::fusion::ShellFeature", "sh-1", insideThickness=t1, outsideThickness=None
    )
    m.run(h.timeline_edit_shell, {"feature": "sh-1", "inside_thickness": 2})
    assert t1.expression == "2 mm"
    m.fails(
        h.timeline_edit_shell, {"feature": "sh-1", "outside_thickness": 1}, ErrorCode.INVALID_PARAMS
    )


# --- 7.1 create_group ----------------------------------------------------------------------------


def test_create_group() -> None:
    m = Model()
    result = m.run(h.timeline_create_group, {"first": "sk-1", "last": "ext-1", "name": "Base"})
    assert result["group"]["ref"] == "timeline:Base"
    assert [(i["name"], i["group"]) for i in result["members"]] == [
        ("Sketch1", "timeline:Base"),
        ("Extrude1", "timeline:Base"),
    ]
    listed = m.run(h.timeline_list)["items"]
    assert [i["group"] for i in listed] == [None, "timeline:Base", "timeline:Base", None]


def test_create_group_rejections() -> None:
    m = Model()
    m.fails(h.timeline_create_group, {"first": "ext-1", "last": "sk-1"}, ErrorCode.INVALID_PARAMS)
    m.fails(
        h.timeline_create_group,
        {"first": "sk-1", "last": "ext-1", "name": "Fillet1"},
        ErrorCode.INVALID_PARAMS,
    )
    m.run(h.timeline_create_group, {"first": "ext-1", "last": "ext-1", "name": "G"})
    m.fails(h.timeline_create_group, {"first": "sk-1", "last": "fil-1"}, ErrorCode.INVALID_PARAMS)
    m.fails(h.timeline_create_group, {"first": "ext-1", "last": "fil-1"}, ErrorCode.INVALID_PARAMS)
    m.timeline.timelineGroups.fail_add = True
    m.fails(
        h.timeline_create_group, {"first": "fil-1", "last": "fil-1"}, ErrorCode.FUSION_API_ERROR
    )


def test_create_group_failure_removes_the_group() -> None:
    m = Model()

    real_add = m.timeline.timelineGroups.add

    def add(start: int, end: int) -> Any:
        group = real_add(start, end)
        original = type(group)

        class Locked(original):  # type: ignore[valid-type, misc]
            def __setattr__(self, key: str, value: Any) -> None:
                if key == "name" and value == "Base":
                    raise RuntimeError("name refused")
                super().__setattr__(key, value)

        group.__class__ = Locked
        return group

    m.timeline.timelineGroups.add = add  # type: ignore[assignment, method-assign]
    m.fails(
        h.timeline_create_group,
        {"first": "sk-1", "last": "ext-1", "name": "Base"},
        ErrorCode.FUSION_API_ERROR,
    )
    assert m.timeline.names() == ["Sketch1", "Extrude1", "Fillet1"]
    assert m.sketch.parentGroup is None


# --- 7.3 collapse / 7.5 ungroup ------------------------------------------------------------------


def test_set_group_collapsed() -> None:
    m = Model()
    m.timeline.timelineGroups.add(0, 1)
    result = m.run(h.timeline_set_group_collapsed, {"group": "timeline:Group1", "collapsed": True})
    assert result["group"]["is_collapsed"] is True
    error = m.fails(
        h.timeline_set_group_collapsed,
        {"group": "ext-1", "collapsed": True},
        ErrorCode.INVALID_PARAMS,
    )
    assert "not a timeline group" in error.message


def test_ungroup_keeps_members() -> None:
    m = Model()
    m.timeline.timelineGroups.add(0, 1)
    result = m.run(h.timeline_ungroup, {"group": "timeline:Group1"})
    assert [(i["name"], i["group"]) for i in result["members"]] == [
        ("Sketch1", None),
        ("Extrude1", None),
    ]
    assert m.timeline.names() == ["Sketch1", "Extrude1", "Fillet1"]
    assert m.timeline.group_deletions == [("Group1", False)]
    m.fails(h.timeline_ungroup, {"group": "sk-1"}, ErrorCode.INVALID_PARAMS)

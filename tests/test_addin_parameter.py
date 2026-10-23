"""``parameter.*`` handlers on fake parameters (c04 D6, D7, tasks 4.1-4.4)."""

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import parameter as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.design import FakeEntity
from tests.fakes.documents import FakeDocsApp
from tests.fakes.parameters import FakeModelParameters, FakeParameterSet
from tests.fakes.timeline import FakeTimeline

adsk = fusion_addin.adsk
STATES = adsk.fusion.FeatureHealthStates


class Model:
    """A design with an extrude (model parameter ``d1``) and a fillet on the timeline."""

    def __init__(self) -> None:
        self.app = FakeDocsApp()
        self.design: Any = self.app.open_document("Part").design
        self.params: FakeParameterSet = self.design.parameter_set
        self.design._timeline = FakeTimeline(["Extrude1", "Fillet1"])
        self.extrude_item, self.fillet_item = (self.design.timeline.item(i) for i in (0, 1))
        self.extrude = FakeEntity("adsk::fusion::ExtrudeFeature", "Extrude1", token="ext-1")
        self.root = self.design.rootComponent
        self.d1 = self.params.add_model(
            "d1", "10 mm", created_by=self.extrude, role="AlongDistance", component=self.root
        )

    def user(self, name: str, expression: str, unit: str = "mm", comment: str = "") -> Any:
        return self.params.add_user(name, expression, unit, comment)

    def run(self, fn: Any, params: dict[str, Any]) -> Any:
        return fn(Context(self.app, params), params)

    def fails(self, fn: Any, params: dict[str, Any], code: ErrorCode) -> BridgeError:
        with pytest.raises(BridgeError) as info:
            self.run(fn, params)
        assert info.value.code is code, info.value
        return info.value

    def fillet_fails_when(self, predicate: Any, warn: Any = None) -> None:
        """Drive the fillet's health from parameter values, like a recompute would."""

        def recompute(params: FakeParameterSet) -> None:
            state = STATES.HealthyFeatureHealthState
            if predicate(params):
                state = STATES.ErrorFeatureHealthState
            elif warn is not None and warn(params):
                state = STATES.WarningFeatureHealthState
            self.fillet_item.healthState = state
            self.fillet_item.errorOrWarningMessage = "fillet radius too large"

        self.params.on_change = recompute

    def snapshot(self) -> list[tuple[str, str, str, bool]]:
        return [(p.name, p.expression, p.comment, p.isFavorite) for p in self.params.params]


def test_registration() -> None:
    assert REGISTRY.get("parameter.list").mutating is False  # type: ignore[union-attr]
    for name in ("parameter.create", "parameter.update", "parameter.delete"):
        assert REGISTRY.get(name).mutating is True  # type: ignore[union-attr]


# --- 4.1 list ----------------------------------------------------------------------------------


def test_list_user_length_parameter() -> None:
    m = Model()
    m.user("width", "40 mm", comment="outer")
    (width,) = m.run(h.parameter_list, {"kind": "user"})["parameters"]
    assert width == {
        "ref": "param:width",
        "name": "width",
        "kind": "user",
        "expression": "40 mm",
        "unit": "mm",
        "value": pytest.approx(40.0),
        "value_unit": "mm",
        "display": "40 mm",
        "comment": "outer",
        "is_favorite": False,
        "created_by": None,
        "role": None,
        "component": None,
    }


def test_list_model_parameter_of_feature() -> None:
    m = Model()
    (d1,) = m.run(h.parameter_list, {"kind": "model"})["parameters"]
    assert d1["name"] == "d1" and d1["kind"] == "model"
    assert d1["created_by"] == {"ref": "ext-1", "type": "ExtrudeFeature", "name": "Extrude1"}
    assert d1["role"] == "AlongDistance"
    assert d1["component"] == {"ref": "component:root", "type": "Component", "name": "root"}
    assert d1["value"] == pytest.approx(10.0)


def test_list_angle_unitless_and_other_units() -> None:
    m = Model()
    m.user("tilt", "30 deg", "deg")
    m.user("count", "4", "")
    m.user("mass", "2", "kg")
    m.design.unitsManager.unformattable.add("kg")
    out = {p["name"]: p for p in m.run(h.parameter_list, {"kind": "user"})["parameters"]}
    assert out["tilt"]["value"] == pytest.approx(30.0)
    assert out["tilt"]["value_unit"] == "deg"
    assert (out["count"]["value"], out["count"]["value_unit"]) == (4.0, "")
    assert "value" not in out["mass"] and "value_unit" not in out["mass"]
    assert out["mass"]["display"] == "2"  # formatting failed: falls back to the expression


def test_list_filters() -> None:
    m = Model()
    m.user("width", "40 mm")
    m.user("half_width", "width / 2")
    names = [p["name"] for p in m.run(h.parameter_list, {})["parameters"]]
    assert names == ["width", "half_width", "d1"]
    names = [p["name"] for p in m.run(h.parameter_list, {"name_contains": "WIDTH"})["parameters"]]
    assert names == ["width", "half_width"]
    sub: Any = FakeEntity("adsk::fusion::Component", "sub")
    m.design.allComponents.add(sub)
    sub.modelParameters = FakeModelParameters(m.params, sub)
    m.params.add_model("d2", "5 mm", component=sub)
    out = m.run(h.parameter_list, {"kind": "model", "component": "component:sub"})
    assert [p["name"] for p in out["parameters"]] == ["d2"]
    out = m.run(h.parameter_list, {"kind": "model"})
    assert [p["name"] for p in out["parameters"]] == ["d1", "d2"]


def test_list_dependents() -> None:
    m = Model()
    m.user("width", "40 mm")
    m.user("half", "width / 2")
    out = m.run(h.parameter_list, {"kind": "user", "include_dependents": True})["parameters"]
    assert [p["dependents"] for p in out] == [["half"], []]
    assert "dependents" not in m.run(h.parameter_list, {})["parameters"][0]


def test_list_invalid_arguments() -> None:
    m = Model()
    m.fails(h.parameter_list, {"kind": "both"}, ErrorCode.INVALID_PARAMS)
    m.fails(h.parameter_list, {"component": "component:none"}, ErrorCode.ENTITY_NOT_FOUND)


# --- 4.2 create --------------------------------------------------------------------------------


def test_create_dependent_parameters() -> None:
    m = Model()
    out = m.run(
        h.parameter_create,
        {
            "parameters": [
                {"name": "width", "expression": 40},
                {"name": "half", "expression": "width / 2", "comment": "derived"},
            ]
        },
    )
    assert m.design.userParameters.added == [
        ("width", "40 mm", "mm", ""),
        ("half", "width / 2", "mm", "derived"),
    ]
    width, half = out["parameters"]
    assert width["value"] == pytest.approx(40.0)
    assert half["value"] == pytest.approx(20.0)
    assert out["warnings"] == []


def test_create_number_in_given_unit() -> None:
    m = Model()
    out = m.run(
        h.parameter_create,
        {
            "parameters": [
                {"name": "w", "expression": 1.5, "unit": "in"},
                {"name": "a", "expression": 45, "unit": "deg"},
                {"name": "n", "expression": 3, "unit": ""},
            ]
        },
    )
    assert [e for _, e, _, _ in m.design.userParameters.added] == ["1.5 in", "45 deg", "3"]
    w, a, n = out["parameters"]
    assert w["value"] == pytest.approx(38.1)
    assert a["value"] == pytest.approx(45.0)
    assert n["value"] == 3.0


@pytest.mark.parametrize(
    "items",
    [
        [{"name": "d1", "expression": 1}],  # exists (a model parameter)
        [{"name": "a", "expression": 1}, {"name": "a", "expression": 2}],
        [{"name": "1bad", "expression": 1}],
        [{"name": "ok", "expression": 1}, {"name": "has space", "expression": 1}],
        [{"name": "ok", "expression": True}],
        [{"name": "ok", "expression": math.nan}],
        [{"name": "ok", "expression": " "}],
        [{"name": "ok"}],
        [{"name": "ok", "expression": 1, "unit": 5}],
        ["ok"],
    ],
)
def test_create_prevalidation_creates_nothing(items: list[Any]) -> None:
    m = Model()
    m.user("width", "40 mm")
    m.fails(h.parameter_create, {"parameters": items}, ErrorCode.INVALID_PARAMS)
    assert [p.name for p in m.params.params] == ["d1", "width"]
    assert m.design.userParameters.added == []


def test_create_batch_size() -> None:
    m = Model()
    m.fails(h.parameter_create, {"parameters": []}, ErrorCode.INVALID_PARAMS)
    many = [{"name": f"p{i}", "expression": 1} for i in range(101)]
    m.fails(h.parameter_create, {"parameters": many}, ErrorCode.INVALID_PARAMS)
    m.fails(h.parameter_create, {}, ErrorCode.INVALID_PARAMS)


def test_create_failure_mid_batch_rolls_back() -> None:
    m = Model()
    error = m.fails(
        h.parameter_create,
        {
            "parameters": [
                {"name": "first", "expression": 10},
                {"name": "second", "expression": "nope * 2"},
                {"name": "third", "expression": 30},
            ]
        },
        ErrorCode.FUSION_API_ERROR,
    )
    assert error.details["item"] == "parameters[1]"
    assert error.details["name"] == "second"
    assert "rollback_error" not in error.details
    assert [p.name for p in m.params.params] == ["d1"]


def test_create_reports_new_warnings() -> None:
    m = Model()
    m.fillet_fails_when(lambda p: False, warn=lambda p: p.get("width") is not None)
    out = m.run(h.parameter_create, {"parameters": [{"name": "width", "expression": 40}]})
    assert out["warnings"] == [
        {"ref": "timeline:Fillet1", "name": "Fillet1", "message": "fillet radius too large"}
    ]


# --- 4.3 update --------------------------------------------------------------------------------


def test_update_model_parameter_drives_feature() -> None:
    m = Model()
    m.user("width", "40 mm")
    out = m.run(h.parameter_update, {"parameters": [{"name": "d1", "expression": "width * 2"}]})
    (d1,) = out["parameters"]
    assert d1["expression"] == "width * 2"
    assert d1["value"] == pytest.approx(80.0)
    assert m.d1.expression == "width * 2"


def test_update_every_field_of_user_parameter() -> None:
    m = Model()
    m.user("width", "40 mm", comment="old")
    m.user("half", "width / 2")
    out = m.run(
        h.parameter_update,
        {
            "parameters": [
                {
                    "name": "width",
                    "expression": 25,
                    "new_name": "w",
                    "comment": "new",
                    "is_favorite": True,
                }
            ]
        },
    )
    (w,) = out["parameters"]
    assert (w["name"], w["expression"], w["comment"], w["is_favorite"]) == (
        "w",
        "25 mm",
        "new",
        True,
    )
    assert m.params["half"].expression == "w / 2"


def test_update_number_uses_mm_degrees_or_own_unit() -> None:
    m = Model()
    m.user("w", "1 in", "in")
    m.user("a", "10 deg", "deg")
    m.user("n", "2", "")
    m.run(
        h.parameter_update,
        {
            "parameters": [
                {"name": "w", "expression": 25.4},
                {"name": "a", "expression": 90},
                {"name": "n", "expression": 5},
            ]
        },
    )
    assert [m.params[n].expression for n in ("w", "a", "n")] == ["25.4 mm", "90 deg", "5"]


@pytest.mark.parametrize(
    ("items", "code"),
    [
        ([{"name": "width"}], ErrorCode.INVALID_PARAMS),
        ([{"name": "nope", "comment": "x"}], ErrorCode.ENTITY_NOT_FOUND),
        ([{"name": "width", "new_name": "d1"}], ErrorCode.INVALID_PARAMS),
        ([{"name": "width", "new_name": "2x"}], ErrorCode.INVALID_PARAMS),
        ([{"name": "width", "is_favorite": "yes"}], ErrorCode.INVALID_PARAMS),
        ([{"name": "width", "comment": 3}], ErrorCode.INVALID_PARAMS),
        (
            [{"name": "width", "comment": "a"}, {"name": "width", "comment": "b"}],
            ErrorCode.INVALID_PARAMS,
        ),
        (
            [{"name": "width", "new_name": "x"}, {"name": "d1", "new_name": "x"}],
            ErrorCode.INVALID_PARAMS,
        ),
    ],
)
def test_update_prevalidation_changes_nothing(items: list[Any], code: ErrorCode) -> None:
    m = Model()
    m.user("width", "40 mm")
    before = m.snapshot()
    m.fails(h.parameter_update, {"parameters": items}, code)
    assert m.snapshot() == before


def test_update_change_breaks_feature_rolls_back() -> None:
    m = Model()
    m.user("width", "40 mm")
    m.fillet_fails_when(lambda p: p["d1"].value > 5.0)
    before = m.snapshot()
    error = m.fails(
        h.parameter_update,
        {
            "parameters": [
                {"name": "width", "expression": "20 mm", "comment": "c"},
                {"name": "d1", "expression": "width * 4"},
            ]
        },
        ErrorCode.FUSION_API_ERROR,
    )
    assert [f["name"] for f in error.details["failed_features"]] == ["Fillet1"]
    assert m.snapshot() == before
    assert m.fillet_item.healthState == STATES.HealthyFeatureHealthState


def test_update_ignores_feature_already_in_error() -> None:
    m = Model()
    m.user("width", "40 mm")
    m.fillet_item.healthState = STATES.ErrorFeatureHealthState
    out = m.run(h.parameter_update, {"parameters": [{"name": "width", "expression": "41 mm"}]})
    assert out["parameters"][0]["expression"] == "41 mm"


def test_update_fusion_error_mid_batch_rolls_back() -> None:
    m = Model()
    m.user("width", "40 mm")
    before = m.snapshot()
    error = m.fails(
        h.parameter_update,
        {
            "parameters": [
                {"name": "width", "expression": "50 mm"},
                {"name": "d1", "expression": "unknown_thing"},
            ]
        },
        ErrorCode.FUSION_API_ERROR,
    )
    assert error.details["item"] == "parameters[1]"
    assert m.snapshot() == before


# --- 4.4 delete --------------------------------------------------------------------------------


def test_delete_user_parameters() -> None:
    m = Model()
    m.user("width", "40 mm")
    m.user("spare", "1 mm")
    assert m.run(h.parameter_delete, {"names": ["spare"]}) == {"deleted": ["spare"], "warnings": []}
    assert [p.name for p in m.params.params] == ["d1", "width"]


def test_delete_referenced_parameter_lists_dependents() -> None:
    m = Model()
    m.user("width", "40 mm")
    m.user("half", "width / 2")
    error = m.fails(h.parameter_delete, {"names": ["width"]}, ErrorCode.INVALID_PARAMS)
    assert error.details["dependents"] == ["half"]
    assert m.params.get("width") is not None


def test_delete_parameter_with_its_dependents_in_one_batch() -> None:
    m = Model()
    m.user("width", "40 mm")
    m.user("half", "width / 2")
    out = m.run(h.parameter_delete, {"names": ["width", "half"]})
    assert out["deleted"] == ["width", "half"]
    assert [p.name for p in m.params.params] == ["d1"]


def test_delete_parameter_used_by_features() -> None:
    m = Model()
    m.user("width", "40 mm").used_by_features = True
    error = m.fails(h.parameter_delete, {"names": ["width"]}, ErrorCode.INVALID_PARAMS)
    assert error.details["dependents"] == []
    assert "features" in error.message


def test_delete_model_and_unknown() -> None:
    m = Model()
    m.fails(h.parameter_delete, {"names": ["d1"]}, ErrorCode.INVALID_PARAMS)
    m.fails(h.parameter_delete, {"names": ["nope"]}, ErrorCode.ENTITY_NOT_FOUND)
    m.fails(h.parameter_delete, {"names": []}, ErrorCode.INVALID_PARAMS)
    m.user("a", "1 mm")
    m.fails(h.parameter_delete, {"names": ["a", "a"]}, ErrorCode.INVALID_PARAMS)
    assert m.params.get("a") is not None


def test_delete_is_atomic() -> None:
    m = Model()
    m.user("first", "1 mm", comment="keep me")
    second = m.user("second", "2 mm")

    def refuse() -> bool:
        raise RuntimeError("Fusion refused")

    second.deleteMe = refuse
    before = m.snapshot()
    error = m.fails(h.parameter_delete, {"names": ["first", "second"]}, ErrorCode.FUSION_API_ERROR)
    assert error.details["name"] == "second"
    assert sorted(m.snapshot()) == sorted(before)
    assert m.params["first"].comment == "keep me"

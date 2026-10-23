"""``fusion_addin.core.health`` (c03 2.6, D14)."""

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.health import check_health, entity_health, health_of, timeline_errors
from fusion_addin.core.registry import Registry
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.brep import FakeFeature
from tests.fakes.design import FakeApp, FakeDesign
from tests.fakes.timeline import FakeTimeline

HS = fusion_addin.adsk.fusion.FeatureHealthStates


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (HS.HealthyFeatureHealthState, "ok"),
        (HS.WarningFeatureHealthState, "warning"),
        (HS.ErrorFeatureHealthState, "error"),
        (HS.SuppressedFeatureHealthState, "unknown"),
        (HS.RolledBackFeatureHealthState, "unknown"),
        (HS.UnknownFeatureHealthState, "unknown"),
        (42, "unknown"),
    ],
)
def test_health_of_maps_every_state(state: int, expected: str) -> None:
    assert health_of(FakeFeature(health=state)) == expected


def test_health_of_without_attribute_or_raising() -> None:
    assert health_of(object()) == "unknown"

    class Raising:
        @property
        def healthState(self) -> int:
            raise RuntimeError("not available")

    assert health_of(Raising()) == "unknown"


def test_entity_health() -> None:
    assert entity_health(FakeFeature(health=1, message="careful")) == {
        "state": "warning",
        "message": "careful",
    }
    assert entity_health(object()) == {"state": "unknown", "message": ""}


def test_check_health_error_raises() -> None:
    feature = FakeFeature("FilletFeature", health=HS.ErrorFeatureHealthState, message="no edge")
    with pytest.raises(BridgeError) as info:
        check_health(feature)
    error = info.value
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.message == "FilletFeature failed: no edge"
    assert error.details == {
        "health": {"state": "error", "message": "no edge"},
        "feature_type": "FilletFeature",
    }


def test_check_health_warning_and_ok_pass() -> None:
    assert check_health(FakeFeature(health=HS.WarningFeatureHealthState, message="w")) == {
        "state": "warning",
        "message": "w",
    }
    assert check_health(FakeFeature(health=HS.HealthyFeatureHealthState))["state"] == "ok"


def test_timeline_errors() -> None:
    timeline = FakeTimeline(["Sketch1", "Extrude1", "Fillet1"])
    timeline.item(0).healthState = HS.HealthyFeatureHealthState  # type: ignore[attr-defined]
    bad = timeline.item(2)
    bad.healthState = HS.ErrorFeatureHealthState  # type: ignore[attr-defined]
    bad.errorOrWarningMessage = "radius too big"  # type: ignore[attr-defined]
    bad.entity.entityToken = "tok-fillet"  # type: ignore[union-attr]
    design = FakeDesign(timeline=timeline)
    assert timeline_errors(design) == [
        {"ref": "tok-fillet", "name": "Fillet1", "message": "radius too big"}
    ]


def test_timeline_errors_without_entity_ref_and_direct_design() -> None:
    timeline = FakeTimeline(["Group1"])
    timeline.item(0).healthState = HS.ErrorFeatureHealthState  # type: ignore[attr-defined]
    timeline.item(0).entity = None
    assert timeline_errors(FakeDesign(timeline=timeline))[0]["ref"] == "timeline:Group1"
    assert timeline_errors(FakeDesign(parametric=False)) == []


def test_failed_feature_in_mutating_handler_is_rolled_back() -> None:
    timeline = FakeTimeline(["Sketch1"])
    design = FakeDesign(timeline=timeline)
    registry = Registry()

    def make_fillet(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        ctx.design.timeline.add("Fillet1")
        feature = FakeFeature("FilletFeature", health=HS.ErrorFeatureHealthState, message="bad")
        check_health(feature)
        return {"never": True}

    registry.register("test.fillet", make_fillet, mutating=True)
    dispatcher = Dispatcher(
        "tok", registry, context_factory=lambda params: Context(FakeApp(design), params)
    )
    response = dispatcher.dispatch(Request("1", "tok", "test.fillet", {}))
    assert response.ok is False
    assert response.error is not None
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert response.error.details["feature_type"] == "FilletFeature"
    assert timeline.names() == ["Sketch1"]
    assert timeline.markerPosition == 1

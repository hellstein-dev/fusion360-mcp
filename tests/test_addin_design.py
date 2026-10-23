"""``design.*`` handlers: settings, display units and design type (c04 D4, tasks 3.1-3.2)."""

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import design as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.documents import FakeDocsApp
from tests.fakes.timeline import FakeTimeline

adsk = fusion_addin.adsk
TOKEN = "t" * 32


def setup(**kwargs: Any) -> tuple[FakeDocsApp, Any]:
    app = FakeDocsApp()
    doc = app.open_document("Bracket", file_id="urn:1", **kwargs)
    return app, doc.design


def run(fn: Any, app: FakeDocsApp, params: dict[str, Any] | None = None) -> Any:
    params = params or {}
    return fn(Context(app, params), params)


def test_registration() -> None:
    assert REGISTRY.get("design.get_settings").mutating is False  # type: ignore[union-attr]
    assert REGISTRY.get("design.set_units").mutating is False  # type: ignore[union-attr]
    assert REGISTRY.get("design.set_type").mutating is True  # type: ignore[union-attr]


# --- 3.1 settings / units ----------------------------------------------------------------------


def test_get_settings() -> None:
    app, design = setup()
    design._timeline = FakeTimeline(["Sketch1", "Extrude1"])
    design.userParameters.add("w", adsk.core.ValueInput.createByString("4 mm"), "mm", "")
    assert run(h.design_get_settings, app) == {
        "design_type": "parametric",
        "units": "mm",
        "document": {"ref": "document:file:urn:1", "type": "Document", "name": "Bracket"},
        "root_component": {"ref": "component:root", "type": "Component", "name": "root"},
        "parameter_count": 1,
        "timeline_count": 2,
    }


def test_get_settings_direct_has_no_timeline() -> None:
    app, design = setup()
    design.designType = adsk.fusion.DesignTypes.DirectDesignType
    design.parametric = False
    out = run(h.design_get_settings, app)
    assert out["design_type"] == "direct"
    assert out["timeline_count"] is None


def test_get_settings_without_design() -> None:
    with pytest.raises(BridgeError) as info:
        run(h.design_get_settings, FakeDocsApp())
    assert info.value.code is ErrorCode.NO_ACTIVE_DESIGN


def test_set_units_changes_display_only() -> None:
    app, design = setup()
    out = run(h.design_set_units, app, {"units": "in"})
    assert (
        design.fusionUnitsManager.distanceDisplayUnits
        == adsk.fusion.DistanceUnits.InchDistanceUnits
    )
    assert out["units"] == "in"
    assert run(h.design_get_settings, app)["units"] == "in"
    # A later length input of 25 is still 25 mm: request units default to mm.
    later = Context(app, {})
    assert later.units.length(25) == pytest.approx(2.5)


def test_set_units_requires_valid_units() -> None:
    app, _ = setup()
    with pytest.raises(BridgeError) as info:
        run(h.design_set_units, app, {})
    assert info.value.code is ErrorCode.INVALID_PARAMS
    with pytest.raises(BridgeError):
        run(h.design_set_units, app, {"units": "yd"})


# --- 3.2 design type ---------------------------------------------------------------------------


def test_set_type_same_is_noop() -> None:
    app, design = setup()
    out = run(h.design_set_type, app, {"design_type": "parametric"})
    assert out["changed"] is False and out["history_discarded"] is False
    assert design.designType == adsk.fusion.DesignTypes.ParametricDesignType


def test_set_type_to_direct_needs_confirmation() -> None:
    app, design = setup()
    design._timeline = FakeTimeline(["Sketch1", "Extrude1"])
    with pytest.raises(BridgeError) as info:
        run(h.design_set_type, app, {"design_type": "direct"})
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["param"] == "confirm_discard_history"
    assert design.designType == adsk.fusion.DesignTypes.ParametricDesignType
    assert design.timeline.names() == ["Sketch1", "Extrude1"]


def test_set_type_to_direct_confirmed() -> None:
    app, design = setup()
    out = run(h.design_set_type, app, {"design_type": "direct", "confirm_discard_history": True})
    assert design.designType == adsk.fusion.DesignTypes.DirectDesignType
    assert out["changed"] is True and out["history_discarded"] is True
    assert out["design_type"] == "direct" and out["timeline_count"] is None


def test_set_type_to_parametric() -> None:
    app, design = setup()
    design.designType = adsk.fusion.DesignTypes.DirectDesignType
    out = run(h.design_set_type, app, {"design_type": "parametric"})
    assert design.designType == adsk.fusion.DesignTypes.ParametricDesignType
    assert out["changed"] is True and out["history_discarded"] is False


@pytest.mark.parametrize("params", [{}, {"design_type": "sheet"}, {"design_type": 1}])
def test_set_type_invalid(params: dict[str, Any]) -> None:
    app, _ = setup()
    with pytest.raises(BridgeError) as info:
        run(h.design_set_type, app, params)
    assert info.value.code is ErrorCode.INVALID_PARAMS


def test_set_type_guard_through_dispatcher_keeps_timeline() -> None:
    app, design = setup()
    design._timeline = FakeTimeline(["Sketch1"])
    dispatcher = Dispatcher(TOKEN, context_factory=lambda params: Context(app, params))
    response = dispatcher.dispatch(
        Request("1", TOKEN, "design.set_type", {"design_type": "direct"})
    )
    assert response.ok is False
    assert response.error is not None and response.error.code is ErrorCode.INVALID_PARAMS
    assert design.timeline.names() == ["Sketch1"]

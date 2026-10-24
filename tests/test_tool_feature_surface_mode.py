"""``is_solid`` of ``feature_extrude`` / ``revolve`` / ``sweep`` / ``loft`` on ``FakeBridge``
(c11 D12; tasks 4.1-4.4)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text

RESULT = {
    "feature": {"ref": "f", "type": "ExtrudeFeature", "name": "Extrude1"},
    "health": {"state": "ok", "message": ""},
    "created_bodies": [],
    "modified_bodies": [],
    "removed_bodies": [],
}
DIST = {"type": "distance", "distance": 10}
SECTIONS = [{"entity": "profile:a@0,0"}, {"entity": "profile:b@0,0"}]
CASES: dict[str, tuple[str, dict[str, Any]]] = {
    "feature_extrude": ("feature.extrude", {"profiles": ["line-1"], "side1": DIST}),
    "feature_revolve": ("feature.revolve", {"profiles": ["profile:a@0,0"], "axis": "origin:Z"}),
    "feature_sweep": ("feature.sweep", {"profiles": ["line-1"], "path": ["c1"]}),
    "feature_loft": ("feature.loft", {"sections": SECTIONS}),
}


def call(name: str, arguments: dict[str, Any], bridge: FakeBridge) -> Any:
    return asyncio.run(call_tool(create_server(bridge), name, arguments))


@pytest.mark.parametrize("name", sorted(CASES))
def test_is_solid_default_true_and_false_is_sent(name: str) -> None:
    method, arguments = CASES[name]
    bridge = FakeBridge({method: RESULT})
    result = call(name, arguments, bridge)
    assert result.is_error is False, result_text(result)
    assert bridge.last.method == method
    assert bridge.last.params["is_solid"] is True
    result = call(name, {**arguments, "is_solid": False, "operation": "new_component"}, bridge)
    assert result.is_error is False, result_text(result)
    assert bridge.last.params["is_solid"] is False
    assert bridge.last.params["operation"] == "new_component"


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize("operation", ["join", "cut", "intersect"])
def test_surface_with_boolean_operation_fails_without_bridge(name: str, operation: str) -> None:
    _, arguments = CASES[name]
    bridge = FakeBridge()
    result = call(name, {**arguments, "is_solid": False, "operation": operation}, bridge)
    assert bridge.calls == []
    text = result_text(result)
    assert result.is_error is True and text.startswith("INVALID_PARAMS:"), text
    assert '"param": "operation"' in text


def test_surface_extrude_with_thin_fails_without_bridge() -> None:
    bridge = FakeBridge()
    result = call(
        "feature_extrude",
        {"profiles": ["line-1"], "side1": DIST, "is_solid": False, "thin": {"thickness": 1}},
        bridge,
    )
    assert bridge.calls == []
    assert result.is_error is True and '"param": "thin"' in result_text(result)


@pytest.mark.parametrize("name", sorted(CASES))
def test_docstring_mentions_surface_mode(name: str) -> None:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    tool = next(t for t in tools if t.name == name)
    assert "is_solid=false" in (tool.description or "")
    assert "is_solid" in tool.input_schema["properties"]

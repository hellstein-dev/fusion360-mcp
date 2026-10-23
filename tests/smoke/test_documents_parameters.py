"""Smoke test of documents, design settings and parameters on a live Fusion 360
(``pytest -m fusion``, c04 6.1).

A new design document gets two user parameters (``width = 40 mm``, ``half = width / 2``),
which are listed, updated and deleted (with the dependency guard); the document is closed
without saving and the previously active document must be active again. Nothing is ever
saved to the cloud. Skipped when the bridge is not reachable.
"""

import asyncio
import json
from typing import Any

import pytest
from mcp.types import CallToolResult

from fusion_mcp.server.app import create_server
from fusion_mcp.server.bridge import BridgeClient
from fusion_mcp.server.testing import result_text

pytestmark = pytest.mark.fusion


def call_raw(name: str, arguments: dict[str, Any]) -> CallToolResult:
    assert name != "document_save", "the smoke test must never save to the cloud"

    async def go() -> CallToolResult:
        bridge = BridgeClient()
        try:
            result = await create_server(bridge).call_tool(name, arguments)
        finally:
            await bridge.close()
        assert isinstance(result, CallToolResult)
        return result

    return asyncio.run(go())


def call(name: str, arguments: dict[str, Any]) -> Any:
    result = call_raw(name, arguments)
    text = result_text(result)
    assert not result.is_error, text
    return json.loads(text)


def active_ref() -> str | None:
    documents = call("document_list", {})["documents"]
    return next((d["ref"] for d in documents if d["is_active"]), None)


def user_params() -> dict[str, dict[str, Any]]:
    listed = call("parameter_list", {"kind": "user", "include_dependents": True})
    return {p["name"]: p for p in listed["parameters"]}


def test_document_parameters_round_trip() -> None:
    probe = call_raw("fusion_status", {})
    if probe.is_error and result_text(probe).startswith("NOT_CONNECTED"):
        pytest.skip(f"Fusion bridge not reachable: {result_text(probe).splitlines()[0]}")
    original = active_ref()

    new = call("document_new", {})
    closed = False
    try:
        assert new["is_active"] is True
        assert (new["design_type"], new["units"], new["is_saved"]) == ("parametric", "mm", False)
        assert active_ref() == new["ref"]

        settings = call("design_get_settings", {})
        assert settings["design_type"] == "parametric"
        assert settings["units"] == "mm"
        assert settings["document"]["ref"] == new["ref"]
        assert settings["timeline_count"] == 0

        created = call(
            "parameter_create",
            {
                "parameters": [
                    {"name": "width", "expression": 40},
                    {"name": "half", "expression": "width / 2"},
                ]
            },
        )
        width, half = created["parameters"]
        assert width["expression"] == "40 mm"
        assert width["value"] == pytest.approx(40.0)
        assert half["value"] == pytest.approx(20.0)
        assert half["value_unit"] == "mm"

        params = user_params()
        assert set(params) == {"width", "half"}
        assert params["width"]["dependents"] == ["half"]
        assert "40" in params["width"]["display"]

        call("parameter_update", {"parameters": [{"name": "width", "expression": "50 mm"}]})
        assert user_params()["half"]["value"] == pytest.approx(25.0)

        guarded = call_raw("parameter_delete", {"names": ["width"]})
        assert guarded.is_error
        assert result_text(guarded).startswith("INVALID_PARAMS")
        assert "half" in result_text(guarded)
        assert set(user_params()) == {"width", "half"}

        deleted = call("parameter_delete", {"names": ["width", "half"]})
        assert sorted(deleted["deleted"]) == ["half", "width"]
        assert user_params() == {}

        out = call("document_close", {"discard_changes": True})
        closed = True
        assert out["closed"]["ref"] == new["ref"]
        if original is not None:
            assert out["active"] is not None and out["active"]["ref"] == original
            assert active_ref() == original
    finally:
        if not closed and active_ref() == new["ref"]:
            call("document_close", {"discard_changes": True})

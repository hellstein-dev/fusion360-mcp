"""``server.app`` and ``server.testing``: server creation, registry, error mapping (4.2)."""

import asyncio
import json

import pytest

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server import app
from fusion_mcp.server.bridge import BridgeClient
from fusion_mcp.server.testing import FakeBridge, result_text


def test_create_server_registers_tools() -> None:
    mcp = app.create_server(FakeBridge())
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}
    assert "fusion_status" in names


def test_create_server_defaults_to_bridge_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []
    monkeypatch.setattr(app, "register_all", lambda mcp, bridge: seen.append(bridge))
    app.create_server()
    assert isinstance(seen[0], BridgeClient)


def test_ok_result_has_json_text_and_structured_content() -> None:
    result = asyncio.run(app.call_bridge(FakeBridge({"x.y": {"a": 1}}), "x.y", {}))
    assert result.is_error is False
    assert json.loads(result_text(result)) == {"a": 1}
    assert result.structured_content == {"a": 1}


def test_non_dict_result_is_text_only() -> None:
    result = asyncio.run(app.call_bridge(FakeBridge({"x.y": [1, 2]}), "x.y"))
    assert json.loads(result_text(result)) == [1, 2]
    assert result.structured_content is None


def test_bridge_error_becomes_is_error_result() -> None:
    error = BridgeError(ErrorCode.FUSION_API_ERROR, "3 : bad profile", {"traceback": "tb"})
    result = asyncio.run(app.call_bridge(FakeBridge({"x.y": error}), "x.y"))
    assert result.is_error is True
    text = result_text(result)
    assert text.startswith("FUSION_API_ERROR: 3 : bad profile")
    assert json.loads(text.split("details: ", 1)[1]) == {"traceback": "tb"}


def test_error_without_details() -> None:
    result = app.error_result(BridgeError(ErrorCode.TIMEOUT, "slow"))
    assert result_text(result) == "TIMEOUT: slow"


def test_fake_bridge_records_and_answers() -> None:
    bridge = FakeBridge()
    bridge.respond("a.b", lambda params: {"echo": params})

    async def go():
        return await bridge.call("a.b", {"x": 1}, 3), await bridge.call("c.d")

    first, second = asyncio.run(go())
    assert first == {"echo": {"x": 1}}
    assert second == {}
    assert [(c.method, c.params, c.timeout_s) for c in bridge.calls] == [
        ("a.b", {"x": 1}, 3),
        ("c.d", {}, None),
    ]
    assert bridge.last.method == "c.d"

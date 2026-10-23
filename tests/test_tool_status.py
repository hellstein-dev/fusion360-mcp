"""``fusion_status`` on ``FakeBridge`` (4.3)."""

import asyncio
import json

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text

STATUS = {
    "fusion_version": "2.0.21000",
    "addin_version": "0.1.0",
    "python_version": "3.14.7",
    "document": "Housing",
    "design_type": "parametric",
    "units": "mm",
}


def test_fusion_status_calls_status_get() -> None:
    bridge = FakeBridge({"status.get": STATUS})
    result = asyncio.run(call_tool(create_server(bridge), "fusion_status", {}))
    assert result.is_error is False
    assert json.loads(result_text(result)) == STATUS
    assert bridge.last.method == "status.get"
    assert bridge.last.params == {}


def test_fusion_status_not_connected() -> None:
    bridge = FakeBridge({"status.get": BridgeError(ErrorCode.NOT_CONNECTED, "start Fusion")})
    result = asyncio.run(call_tool(create_server(bridge), "fusion_status", {}))
    assert result.is_error is True
    assert result_text(result).startswith("NOT_CONNECTED")


def test_fusion_status_schema_and_description() -> None:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    tool = next(t for t in tools if t.name == "fusion_status")
    assert tool.input_schema["type"] == "object"
    assert tool.description
    assert "NOT_CONNECTED" in tool.description

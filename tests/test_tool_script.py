"""``execute_python`` on ``FakeBridge`` (5.2)."""

import asyncio
import json

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text


def test_execute_python_sends_code_and_params() -> None:
    bridge = FakeBridge({"script.execute": {"result": 3, "stdout": "", "stderr": ""}})
    result = asyncio.run(
        call_tool(
            create_server(bridge),
            "execute_python",
            {"code": "result = root.bRepBodies.count", "params": {"a": 1}, "timeout_s": 30},
        )
    )
    assert result.is_error is False
    assert json.loads(result_text(result)) == {"result": 3, "stdout": "", "stderr": ""}
    assert bridge.last.method == "script.execute"
    assert bridge.last.params == {"code": "result = root.bRepBodies.count", "params": {"a": 1}}
    assert bridge.last.timeout_s == 30


def test_execute_python_defaults() -> None:
    bridge = FakeBridge()
    asyncio.run(call_tool(create_server(bridge), "execute_python", {"code": "pass"}))
    assert bridge.last.params == {"code": "pass", "params": {}}
    assert bridge.last.timeout_s == 60


def test_execute_python_error_is_visible() -> None:
    error = BridgeError(
        ErrorCode.FUSION_API_ERROR, "ZeroDivisionError: division by zero", {"traceback": "tb"}
    )
    bridge = FakeBridge({"script.execute": error})
    result = asyncio.run(call_tool(create_server(bridge), "execute_python", {"code": "1/0"}))
    assert result.is_error is True
    text = result_text(result)
    assert text.startswith("FUSION_API_ERROR: ZeroDivisionError")
    assert '"traceback": "tb"' in text


def test_execute_python_schema_and_docstring() -> None:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    tool = next(t for t in tools if t.name == "execute_python")
    schema = tool.input_schema
    assert schema["required"] == ["code"]
    assert set(schema["properties"]) == {"code", "params", "timeout_s"}
    description = tool.description or ""
    for needle in (
        "adsk",
        "app",
        "ui",
        "design",
        "root",
        "params",
        "CENTIMETRES",
        "RADIANS",
        "Example",
    ):
        assert needle in description

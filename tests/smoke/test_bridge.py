"""Smoke tests on a live Fusion 360 with the FusionMCP add-in running (``pytest -m fusion``).

They go through the real discovery file and socket. Skipped when the bridge is not
reachable, so ``-m fusion`` without Fusion reports skips, not failures.
"""

import asyncio
import json
from typing import Any

import pytest

from fusion_mcp.server.app import create_server
from fusion_mcp.server.bridge import BridgeClient
from fusion_mcp.server.testing import result_text

pytestmark = pytest.mark.fusion


def call(name: str, arguments: dict[str, Any]) -> tuple[bool, str]:
    async def go() -> tuple[bool, str]:
        bridge = BridgeClient()
        try:
            result = await create_server(bridge).call_tool(name, arguments)
        finally:
            await bridge.close()
        assert not isinstance(result, dict)
        return bool(getattr(result, "is_error", False)), result_text(result)  # type: ignore[arg-type]

    return asyncio.run(go())


@pytest.fixture(scope="module")
def status() -> dict[str, Any]:
    is_error, text = call("fusion_status", {})
    if is_error and text.startswith("NOT_CONNECTED"):
        pytest.skip(f"Fusion bridge not reachable: {text.splitlines()[0]}")
    assert not is_error, text
    return json.loads(text)


def test_fusion_status(status: dict[str, Any]) -> None:
    for key in (
        "fusion_version",
        "addin_version",
        "python_version",
        "document",
        "design_type",
        "units",
    ):
        assert key in status
    assert status["python_version"]


def test_execute_python_result(status: dict[str, Any]) -> None:
    if status["design_type"] is None:
        pytest.skip("no active design in Fusion")
    is_error, text = call("execute_python", {"code": "result = root.bRepBodies.count"})
    assert not is_error, text
    out = json.loads(text)
    assert isinstance(out["result"], int)
    assert out["stdout"] == ""
    assert out["stderr"] == ""


def test_execute_python_error_rolls_back(status: dict[str, Any]) -> None:
    if status["design_type"] != "parametric":
        pytest.skip("rollback needs a parametric design")
    count = "result = design.timeline.count"
    before = json.loads(call("execute_python", {"code": count})[1])["result"]
    is_error, text = call(
        "execute_python",
        {"code": "root.sketches.add(root.xYConstructionPlane)\nraise RuntimeError('smoke')"},
    )
    assert is_error
    assert text.startswith("FUSION_API_ERROR")
    assert "smoke" in text
    after = json.loads(call("execute_python", {"code": count})[1])["result"]
    assert after == before

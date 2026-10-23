"""Meta-test (4.4, INV-8): every registered MCP tool is called in some FakeBridge test.

Tool tests call tools through ``fusion_mcp.server.testing.call_tool``, which records the
name. ``conftest.py`` moves this test to the end of the session so the record is complete.
"""

import asyncio

import pytest

from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import EXERCISED_TOOLS, FakeBridge


def registered_tools() -> set[str]:
    return {tool.name for tool in asyncio.run(create_server(FakeBridge()).list_tools())}


def test_every_tool_is_exercised_on_fake_bridge(request: pytest.FixtureRequest) -> None:
    tool_tests = [i for i in request.session.items if i.path.name.startswith("test_tool_")]
    if not tool_tests:
        pytest.skip("tool tests are not part of this run")
    missing = registered_tools() - EXERCISED_TOOLS
    assert not missing, f"tools without a FakeBridge test: {sorted(missing)}"

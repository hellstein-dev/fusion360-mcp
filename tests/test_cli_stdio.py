"""``fusion-mcp`` without a subcommand serves MCP over stdio (4.5)."""

import json
import subprocess
import sys
from typing import Any

from mcp.types import LATEST_PROTOCOL_VERSION


def rpc(id: int | None, method: str, params: dict[str, Any] | None = None) -> str:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        message["id"] = id
    if params is not None:
        message["params"] = params
    return json.dumps(message) + "\n"


def test_initialize_and_list_tools_over_stdio() -> None:
    stdin = (
        rpc(
            1,
            "initialize",
            {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        )
        + rpc(None, "notifications/initialized")
        + rpc(2, "tools/list", {})
    )
    proc = subprocess.run(
        [sys.executable, "-m", "fusion_mcp"],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    replies = {
        msg["id"]: msg
        for msg in (json.loads(line) for line in proc.stdout.splitlines() if line.strip())
        if "id" in msg
    }
    assert 1 in replies, proc.stderr
    assert replies[1]["result"]["serverInfo"]["name"] == "fusion-mcp"
    tools = {tool["name"]: tool for tool in replies[2]["result"]["tools"]}
    assert {"fusion_status", "execute_python"} <= set(tools)
    for tool in tools.values():
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"]

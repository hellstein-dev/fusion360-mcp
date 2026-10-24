"""``fusion-mcp`` without a subcommand serves MCP over stdio (4.5)."""

import json
import subprocess
import sys
import threading
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
    # stdin stays open until the last reply arrives: closing it right away lets the server
    # shut down while tools/list is still in flight (the reply is then lost).
    proc = subprocess.Popen(
        [sys.executable, "-m", "fusion_mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    killer = threading.Timer(60, proc.kill)
    killer.start()
    replies: dict[int, Any] = {}
    try:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(stdin)
        proc.stdin.flush()
        for line in proc.stdout:
            if line.strip():
                msg = json.loads(line)
                if "id" in msg:
                    replies[msg["id"]] = msg
            if 2 in replies:
                break
        proc.stdin.close()
        _, stderr = proc.communicate(timeout=60)
    finally:
        killer.cancel()
        if proc.poll() is None:
            proc.kill()
    assert 1 in replies, stderr
    assert replies[1]["result"]["serverInfo"]["name"] == "fusion-mcp"
    tools = {tool["name"]: tool for tool in replies[2]["result"]["tools"]}
    assert {"fusion_status", "execute_python"} <= set(tools)
    for tool in tools.values():
        assert tool["inputSchema"]["type"] == "object"
        assert tool["description"]

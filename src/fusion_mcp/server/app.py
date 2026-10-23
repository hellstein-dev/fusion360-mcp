"""MCP server assembly (design D10): ``create_server`` and the bridge-error mapping (D8)."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from fusion_mcp import __version__
from fusion_mcp.protocol.errors import BridgeError
from fusion_mcp.server.bridge import Bridge, BridgeClient

SERVER_NAME = "fusion-mcp"
INSTRUCTIONS = (
    "Tools to build parametric models in Autodesk Fusion 360. Call fusion_status first to "
    "check the connection and the active document. Lengths are millimetres and angles "
    "degrees unless a tool says otherwise; strings are Fusion expressions. Entities are "
    "addressed by the 'ref' values tools return. A failed tool leaves the timeline as it was."
)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=repr)


def error_result(error: BridgeError) -> CallToolResult:
    """``isError`` result with ``"<code>: <message>"`` and the details as JSON."""
    text = f"{error.code.value}: {error.message}"
    if error.details:
        text += "\ndetails: " + _dumps(error.details)
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def ok_result(result: Any) -> CallToolResult:
    structured = result if isinstance(result, dict) else None
    return CallToolResult(
        content=[TextContent(type="text", text=_dumps(result))], structured_content=structured
    )


async def call_bridge(
    bridge: Bridge,
    method: str,
    params: dict[str, Any] | None = None,
    timeout_s: float | None = None,
) -> CallToolResult:
    """Call ``method`` on the add-in; bridge errors become tool results the agent can read."""
    try:
        result = await bridge.call(method, params or {}, timeout_s)
    except BridgeError as error:
        return error_result(error)
    return ok_result(result)


def register_all(mcp: MCPServer[Any], bridge: Bridge) -> None:
    from fusion_mcp.server.tools import TOOL_MODULES

    for module in TOOL_MODULES:
        module.register(mcp, bridge)


def create_server(bridge: Bridge | None = None) -> MCPServer[Any]:
    mcp: MCPServer[Any] = MCPServer(
        name=SERVER_NAME, instructions=INSTRUCTIONS, version=__version__
    )
    register_all(mcp, bridge if bridge is not None else BridgeClient())
    return mcp

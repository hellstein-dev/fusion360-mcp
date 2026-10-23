"""``FakeBridge`` and helpers for tool tests without Fusion (INV-8)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult

from fusion_mcp.protocol.errors import BridgeError

# Tool names called through ``call_tool`` during this test session (meta-test, task 4.4).
EXERCISED_TOOLS: set[str] = set()


@dataclass
class BridgeCall:
    method: str
    params: dict[str, Any]
    timeout_s: float | None = None


@dataclass
class FakeBridge:
    """Records calls and answers with canned results, ``BridgeError``s or callables."""

    responses: dict[str, Any] = field(default_factory=dict)
    calls: list[BridgeCall] = field(default_factory=list)

    def respond(self, method: str, result: Any) -> None:
        """``result``: a value, a ``BridgeError`` to raise, or ``fn(params) -> value``."""
        self.responses[method] = result

    async def call(
        self, method: str, params: dict[str, Any] | None = None, timeout_s: float | None = None
    ) -> Any:
        self.calls.append(BridgeCall(method, dict(params or {}), timeout_s))
        response = self.responses.get(method, {})
        if isinstance(response, BridgeError):
            raise response
        if callable(response):
            fn: Callable[[dict[str, Any]], Any] = response
            return fn(dict(params or {}))
        return response

    @property
    def last(self) -> BridgeCall:
        return self.calls[-1]


async def call_tool(mcp: MCPServer[Any], name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Call a tool the way a client would and record it as exercised."""
    EXERCISED_TOOLS.add(name)
    result = await mcp.call_tool(name, arguments)
    assert isinstance(result, CallToolResult)
    return result


def result_text(result: CallToolResult) -> str:
    return "\n".join(getattr(block, "text", "") for block in result.content)

"""MCP tool groups: each module has ``register(mcp, bridge)`` (design D10)."""

from . import status

TOOL_MODULES = [status]

__all__ = ["TOOL_MODULES", "status"]

"""MCP tool groups: each module has ``register(mcp, bridge)`` (design D10)."""

from . import script, status

TOOL_MODULES = [status, script]

__all__ = ["TOOL_MODULES", "script", "status"]

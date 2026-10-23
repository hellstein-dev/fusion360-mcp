"""MCP tool groups: each module has ``register(mcp, bridge)`` (design D10)."""

from . import inspect, measure, script, status, viewport

TOOL_MODULES = [status, script, inspect, measure, viewport]

__all__ = ["TOOL_MODULES", "inspect", "measure", "script", "status", "viewport"]

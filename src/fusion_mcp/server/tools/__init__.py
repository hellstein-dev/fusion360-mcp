"""MCP tool groups: each module has ``register(mcp, bridge)`` (design D10)."""

from . import design, document, inspect, measure, parameter, script, sketch, status, viewport

TOOL_MODULES = [status, script, document, design, parameter, inspect, measure, viewport, sketch]

__all__ = [
    "TOOL_MODULES",
    "design",
    "document",
    "inspect",
    "measure",
    "parameter",
    "script",
    "sketch",
    "status",
    "viewport",
]

"""MCP tool groups: each module has ``register(mcp, bridge)`` (design D10)."""

from . import (
    construction,
    design,
    document,
    inspect,
    measure,
    parameter,
    script,
    sketch,
    status,
    viewport,
)

TOOL_MODULES = [
    status,
    script,
    document,
    design,
    parameter,
    inspect,
    measure,
    viewport,
    sketch,
    construction,
]

__all__ = [
    "TOOL_MODULES",
    "construction",
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

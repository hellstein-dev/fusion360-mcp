"""MCP tool groups: each module has ``register(mcp, bridge)`` (design D10)."""

from . import (
    construction,
    design,
    document,
    feature_hole,
    feature_sketch,
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
    feature_sketch,
    feature_hole,
]

__all__ = [
    "TOOL_MODULES",
    "construction",
    "design",
    "document",
    "feature_hole",
    "feature_sketch",
    "inspect",
    "measure",
    "parameter",
    "script",
    "sketch",
    "status",
    "viewport",
]

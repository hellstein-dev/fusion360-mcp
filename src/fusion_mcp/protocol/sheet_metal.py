"""Vocabularies of the ``sheet_metal.*`` methods (c13 D2), shared by the MCP server and the
add-in. Stdlib only.

The server writes the tuples out as ``Literal[...]`` types (a test keeps them in sync); the
add-in re-checks raw params with ``fusion_mcp.protocol.features.check_choice`` before it maps
a value to a Fusion enum.
"""

from __future__ import annotations

RELIEF_SHAPES = ("straight", "tear", "round")
FLANGE_ORIENTATIONS = ("side_one", "side_two", "centered")
FLANGE_DIRECTIONS = ("one_side", "symmetric", "two_sides")
HEIGHT_DATUMS = ("inner", "outer", "tangent")
BEND_POSITIONS = ("start_edge", "inside", "outside", "tangent_to_side")
EDGE_WIDTH_TYPES = ("full", "symmetric", "two_sided", "two_offsets")
RULE_SOURCES = ("design", "library", "all")
FLAT_EXPORT_FORMATS = ("dxf", "step")
FLANGE_OPERATIONS = ("new_body", "new_component")

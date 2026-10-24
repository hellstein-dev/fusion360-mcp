"""String vocabularies of the ``surface.*`` methods and of the surface mode of c07 features
(c11 D2), shared by the MCP server and the add-in. Stdlib only.

The server turns each tuple into a ``Literal[...]`` (``server/tools/_surface_models.py``, a
test keeps both in sync); the add-in re-checks raw params with ``check_choice`` (c07) before it
maps a value to a Fusion enum (``fusion_addin/core/surfaces.py``).
"""

from __future__ import annotations

# Patch, ruled, offset and stitch create new bodies only (the API takes no join/cut there).
SURFACE_OPERATIONS = ("new_body", "new_component")
CONTINUITY = ("connected", "tangent", "curvature")
EXTEND_TYPES = ("natural", "tangent", "perpendicular")
EXTEND_ALIGNMENTS = ("free", "align")
UNTRIM_LOOP_TYPES = ("all", "external", "internal")
RULED_TYPES = ("normal", "tangent", "direction")
RULED_CORNERS = ("rounded", "mitered")
OFFSET_TYPES = ("sharp", "rounded")

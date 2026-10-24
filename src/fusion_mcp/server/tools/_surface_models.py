"""Input models of the ``surface_*`` tools (c11 D1-D2).

Builds on the shared feature models (``_feature_models.py``: ``Ref``, ``Length``, ``Angle``,
``Units``, ``to_params``). The string vocabularies are the tuples of
``fusion_mcp.protocol.surfaces`` written out as ``Literal`` (a test keeps them in sync).
"""

from __future__ import annotations

from typing import Literal

SurfaceOperation = Literal["new_body", "new_component"]
Continuity = Literal["connected", "tangent", "curvature"]
ExtendType = Literal["natural", "tangent", "perpendicular"]
ExtendAlignment = Literal["free", "align"]
UntrimLoopType = Literal["all", "external", "internal"]
RuledType = Literal["normal", "tangent", "direction"]
RuledCorner = Literal["rounded", "mitered"]
OffsetType = Literal["sharp", "rounded"]

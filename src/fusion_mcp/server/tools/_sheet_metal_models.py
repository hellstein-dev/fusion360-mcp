"""Input models of the ``sheet_metal_*`` tools (c13 D1-D2, D5).

Builds on the shared feature models (``_feature_models.py``: ``Length``). The string
vocabularies are the tuples of ``fusion_mcp.protocol.sheet_metal`` written out as ``Literal``
(a test keeps them in sync). ``EdgeFlangeWidth`` is the edge flange width, one for all edges.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from fusion_mcp.server.tools._feature_models import Length

ReliefShape = Literal["straight", "tear", "round"]
FlangeOrientation = Literal["side_one", "side_two", "centered"]
FlangeDirection = Literal["one_side", "symmetric", "two_sides"]
HeightDatum = Literal["inner", "outer", "tangent"]
BendPosition = Literal["start_edge", "inside", "outside", "tangent_to_side"]
EdgeWidthType = Literal["full", "symmetric", "two_sided", "two_offsets"]
RuleSource = Literal["design", "library", "all"]
FlatExportFormat = Literal["dxf", "step"]
FlangeOperation = Literal["new_body", "new_component"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FullWidth(_Model):
    """The flange runs along the whole edge."""

    type: Literal["full"]


class SymmetricWidth(_Model):
    """A flange of ``width`` centred on the edge midpoint."""

    type: Literal["symmetric"]
    width: Annotated[Length, Field(description="Total flange width")]


class TwoSidedWidth(_Model):
    """Widths to both sides of the edge midpoint."""

    type: Literal["two_sided"]
    width1: Annotated[Length, Field(description="Width to the side of the edge start")]
    width2: Annotated[Length, Field(description="Width to the side of the edge end")]


class TwoOffsetsWidth(_Model):
    """The flange is inset from both edge ends."""

    type: Literal["two_offsets"]
    offset1: Annotated[Length, Field(description="Inset from the edge start")]
    offset2: Annotated[Length, Field(description="Inset from the edge end")]


EdgeFlangeWidth = Annotated[
    FullWidth | SymmetricWidth | TwoSidedWidth | TwoOffsetsWidth,
    Field(discriminator="type"),
]

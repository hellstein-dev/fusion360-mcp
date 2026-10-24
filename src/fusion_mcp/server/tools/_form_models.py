"""Input models of the ``form_*`` tools (c14 D1, D2, D5).

The string vocabularies are the tuples of ``fusion_mcp.protocol.form`` written out as
``Literal`` (a test keeps them in sync). ``Symmetry`` is ``{"x", "y", "z": bool}`` or
``"circular"`` (cylinder, sphere, torus). Points are ``[x, y, z]`` numbers in ``units``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Shape = Literal["box", "plane", "cylinder", "sphere", "torus", "quadball"]
ElementKind = Literal["vertex", "edge", "face"]
InsertionMode = Literal["simple", "exact"]
MoveKind = Literal["vertex", "face", "body"]
InspectPart = Literal["vertices", "edges", "faces"]

Number = float | int
Point3 = Annotated[
    list[Number], Field(min_length=3, max_length=3, description="[x, y, z] in `units`")
]
Vector3 = Annotated[
    list[Number], Field(min_length=3, max_length=3, description="[dx, dy, dz] in `units`")
]
Direction = Annotated[
    list[Number], Field(min_length=3, max_length=3, description="[x, y, z] direction (unitless)")
]
FaceCount = Annotated[int, Field(ge=1, description="Number of faces (default: Fusion's)")]


class AxisSymmetry(BaseModel):
    """Mirror symmetry about the primitive's local X / Y / Z planes."""

    model_config = ConfigDict(extra="forbid")

    x: bool = False
    y: bool = False
    z: bool = False


Symmetry = AxisSymmetry | Literal["circular"]

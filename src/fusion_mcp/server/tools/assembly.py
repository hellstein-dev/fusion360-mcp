"""``assembly_*`` tools: components, occurrences, joints and interference (c10 D8).

Input models: ``JointGeometry`` (where a joint or joint origin snaps), ``Rotate`` and
``Placement`` (occurrence transforms, D3); ``JointType`` / ``Dof`` are the vocabularies of
the add-in (``protocol/assembly.py``, D5). Lengths are mm, angles degrees; refs of
entities inside an occurrence look like ``<token>@<occurrence path>``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field, model_validator

from fusion_mcp.server.bridge import Bridge
from fusion_mcp.server.tools._feature_models import Number, Ref, _Model

JointType = Literal["rigid", "revolute", "slider", "cylindrical", "pin_slot", "planar", "ball"]
Dof = Literal["rotation", "slide", "primary_slide", "secondary_slide", "pitch", "yaw", "roll"]
JointGeometryKind = Literal[
    "point", "planar_face", "nonplanar_face", "curve", "profile", "joint_origin"
]
KeyPoint = Literal["start", "middle", "end", "center"]

Vector3 = Annotated[
    list[Number], Field(min_length=3, max_length=3, description="[x, y, z] direction (unitless)")
]
Point3 = Annotated[list[Number], Field(min_length=3, max_length=3, description="[x, y, z] in mm")]

# Optional fields allowed per geometry kind (``entity`` is always required).
_KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "point": (),
    "planar_face": ("key_point", "edge"),
    "nonplanar_face": ("key_point",),
    "curve": ("key_point",),
    "profile": ("key_point", "curve"),
    "joint_origin": (),
}


def _nonzero(vector: list[float], name: str) -> None:
    if all(v == 0 for v in vector):
        raise ValueError(f"{name} must not be the zero vector")


class JointGeometry(_Model):
    """Where a joint (or joint origin) snaps: an entity plus a key point on it.

    kind: point (vertex, sketch/construction point), planar_face (+ key_point, optional
    edge), nonplanar_face (+ key_point), curve (edge or sketch curve, + key_point), profile
    (+ key_point, optional curve), joint_origin (an existing joint origin).
    """

    kind: JointGeometryKind
    entity: Annotated[Ref, Field(description="Entity ref ('<token>@<occurrence path>' in parts)")]
    key_point: Annotated[
        KeyPoint | None,
        Field(description="start|middle|end|center (default: center, middle for curves)"),
    ] = None
    edge: Annotated[
        str | None, Field(min_length=1, description="planar_face only: edge the key point is on")
    ] = None
    curve: Annotated[
        str | None, Field(min_length=1, description="profile only: curve the key point is on")
    ] = None

    @model_validator(mode="after")
    def _fields_for_kind(self) -> JointGeometry:
        allowed = _KIND_FIELDS[self.kind]
        for name in ("key_point", "edge", "curve"):
            if getattr(self, name) is not None and name not in allowed:
                raise ValueError(f"'{name}' does not apply to kind '{self.kind}'")
        return self


class Rotate(_Model):
    """Rotation by angle_deg (right-hand rule) about axis through origin_mm."""

    axis: Annotated[Vector3, Field(description="[x, y, z] axis direction (not zero)")]
    angle_deg: Annotated[Number, Field(description="Degrees, right-hand rule about axis")]
    origin_mm: Annotated[
        Point3, Field(default_factory=lambda: [0.0, 0.0, 0.0], description="Point on the axis, mm")
    ]

    @model_validator(mode="after")
    def _axis_not_zero(self) -> Rotate:
        _nonzero(self.axis, "axis")
        return self


class Placement(_Model):
    """Absolute placement: the occurrence's origin and X/Y axes in assembly space."""

    origin_mm: Annotated[Point3, Field(description="Origin in mm")]
    x_axis: Annotated[Vector3, Field(description="X axis direction")]
    y_axis: Annotated[Vector3, Field(description="Y axis direction, perpendicular to x_axis")]

    @model_validator(mode="after")
    def _axes_not_zero(self) -> Placement:
        _nonzero(self.x_axis, "x_axis")
        _nonzero(self.y_axis, "y_axis")
        return self


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    """Register the ``assembly_*`` tools (added group by group, c10 tasks 2-6)."""

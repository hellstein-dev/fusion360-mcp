"""``construction_*`` tools: construction planes, axes and points (c06 D5).

One tool per kind; ``method`` picks the Fusion definition and the fields it needs come from
``fusion_mcp.protocol.construction.METHODS`` (D3). The server runs the same ``validate`` as
the add-in before calling the bridge, so a wrong field set fails without touching Fusion;
only the fields the agent set go over the wire.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.protocol.construction import validate
from fusion_mcp.protocol.errors import BridgeError
from fusion_mcp.server.app import call_bridge, error_result
from fusion_mcp.server.bridge import Bridge

PlaneMethod = Literal[
    "offset",
    "angle",
    "midplane",
    "two_edges",
    "three_points",
    "tangent",
    "tangent_at_point",
    "along_path",
]

Ref = Annotated[str, Field(min_length=1)]
PlaneRef = Annotated[
    str | None,
    Field(min_length=1, description="origin:XY|XZ|YZ, a construction plane or a planar face"),
]
EdgeRef = Annotated[
    str | None,
    Field(min_length=1, description="origin:X|Y|Z, a construction axis, an edge or sketch line"),
]
FaceRef = Annotated[str | None, Field(min_length=1, description="Face ref (from inspect_query)")]
PointRef = Annotated[
    str | None,
    Field(min_length=1, description="origin:O, a vertex, sketch point or construction point"),
]
Name = Annotated[str | None, Field(min_length=1, description="Name of the new element")]
Component = Annotated[
    str | None,
    Field(min_length=1, description="component:/occurrence: ref; default: active component"),
]


def _given(**params: Any) -> dict[str, Any]:
    """Only the parameters the agent set: ``None`` means "not given"."""
    return {k: v for k, v in params.items() if v is not None}


async def _construct(bridge: Bridge, kind: str, params: dict[str, Any]) -> CallToolResult:
    """Validate ``params`` (D3) and send them to ``construction.<kind>``."""
    try:
        validate(kind, params.get("method"), params)
    except BridgeError as error:
        return error_result(error)
    return await call_bridge(bridge, f"construction.{kind}", params)


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="construction_plane")
    async def construction_plane(
        method: Annotated[PlaneMethod, Field(description="How the plane is defined")],
        plane: PlaneRef = None,
        plane2: PlaneRef = None,
        edge: EdgeRef = None,
        edge2: EdgeRef = None,
        face: FaceRef = None,
        point: PointRef = None,
        points: Annotated[list[Ref] | None, Field(description="three_points: 3 point refs")] = None,
        path: Annotated[
            str | None, Field(min_length=1, description="Edge or sketch curve ref")
        ] = None,
        distance: Annotated[
            float | str | None,
            Field(description="mm (negative = against the normal) or expression"),
        ] = None,
        angle: Annotated[float | str | None, Field(description="Degrees or expression")] = None,
        proportion: Annotated[
            float | None, Field(description="Share of the path length, 0..1")
        ] = None,
        component: Component = None,
        name: Name = None,
    ) -> CallToolResult:
        """Create a construction plane — a reference for sketches, mirrors, splits.

        Give `method` and exactly its fields (others are refused with INVALID_PARAMS and
        the list of fields the method takes):

        | method           | fields                                  |
        |------------------|-----------------------------------------|
        | offset           | plane, distance                         |
        | angle            | edge (axis of rotation), angle, plane   |
        | midplane         | plane, plane2 (midway between them)     |
        | two_edges        | edge, edge2 (coplanar straight edges)   |
        | three_points     | points (3 refs)                         |
        | tangent          | face (cylinder/cone), plane, angle = 0  |
        | tangent_at_point | face (cylinder/cone), point             |
        | along_path       | path, proportion (0..1, normal to path) |

        Refs: `origin:XY|XZ|YZ|X|Y|Z|O` or refs from inspect_query / inspect_tree. Lengths
        are mm, angles degrees; a string is a Fusion expression ("height / 2") and keeps
        the plane driven by it. A wrong entity kind (e.g. a cylindrical face as `plane`)
        fails with INVALID_PARAMS and `details {param, expected, got}`; a definition Fusion
        cannot build fails with FUSION_API_ERROR and leaves the timeline unchanged.

        Returns {"ref", "type": "ConstructionPlane", "name", "origin_mm", "normal",
        "health", "warning"?}.

        Examples:
        - construction_plane(method="offset", plane="origin:XY", distance=15)
        - construction_plane(method="midplane", plane=<top face>, plane2=<bottom face>)
        - construction_plane(method="along_path", path=<edge ref>, proportion=0.5)
        """
        params = _given(
            method=method,
            plane=plane,
            plane2=plane2,
            edge=edge,
            edge2=edge2,
            face=face,
            point=point,
            points=points,
            path=path,
            distance=distance,
            angle=angle,
            proportion=proportion,
            component=component,
            name=name,
        )
        return await _construct(bridge, "plane", params)

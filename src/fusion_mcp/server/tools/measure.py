"""``measure_distance`` / ``measure_angle`` tools (c03 D10, D12)."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.server.app import call_bridge
from fusion_mcp.server.bridge import Bridge

Target = Annotated[
    str | Annotated[list[float], Field(min_length=3, max_length=3)],
    Field(
        description="An entity ref (e.g. a face, edge, vertex, body, origin:XY) or a point "
        "[x, y, z] in mm"
    ),
]


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="measure_distance")
    async def measure_distance(a: Target, b: Target) -> CallToolResult:
        """Minimum distance between two entities or points, in mm.

        Each operand is a ref from another tool or a point [x, y, z] in mm.
        Returns {"distance_mm", "point_a_mm", "point_b_mm"}: the closest points on each
        operand (null when Fusion does not report them).

        Example: measure_distance(a="<top face ref>", b="<bottom face ref>") -> height.
        """
        return await call_bridge(bridge, "measure.distance", {"a": a, "b": b})

    @mcp.tool(name="measure_angle")
    async def measure_angle(a: Target, b: Target, c: Target | None = None) -> CallToolResult:
        """Angle in degrees between two entities (faces, edges, axes, planes), or — with
        three operands — the angle at vertex `b` of the points/entities a-b-c.

        Returns {"angle_deg"}.

        Examples: measure_angle(a="<face ref>", b="origin:XY");
        measure_angle(a=[10, 0, 0], b=[0, 0, 0], c=[0, 10, 0]) -> 90.
        """
        params: dict[str, Any] = {"a": a, "b": b}
        if c is not None:
            params["c"] = c
        return await call_bridge(bridge, "measure.angle", params)

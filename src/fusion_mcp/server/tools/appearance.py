"""``appearance_*`` tools: appearance lookup, assignment and simple colour overrides (c12 D6,
D12)."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.protocol.errors import BridgeError
from fusion_mcp.protocol.exchange import parse_color
from fusion_mcp.server.app import call_bridge, error_result
from fusion_mcp.server.bridge import Bridge

from .material import InDesign, Library, Limit, Offset, Query, list_params

AppearanceTarget = Annotated[
    str, Field(description="Ref of a body, a face or an occurrence (not a component)")
]
Color = Annotated[
    str | list[int],
    Field(description='"#RRGGBB", "#RRGGBBAA" or [r, g, b] / [r, g, b, a] with 0..255'),
]


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="appearance_list")
    async def appearance_list(
        library: Library = None,
        query: Query = None,
        in_design: InDesign = False,
        limit: Limit = 100,
        offset: Offset = 0,
    ) -> CallToolResult:
        """Appearances (visual look) of one library, of every library (default) or of the
        design (in_design=true), filtered by a name substring and paginated.

        Returns {"items": [{"id", "name", "library"}], "total", "truncated"}.

        Example: appearance_list(query="paint", limit=20).
        """
        params = list_params(library, query, in_design, limit, offset)
        return await call_bridge(bridge, "appearance.list", params)

    @mcp.tool(name="appearance_assign")
    async def appearance_assign(
        target: AppearanceTarget,
        appearance: Annotated[
            str, Field(description="Appearance id from appearance_list (preferred) or exact name")
        ],
        library: Annotated[
            str | None, Field(description="Library name or id to resolve an ambiguous name")
        ] = None,
    ) -> CallToolResult:
        """Assign an appearance to a body, a face or an occurrence. A library appearance is
        copied into the design first (the library is never changed). A component has no
        appearance: assign it to its bodies or to an occurrence. Changes only the look, not
        the physical material (see material_assign).

        Returns {"target", "appearance": {"id", "name", "library": "design"}, "previous":
        {...} | null, "copied": bool}.

        Example: appearance_assign(target="<face ref>", appearance="<id from appearance_list>").
        """
        params: dict[str, Any] = {"target": target, "appearance": appearance}
        if library is not None:
            params["library"] = library
        return await call_bridge(bridge, "appearance.assign", params)

    @mcp.tool(name="appearance_set_color")
    async def appearance_set_color(
        target: AppearanceTarget,
        color: Color,
        base: Annotated[
            str | None,
            Field(description="Appearance id/name to copy; default: the target's appearance"),
        ] = None,
        name: Annotated[
            str | None,
            Field(description='Name of the design appearance; default "MCP <base> #RRGGBB"'),
        ] = None,
    ) -> CallToolResult:
        """Give a body, a face or an occurrence a plain colour: a design copy of `base` (or
        of the target's current appearance) with the RGBA colour is assigned. An identical
        copy made earlier is reused ("created": false). Library appearances are not changed.

        Returns {"target", "appearance": {"id", "name", "library": "design"}, "created",
        "color": "#RRGGBBAA"}.

        Example: appearance_set_color(target="<body ref>", color="#FF0000").
        """
        try:
            parse_color(color)
        except BridgeError as error:
            return error_result(error)
        params: dict[str, Any] = {"target": target, "color": color}
        if base is not None:
            params["base"] = base
        if name is not None:
            params["name"] = name
        return await call_bridge(bridge, "appearance.set_color", params)

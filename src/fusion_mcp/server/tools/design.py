"""``design_*`` tools: design type and distance units of the active design (c04 D4, D9)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.server.app import call_bridge
from fusion_mcp.server.bridge import Bridge

DesignType = Literal["parametric", "direct"]
DistanceUnits = Literal["mm", "cm", "m", "in", "ft"]


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="design_get_settings")
    async def design_get_settings() -> CallToolResult:
        """Settings of the active design.

        Returns {"design_type": "parametric"|"direct", "units": "mm"|"cm"|"m"|"in"|"ft"
        (display units), "document": {"ref", "type", "name"}, "root_component": {"ref",
        "type", "name"}, "parameter_count", "timeline_count" (null for direct designs)}.
        """
        return await call_bridge(bridge, "design.get_settings", {})

    @mcp.tool(name="design_set_units")
    async def design_set_units(
        units: Annotated[DistanceUnits, Field(description="Distance display units")],
    ) -> CallToolResult:
        """Change the distance units Fusion displays and uses for unitless expressions in
        the active design. Numbers passed to tools are NOT affected: they stay in mm
        (e.g. a length of 25 is still 25 mm after design_set_units("in")).

        Returns the settings (see design_get_settings).

        Example: design_set_units(units="in").
        """
        return await call_bridge(bridge, "design.set_units", {"units": units})

    @mcp.tool(name="design_set_type")
    async def design_set_type(
        design_type: Annotated[DesignType, Field(description="parametric or direct")],
        confirm_discard_history: Annotated[
            bool,
            Field(description="Required for parametric -> direct; only if the user agreed"),
        ] = False,
    ) -> CallToolResult:
        """Switch the active design between parametric (timeline) and direct modelling.

        Parametric -> direct DELETES THE WHOLE TIMELINE (feature history and parameters
        driving it) and cannot be undone or rolled back; it needs
        confirm_discard_history=true, otherwise INVALID_PARAMS and nothing changes. Ask the
        user before confirming. Direct -> parametric starts a new timeline. The same type
        is a no-op ("changed": false).

        Returns the settings plus "changed" and "history_discarded".

        Example: design_set_type(design_type="parametric").
        """
        params = {"design_type": design_type, "confirm_discard_history": confirm_discard_history}
        return await call_bridge(bridge, "design.set_type", params)

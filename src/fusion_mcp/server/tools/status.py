"""``fusion_status``: connection check and facts about the running Fusion."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult

from fusion_mcp.server.app import call_bridge
from fusion_mcp.server.bridge import Bridge

STATUS_TIMEOUT_S = 10.0


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="fusion_status")
    async def fusion_status() -> CallToolResult:
        """Check that Fusion 360 and the FusionMCP add-in are reachable; call this first.

        Returns JSON with:
        - fusion_version: Fusion build, e.g. "2.0.21000"
        - addin_version: FusionMCP add-in version
        - python_version: Python inside Fusion
        - document: name of the active document, or null when none is open
        - design_type: "parametric" (has a timeline; failed tools roll back), "direct",
          or null when the active document is not a design
        - units: the design's default length unit ("mm", "cm", "in", ...); tool inputs
          are still millimetres unless a tool takes an explicit `units`

        When Fusion or the add-in is not running the result is an error starting with
        NOT_CONNECTED and says how to fix it. After a TIMEOUT from another tool, call
        this before retrying: the timed-out operation may still be running in Fusion.
        """
        return await call_bridge(bridge, "status.get", {}, STATUS_TIMEOUT_S)

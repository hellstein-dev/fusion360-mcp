"""``execute_python``: escape hatch that runs Python inside Fusion (design D11)."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.protocol.wire import DEFAULT_TIMEOUT_S
from fusion_mcp.server.app import call_bridge
from fusion_mcp.server.bridge import Bridge


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="execute_python")
    async def execute_python(
        code: Annotated[str, Field(description="Python source to run inside Fusion")],
        params: Annotated[
            dict[str, Any] | None,
            Field(description="JSON values made available to the code as `params`"),
        ] = None,
        timeout_s: Annotated[
            float, Field(gt=0, le=600, description="Seconds to wait for the code to finish")
        ] = DEFAULT_TIMEOUT_S,
    ) -> CallToolResult:
        """Run Python code inside Fusion 360 on its main thread. Use it only when no
        dedicated tool can do the job.

        In scope: `adsk` (adsk.core, adsk.fusion), `app` (adsk.core.Application), `ui`,
        `design` (active adsk.fusion.Design or None), `root` (design.rootComponent or
        None) and `params` (the `params` argument, default {}).

        Units: the Fusion API itself works in CENTIMETRES and RADIANS. Convert yourself:
        25 mm -> 2.5, 90 deg -> math.pi / 2. Strings passed to
        adsk.core.ValueInput.createByString are Fusion expressions ("25 mm", "width / 2").

        Returns JSON {"result", "stdout", "stderr"}: `result` is the value of the variable
        `result` after the code runs (JSON as is, otherwise its repr); stdout/stderr are
        captured and cut at 64 KiB with a truncation marker. If the code raises, the result
        is an error FUSION_API_ERROR with the traceback in details, and timeline items the
        code added are rolled back (parametric designs only).

        Example:
            code = "sk = root.sketches.add(root.xYConstructionPlane)\\n"
                   "sk.sketchCurves.sketchCircles.addByCenterRadius("
                   "adsk.core.Point3D.create(0, 0, 0), params['r_mm'] / 10)\\n"
                   "result = sk.name"
            params = {"r_mm": 12.5}
        """
        payload = {"code": code, "params": params or {}}
        return await call_bridge(bridge, "script.execute", payload, timeout_s)

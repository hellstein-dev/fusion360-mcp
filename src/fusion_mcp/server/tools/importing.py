"""``import_file`` tool: CAD and mesh import into the active design (c12 D11, D12). The module
is not named ``import`` (a keyword). The input path is checked here before the bridge."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.protocol.errors import BridgeError
from fusion_mcp.protocol.exchange import EXTENSIONS, IMPORT_FORMATS, extensions_of
from fusion_mcp.protocol.paths import check_input_path
from fusion_mcp.server.app import call_bridge, error_result
from fusion_mcp.server.bridge import Bridge

ImportFormat = Literal["step", "iges", "sat", "smt", "f3d", "stl", "obj", "3mf"]
MeshUnits = Literal["mm", "cm", "m", "in", "ft"]
IMPORT_TIMEOUT_S = 300.0


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="import_file")
    async def import_file(
        path: Annotated[str, Field(description="Absolute path of an existing file")],
        format: Annotated[
            ImportFormat | None, Field(description="Format; default from the extension")
        ] = None,
        target: Annotated[
            str | None, Field(description="Ref of the component to import into; default root")
        ] = None,
        mesh_units: Annotated[
            MeshUnits, Field(description="Units of STL/OBJ/3MF coordinates (files carry none)")
        ] = "mm",
    ) -> CallToolResult:
        """Import a CAD file (STEP, IGES, SAT, SMT, F3D) or a mesh (STL, OBJ, 3MF) into a
        component of the active design. CAD files usually arrive as a new occurrence, meshes
        as mesh bodies. A failed import leaves the design as it was.

        Returns {"format", "target", "created": {"occurrences": [ref], "bodies": [ref],
        "mesh_bodies": [ref]}}.

        Example: import_file(path="C:/in/bolt.step").
        """
        try:
            extensions = EXTENSIONS[format] if format is not None else extensions_of(IMPORT_FORMATS)
            check_input_path(path, extensions)
        except BridgeError as error:
            return error_result(error)
        params: dict[str, Any] = {"path": path, "mesh_units": mesh_units}
        if format is not None:
            params["format"] = format
        if target is not None:
            params["target"] = target
        return await call_bridge(bridge, "import.file", params, IMPORT_TIMEOUT_S)

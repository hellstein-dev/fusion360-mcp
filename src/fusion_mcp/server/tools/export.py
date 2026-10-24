"""``export_*`` tools: CAD, mesh and sketch DXF export (c12 D7-D10, D12).

The output path is checked here before the bridge is called (the server and Fusion run on one
machine), then again by the add-in.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.protocol.errors import BridgeError
from fusion_mcp.protocol.exchange import (
    CAD_EXPORT_FORMATS,
    EXTENSIONS,
    MESH_EXPORT_FORMATS,
    detect_format,
)
from fusion_mcp.protocol.paths import check_output_path
from fusion_mcp.server.app import call_bridge, error_result
from fusion_mcp.server.bridge import Bridge

CadFormat = Literal["step", "iges", "sat", "smt", "f3d", "usdz"]
MeshFormat = Literal["stl", "3mf", "obj"]
Refinement = Literal["low", "medium", "high", "custom"]
FileUnits = Literal["mm", "cm", "m", "in", "ft"]
EXPORT_TIMEOUT_S = 300.0

OutputPath = Annotated[
    str,
    Field(description="Absolute file path; the extension must match the format"),
]
Overwrite = Annotated[
    bool, Field(description="Replace an existing file (it is deleted before the export)")
]


def check_path(path: str, fmt: str | None, formats: tuple[str, ...], overwrite: bool) -> None:
    """Early refusal of a bad output path (``BridgeError``), the add-in checks again."""
    name = fmt if fmt is not None else detect_format(path, formats)
    check_output_path(path, EXTENSIONS[name], overwrite)


async def _export(
    bridge: Bridge,
    method: str,
    params: dict[str, Any],
    fmt: str | None,
    formats: tuple[str, ...],
) -> CallToolResult:
    try:
        check_path(params["path"], fmt, formats, params["overwrite"])
    except BridgeError as error:
        return error_result(error)
    return await call_bridge(bridge, method, params, EXPORT_TIMEOUT_S)


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="export_cad")
    async def export_cad(
        path: OutputPath,
        format: Annotated[
            CadFormat | None,
            Field(description="Format; default from the extension (.step/.stp, .iges/.igs, ...)"),
        ] = None,
        scope: Annotated[
            str | None,
            Field(
                description="Ref of an occurrence or a component (its first occurrence); "
                "default: the whole design. Bodies are not allowed (use export_mesh)"
            ),
        ] = None,
        overwrite: Overwrite = False,
    ) -> CallToolResult:
        """Export the design, a component or an occurrence to STEP, IGES, SAT, SMT, an F3D
        archive or USDZ (if this Fusion supports USD).

        The path must be absolute, its directory must exist and its extension must match the
        format; an existing file is replaced only with overwrite=true (the old file is
        deleted first, so a failed export loses it). Exporting does not save the document
        (document_save does).

        Returns {"files": [{"path", "size_bytes"}], "format", "scope": <ref> | "root"}.

        Example: export_cad(path="C:/out/housing.step").
        """
        params: dict[str, Any] = {"path": path, "overwrite": overwrite}
        if format is not None:
            params["format"] = format
        if scope is not None:
            params["scope"] = scope
        return await _export(bridge, "export.cad", params, format, CAD_EXPORT_FORMATS)

    @mcp.tool(name="export_mesh")
    async def export_mesh(
        path: OutputPath,
        format: Annotated[
            MeshFormat | None, Field(description="Format; default from the extension")
        ] = None,
        scope: Annotated[
            str | None,
            Field(description="Ref of a body, an occurrence or a component; default: everything"),
        ] = None,
        refinement: Annotated[
            Refinement, Field(description="Tessellation quality; custom uses the values below")
        ] = "medium",
        surface_deviation: Annotated[
            float | None, Field(gt=0, description="Custom only: max surface deviation (units)")
        ] = None,
        normal_deviation: Annotated[
            float | None, Field(gt=0, description="Custom only: max normal deviation, degrees")
        ] = None,
        max_edge_length: Annotated[
            float | None, Field(gt=0, description="Custom only: max edge length (units)")
        ] = None,
        aspect_ratio: Annotated[
            float | None, Field(gt=0, description="Custom only: max triangle aspect ratio")
        ] = None,
        file_units: Annotated[
            FileUnits, Field(description="Units written to STL/OBJ (3MF has none)")
        ] = "mm",
        binary: Annotated[bool, Field(description="Binary STL (false: ASCII); STL only")] = True,
        units: Annotated[
            FileUnits, Field(description="Units of surface_deviation and max_edge_length")
        ] = "mm",
        overwrite: Overwrite = False,
    ) -> CallToolResult:
        """Export the design, a component, an occurrence or a body as a mesh: STL, 3MF or OBJ
        (OBJ also writes <name>.mtl). refinement low|medium|high, or custom with at least
        one of surface_deviation / normal_deviation / max_edge_length / aspect_ratio (numbers
        only; giving them without refinement="custom" is an error).

        The path must be absolute, its directory must exist and its extension must match the
        format; an existing file is replaced only with overwrite=true (the old file is
        deleted first). Exporting does not save the document.

        Returns {"files": [{"path", "size_bytes"}], "format", "scope"} (+ "warnings", e.g.
        file_units ignored for 3MF).

        Example: export_mesh(path="C:/out/bracket.stl", scope="<body ref>", refinement="high").
        """
        params: dict[str, Any] = {
            "path": path,
            "refinement": refinement,
            "file_units": file_units,
            "binary": binary,
            "units": units,
            "overwrite": overwrite,
        }
        optional = {
            "format": format,
            "scope": scope,
            "surface_deviation": surface_deviation,
            "normal_deviation": normal_deviation,
            "max_edge_length": max_edge_length,
            "aspect_ratio": aspect_ratio,
        }
        params.update({k: v for k, v in optional.items() if v is not None})
        return await _export(bridge, "export.mesh", params, format, MESH_EXPORT_FORMATS)

    @mcp.tool(name="export_sketch_dxf")
    async def export_sketch_dxf(
        sketch: Annotated[str, Field(description="Ref of the sketch")],
        path: Annotated[str, Field(description="Absolute path ending with .dxf")],
        overwrite: Overwrite = False,
    ) -> CallToolResult:
        """Write a sketch to a DXF file (e.g. for laser cutting). Same path rules as
        export_cad; fails with FUSION_API_ERROR if this Fusion cannot export sketches to DXF.

        Returns {"files": [{"path", "size_bytes"}], "format": "dxf", "scope": <sketch ref>}.

        Example: export_sketch_dxf(sketch="<sketch ref>", path="C:/out/plate.dxf").
        """
        params = {"sketch": sketch, "path": path, "overwrite": overwrite}
        return await _export(bridge, "export.sketch_dxf", params, "dxf", ("dxf",))

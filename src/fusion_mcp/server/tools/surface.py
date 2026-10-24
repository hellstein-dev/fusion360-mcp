"""``surface_*`` tools (c11 D4-D11, D13): patch, extend, trim (cell preview and removal by
points), untrim, stitch, unstitch, ruled surface and offset surface.

Inputs are the shared models of ``_feature_models.py`` / ``_body_models.py`` /
``_surface_models.py``; the add-in handlers live in ``fusion_addin/handlers/surface.py`` and
re-check everything. Cheap cross-field rules are checked here too, so such a mistake fails
without a bridge round trip.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import call_bridge, error_result
from fusion_mcp.server.bridge import Bridge
from fusion_mcp.server.tools._body_models import Point3
from fusion_mcp.server.tools._feature_models import (
    Angle,
    Length,
    Number,
    ProfileSpec,
    Ref,
    Units,
    to_params,
)
from fusion_mcp.server.tools._surface_models import (
    Continuity,
    ExtendAlignment,
    ExtendType,
    OffsetType,
    RuledCorner,
    RuledType,
    SurfaceOperation,
    UntrimLoopType,
)

UnitsArg = Annotated[Units, Field(description="Unit of plain-number lengths and points")]
OperationArg = Annotated[SurfaceOperation, Field(description="new_body (default) or new_component")]
FaceRefs = Annotated[
    list[Ref],
    Field(min_length=1, description="Face refs (inspect_query); re-query after later features"),
]
TrimTool = Annotated[
    Ref,
    Field(
        description="Cutting tool: surface body, face, construction plane / "
        "origin:XY|XZ|YZ or sketch curve"
    ),
]


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="surface_patch")
    async def surface_patch(
        boundary: Annotated[
            list[ProfileSpec],
            Field(
                min_length=1,
                description="Closed boundary: a profile ('profile:<sketch>@x,y' or a profile "
                "spec), sketch curve refs or edge refs (inspect_query); one open curve/edge is "
                "chained by Fusion",
            ),
        ],
        continuity: Annotated[
            Continuity,
            Field(description="connected (default), tangent or curvature to the edges' faces"),
        ] = "connected",
        weight: Annotated[
            float, Field(ge=0.0, le=1.0, description="Continuity weight 0..1 (default 0.5)")
        ] = 0.5,
        flip_continuity_direction: Annotated[
            bool, Field(description="Flip the side the continuity is taken from")
        ] = False,
        operation: OperationArg = "new_body",
    ) -> CallToolResult:
        """Patch surface: fill a closed boundary with a new surface body (Patch feature).

        The boundary is a sketch profile, sketch curves or B-Rep edges (e.g. the free edges
        of an opening in a surface body). continuity/weight/flip apply to edges of bodies
        (ignored for sketch curves). Refs: profiles from sketch_profiles / sketch_draw,
        curves from sketch_* results, edges from inspect_query (refs of surface bodies change
        after trim/extend/stitch - take them from the latest results).

        Next steps: close surfaces into a solid with surface_stitch and check is_solid; give
        a surface thickness with body_thicken.

        Errors: join/cut operation, weight outside 0..1, a body ref -> INVALID_PARAMS
        (nothing changed); a boundary Fusion cannot fill -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies": [{ref, type, name, is_solid}],
        "modified_bodies", "removed_bodies"}.

        Example: surface_patch(boundary=["profile:<sketch>@0,0"])
        """
        params = to_params(
            boundary=boundary,
            continuity=continuity,
            weight=weight,
            flip_continuity_direction=flip_continuity_direction,
            operation=operation,
        )
        return await call_bridge(bridge, "surface.patch", params)

    @mcp.tool(name="surface_extend")
    async def surface_extend(
        edges: Annotated[
            list[Ref],
            Field(min_length=1, description="Free (boundary) edges of surface bodies"),
        ],
        distance: Annotated[Length, Field(description="Extension length (not 0)")],
        extend_type: Annotated[
            ExtendType,
            Field(description="natural (default, follows the curvature), tangent or perpendicular"),
        ] = "natural",
        chain: Annotated[bool, Field(description="Add tangent-connected free edges")] = True,
        alignment: Annotated[
            ExtendAlignment,
            Field(description="free (default) or align the extended edges (newer Fusion API)"),
        ] = "free",
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Extend a surface body beyond its free edges by a distance (Extend feature).

        Units: a plain-number distance is in `units` (default mm), a string is a Fusion
        expression. Refs: free edges of surface bodies from inspect_query (edges of a solid
        have no free side). The extended body keeps its identity but may get a new ref - use
        modified_bodies.

        Errors: distance 0, a face ref -> INVALID_PARAMS; alignment="align" on a Fusion API
        without extendAlignment -> FUSION_API_ERROR "not available" (nothing changed).

        Returns {"feature", "health", "created_bodies" / "modified_bodies": [{ref, type,
        name, is_solid}], "removed_bodies"}.

        Example: surface_extend(edges=[<free edge>], distance=5)
        """
        params = to_params(
            edges=edges,
            distance=distance,
            extend_type=extend_type,
            chain=chain,
            alignment=alignment,
            units=units,
        )
        return await call_bridge(bridge, "surface.extend", params)

    @mcp.tool(name="surface_trim_cells")
    async def surface_trim_cells(trim_tool: TrimTool) -> CallToolResult:
        """Preview of a trim: the cells the tool cuts the surrounding surfaces into.

        Nothing changes in the design (the trim input is cancelled). Each cell has "center"
        [x, y, z] mm, "bbox" {"min", "max"} mm, "area_mm2" and "is_solid". Cells are
        temporary and have no refs: pick the ones to remove by a point near them and pass
        those points to surface_trim.

        Errors: a tool of another type -> INVALID_PARAMS.

        Returns {"cells": [{center, bbox, area_mm2, is_solid}], "count"}.

        Example: surface_trim_cells(trim_tool="origin:XY")
        """
        return await call_bridge(bridge, "surface.trim_cells", to_params(trim_tool=trim_tool))

    @mcp.tool(name="surface_trim")
    async def surface_trim(
        trim_tool: TrimTool,
        remove_points: Annotated[
            list[Point3],
            Field(
                min_length=1,
                description="Points [x, y, z] in `units`, each on or near a cell to remove "
                "(e.g. a cell center from surface_trim_cells)",
            ),
        ],
        max_distance: Annotated[
            Number,
            Field(ge=0, description="How far (in `units`) a point may be from its cell"),
        ] = 1.0,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Trim surfaces with a tool and remove the cells nearest to the given points.

        Call surface_trim_cells first to see the cells (center / bbox / area in mm), then
        pass a point per cell to remove (in `units`, default mm). Several points in one cell
        remove it once. Cells are never addressed by index.

        Errors: a point farther than max_distance from every cell -> INVALID_PARAMS with
        details.cells (the available cells) and nothing changed; a tool that cuts nothing ->
        INVALID_PARAMS reason "no_cells".

        Returns {"feature", "health", "created_bodies" / "modified_bodies": [{ref, type,
        name, is_solid}], "removed_bodies", "removed_cells": n}.

        Example: surface_trim(trim_tool="origin:XY", remove_points=[[0, 0, 20]])
        """
        params = to_params(
            trim_tool=trim_tool,
            remove_points=remove_points,
            max_distance=max_distance,
            units=units,
        )
        return await call_bridge(bridge, "surface.trim", params)

    @mcp.tool(name="surface_untrim")
    async def surface_untrim(
        faces: FaceRefs,
        loop_type: Annotated[
            UntrimLoopType,
            Field(description="all (default), external (outer boundary) or internal (holes)"),
        ] = "all",
        extension_distance: Annotated[
            Length | None,
            Field(description="Extend the untrimmed faces by this length (all / external)"),
        ] = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Untrim faces of surface bodies: remove their boundary loops (holes / outer trim).

        loop_type internal fills holes, external restores the outer boundary of the
        underlying surface, all does both. Faces of solid bodies are refused (unstitch them
        first). Refs: faces from inspect_query.

        Errors: a face of a solid, extension_distance with internal -> INVALID_PARAMS
        (nothing changed).

        Returns {"feature", "health", "created_bodies" / "modified_bodies": [{ref, type,
        name, is_solid}], "removed_bodies"}.

        Example: surface_untrim(faces=[<face with a hole>], loop_type="internal")
        """
        if loop_type == "internal" and extension_distance is not None:
            return error_result(
                _invalid(
                    "parameter 'extension_distance' applies only to loop_type all | external",
                    param="extension_distance",
                )
            )
        params = to_params(
            faces=faces,
            loop_type=loop_type,
            extension_distance=extension_distance,
            units=units,
        )
        return await call_bridge(bridge, "surface.untrim", params)

    @mcp.tool(name="surface_stitch")
    async def surface_stitch(
        surfaces: Annotated[
            list[Ref],
            Field(min_length=2, description="At least 2 surface bodies or faces to join"),
        ],
        tolerance: Annotated[
            Length | None,
            Field(description="Largest gap to close, in `units`; omitted = 0.1 mm in any units"),
        ] = None,
        operation: OperationArg = "new_body",
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Stitch surface bodies / faces along shared edges into one body (Stitch feature).

        If the surfaces enclose a volume the result is a solid: check "is_solid" in the
        result. is_solid=false means gaps remain - raise `tolerance` (default 0.1 mm), or
        patch the openings with surface_patch first. Refs: bodies / faces from inspect_query
        or earlier results.

        Errors: fewer than 2 distinct surfaces, join/cut operation -> INVALID_PARAMS.

        Returns {"feature", "health", "created_bodies" / "modified_bodies": [{ref, type,
        name, is_solid}], "removed_bodies",
        "is_solid": bool of the resulting body}.

        Example: surface_stitch(surfaces=[<body1>, <body2>, <patch>], tolerance=0.1)
        """
        if len(set(surfaces)) < 2:
            return error_result(
                _invalid("parameter 'surfaces' needs at least 2 distinct refs", param="surfaces")
            )
        params = to_params(surfaces=surfaces, tolerance=tolerance, operation=operation, units=units)
        return await call_bridge(bridge, "surface.stitch", params)

    @mcp.tool(name="surface_unstitch")
    async def surface_unstitch(
        entities: Annotated[
            list[Ref],
            Field(min_length=1, description="Faces or bodies (solid or surface) to split up"),
        ],
        chain: Annotated[
            bool, Field(description="Add tangent-connected faces of the given faces")
        ] = True,
    ) -> CallToolResult:
        """Unstitch: split faces or whole bodies into separate surface bodies.

        A face of a solid becomes its own surface body and the rest of the solid becomes a
        surface body too (check is_solid). Refs: faces / bodies from inspect_query.

        Returns {"feature", "health", "created_bodies" / "modified_bodies": [{ref, type,
        name, is_solid}], "removed_bodies"}.

        Example: surface_unstitch(entities=[<top face>], chain=False)
        """
        params = to_params(entities=entities, chain=chain)
        return await call_bridge(bridge, "surface.unstitch", params)

    @mcp.tool(name="surface_ruled")
    async def surface_ruled(
        edges: Annotated[
            list[Ref],
            Field(
                min_length=1,
                description="Edge refs (inspect_query) or sketch curve refs - one kind only",
            ),
        ],
        distance: Annotated[Length, Field(description="Width of the ruled surface (not 0)")],
        angle: Annotated[Angle, Field(description="Angle from the ruling direction, degrees")] = (
            0.0
        ),
        ruled_type: Annotated[
            RuledType,
            Field(
                description="normal (default, to the face), tangent (to the face) or "
                "direction (needs `direction`)"
            ),
        ] = "normal",
        direction: Annotated[
            Ref | None,
            Field(
                description="direction only: straight edge, axis (origin:X|Y|Z), planar face "
                "or plane"
            ),
        ] = None,
        corner: Annotated[
            RuledCorner, Field(description="rounded (default) or mitered corners")
        ] = "rounded",
        alternate_face: Annotated[
            bool, Field(description="Use the other face of the edges for normal / tangent")
        ] = False,
        chain: Annotated[bool, Field(description="Chain connected edges / curves")] = True,
        operation: OperationArg = "new_body",
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Ruled surface: a strip of straight rulings grown from edges or sketch curves.

        Units: distance in `units` (default mm), angle in degrees; strings are expressions.
        ruled_type normal/tangent is measured against the face of the edges, direction
        follows the `direction` entity. Refs: edges from inspect_query, curves from sketch_*.
        Close the result with surface_stitch / thicken it with body_thicken.

        Errors: ruled_type direction without direction (or direction with another type),
        edges mixed with sketch curves -> INVALID_PARAMS (nothing changed).

        Returns {"feature", "health", "created_bodies" / "modified_bodies": [{ref, type,
        name, is_solid}], "removed_bodies"}.

        Example: surface_ruled(edges=[<top edge>], distance=10)
        """
        if ruled_type == "direction" and direction is None:
            return error_result(
                _invalid("ruled_type 'direction' needs 'direction'", param="direction")
            )
        if ruled_type != "direction" and direction is not None:
            return error_result(
                _invalid(
                    "parameter 'direction' applies only to ruled_type 'direction'",
                    param="direction",
                )
            )
        params = to_params(
            edges=edges,
            distance=distance,
            angle=angle,
            ruled_type=ruled_type,
            direction=direction,
            corner=corner,
            alternate_face=alternate_face,
            chain=chain,
            operation=operation,
            units=units,
        )
        return await call_bridge(bridge, "surface.ruled", params)

    @mcp.tool(name="surface_offset")
    async def surface_offset(
        faces: FaceRefs,
        distance: Annotated[
            Length, Field(description="Signed offset (not 0): positive along the face normal")
        ],
        chain: Annotated[bool, Field(description="Add tangent-connected faces")] = True,
        offset_type: Annotated[
            OffsetType, Field(description="sharp (default) or rounded corners (newer API)")
        ] = "sharp",
        operation: OperationArg = "new_body",
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Offset surface: new surface bodies at a distance from faces (Offset feature).

        The source faces stay unchanged. This differs from feature_offset_faces (c07), which
        moves faces of a solid; to give the result a thickness use body_thicken (c08).
        Units: distance in `units` (default mm). Refs: faces from inspect_query.

        Errors: distance 0, a non-face ref, join/cut -> INVALID_PARAMS; offset_type rounded
        on an API without offsetType -> FUSION_API_ERROR "not available".

        Returns {"feature", "health", "created_bodies" / "modified_bodies": [{ref, type,
        name, is_solid}], "removed_bodies"}.

        Example: surface_offset(faces=[<face>], distance=2)
        """
        params = to_params(
            faces=faces,
            distance=distance,
            chain=chain,
            offset_type=offset_type,
            operation=operation,
            units=units,
        )
        return await call_bridge(bridge, "surface.offset", params)

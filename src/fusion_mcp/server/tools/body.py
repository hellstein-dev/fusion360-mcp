"""``body_*`` tools (c08 D8-D13): combine, split, split face, move / copy, scale, thicken,
boundary fill, delete / replace faces, rename, visibility and remove.

Inputs are the shared models of ``_feature_models.py`` / ``_body_models.py`` (D14); the add-in
handlers live in ``fusion_addin/handlers/body.py`` and ``body_faces.py`` and re-check
everything. Cheap cross-field rules are checked here too, so such a mistake fails without a
bridge round trip.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import call_bridge, error_result
from fusion_mcp.server.bridge import Bridge
from fusion_mcp.server.tools._body_models import (
    CombineOperation,
    Factor,
    Point3,
    RemoveMode,
    ScaleFactors,
    SplitFaceType,
    Transform,
)
from fusion_mcp.server.tools._feature_models import Length, Operation, Ref, Units, to_params

UnitsArg = Annotated[Units, Field(description="Unit of plain-number lengths")]
BodyRefs = Annotated[
    list[Ref],
    Field(min_length=1, description="Body refs (inspect_query / earlier results)"),
]
FaceRefs = Annotated[
    list[Ref],
    Field(min_length=1, description="Face refs (inspect_query); re-query after later features"),
]


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="body_combine")
    async def body_combine(
        target: Annotated[Ref, Field(description="Body that receives the result")],
        tools: Annotated[
            list[Ref], Field(min_length=1, description="Tool bodies (not the target)")
        ],
        operation: Annotated[
            CombineOperation, Field(description="join (default), cut or intersect")
        ] = "join",
        keep_tools: Annotated[bool, Field(description="Keep the tool bodies")] = False,
        new_component: Annotated[
            bool, Field(description="Put the result in a new component")
        ] = False,
    ) -> CallToolResult:
        """Boolean of bodies: join, cut or intersect tool bodies with a target body.

        The target is changed (modified_bodies); consumed tools are listed by token in
        removed_bodies unless keep_tools=true. With new_component=true the result body is
        created in a new component ("component" in the result). Refs: bodies from
        inspect_query or earlier results (tokens change after this feature - use the refs in
        the result).

        Errors: the target listed in tools, a non-body ref -> INVALID_PARAMS (nothing changed);
        a combine Fusion cannot compute -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}
        (+ "component" with new_component).

        Example: body_combine(target=<base>, tools=[<boss1>, <boss2>])
        """
        if target in tools:
            return error_result(
                _invalid("the 'target' body is also listed in 'tools'", param="tools")
            )
        params = to_params(
            target=target,
            tools=tools,
            operation=operation,
            keep_tools=keep_tools,
            new_component=new_component,
        )
        return await call_bridge(bridge, "body.combine", params)

    @mcp.tool(name="body_split")
    async def body_split(
        bodies: BodyRefs,
        tool: Annotated[
            Ref,
            Field(description="Splitting tool: construction plane, origin:XY|XZ|YZ, face or body"),
        ],
        extend_tool: Annotated[
            bool, Field(description="Extend the tool to cut through the whole body")
        ] = True,
    ) -> CallToolResult:
        """Split bodies into parts with a plane, a face or another body.

        The new parts are listed in created_bodies, the split bodies in modified_bodies. Refs:
        planes from origin:* / construction_*, faces and bodies from inspect_query.

        Errors: the tool among the bodies, a tool of another type -> INVALID_PARAMS (nothing
        changed); a tool that does not cross the body -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}.

        Example: body_split(bodies=[<body>], tool="origin:XZ")
        """
        params = to_params(bodies=bodies, tool=tool, extend_tool=extend_tool)
        return await call_bridge(bridge, "body.split", params)

    @mcp.tool(name="body_split_face")
    async def body_split_face(
        faces: FaceRefs,
        tool: Annotated[
            Ref, Field(description="Face, construction/origin plane, body or sketch curve")
        ],
        split_type: Annotated[
            SplitFaceType,
            Field(
                description="intersection (default), along_vector (needs direction), closest_point"
            ),
        ] = "intersection",
        direction: Annotated[
            Ref | None,
            Field(description="along_vector: straight edge, sketch line, axis or plane"),
        ] = None,
        extend_tool: Annotated[bool, Field(description="Extend the tool across the faces")] = (
            True
        ),
    ) -> CallToolResult:
        """Split faces of a body with a tool (to get separate faces for draft, offset, colors).

        intersection: along the tool's intersection; along_vector: project the tool along
        `direction`; closest_point: project to the closest points. Refs: faces/bodies from
        inspect_query, planes from origin:* / construction_*, curves from sketch_* results.

        Errors: along_vector without direction, direction with another type ->
        INVALID_PARAMS (nothing changed); a split type this Fusion API lacks ->
        FUSION_API_ERROR "not available".

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies",
        "faces": [{ref, type}] - the new faces}.

        Example: body_split_face(faces=[<top face>], tool="origin:YZ")
        """
        if split_type == "along_vector" and direction is None:
            return error_result(
                _invalid("split_type 'along_vector' needs 'direction'", param="direction")
            )
        if split_type != "along_vector" and direction is not None:
            return error_result(
                _invalid(
                    "parameter 'direction' applies only to split_type 'along_vector'",
                    param="direction",
                )
            )
        params = to_params(
            faces=faces,
            tool=tool,
            split_type=split_type,
            direction=direction,
            extend_tool=extend_tool,
        )
        return await call_bridge(bridge, "body.split_face", params)

    @mcp.tool(name="body_move")
    async def body_move(
        bodies: BodyRefs,
        transform: Annotated[
            Transform,
            Field(
                description='{"type": "translate", x, y, z, design_space} | '
                '{"type": "translate_along", entity, distance} | '
                '{"type": "rotate", axis, angle} | '
                '{"type": "rotate_about", point, direction, angle} | '
                '{"type": "align", from, to} | {"type": "matrix", matrix: 16 numbers}'
            ),
        ],
        keep_original: Annotated[
            bool, Field(description="Copy the bodies first and move only the copies")
        ] = False,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Move (or copy and move) bodies as a Move feature in the timeline.

        Transforms: translate (x/y/z in `units` or expressions; design_space=false uses the
        component axes), translate_along (straight edge/axis + distance), rotate (axis ref +
        angle in degrees), rotate_about (free axis: point [x,y,z] in `units`, direction
        vector, numeric angle in degrees), align (move point `from` onto point `to`: vertex,
        sketch/construction point, origin:O), matrix (16 numbers row-major, a rigid rotation +
        translation in `units`; use body_scale to scale). keep_original=true copies with
        Copy/Paste (a non-associative copy; for associative copies use pattern_*), and only
        the copies move. Refs: bodies/edges/vertices from inspect_query, axes/points from
        origin:* or construction_*.

        Errors: a non-rigid matrix, a wrong ref kind -> INVALID_PARAMS (nothing changed); on
        an old Fusion API expressions / component-space moves -> FUSION_API_ERROR "not
        available".

        Returns {"feature", "health", "created_bodies" (copies), "modified_bodies" (moved
        originals), "removed_bodies"} + "copy": {"feature", "method"} with keep_original.

        Examples:
        - body_move(bodies=[b], transform={"type": "translate", "z": 50})
        - body_move(bodies=[b], transform={"type": "rotate", "axis": "origin:Z", "angle": 90},
          keep_original=true)
        """
        params = to_params(
            bodies=bodies, transform=transform, keep_original=keep_original, units=units
        )
        return await call_bridge(bridge, "body.move", params)

    @mcp.tool(name="body_scale")
    async def body_scale(
        bodies: BodyRefs,
        factor: Annotated[Factor, Field(description="Uniform factor (unitless, > 0)")] = 1.0,
        factors: Annotated[
            ScaleFactors | None,
            Field(description="Non-uniform {x, y, z} factors instead of `factor`"),
        ] = None,
        point: Annotated[
            Ref,
            Field(description="Base point: origin:O (default), vertex, sketch/construction point"),
        ] = "origin:O",
    ) -> CallToolResult:
        """Scale bodies about a base point, uniformly (factor) or per axis (factors).

        Factors are unitless numbers > 0 (or Fusion expressions) - no unit conversion. Pass
        either factor or factors. Refs: bodies from inspect_query, points from origin:O,
        construction_point or vertices.

        Errors: a factor <= 0, factor together with factors, a non-point base -> INVALID_PARAMS
        (nothing changed).

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}.

        Examples: body_scale(bodies=[b], factor=0.5);
        body_scale(bodies=[b], factors={"x": 1, "y": 2, "z": 0.5})
        """
        if factors is not None and factor != 1:
            return error_result(
                _invalid(
                    "pass either 'factor' (uniform) or 'factors' (non-uniform), not both",
                    param="factors",
                )
            )
        params = to_params(
            bodies=bodies,
            factor=factor if factors is None else None,
            factors=factors,
            point=point,
        )
        return await call_bridge(bridge, "body.scale", params)

    @mcp.tool(name="body_thicken")
    async def body_thicken(
        faces: FaceRefs,
        thickness: Annotated[
            Length, Field(description="Signed thickness; negative = the other side")
        ],
        symmetric: Annotated[bool, Field(description="Grow to both sides")] = False,
        operation: Annotated[
            Operation, Field(description="new_body (default), join, cut, intersect, new_component")
        ] = "new_body",
        chain: Annotated[bool, Field(description="Also take tangent-connected faces")] = True,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Thicken faces (usually of a surface body) into a solid.

        Units: thickness in `units` (default mm) or a Fusion expression. Faces from
        inspect_query.

        Errors: thickness 0, a non-face ref -> INVALID_PARAMS (nothing changed); a thicken
        Fusion cannot compute -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}.

        Example: body_thicken(faces=[<patch face>], thickness=2)
        """
        params = to_params(
            faces=faces,
            thickness=thickness,
            symmetric=symmetric,
            operation=operation,
            chain=chain,
            units=units,
        )
        return await call_bridge(bridge, "body.thicken", params)

    @mcp.tool(name="body_boundary_fill")
    async def body_boundary_fill(
        tools: Annotated[
            list[Ref],
            Field(min_length=1, description="Bodies, faces and planes that bound the cells"),
        ],
        operation: Annotated[
            Operation, Field(description="new_body (default), join, cut, intersect, new_component")
        ] = "new_body",
        cell_points: Annotated[
            list[Point3] | None,
            Field(
                description="Fill only the cells containing these [x, y, z] points (in "
                "`units`); omitted = every cell"
            ),
        ] = None,
        remove_tools: Annotated[bool, Field(description="Remove the tool bodies")] = False,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Boundary fill: make solids from the closed cells that tool bodies, faces and planes
        enclose.

        Cells are chosen by points, never by index: every cell by default, or the cells that
        contain cell_points. If a point lies in no cell, nothing is created and the error
        lists every cell (details.cells: center [x, y, z] mm and volume mm³) to pick points
        from. Refs: bodies/faces from inspect_query, planes from origin:* / construction_*.

        Errors: a point in no cell, tools enclosing no cell -> INVALID_PARAMS (nothing
        changed); a fill Fusion cannot compute -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}.

        Example: body_boundary_fill(tools=[<shell>, "origin:XY"], cell_points=[[5, 5, 5]])
        """
        params = to_params(
            tools=tools,
            operation=operation,
            cell_points=cell_points,
            remove_tools=remove_tools,
            units=units,
        )
        return await call_bridge(bridge, "body.boundary_fill", params)

    @mcp.tool(name="body_delete_faces")
    async def body_delete_faces(faces: FaceRefs) -> CallToolResult:
        """Delete faces from a body; Fusion heals the gap (e.g. remove a fillet or a boss).

        Faces from inspect_query on the latest body. The bodies are listed in
        modified_bodies.

        Errors: a non-face ref -> INVALID_PARAMS (nothing changed); faces Fusion cannot heal
        -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}.

        Example: body_delete_faces(faces=[<fillet face 1>, <fillet face 2>])
        """
        return await call_bridge(bridge, "body.delete_faces", to_params(faces=faces))

    @mcp.tool(name="body_replace_face")
    async def body_replace_face(
        faces: FaceRefs,
        target: Annotated[
            Ref,
            Field(description="Face, construction plane / origin:XY|XZ|YZ or surface body"),
        ],
        tangent_chain: Annotated[
            bool, Field(description="Also replace tangent-connected faces")
        ] = True,
    ) -> CallToolResult:
        """Replace faces of a body with a target face, plane or surface body (the body grows
        or shrinks to the target).

        Refs: faces from inspect_query, planes from origin:* / construction_*, surface bodies
        from earlier results. A solid body is not a valid target.

        Errors: a solid body or one of the faces as target -> INVALID_PARAMS (nothing
        changed); a replace Fusion cannot compute -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}.

        Example: body_replace_face(faces=[<top face>], target=<offset plane>)
        """
        params = to_params(faces=faces, target=target, tangent_chain=tangent_chain)
        return await call_bridge(bridge, "body.replace_face", params)

    @mcp.tool(name="body_rename")
    async def body_rename(
        body: Annotated[Ref, Field(description="Body ref")],
        name: Annotated[str, Field(min_length=1, description="New name (trimmed)")],
    ) -> CallToolResult:
        """Rename a body (not a timeline feature).

        Returns {"body": {ref, type, name}, "previous_name": str}.

        Errors: an empty name, a non-body ref -> INVALID_PARAMS.

        Example: body_rename(body=<body>, name="Lid")
        """
        if not name.strip():
            return error_result(_invalid("parameter 'name' must not be empty", param="name"))
        return await call_bridge(bridge, "body.rename", to_params(body=body, name=name.strip()))

    @mcp.tool(name="body_set_visibility")
    async def body_set_visibility(
        bodies: BodyRefs,
        visible: Annotated[bool | None, Field(description="Light bulb on/off")] = None,
        opacity: Annotated[
            float | None, Field(ge=0, le=1, description="0 = transparent .. 1 = opaque")
        ] = None,
    ) -> CallToolResult:
        """Show/hide bodies (light bulb) and/or set their opacity.

        All or nothing: if Fusion refuses a value for one body, every body gets its previous
        values back. is_visible in the result is the effective visibility (a hidden
        component hides its bodies even with the light bulb on).

        Errors: neither visible nor opacity, opacity outside 0..1 -> INVALID_PARAMS.

        Returns {"bodies": [{ref, type, name, light_bulb, is_visible, opacity}]}.

        Example: body_set_visibility(bodies=[a, b], visible=false)
        """
        if visible is None and opacity is None:
            return error_result(_invalid("give 'visible' and/or 'opacity'", param="visible"))
        params = to_params(bodies=bodies, visible=visible, opacity=opacity)
        return await call_bridge(bridge, "body.set_visibility", params)

    @mcp.tool(name="body_remove")
    async def body_remove(
        bodies: BodyRefs,
        mode: Annotated[
            RemoveMode,
            Field(
                description="remove (default): a Remove feature in the timeline (reversible); "
                "delete: delete the bodies"
            ),
        ] = "remove",
    ) -> CallToolResult:
        """Remove bodies from the design.

        mode=remove adds a Remove feature per body to the timeline of a parametric design
        (the bodies come back if the feature is suppressed or deleted); mode=delete deletes
        them. A direct design (no timeline) always deletes (mode_used = "delete"); there a
        Fusion failure on a later body cannot bring back earlier deleted ones.

        Errors: a non-body ref -> INVALID_PARAMS (nothing removed).

        Returns {"removed": [refs before removal], "features": [{ref, type, name}],
        "mode_used": "remove" | "delete"}.

        Example: body_remove(bodies=[<tool body>])
        """
        return await call_bridge(bridge, "body.remove", to_params(bodies=bodies, mode=mode))

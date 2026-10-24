"""``pattern_rectangular``, ``pattern_circular``, ``pattern_path`` and ``pattern_mirror`` tools
(c08 D3-D7).

Inputs are the shared models of ``_feature_models.py`` / ``_body_models.py`` (D14); the add-in
handlers live in ``fusion_addin/handlers/pattern.py`` and re-check everything. Cheap
cross-field rules (a second direction without a distance) are checked here too, so such a
mistake fails without a bridge round trip.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import call_bridge, error_result
from fusion_mcp.server.bridge import Bridge
from fusion_mcp.server.tools._body_models import ComputeOption, Count, PatternDistanceType
from fusion_mcp.server.tools._feature_models import Angle, Length, Ref, Units, to_params

UnitsArg = Annotated[Units, Field(description="Unit of plain-number lengths")]
Entities = Annotated[
    list[Ref],
    Field(
        min_length=1,
        description="What to pattern - all of ONE kind: feature refs (timeline features), "
        "body refs, face refs (inspect_query) or occurrence refs",
    ),
]
Compute = Annotated[
    ComputeOption,
    Field(description="Feature input only: adjust (default), optimized or identical"),
]


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="pattern_rectangular")
    async def pattern_rectangular(
        entities: Entities,
        direction1: Annotated[
            Ref,
            Field(description="Straight edge, sketch line, construction axis or origin:X|Y|Z"),
        ],
        distance1: Annotated[
            Length, Field(description="Spacing (or extent) along direction1; negative flips it")
        ],
        quantity1: Annotated[Count, Field(description="Instances along direction1")] = 2,
        distance_type: Annotated[
            PatternDistanceType,
            Field(description="spacing (default): distance between instances; extent: total"),
        ] = "spacing",
        symmetric1: Annotated[bool, Field(description="Spread both ways along direction1")] = (
            False
        ),
        direction2: Annotated[Ref | None, Field(description="Optional second direction")] = None,
        quantity2: Annotated[Count, Field(description="Instances along direction2")] = 1,
        distance2: Annotated[
            Length | None, Field(description="Spacing (or extent) along direction2")
        ] = None,
        symmetric2: Annotated[bool, Field(description="Spread both ways along direction2")] = (
            False
        ),
        compute: Compute = "adjust",
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Rectangular pattern: copy features, bodies, faces or occurrences along one or two
        directions.

        Quantities count the original (quantity1=3 -> the original + 2 copies). Units:
        distances in `units` (default mm) or Fusion expressions; quantities are whole numbers
        or expressions. Refs: bodies/faces from inspect_query, features from earlier
        feature_* results, directions from inspect_query edges, construction_* axes or
        origin:X|Y|Z. All entities must be of one kind (Fusion requirement).

        Errors: mixed kinds (details.kinds), quantity < 1, direction2 with quantity2 > 1 but
        no distance2 -> INVALID_PARAMS (nothing changed); a pattern Fusion cannot compute ->
        FUSION_API_ERROR, rolled back.

        Returns {"feature": {ref, type, name}, "health": {"state", "message"},
        "created_bodies": [{ref, type, name}], "modified_bodies": [...], "removed_bodies":
        [tokens]} plus "created_occurrences": [{ref, type, name, full_path_name}] for
        occurrence input.

        Example: pattern_rectangular(entities=[<body>], direction1="origin:X", quantity1=3,
        distance1=30, direction2="origin:Y", quantity2=2, distance2=25)
        """
        try:
            q2 = _count(quantity2)
            if direction2 is not None and (q2 is None or q2 > 1) and distance2 is None:
                raise _invalid(
                    "parameter 'direction2' with quantity2 > 1 needs 'distance2'",
                    param="distance2",
                )
            if direction2 is None and (q2 != 1 or distance2 is not None or symmetric2):
                raise _invalid(
                    "quantity2 / distance2 / symmetric2 need 'direction2'", param="direction2"
                )
        except BridgeError as error:
            return error_result(error)
        params = to_params(
            entities=entities,
            direction1=direction1,
            quantity1=quantity1,
            distance1=distance1,
            distance_type=distance_type,
            symmetric1=symmetric1,
            direction2=direction2,
            quantity2=quantity2 if direction2 is not None else None,
            distance2=distance2,
            symmetric2=symmetric2 if direction2 is not None else None,
            compute=compute,
            units=units,
        )
        return await call_bridge(bridge, "pattern.rectangular", params)

    @mcp.tool(name="pattern_circular")
    async def pattern_circular(
        entities: Entities,
        axis: Annotated[
            Ref,
            Field(
                description="Axis: origin:X|Y|Z, construction axis, straight edge, sketch line "
                "or cylindrical/conical face"
            ),
        ],
        quantity: Annotated[Count, Field(description="Instances including the original")],
        total_angle: Annotated[Angle, Field(description="Angle the instances span")] = 360.0,
        symmetric: Annotated[bool, Field(description="Spread both ways from the original")] = (
            False
        ),
        compute: Compute = "adjust",
    ) -> CallToolResult:
        """Circular pattern: copy features, bodies, faces or occurrences around an axis.

        Instances are spread evenly over total_angle (degrees, default 360 = full circle).
        Refs: bodies/faces from inspect_query, features from earlier feature_* results, the
        axis from origin:X|Y|Z, construction_* or inspect_query (a cylindrical face uses its
        axis). All entities must be of one kind.

        Errors: mixed kinds, quantity < 1, a non-axis ref -> INVALID_PARAMS (nothing
        changed); a pattern Fusion cannot compute -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}
        (+ "created_occurrences" for occurrence input).

        Example: pattern_circular(entities=[<hole feature>], axis="origin:Z", quantity=6)
        """
        params = to_params(
            entities=entities,
            axis=axis,
            quantity=quantity,
            total_angle=total_angle,
            symmetric=symmetric,
            compute=compute,
        )
        return await call_bridge(bridge, "pattern.circular", params)

    @mcp.tool(name="pattern_path")
    async def pattern_path(
        entities: Entities,
        path: Annotated[
            list[Ref],
            Field(min_length=1, description="Sketch curve refs or edge refs (one kind)"),
        ],
        quantity: Annotated[Count, Field(description="Instances including the original")],
        distance: Annotated[Length, Field(description="Extent (default) or spacing along path")],
        path_chain: Annotated[
            bool, Field(description="Extend the path through connected curves/tangent edges")
        ] = True,
        distance_type: Annotated[
            PatternDistanceType,
            Field(description="extent (default): total length used; spacing: between instances"),
        ] = "extent",
        start: Annotated[
            float, Field(ge=0, le=1, description="Start on the path: 0 = start .. 1 = end")
        ] = 0.0,
        flip: Annotated[bool, Field(description="Go the other way along the path")] = False,
        orientation_along_path: Annotated[
            bool, Field(description="Rotate instances to follow the path tangent")
        ] = False,
        symmetric: Annotated[bool, Field(description="Spread both ways along the path")] = False,
        compute: Compute = "adjust",
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Path pattern: copy features, bodies, faces or occurrences along a path of sketch
        curves or edges.

        Units: distance in `units` (default mm) or an expression; start is a share of the path
        length. Refs: curves from sketch_* results, edges from inspect_query. All entities
        must be of one kind.

        Errors: mixed kinds, a path mixing curves and edges, start outside 0..1 ->
        INVALID_PARAMS (nothing changed); a path with gaps or a pattern Fusion cannot compute
        -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}
        (+ "created_occurrences" for occurrence input).

        Example: pattern_path(entities=[<body>], path=[<sketch line>], quantity=4,
        distance=100, orientation_along_path=true)
        """
        params = to_params(
            entities=entities,
            path=path,
            path_chain=path_chain,
            quantity=quantity,
            distance=distance,
            distance_type=distance_type,
            start=start,
            flip=flip,
            orientation_along_path=orientation_along_path,
            symmetric=symmetric,
            compute=compute,
            units=units,
        )
        return await call_bridge(bridge, "pattern.path", params)

    @mcp.tool(name="pattern_mirror")
    async def pattern_mirror(
        entities: Entities,
        plane: Annotated[
            Ref,
            Field(description="Planar face, construction plane or origin:XY|XZ|YZ"),
        ],
        combine: Annotated[
            bool,
            Field(description="Bodies only: join each mirrored body with its original"),
        ] = False,
        compute: Compute = "adjust",
    ) -> CallToolResult:
        """Mirror features, bodies, faces or occurrences across a plane.

        With combine=true (body input only) every mirrored body is joined with its original
        (a symmetric part from one half); the originals are then in modified_bodies. Refs:
        planes from origin:XY|XZ|YZ, construction_* or a planar face from inspect_query.

        Errors: mixed kinds, combine with non-body input, a non-planar ref -> INVALID_PARAMS
        (nothing changed); a mirror Fusion cannot compute -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}
        (+ "created_occurrences" for occurrence input).

        Example: pattern_mirror(entities=[<body>], plane="origin:YZ", combine=true)
        """
        params = to_params(entities=entities, plane=plane, combine=combine, compute=compute)
        return await call_bridge(bridge, "pattern.mirror", params)

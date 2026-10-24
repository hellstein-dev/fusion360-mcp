"""``feature_*`` tools for sketch-based solid features (c07 D1, D10-D14): extrude, revolve,
sweep, loft, rib and web.

Inputs are the shared models of ``_feature_models.py`` (D20); the add-in handlers live in
``fusion_addin/handlers/feature_sketch.py``. Cheap cross-field rules (which extent fields a
mode takes) are checked here too, so such a mistake fails without a bridge round trip; the
add-in re-checks everything.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.surfaces import SURFACE_OPERATIONS
from fusion_mcp.server.app import call_bridge, error_result
from fusion_mcp.server.bridge import Bridge
from fusion_mcp.server.tools._feature_models import (
    Angle,
    Direction,
    DistanceExtent,
    Extent,
    ExtrudeMode,
    Length,
    LoftSection,
    Operation,
    ProfileSpec,
    RailScaling,
    Ref,
    RevolveExtent,
    RibDepth,
    RibThicknessLocation,
    StartExtent,
    SweepOrientation,
    ThinOptions,
    Units,
    to_params,
)

Profiles = Annotated[
    list[ProfileSpec],
    Field(
        min_length=1,
        description="Profiles to use: 'profile:<sketch>@x,y' refs from sketch_profiles / "
        'sketch_draw, {"sketch", "point"} / {"sketch", "curves"} / {"sketch", "all": true}, '
        "or planar face refs (inspect_query)",
    ),
]
OperationArg = Annotated[
    Operation,
    Field(description="new_body (default), join, cut, intersect or new_component"),
]
Participants = Annotated[
    list[Ref] | None,
    Field(
        description="Body refs join/cut/intersect act on (default: every body it touches); "
        "not allowed with new_body / new_component"
    ),
]
UnitsArg = Annotated[Units, Field(description="Unit of plain-number lengths and points")]
RibCurves = Annotated[
    list[Ref],
    Field(
        min_length=1,
        description="Open sketch curve refs (sketch_draw), drawn on a plane through the body, "
        "ending at or near its walls",
    ),
]
RibLocationArg = Annotated[
    RibThicknessLocation,
    Field(description="symmetric (default) about the curves, or side1 / side2 of them"),
]
RibDepthArg = Annotated[
    RibDepth | None,
    Field(
        description='{"type": "to_next"} (default, up to the next face) | '
        '{"type": "distance", "distance"}'
    ),
]
RibFlip = Annotated[bool, Field(description="Grow the other way from the sketch plane")]
RibTarget = Annotated[
    str | None,
    Field(
        min_length=1,
        description="Body the rib/web is built on and joined to; default: the one solid body "
        "the curves touch",
    ),
]
IsSolidArg = Annotated[
    bool,
    Field(
        description="true (default) = solid; false = surface body (new_body / new_component "
        "only; close surfaces with surface_stitch, check is_solid)"
    ),
]


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _check_extrude(
    mode: str,
    side1: Any,
    side2: Any,
    direction: Any,
    taper2: Any,
    symmetric_full_length: bool,
    thin: ThinOptions | None,
) -> None:
    """Mode-dependent fields of ``feature_extrude`` (D10); the add-in applies the same rules."""
    if mode == "two_sides" and side2 is None:
        raise _invalid("mode 'two_sides' needs side2", param="side2", mode=mode)
    if mode != "two_sides":
        for name, value in (("side2", side2), ("taper2", taper2)):
            if value is not None:
                raise _invalid(
                    f"parameter '{name}' applies only to mode two_sides, not '{mode}'",
                    param=name,
                    mode=mode,
                )
        if thin is not None and thin.thickness2 is not None:
            raise _invalid(
                "parameter 'thin.thickness2' applies only to mode 'two_sides'",
                param="thin.thickness2",
                mode=mode,
            )
    if direction is not None and mode != "one_side":
        raise _invalid(
            f"parameter 'direction' applies only to mode one_side, not '{mode}'",
            param="direction",
            mode=mode,
        )
    if symmetric_full_length and mode != "symmetric":
        raise _invalid(
            f"parameter 'symmetric_full_length' applies only to mode symmetric, not '{mode}'",
            param="symmetric_full_length",
            mode=mode,
        )
    if mode == "symmetric" and not isinstance(side1, DistanceExtent):
        raise _invalid(
            "mode 'symmetric' needs side1 of type 'distance'",
            param="side1.type",
            value=getattr(side1, "type", None),
            expected=["distance"],
        )


def _is_zero(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value == 0


def _check_sweep(
    guide_rail: list[str] | None, twist_angle: Any, taper_angle: Any, rail_scaling: str
) -> None:
    """Guide-rail rules of ``feature_sweep`` (D12); the add-in applies the same rules."""
    if guide_rail is not None:
        for name, value in (("twist_angle", twist_angle), ("taper_angle", taper_angle)):
            if not _is_zero(value):
                raise _invalid(f"parameter '{name}' cannot be used with guide_rail", param=name)
    elif rail_scaling != "scale":
        raise _invalid(
            "parameter 'rail_scaling' applies only together with guide_rail",
            param="rail_scaling",
        )


def _check_surface(is_solid: bool, operation: str, thin: ThinOptions | None = None) -> None:
    """Surface mode (c11 D12): ``is_solid=false`` only creates bodies, never a thin wall."""
    if is_solid:
        return
    if operation not in SURFACE_OPERATIONS:
        raise _invalid(
            f"operation '{operation}' needs a solid; with is_solid=false use "
            f"{' | '.join(SURFACE_OPERATIONS)}",
            param="operation",
            value=operation,
        )
    if thin is not None:
        raise _invalid("parameter 'thin' cannot be used with is_solid=false", param="thin")


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="feature_extrude")
    async def feature_extrude(
        profiles: Profiles,
        side1: Annotated[
            Extent,
            Field(
                description='{"type": "distance", "distance"} | {"type": "to_entity", "entity", '
                '"offset"=0, "chained"=true} | {"type": "through_all"}'
            ),
        ],
        operation: OperationArg = "new_body",
        mode: Annotated[
            ExtrudeMode, Field(description="one_side (default), two_sides or symmetric")
        ] = "one_side",
        side2: Annotated[
            Extent | None, Field(description="Second side extent; required for two_sides only")
        ] = None,
        direction: Annotated[
            Direction | None,
            Field(
                description="one_side only: positive (default, along the sketch normal) or negative"
            ),
        ] = None,
        taper1: Annotated[Angle, Field(description="Taper of side 1, degrees")] = 0.0,
        taper2: Annotated[
            Angle | None, Field(description="Taper of side 2, degrees (two_sides only)")
        ] = None,
        symmetric_full_length: Annotated[
            bool,
            Field(
                description="symmetric only: true = side1.distance is the total length, "
                "false = length of each half"
            ),
        ] = False,
        start: Annotated[
            StartExtent | None,
            Field(
                description='Where the extrude starts: {"type": "profile_plane"} (default) | '
                '{"type": "offset", "offset"} | {"type": "entity", "entity", "offset"=0}'
            ),
        ] = None,
        thin: Annotated[
            ThinOptions | None,
            Field(
                description='Thin wall instead of a solid: {"thickness", "location": '
                'side1|side2|center, "thickness2"? (two_sides)}'
            ),
        ] = None,
        is_solid: IsSolidArg = True,
        participant_bodies: Participants = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Extrude sketch profiles, planar faces or sketch texts into a solid (or cut/join/
        intersect existing bodies).

        Units: plain-number lengths are in `units` (default mm), angles in degrees; a string
        is a Fusion expression ("wall * 2", "5 deg") and keeps the feature driven by it.
        Refs: profiles from sketch_draw / sketch_profiles ("profile:<sketch>@x,y") or
        {"sketch": <sketch ref>, "all": true}; planar faces and to_entity targets from
        inspect_query; planes from construction_plane or origin:XY|XZ|YZ; sketch texts from
        sketch_draw (kind "text"). Refs to faces/bodies go stale after later features - take
        them from the latest results.

        Modes:
        - one_side: side1 + direction (positive = sketch normal) + taper1;
        - two_sides: side1 and side2 independent (each its own extent and taper);
        - symmetric: side1 must be a distance; symmetric_full_length picks total vs half.
        A negative distance extrudes the other way. operation="cut" with through_all and
        direction="negative" cuts down through a body below the sketch.

        Surface: is_solid=false extrudes a surface body (operation new_body / new_component,
        no thin); profiles may then also be open sketch curve refs or edge refs (one kind,
        connected curves are chained) - e.g. an open line becomes a wall sheet.

        Errors: wrong field for the mode or a wrong entity kind -> INVALID_PARAMS (nothing
        changed); a stale profile point -> ENTITY_NOT_FOUND with details.profiles (current
        profiles); a feature Fusion computes with an error -> FUSION_API_ERROR and the
        timeline is rolled back. A warning keeps the feature and is reported in `health`.

        Returns {"feature": {ref, type, name}, "health": {"state": ok|warning|error|unknown,
        "message"}, "created_bodies": [{ref, type, name}], "modified_bodies": [{ref, type,
        name}], "removed_bodies": [<refs of bodies consumed by join/cut>], "component"?:
        {ref, type, name} (new_component only)}. Body geometry is not repeated: describe the
        bodies with inspect_* if needed.

        Examples:
        - feature_extrude(profiles=["profile:<sk>@10,10"], side1={"type": "distance",
          "distance": 20})
        - feature_extrude(profiles=[...], operation="cut", side1={"type": "through_all"},
          direction="negative")
        - feature_extrude(profiles=[...], mode="symmetric", side1={"type": "distance",
          "distance": 40}, symmetric_full_length=true, thin={"thickness": 2, "location":
          "center"})
        """
        try:
            _check_extrude(mode, side1, side2, direction, taper2, symmetric_full_length, thin)
            _check_surface(is_solid, operation, thin)
        except BridgeError as error:
            return error_result(error)
        params = to_params(
            profiles=profiles,
            operation=operation,
            mode=mode,
            side1=side1,
            side2=side2,
            direction=direction,
            taper1=taper1,
            taper2=taper2,
            symmetric_full_length=symmetric_full_length or None,
            start=start,
            thin=thin,
            is_solid=is_solid,
            participant_bodies=participant_bodies,
            units=units,
        )
        return await call_bridge(bridge, "feature.extrude", params)

    @mcp.tool(name="feature_revolve")
    async def feature_revolve(
        profiles: Profiles,
        axis: Annotated[
            Ref,
            Field(
                description="Axis of revolution: a sketch line, a construction axis "
                "(construction_axis or origin:X|Y|Z) or a straight edge (inspect_query)"
            ),
        ],
        extent: Annotated[
            RevolveExtent | None,
            Field(
                description='{"type": "full"} (default, 360 deg) | {"type": "angle", '
                '"angle"=360, "symmetric"=false} | {"type": "two_angles", "angle1", '
                '"angle2"} | {"type": "to_entity", "entity", "direction"=positive}'
            ),
        ] = None,
        operation: OperationArg = "new_body",
        is_solid: IsSolidArg = True,
        participant_bodies: Participants = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Revolve sketch profiles or planar faces around an axis - shafts, pulleys,
        bottles, grooves (operation="cut").

        Units: angles are degrees (a string is a Fusion expression, e.g. "180 deg");
        `units` only applies to {"sketch", "point"} profile specs. Refs: profiles from
        sketch_draw / sketch_profiles; the axis is a sketch line ref (sketch_draw refs), a
        construction axis (construction_axis, origin:X|Y|Z) or a straight BRep edge
        (inspect_query). The profile must lie on one side of the axis. is_solid=false
        revolves a surface body (new_body / new_component only).

        Extents: full (default) = 360 deg; angle with symmetric=true splits the angle to
        both sides of the sketch plane; two_angles sets each side; to_entity revolves up to
        a face, body, construction plane, vertex or construction point, starting towards the
        sketch normal (direction="positive") or away from it ("negative").

        Errors: an axis that is not linear (e.g. a circular edge) or a wrong extent field ->
        INVALID_PARAMS (nothing changed); a feature Fusion computes with an error ->
        FUSION_API_ERROR and the timeline is rolled back.

        Returns {"feature": {ref, type, name}, "health": {"state": ok|warning|error|unknown,
        "message"}, "created_bodies": [{ref, type, name}], "modified_bodies": [{ref, type,
        name}], "removed_bodies": [<refs of bodies consumed by join/cut>], "component"?:
        {ref, type, name} (new_component only)}. Body geometry is not repeated: describe the
        bodies with inspect_* if needed.

        Examples:
        - feature_revolve(profiles=["profile:<sk>@5,20"], axis="origin:Z")
        - feature_revolve(profiles=[...], axis=<sketch line ref>, extent={"type": "angle",
          "angle": 90, "symmetric": true}, operation="cut")
        """
        try:
            _check_surface(is_solid, operation)
        except BridgeError as error:
            return error_result(error)
        params = to_params(
            profiles=profiles,
            axis=axis,
            extent=extent,
            operation=operation,
            is_solid=is_solid,
            participant_bodies=participant_bodies,
            units=units,
        )
        return await call_bridge(bridge, "feature.revolve", params)

    @mcp.tool(name="feature_sweep")
    async def feature_sweep(
        profiles: Profiles,
        path: Annotated[
            list[Ref],
            Field(
                min_length=1,
                description="Sweep path: sketch curve refs (sketch_draw) or BRep edge refs "
                "(inspect_query) - one kind only",
            ),
        ],
        path_chain: Annotated[
            bool,
            Field(
                description="true (default): extend each path/rail curve to every curve "
                "connected to it end to end (tangent chain for a single edge)"
            ),
        ] = True,
        guide_rail: Annotated[
            list[Ref] | None,
            Field(
                min_length=1,
                description="Optional guide rail (sketch curves or edges) that steers and "
                "scales the profile; excludes twist_angle and taper_angle",
            ),
        ] = None,
        rail_scaling: Annotated[
            RailScaling,
            Field(description="With guide_rail: scale (default), stretch or none"),
        ] = "scale",
        orientation: Annotated[
            SweepOrientation,
            Field(
                description="perpendicular (default, the profile follows the path) or "
                "parallel (keeps its orientation)"
            ),
        ] = "perpendicular",
        twist_angle: Annotated[
            Angle, Field(description="Twist over the whole path, degrees (no guide_rail)")
        ] = 0.0,
        taper_angle: Annotated[
            Angle, Field(description="Taper of the profile, degrees (no guide_rail)")
        ] = 0.0,
        path_extent: Annotated[
            float,
            Field(gt=0, le=1, description="Share of the path to sweep along, (0, 1]"),
        ] = 1.0,
        flip: Annotated[bool, Field(description="Sweep the other way along the path")] = False,
        operation: OperationArg = "new_body",
        is_solid: IsSolidArg = True,
        participant_bodies: Participants = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Sweep sketch profiles or planar faces along a path - pipes, handles, springs
        (with twist), rails, swept cuts (operation="cut").

        Units: angles in degrees (a string is a Fusion expression, e.g. "pitch_angle");
        path_extent is a plain share of the path length; `units` only applies to
        {"sketch", "point"} profile specs. Refs: profiles from sketch_draw / sketch_profiles;
        path and guide rail from sketch_draw (sketch curve refs, the path sketch usually lies
        on a plane perpendicular to the profile sketch) or BRep edges from inspect_query.
        Do not mix sketch curves and edges in one path. With path_chain=true one curve
        is enough to take the whole connected chain. is_solid=false sweeps a surface
        (new_body / new_component only); the profile may then be open sketch curves or edges.

        Rules: twist_angle / taper_angle with guide_rail -> INVALID_PARAMS; path_extent
        outside (0, 1] -> rejected; a path with gaps -> FUSION_API_ERROR. A feature Fusion
        computes with an error -> FUSION_API_ERROR and the timeline is rolled back; a warning
        keeps the feature and is reported in `health`.

        Returns {"feature": {ref, type, name}, "health": {"state": ok|warning|error|unknown,
        "message"}, "created_bodies": [{ref, type, name}], "modified_bodies": [{ref, type,
        name}], "removed_bodies": [<refs of bodies consumed by join/cut>], "component"?:
        {ref, type, name} (new_component only)}.

        Examples:
        - feature_sweep(profiles=["profile:<sk>@0,0"], path=[<path line ref>, <arc ref>])
        - feature_sweep(profiles=[...], path=[<helix-like spline ref>], twist_angle=360)
        - feature_sweep(profiles=[...], path=[<edge ref>], guide_rail=[<curve ref>],
          rail_scaling="stretch")
        """
        try:
            _check_sweep(guide_rail, twist_angle, taper_angle, rail_scaling)
            _check_surface(is_solid, operation)
        except BridgeError as error:
            return error_result(error)
        with_rail = guide_rail is not None
        params = to_params(
            profiles=profiles,
            path=path,
            path_chain=path_chain,
            guide_rail=guide_rail,
            rail_scaling=rail_scaling if with_rail else None,
            orientation=orientation,
            twist_angle=None if with_rail else twist_angle,
            taper_angle=None if with_rail else taper_angle,
            path_extent=path_extent,
            flip=flip,
            operation=operation,
            is_solid=is_solid,
            participant_bodies=participant_bodies,
            units=units,
        )
        return await call_bridge(bridge, "feature.sweep", params)

    @mcp.tool(name="feature_loft")
    async def feature_loft(
        sections: Annotated[
            list[LoftSection],
            Field(
                min_length=2,
                description='Sections in order: {"entity", "end_condition"=free, "angle"=0, '
                '"weight"=1}. entity = one profile (ref or spec), a planar face ref, or a '
                "point (sketch point, construction point, vertex) - points only first/last",
            ),
        ],
        rails: Annotated[
            list[Ref] | None,
            Field(description="Rail curves/edges guiding the loft (not with centerline)"),
        ] = None,
        centerline: Annotated[
            Ref | None,
            Field(description="Centerline curve/edge the sections follow (not with rails)"),
        ] = None,
        closed: Annotated[
            bool, Field(description="Close the loft back to the first section (no points)")
        ] = False,
        merge_tangent_edges: Annotated[
            bool, Field(description="Merge tangent edges of the result (default true)")
        ] = True,
        operation: OperationArg = "new_body",
        is_solid: IsSolidArg = True,
        participant_bodies: Participants = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Loft a solid through two or more sections - transitions, ducts, bottle necks,
        domes ending in a point.

        Sections: each is exactly one profile ('profile:<sketch>@x,y' or a {"sketch", ...}
        spec selecting one region), a planar face (inspect_query), or a point - a sketch
        point, construction point (construction_point, origin:O) or vertex - which may only
        be the first or the last section. End conditions: free (default), tangent / smooth
        (with weight), direction (angle in degrees + weight), point_sharp / point_tangent
        (point sections only). Inner sections keep free unless asked otherwise.

        Guides: rails (sketch curves or edges touching every section) or one centerline,
        never both. closed=true joins the last section back to the first (needs 3+ profile
        sections and no point section). is_solid=false lofts a surface body (new_body /
        new_component only).

        Units: angles in degrees, weights plain numbers; `units` only applies to
        {"sketch", "point"} profile specs. Errors: fewer than 2 sections, rails with a
        centerline, a point in the middle, point_* on a profile, closed with a point, more
        than one profile in a section -> INVALID_PARAMS (nothing changed); a feature Fusion
        computes with an error -> FUSION_API_ERROR and the timeline is rolled back.

        Returns {"feature": {ref, type, name}, "health": {"state": ok|warning|error|unknown,
        "message"}, "created_bodies": [{ref, type, name}], "modified_bodies": [{ref, type,
        name}], "removed_bodies": [<refs of bodies consumed by join/cut>], "component"?:
        {ref, type, name} (new_component only)}.

        Examples:
        - feature_loft(sections=[{"entity": "profile:<sk1>@0,0"}, {"entity":
          "profile:<sk2>@0,0"}], rails=[<rail curve ref>])
        - feature_loft(sections=[{"entity": <face ref>, "end_condition": "tangent"},
          {"entity": <sketch point ref>, "end_condition": "point_tangent", "weight": 2}],
          operation="join")
        """
        try:
            _check_surface(is_solid, operation)
        except BridgeError as error:
            return error_result(error)
        if rails and centerline is not None:
            return error_result(
                _invalid(
                    "parameters 'rails' and 'centerline' cannot be used together",
                    param="centerline",
                    reason="rails_and_centerline",
                )
            )
        params = to_params(
            sections=sections,
            rails=rails or None,
            centerline=centerline,
            closed=closed,
            merge_tangent_edges=merge_tangent_edges,
            operation=operation,
            is_solid=is_solid,
            participant_bodies=participant_bodies,
            units=units,
        )
        return await call_bridge(bridge, "feature.loft", params)

    @mcp.tool(name="feature_rib")
    async def feature_rib(
        curves: RibCurves,
        thickness: Annotated[Length, Field(description="Rib thickness")],
        thickness_location: RibLocationArg = "symmetric",
        depth: RibDepthArg = None,
        flip: RibFlip = False,
        target_body: RibTarget = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Add a rib (a thin stiffening wall) from open sketch curves, joined to the body
        it meets - gussets in housings, brackets, bosses.

        Units: thickness and depth.distance are in `units` (default mm), a string is a
        Fusion expression ("wall"). Refs: open sketch curves from sketch_draw (a line or arc
        drawn in a sketch on a plane cutting the body, e.g. a construction_plane through the
        corner); circles and closed splines are refused. The rib is always joined to
        `target_body` (no operation parameter); without it, to the one solid body of the
        curves' component that a curve touches.

        Errors: closed curve, wrong thickness_location or depth -> INVALID_PARAMS (nothing
        changed); no target_body and the curves touch no body or several -> INVALID_PARAMS
        with details.candidates (body refs to pass as target_body); thickness_location
        "side2" is not available in the current Fusion API (FUSION_API_ERROR, no change):
        use "side1" with the curve drawn the other way; a Fusion API version without rib
        features -> FUSION_API_ERROR "not available" with no change (use execute_python
        then); a rib Fusion computes with an error -> FUSION_API_ERROR and the timeline is
        rolled back.

        Returns {"feature": {ref, type, name}, "health": {"state": ok|warning|error|unknown,
        "message"}, "created_bodies": [...], "modified_bodies": [{ref, type, name}] (the body
        the rib joins), "removed_bodies": [...]}.

        Example: feature_rib(curves=[<sketch line ref>], thickness=3)
        """
        params = to_params(
            curves=curves,
            thickness=thickness,
            thickness_location=thickness_location,
            depth=depth,
            flip=flip,
            target_body=target_body,
            units=units,
        )
        return await call_bridge(bridge, "feature.rib", params)

    @mcp.tool(name="feature_web")
    async def feature_web(
        curves: RibCurves,
        thickness: Annotated[Length, Field(description="Wall thickness of the web")],
        thickness_location: RibLocationArg = "symmetric",
        depth: RibDepthArg = None,
        flip: RibFlip = False,
        target_body: RibTarget = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Add a web - a network of thin walls from several open sketch curves (e.g. a grid
        of stiffeners inside a shelled housing), joined to the body it meets.

        Units: thickness and depth.distance are in `units` (default mm), a string is a
        Fusion expression. Refs: open sketch curves from sketch_draw, all in one sketch
        inside the body; circles and closed splines are refused. Always joined to
        `target_body` (no operation parameter); without it, to the one solid body of the
        curves' component that a curve touches.

        Errors: closed curve, wrong thickness_location or depth -> INVALID_PARAMS (nothing
        changed); no target_body and the curves touch no body or several -> INVALID_PARAMS
        with details.candidates (body refs to pass as target_body); thickness_location
        "side2" is not available in the current Fusion API (FUSION_API_ERROR, no change);
        a Fusion API version without web features -> FUSION_API_ERROR "not
        available" with no change (use execute_python then); a web Fusion computes with an
        error -> FUSION_API_ERROR and the timeline is rolled back.

        Returns {"feature": {ref, type, name}, "health": {"state": ok|warning|error|unknown,
        "message"}, "created_bodies": [...], "modified_bodies": [{ref, type, name}] (the body
        the web joins), "removed_bodies": [...]}.

        Example: feature_web(curves=[<line1 ref>, <line2 ref>, <line3 ref>], thickness=2,
        depth={"type": "distance", "distance": 15})
        """
        params = to_params(
            curves=curves,
            thickness=thickness,
            thickness_location=thickness_location,
            depth=depth,
            flip=flip,
            target_body=target_body,
            units=units,
        )
        return await call_bridge(bridge, "feature.web", params)

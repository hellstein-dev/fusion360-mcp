"""``feature_*`` tools for sketch-based solid features (c07 D1, D10, D11): extrude, revolve.

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
from fusion_mcp.server.app import call_bridge, error_result
from fusion_mcp.server.bridge import Bridge
from fusion_mcp.server.tools._feature_models import (
    Angle,
    Direction,
    DistanceExtent,
    Extent,
    ExtrudeMode,
    Operation,
    ProfileSpec,
    Ref,
    RevolveExtent,
    StartExtent,
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
        participant_bodies: Participants = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Revolve sketch profiles or planar faces around an axis - shafts, pulleys,
        bottles, grooves (operation="cut").

        Units: angles are degrees (a string is a Fusion expression, e.g. "180 deg");
        `units` only applies to {"sketch", "point"} profile specs. Refs: profiles from
        sketch_draw / sketch_profiles; the axis is a sketch line ref (sketch_draw refs), a
        construction axis (construction_axis, origin:X|Y|Z) or a straight BRep edge
        (inspect_query). The profile must lie on one side of the axis.

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
        params = to_params(
            profiles=profiles,
            axis=axis,
            extent=extent,
            operation=operation,
            participant_bodies=participant_bodies,
            units=units,
        )
        return await call_bridge(bridge, "feature.revolve", params)

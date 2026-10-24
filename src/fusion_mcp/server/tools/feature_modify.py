"""``feature_fillet``, ``feature_rule_fillet``, ``feature_chamfer``, ``feature_shell``,
``feature_draft`` and ``feature_offset_faces`` tools (c07 D17-D19).

Inputs are the shared models of ``_feature_models.py`` (D20); the add-in handlers live in
``fusion_addin/handlers/feature_modify.py``. Cheap cross-field rules (which fields a mode
takes, shell faces vs bodies, a thickness) are checked here too, so such a mistake fails
without a bridge round trip; the add-in re-checks everything.
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
    ChamferMode,
    FilletCorner,
    FilletMid,
    FilletMode,
    Length,
    Ref,
    RuleFilletRule,
    Units,
    to_params,
)

UnitsArg = Annotated[Units, Field(description="Unit of plain-number lengths")]
EdgeRefs = Annotated[
    list[Ref],
    Field(min_length=1, description="Edge refs (inspect_query); re-query after later features"),
]
FaceRefs = Annotated[
    list[Ref],
    Field(min_length=1, description="Face refs (inspect_query); re-query after later features"),
]
TangentChain = Annotated[bool, Field(description="Also take tangent-connected edges/faces")]

_FILLET_FIELDS = {
    "constant": ("radius",),
    "variable": ("start_radius", "end_radius"),
    "chord": ("chord_length",),
}
_CHAMFER_FIELDS = {
    "equal": ("distance",),
    "two_distances": ("distance", "distance2"),
    "distance_angle": ("distance",),
}


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _check_mode(
    mode: str,
    fields: dict[str, tuple[str, ...]],
    given: dict[str, Any],
    extra: dict[str, tuple[str, ...]] | None = None,
) -> None:
    """Fields of another mode are refused, the fields of ``mode`` are required. ``extra``
    maps optional fields to the modes that take them."""
    owners: dict[str, list[str]] = {}
    for owner, names in fields.items():
        for name in names:
            owners.setdefault(name, []).append(owner)
    for name, takers in (extra or {}).items():
        owners[name] = list(takers)
    for name, allowed in owners.items():
        if mode not in allowed and given.get(name) is not None:
            raise _invalid(
                f"parameter '{name}' applies only to mode {' | '.join(allowed)}, not '{mode}'",
                param=name,
                mode=mode,
            )
    for name in fields[mode]:
        if given.get(name) is None:
            raise _invalid(f"mode '{mode}' needs '{name}'", param=name, mode=mode)


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="feature_fillet")
    async def feature_fillet(
        edges: EdgeRefs,
        mode: Annotated[
            FilletMode, Field(description="constant (default), variable or chord")
        ] = "constant",
        radius: Annotated[Length | None, Field(description="constant: radius")] = None,
        start_radius: Annotated[
            Length | None, Field(description="variable: radius at the edge start")
        ] = None,
        end_radius: Annotated[
            Length | None, Field(description="variable: radius at the edge end")
        ] = None,
        mid: Annotated[
            list[FilletMid] | None,
            Field(description='variable: [{"position": 0..1, "radius"}] intermediate radii'),
        ] = None,
        chord_length: Annotated[Length | None, Field(description="chord: chord length")] = None,
        tangent_chain: TangentChain = True,
        corner: Annotated[
            FilletCorner, Field(description="Corner type where three fillets meet")
        ] = "rolling_ball",
        g2: Annotated[bool, Field(description="Curvature-continuous (G2) fillet")] = False,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Round edges of a body with a fillet.

        Modes: constant needs radius; variable needs start_radius and end_radius, plus
        optional mid radii at positions 0 (edge start) .. 1 (edge end); chord needs
        chord_length. Fields of another mode are refused. Units: radii and lengths in
        `units` (default mm), strings are Fusion expressions ("wall / 2"). Edge refs come from
        inspect_query on the latest body (refs go stale after later features). The parent
        bodies of the edges are listed in modified_bodies.

        Errors: a missing mode field, a non-edge ref -> INVALID_PARAMS (nothing changed);
        variable/chord fillets on an old Fusion API -> FUSION_API_ERROR "not available"; a
        fillet Fusion computes with an error (radius too big) -> FUSION_API_ERROR and the
        timeline is rolled back.

        Returns {"feature": {ref, type, name}, "health": {"state", "message"},
        "created_bodies": [], "modified_bodies": [{ref, type, name}], "removed_bodies": []}.

        Examples:
        - feature_fillet(edges=[e1, e2, e3, e4], radius=5)
        - feature_fillet(edges=[e], mode="variable", start_radius=2, end_radius=5,
          mid=[{"position": 0.5, "radius": 3}])
        """
        try:
            _check_mode(
                mode,
                _FILLET_FIELDS,
                {
                    "radius": radius,
                    "start_radius": start_radius,
                    "end_radius": end_radius,
                    "chord_length": chord_length,
                    "mid": mid,
                },
                {"mid": ("variable",)},
            )
        except BridgeError as error:
            return error_result(error)
        params = to_params(
            edges=edges,
            mode=mode,
            radius=radius,
            start_radius=start_radius,
            end_radius=end_radius,
            mid=mid,
            chord_length=chord_length,
            tangent_chain=tangent_chain,
            corner=corner,
            g2=g2,
            units=units,
        )
        return await call_bridge(bridge, "feature.fillet", params)

    @mcp.tool(name="feature_rule_fillet")
    async def feature_rule_fillet(
        faces: Annotated[
            list[Ref],
            Field(min_length=1, description="Face refs (inspect_query) or feature refs"),
        ],
        radius: Annotated[Length, Field(description="Fillet radius")],
        rule: Annotated[
            RuleFilletRule,
            Field(description="all_edges (default) of `faces`, or between_faces and faces2"),
        ] = "all_edges",
        faces2: Annotated[
            list[Ref] | None,
            Field(min_length=1, description="between_faces: the second face/feature set"),
        ] = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Rule-based fillet: round every edge of the given faces/features (all_edges) or only
        the edges where `faces` meet `faces2` (between_faces), with one radius.

        Units: radius in `units` (default mm) or a Fusion expression. Faces come from
        inspect_query, features from earlier feature_* results (their "feature" ref). The
        parent bodies are listed in modified_bodies.

        Errors: faces2 with all_edges or missing with between_faces -> INVALID_PARAMS; a Fusion
        API without rule fillets -> FUSION_API_ERROR "not available" (nothing changed; use
        feature_fillet with explicit edges); a failed fillet -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}.

        Example: feature_rule_fillet(faces=[<boss feature ref>], radius=1)
        """
        if rule == "between_faces" and faces2 is None:
            return error_result(
                _invalid("rule 'between_faces' needs 'faces2'", param="faces2", rule=rule)
            )
        if rule != "between_faces" and faces2 is not None:
            return error_result(
                _invalid(
                    "parameter 'faces2' applies only to rule between_faces",
                    param="faces2",
                    rule=rule,
                )
            )
        params = to_params(faces=faces, radius=radius, rule=rule, faces2=faces2, units=units)
        return await call_bridge(bridge, "feature.rule_fillet", params)

    @mcp.tool(name="feature_chamfer")
    async def feature_chamfer(
        edges: EdgeRefs,
        distance: Annotated[Length, Field(description="Chamfer distance (first distance)")],
        mode: Annotated[
            ChamferMode, Field(description="equal (default), two_distances or distance_angle")
        ] = "equal",
        distance2: Annotated[
            Length | None, Field(description="two_distances: second distance")
        ] = None,
        angle: Annotated[
            Angle | None, Field(description="distance_angle: angle in degrees (default 45)")
        ] = None,
        flip: Annotated[
            bool, Field(description="two_distances / distance_angle: swap the sides")
        ] = False,
        tangent_chain: TangentChain = True,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Bevel edges of a body with a chamfer.

        Modes: equal (distance), two_distances (distance and distance2 on the two faces),
        distance_angle (distance and angle, default 45 deg); flip swaps which face gets which
        value. Units: distances in `units` (default mm), angle in degrees, strings are Fusion
        expressions. Edge refs from inspect_query on the latest body. The parent bodies are
        listed in modified_bodies.

        Errors: distance2 / angle / flip with another mode, a missing distance2 ->
        INVALID_PARAMS (nothing changed); a failed chamfer -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}.

        Examples:
        - feature_chamfer(edges=[e], distance=1)
        - feature_chamfer(edges=[e], mode="distance_angle", distance=2, angle=30)
        """
        try:
            _check_mode(
                mode,
                _CHAMFER_FIELDS,
                {"distance": distance, "distance2": distance2, "angle": angle},
                {"angle": ("distance_angle",)},
            )
            if flip and mode == "equal":
                raise _invalid(
                    "parameter 'flip' applies only to mode two_distances | distance_angle, "
                    "not 'equal'",
                    param="flip",
                    mode=mode,
                )
        except BridgeError as error:
            return error_result(error)
        params = to_params(
            edges=edges,
            distance=distance,
            mode=mode,
            distance2=distance2,
            angle=angle,
            flip=flip,
            tangent_chain=tangent_chain,
            units=units,
        )
        return await call_bridge(bridge, "feature.chamfer", params)

    @mcp.tool(name="feature_shell")
    async def feature_shell(
        faces: Annotated[
            list[Ref] | None,
            Field(description="Faces to remove (open shell), from inspect_query"),
        ] = None,
        bodies: Annotated[
            list[Ref] | None,
            Field(description="Bodies to hollow without opening (instead of faces)"),
        ] = None,
        inside_thickness: Annotated[
            Length | None, Field(description="Wall thickness grown inwards")
        ] = None,
        outside_thickness: Annotated[
            Length | None, Field(description="Wall thickness grown outwards")
        ] = None,
        tangent_chain: TangentChain = True,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Hollow a body into a shell with walls of the given thickness.

        Pass faces to remove (an open box: its top face) OR bodies (a closed hollow body),
        and inside_thickness and/or outside_thickness. Units: thicknesses in `units` (default
        mm) or Fusion expressions. The shelled bodies are listed in modified_bodies.

        Errors: no faces and no bodies, both, or no thickness -> INVALID_PARAMS (nothing
        changed); walls Fusion cannot build (too thick) -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}.

        Example: feature_shell(faces=[<bottom face>], inside_thickness=3)
        """
        faces = faces or None
        bodies = bodies or None
        if faces is not None and bodies is not None:
            return error_result(
                _invalid(
                    "pass either 'faces' (faces to remove) or 'bodies' (hollow without "
                    "opening), not both",
                    param="bodies",
                    reason="faces_and_bodies",
                )
            )
        if faces is None and bodies is None:
            return error_result(
                _invalid(
                    "shell needs 'faces' (faces to remove) or 'bodies' (hollow without opening)",
                    param="faces",
                    reason="no_input",
                )
            )
        if inside_thickness is None and outside_thickness is None:
            return error_result(
                _invalid(
                    "shell needs 'inside_thickness' and/or 'outside_thickness'",
                    param="inside_thickness",
                    reason="no_thickness",
                )
            )
        params = to_params(
            faces=faces,
            bodies=bodies,
            inside_thickness=inside_thickness,
            outside_thickness=outside_thickness,
            tangent_chain=tangent_chain,
            units=units,
        )
        return await call_bridge(bridge, "feature.shell", params)

    @mcp.tool(name="feature_draft")
    async def feature_draft(
        faces: FaceRefs,
        plane: Annotated[
            Ref,
            Field(
                description="Neutral plane = pull direction: planar face, construction plane "
                "(construction_plane) or origin:XY|XZ|YZ"
            ),
        ],
        angle: Annotated[Angle, Field(description="Draft angle, degrees (not 0, |a| < 90)")],
        angle2: Annotated[
            Angle | None, Field(description="Second-side angle (two-angle draft)")
        ] = None,
        symmetric: Annotated[
            bool, Field(description="Draft both sides of the plane by `angle`")
        ] = False,
        flip: Annotated[bool, Field(description="Reverse the pull direction")] = False,
        tangent_chain: TangentChain = True,
    ) -> CallToolResult:
        """Tilt faces by a draft angle relative to a neutral plane (moulding / casting draft).

        The plane fixes the line the faces pivot about and its normal is the pull direction
        (flip reverses it). One angle, symmetric=true for both sides by the same angle, or
        angle + angle2 for two sides. Angles in degrees or Fusion expressions. Faces from
        inspect_query; the plane must not be one of the faces. The drafted bodies are listed
        in modified_bodies.

        Errors: angle 0 or >= 90, angle2 with symmetric, a non-planar plane -> INVALID_PARAMS
        (nothing changed); a failed draft -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}.

        Example: feature_draft(faces=[f1, f2, f3, f4], plane="origin:XY", angle=2)
        """
        if angle2 is not None and symmetric:
            return error_result(
                _invalid(
                    "parameter 'angle2' cannot be combined with symmetric=true", param="angle2"
                )
            )
        params = to_params(
            faces=faces,
            plane=plane,
            angle=angle,
            angle2=angle2,
            symmetric=symmetric,
            flip=flip,
            tangent_chain=tangent_chain,
        )
        return await call_bridge(bridge, "feature.draft", params)

    @mcp.tool(name="feature_offset_faces")
    async def feature_offset_faces(
        faces: FaceRefs,
        distance: Annotated[
            Length, Field(description="Signed offset: positive = outward, negative = inward")
        ],
        chain: Annotated[bool, Field(description="Also take tangent-connected faces")] = True,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Press-pull faces of a body: move them along their normals by a signed distance
        (positive grows the body outward, negative pulls the faces in).

        Press-pull of a sketch profile = feature_extrude; press-pull of a fillet edge (change a
        rounding) = feature_fillet. Units: distance in `units` (default mm) or a Fusion
        expression. Faces from inspect_query on the latest body. The bodies are listed in
        modified_bodies.

        Errors: distance 0, a non-face ref -> INVALID_PARAMS (nothing changed); an offset
        Fusion cannot compute -> FUSION_API_ERROR, rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies"}.

        Example: feature_offset_faces(faces=[<top face>], distance=2)
        """
        params = to_params(faces=faces, distance=distance, chain=chain, units=units)
        return await call_bridge(bridge, "feature.offset_faces", params)

"""``sheet_metal_*`` tools (c13 D9): capability report, rules, sheet metal bodies (convert,
base / contour / edge flange), bends, unfold / refold and the flat pattern with its DXF / STEP
export.

Inputs are the shared models of ``_feature_models.py`` and ``_sheet_metal_models.py``; the
add-in handlers live in ``fusion_addin/handlers/sheet_metal.py`` and re-check everything.
Cheap cross-field rules (``distance_two`` only for two-sided flanges, ``height`` xor
``to_entity``) and the export path are checked here too, so such a mistake fails without a
bridge round trip.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.sheet_metal import FLAT_EXPORT_FORMATS
from fusion_mcp.server.app import call_bridge, error_result
from fusion_mcp.server.bridge import Bridge
from fusion_mcp.server.tools._feature_models import (
    Angle,
    Length,
    ProfileSpec,
    Ref,
    Units,
    to_params,
)
from fusion_mcp.server.tools._sheet_metal_models import (
    BendPosition,
    EdgeFlangeWidth,
    FlangeDirection,
    FlangeOperation,
    FlangeOrientation,
    FlatExportFormat,
    FullWidth,
    HeightDatum,
    ReliefShape,
    RuleSource,
)
from fusion_mcp.server.tools.export import EXPORT_TIMEOUT_S, FileUnits, check_path

UnitsArg = Annotated[Units, Field(description="Unit of plain-number lengths (default mm)")]
RuleArg = Annotated[
    str | None,
    Field(
        description="Rule name to make the component's active rule first (sheet_metal_list_rules); "
        "a library rule is copied into the design. Default: keep the active rule (Fusion uses "
        "the library default for the first flange)"
    ),
]
RuleSourceArg = Annotated[
    RuleSource, Field(description="Where to look for `rule`: design, library or all (design first)")
]
OperationArg = Annotated[FlangeOperation, Field(description="new_body (default) or new_component")]
OrientationArg = Annotated[
    FlangeOrientation,
    Field(
        description="Side of the sketch the material grows: side_one (default), side_two, centered"
    ),
]
ComponentArg = Annotated[
    Ref | None, Field(description="Component or occurrence ref; default: the root component")
]


def _invalid(message: str, param: str) -> CallToolResult:
    return error_result(BridgeError(ErrorCode.INVALID_PARAMS, message, {"param": param}))


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="sheet_metal_capabilities")
    async def sheet_metal_capabilities() -> CallToolResult:
        """Report which sheet metal operations this Fusion API supports; changes nothing.

        Call it before a sheet metal workflow: flanges, unfold/refold, convert, bend listing
        and STEP export of a flat pattern use the preview Fusion API, which may be missing.
        sketch_bend is always false: Fusion has no API for Bend along a sketch line - model
        the bend with sheet_metal_edge_flange or sheet_metal_contour_flange instead.

        Returns {"rules", "flat_pattern", "flat_pattern_dxf", "flat_pattern_step", "convert",
        "base_flange", "contour_flange", "edge_flange", "list_bends", "unfold", "refold":
        bool, "sketch_bend": false, "preview_apis": [...], "fusion_version", "notes"}.
        """
        return await call_bridge(bridge, "sheet_metal.capabilities", {})

    @mcp.tool(name="sheet_metal_list_rules")
    async def sheet_metal_list_rules(
        source: Annotated[
            RuleSource, Field(description="design, library or all (default; design first)")
        ] = "all",
    ) -> CallToolResult:
        """List sheet metal rules: thickness, bend radius, gap and relief sizes in mm with
        their expressions, K-factor and relief shape.

        Returns {"rules": [{"name", "source", "units", "thickness": {"mm", "expression"},
        "bend_radius", "gap", "relief_width", "relief_depth", "relief_remnant", "k_factor",
        "relief_shape", "is_default" (library) | "is_used" (design)}], "count"}.
        """
        return await call_bridge(bridge, "sheet_metal.list_rules", {"source": source})

    @mcp.tool(name="sheet_metal_create_rule")
    async def sheet_metal_create_rule(
        name: Annotated[str, Field(min_length=1, description="New design rule name (unique)")],
        base_rule: Annotated[
            str | None,
            Field(description="Rule to copy (default: the library default rule)"),
        ] = None,
        base_source: Annotated[
            RuleSource, Field(description="Where to look for base_rule")
        ] = "all",
        thickness: Annotated[Length | None, Field(description="Sheet thickness")] = None,
        bend_radius: Annotated[
            Length | None, Field(description="Bend radius; e.g. 'Thickness * 1'")
        ] = None,
        gap: Annotated[Length | None, Field(description="Gap at corners / rips")] = None,
        relief_width: Annotated[Length | None, Field(description="Bend relief width")] = None,
        relief_depth: Annotated[Length | None, Field(description="Bend relief depth")] = None,
        relief_remnant: Annotated[Length | None, Field(description="Bend relief remnant")] = None,
        k_factor: Annotated[
            float | None, Field(ge=0.0, le=1.0, description="K-factor 0..1 for the flat pattern")
        ] = None,
        relief_shape: Annotated[
            ReliefShape | None, Field(description="straight, tear or round bend relief")
        ] = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Create a design sheet metal rule by copying a base rule and setting values.

        Units: a plain number is in `units` (default mm); a string is a Fusion expression and
        may use Thickness ("Thickness * 1.5") - write units in expressions ("2 mm"), a rule in
        inches reads bare numbers as inches. The rule is not used until sheet_metal_assign_rule
        or the `rule` argument of a flange tool.

        Errors: name already in the design, k_factor outside 0..1, a negative length ->
        INVALID_PARAMS; unknown base_rule -> ENTITY_NOT_FOUND. On any error no rule is left.

        Returns {"rule": <rule as in sheet_metal_list_rules>, "base": {"name", "source"}}.

        Example: sheet_metal_create_rule(name="Steel 2mm", thickness=2,
        bend_radius="Thickness * 1", k_factor=0.44, relief_shape="round")
        """
        params = to_params(
            name=name,
            base_rule=base_rule,
            base_source=base_source if base_rule is not None else None,
            thickness=thickness,
            bend_radius=bend_radius,
            gap=gap,
            relief_width=relief_width,
            relief_depth=relief_depth,
            relief_remnant=relief_remnant,
            k_factor=k_factor,
            relief_shape=relief_shape,
            units=units,
        )
        return await call_bridge(bridge, "sheet_metal.create_rule", params)

    @mcp.tool(name="sheet_metal_assign_rule")
    async def sheet_metal_assign_rule(
        rule: Annotated[str, Field(min_length=1, description="Rule name")],
        source: RuleSourceArg = "all",
        component: ComponentArg = None,
    ) -> CallToolResult:
        """Make a rule the active sheet metal rule of a component (default: root).

        A library rule is first copied into the design (the design never references the
        library). Fusion may refuse a rule on a component without sheet metal data
        (FUSION_API_ERROR) - then pass `rule` to the first flange tool instead. On error the
        previous rule is restored and a copy made by the call is deleted.

        Returns {"component", "rule": <rule>, "previous": name | null,
        "copied_from_library": bool}.
        """
        params = to_params(rule=rule, source=source, component=component)
        return await call_bridge(bridge, "sheet_metal.assign_rule", params)

    @mcp.tool(name="sheet_metal_convert")
    async def sheet_metal_convert(
        body: Annotated[Ref, Field(description="Solid body to convert (not yet sheet metal)")],
        base_face: Annotated[Ref, Field(description="Planar face of that body (inspect_query)")],
        rule: RuleArg = None,
        rule_source: RuleSourceArg = "all",
    ) -> CallToolResult:
        """Convert a solid plate body to sheet metal from a planar base face.

        The body thickness should match the rule thickness. Use it for a plate built with
        feature_extrude.

        Uses the preview Fusion API: when it is missing the tool fails with FUSION_API_ERROR
        reason=api_unavailable (nothing changed) - check sheet_metal_capabilities first.

        Errors: body already sheet metal, face of another body, non-planar face ->
        INVALID_PARAMS; Fusion refusing the conversion -> FUSION_API_ERROR (rolled back, the
        previous rule restored).

        Returns {"body": ref (may differ from the input), "component", "rule": <rule>,
        "is_sheet_metal": true}.
        """
        params = to_params(body=body, base_face=base_face, rule=rule, rule_source=rule_source)
        return await call_bridge(bridge, "sheet_metal.convert", params)

    @mcp.tool(name="sheet_metal_base_flange")
    async def sheet_metal_base_flange(
        profiles: Annotated[
            list[ProfileSpec],
            Field(min_length=1, description="Closed sketch profiles (sketch_profiles)"),
        ],
        orientation: OrientationArg = "side_one",
        operation: OperationArg = "new_body",
        rule: RuleArg = None,
        rule_source: RuleSourceArg = "all",
    ) -> CallToolResult:
        """Base flange: a flat sheet metal body from closed profiles; the thickness comes from
        the active rule.

        Recommended order: rule (sheet_metal_create_rule / `rule`) -> base or contour flange
        -> sheet_metal_edge_flange -> sheet_metal_create_flat_pattern ->
        sheet_metal_export_flat_pattern. Bend along a sketch line is not available in the
        Fusion API: model bends with edge or contour flanges.

        Uses the preview Fusion API: when it is missing the tool fails with FUSION_API_ERROR
        reason=api_unavailable (nothing changed) - check sheet_metal_capabilities first.

        Returns {"feature", "health", "created_bodies": [{ref, type, name, is_sheet_metal}],
        "modified_bodies", "removed_bodies", "rule": active rule name}.
        """
        params = to_params(
            profiles=profiles,
            orientation=orientation,
            operation=operation,
            rule=rule,
            rule_source=rule_source,
        )
        return await call_bridge(bridge, "sheet_metal.base_flange", params)

    @mcp.tool(name="sheet_metal_contour_flange")
    async def sheet_metal_contour_flange(
        profiles: Annotated[
            list[Ref],
            Field(
                min_length=1,
                description="Open sketch curves (e.g. an L of two lines), edges or open profile "
                "refs; connected curves are chained",
            ),
        ],
        distance: Annotated[Length, Field(description="Flange length along the sketch normal")],
        direction: Annotated[
            FlangeDirection,
            Field(description="one_side (default), symmetric or two_sides (needs distance_two)"),
        ] = "one_side",
        distance_two: Annotated[
            Length | None, Field(description="Second side length (two_sides only)")
        ] = None,
        orientation: OrientationArg = "side_one",
        bend_radius: Annotated[
            Length | None, Field(description="Bend radius override (default: the rule's)")
        ] = None,
        operation: OperationArg = "new_body",
        rule: RuleArg = None,
        rule_source: RuleSourceArg = "all",
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Contour flange: a bent sheet metal body from an open sketch profile (the profile
        is the bend line shape, the thickness and radius come from the rule).

        Recommended order: rule -> contour flange (e.g. an L-shaped open sketch, c05
        sketch_draw lines) -> sheet_metal_edge_flange on its outer edges (inspect_query) ->
        sheet_metal_list_bends / unfold / refold -> sheet_metal_create_flat_pattern ->
        sheet_metal_export_flat_pattern. Bend along a sketch line is not available in the
        Fusion API: add bends with edge flanges or draw them into the contour.


        Uses the preview Fusion API: when it is missing the tool fails with FUSION_API_ERROR
        reason=api_unavailable (nothing changed) - check sheet_metal_capabilities first.

        Errors: two_sides without distance_two or distance_two with another direction ->
        INVALID_PARAMS; a failing flange is rolled back and the previous rule restored.

        Returns {"feature", "health", "created_bodies": [{ref, type, name, is_sheet_metal}],
        "modified_bodies", "removed_bodies", "rule"}.

        Example: sheet_metal_contour_flange(profiles=["<line1>", "<line2>"], distance=40)
        """
        if direction == "two_sides" and distance_two is None:
            return _invalid("direction 'two_sides' needs 'distance_two'", "distance_two")
        if direction != "two_sides" and distance_two is not None:
            return _invalid(
                "parameter 'distance_two' applies only to direction 'two_sides'", "distance_two"
            )
        params = to_params(
            profiles=profiles,
            distance=distance,
            direction=direction,
            distance_two=distance_two,
            orientation=orientation,
            bend_radius=bend_radius,
            operation=operation,
            rule=rule,
            rule_source=rule_source,
            units=units,
        )
        return await call_bridge(bridge, "sheet_metal.contour_flange", params)

    @mcp.tool(name="sheet_metal_edge_flange")
    async def sheet_metal_edge_flange(
        edges: Annotated[
            list[Ref],
            Field(min_length=1, description="Linear outer edges of one sheet metal body"),
        ],
        height: Annotated[
            Length | None, Field(description="Flange height (or give to_entity)")
        ] = None,
        to_entity: Annotated[
            Ref | None,
            Field(description="Face / plane / vertex the flange reaches (single edge only)"),
        ] = None,
        to_offset: Annotated[Length | None, Field(description="Offset from to_entity")] = None,
        angle: Annotated[Angle, Field(description="Flange angle, degrees (default 90)")] = 90.0,
        width: Annotated[
            EdgeFlangeWidth,
            Field(
                description='Width, the same for all edges: {"type": "full"} (default), '
                '{"type": "symmetric", "width"}, {"type": "two_sided", "width1", "width2"}, '
                '{"type": "two_offsets", "offset1", "offset2"}'
            ),
        ] = FullWidth(type="full"),  # noqa: B008 - immutable pydantic default
        height_datum: Annotated[
            HeightDatum, Field(description="Height measured from inner, outer (default), tangent")
        ] = "outer",
        bend_position: Annotated[
            BendPosition,
            Field(description="start_edge (default), inside, outside or tangent_to_side"),
        ] = "start_edge",
        mitered_corners: Annotated[bool, Field(description="Miter adjacent flanges")] = True,
        flip: Annotated[bool, Field(description="Flip the flange direction")] = False,
        bend_radius: Annotated[
            Length | None, Field(description="Bend radius override (default: the rule's)")
        ] = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Edge flange: bend up a wall from linear edges of a sheet metal body.

        Exactly one of `height` and `to_entity`. Refs: edges from inspect_query on the latest
        body (refs change after every flange - take them from the results again). Bend along
        a sketch line is not available in the Fusion API: an edge flange is the way to add a
        bend.

        Uses the preview Fusion API: when it is missing the tool fails with FUSION_API_ERROR
        reason=api_unavailable (nothing changed) - check sheet_metal_capabilities first.

        Errors: both / neither of height and to_entity, to_entity with several edges, an
        edge of a non-sheet-metal body or of several bodies, a non-linear edge ->
        INVALID_PARAMS; a failing flange is rolled back.

        Returns {"feature", "health", "created_bodies", "modified_bodies": [{ref, type, name,
        is_sheet_metal}], "removed_bodies", "rule"}.

        Example: sheet_metal_edge_flange(edges=["<edge>"], height=20)
        """
        if (height is None) == (to_entity is None):
            return _invalid("pass exactly one of 'height' and 'to_entity'", "height")
        if to_entity is not None and len(edges) > 1:
            return _invalid("'to_entity' works with a single edge", "to_entity")
        if to_offset is not None and to_entity is None:
            return _invalid("parameter 'to_offset' needs 'to_entity'", "to_offset")
        params = to_params(
            edges=edges,
            height=height,
            to_entity=to_entity,
            to_offset=to_offset,
            angle=angle,
            width=width,
            height_datum=height_datum,
            bend_position=bend_position,
            mitered_corners=mitered_corners,
            flip=flip,
            bend_radius=bend_radius,
            units=units,
        )
        return await call_bridge(bridge, "sheet_metal.edge_flange", params)

    @mcp.tool(name="sheet_metal_list_bends")
    async def sheet_metal_list_bends(
        body: Annotated[Ref, Field(description="Sheet metal body")],
    ) -> CallToolResult:
        """List the bends of a sheet metal body: inner and outer bend face refs and the inner
        radius in mm (use the faces for sheet_metal_unfold).

        Uses the preview Fusion API: when it is missing the tool fails with FUSION_API_ERROR
        reason=api_unavailable (nothing changed) - check sheet_metal_capabilities first.

        Returns {"body", "bends": [{"inner", "outer", "radius_mm"}], "count"}.
        """
        return await call_bridge(bridge, "sheet_metal.list_bends", {"body": body})

    @mcp.tool(name="sheet_metal_unfold")
    async def sheet_metal_unfold(
        stationary_face: Annotated[Ref, Field(description="Planar face that stays in place")],
        bend_faces: Annotated[
            list[Ref] | None,
            Field(min_length=1, description="Bend faces to unfold (default: all bends)"),
        ] = None,
    ) -> CallToolResult:
        """Unfold all bends (or the given bend faces from sheet_metal_list_bends) of a sheet
        metal body about a stationary face, e.g. to add cuts across a bend; refold with
        sheet_metal_refold.

        Uses the preview Fusion API: when it is missing the tool fails with FUSION_API_ERROR
        reason=api_unavailable (nothing changed) - check sheet_metal_capabilities first.

        Returns {"feature": {ref of the UnfoldFeature, ...}, "health", "created_bodies",
        "modified_bodies", "removed_bodies", "rule"}.
        """
        params = to_params(stationary_face=stationary_face, bend_faces=bend_faces)
        return await call_bridge(bridge, "sheet_metal.unfold", params)

    @mcp.tool(name="sheet_metal_refold")
    async def sheet_metal_refold(
        unfold: Annotated[Ref, Field(description="UnfoldFeature ref from sheet_metal_unfold")],
    ) -> CallToolResult:
        """Refold the bends of an unfold feature.

        Uses the preview Fusion API: when it is missing the tool fails with FUSION_API_ERROR
        reason=api_unavailable (nothing changed) - check sheet_metal_capabilities first.

        Errors: a ref that is not an UnfoldFeature -> INVALID_PARAMS.

        Returns {"feature", "health", "created_bodies", "modified_bodies", "removed_bodies",
        "rule"}.
        """
        return await call_bridge(bridge, "sheet_metal.refold", {"unfold": unfold})

    @mcp.tool(name="sheet_metal_create_flat_pattern")
    async def sheet_metal_create_flat_pattern(
        stationary_face: Annotated[
            Ref, Field(description="Face of the sheet metal body that lies flat (e.g. the top)")
        ],
        replace: Annotated[
            bool, Field(description="Delete the component's existing flat pattern first")
        ] = False,
    ) -> CallToolResult:
        """Create the flat pattern of the component of a sheet metal body (one per
        component) and return its size, thickness and bends. The design workspace is active
        again afterwards.

        Errors: a face of a non-sheet-metal body, an existing flat pattern without
        replace=true -> INVALID_PARAMS (the existing one is kept). With replace=true the old
        flat pattern is deleted first; if the new one then fails, none is left.

        Returns {"component", "exists": true, "size_mm": [x, y], "thickness_mm",
        "bend_count", "bends": [{"angle_deg", "is_up"}], "replaced"}.
        """
        params = to_params(stationary_face=stationary_face, replace=replace)
        return await call_bridge(bridge, "sheet_metal.create_flat_pattern", params)

    @mcp.tool(name="sheet_metal_get_flat_pattern")
    async def sheet_metal_get_flat_pattern(component: ComponentArg = None) -> CallToolResult:
        """Read a component's flat pattern: size, thickness and bends, or exists=false.

        Returns {"component", "exists", "size_mm", "thickness_mm", "bend_count", "bends"}.
        """
        params = to_params(component=component)
        return await call_bridge(bridge, "sheet_metal.get_flat_pattern", params)

    @mcp.tool(name="sheet_metal_delete_flat_pattern")
    async def sheet_metal_delete_flat_pattern(component: ComponentArg = None) -> CallToolResult:
        """Delete a component's flat pattern.

        Errors: no flat pattern -> ENTITY_NOT_FOUND.

        Returns {"component", "deleted": true}.
        """
        params = to_params(component=component)
        return await call_bridge(bridge, "sheet_metal.delete_flat_pattern", params)

    @mcp.tool(name="sheet_metal_export_flat_pattern")
    async def sheet_metal_export_flat_pattern(
        path: Annotated[
            str, Field(description="Absolute file path, .dxf or .step/.stp; the folder must exist")
        ],
        component: ComponentArg = None,
        format: Annotated[
            FlatExportFormat | None, Field(description="dxf or step; default from the extension")
        ] = None,
        file_units: Annotated[FileUnits, Field(description="DXF file units (default mm)")] = "mm",
        bend_lines: Annotated[bool, Field(description="DXF: export bend (center) lines")] = True,
        extent_lines: Annotated[bool, Field(description="DXF: export extent lines")] = True,
        splines_to_polylines: Annotated[
            bool, Field(description="DXF: convert splines to polylines")
        ] = False,
        polyline_tolerance: Annotated[
            float, Field(gt=0, description="DXF: polyline tolerance in `units` (default 0.1)")
        ] = 0.1,
        units: UnitsArg = "mm",
        overwrite: Annotated[
            bool, Field(description="Replace an existing file (deleted before the export)")
        ] = False,
    ) -> CallToolResult:
        """Export a component's flat pattern to DXF (bend / extent lines, file units,
        optional spline-to-polyline) or STEP. STEP uses the preview Fusion API
        (api_unavailable when missing; see sheet_metal_capabilities).

        The path must be absolute with a matching extension; an existing file is replaced
        only with overwrite=true. Errors: no flat pattern -> ENTITY_NOT_FOUND (create it with
        sheet_metal_create_flat_pattern).

        Returns {"files": [{"path", "size_bytes"}], "format", "scope": component}.

        Example: sheet_metal_export_flat_pattern(path="C:/out/bracket.dxf")
        """
        try:
            check_path(path, format, FLAT_EXPORT_FORMATS, overwrite)
        except BridgeError as error:
            return error_result(error)
        params = to_params(
            path=path,
            component=component,
            format=format,
            file_units=file_units,
            bend_lines=bend_lines,
            extent_lines=extent_lines,
            splines_to_polylines=splines_to_polylines,
            polyline_tolerance=polyline_tolerance,
            units=units,
            overwrite=overwrite,
        )
        return await call_bridge(
            bridge, "sheet_metal.export_flat_pattern", params, EXPORT_TIMEOUT_S
        )

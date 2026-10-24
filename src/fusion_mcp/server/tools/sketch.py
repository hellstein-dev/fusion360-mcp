"""``sketch_*`` tools: create, read and edit 2D sketches; profile refs for features (c05 D12).

Coordinates are sketch space in mm (`[x, y]`), dimension values mm/degrees or Fusion
expressions. Every mutating tool returns `status` and, unless `return_profiles=false`,
the current `profiles`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult
from pydantic import Field, ValidationError

from fusion_mcp.server.app import call_bridge
from fusion_mcp.server.bridge import Bridge
from fusion_mcp.server.tools.sketch_schema import (
    MAX_ITEMS,
    PATTERN_ADAPTER,
    XY,
    Angle,
    ConstraintItem,
    DimensionItem,
    DimensionUpdate,
    DrawItem,
    Length,
    Point,
    SketchUnits,
    dump,
)

SketchRef = Annotated[str, Field(min_length=1, description="Sketch ref (from sketch_create)")]
Ref = Annotated[str, Field(min_length=1)]
Refs = Annotated[list[Ref], Field(min_length=1, max_length=MAX_ITEMS)]
GetSection = Literal["points", "curves", "constraints", "dimensions", "texts", "profiles"]
ProjectMode = Literal["project", "include", "intersect", "cut_edges"]
ReturnProfiles = Annotated[
    bool, Field(description="Add the current profiles to the result (false = faster)")
]


def _given(**params: Any) -> dict[str, Any]:
    """Only the parameters the agent set: ``None`` means "not given"."""
    return {k: v for k, v in params.items() if v is not None}


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="sketch_create")
    async def sketch_create(
        plane: Annotated[
            str,
            Field(
                min_length=1,
                description="origin:XY | origin:XZ | origin:YZ, a construction plane ref or a "
                "planar face ref",
            ),
        ],
        name: Annotated[str | None, Field(min_length=1, description="Sketch name")] = None,
        component: Annotated[
            str | None, Field(min_length=1, description="Component ref; default: root")
        ] = None,
        project_edges: Annotated[
            bool, Field(description="On a face: project its edges into the sketch")
        ] = False,
    ) -> CallToolResult:
        """Create a 2D sketch on an origin plane, a construction plane or a planar face.

        All sketch tools work in SKETCH SPACE, mm: `[x, y]` on the sketch plane. Fusion picks
        the sketch axes; the result `transform` = {origin, x_axis, y_axis, normal} (model mm
        and unit vectors) maps them: model = origin + x*x_axis + y*y_axis.
        Pitfall: on `origin:XZ` the sketch Y axis is model -Z (so a point drawn at y = 10 is
        at model z = -10) — always read `transform`, or give points as
        {"model": [x, y, z]} to sketch_draw. On `origin:XY` sketch x/y = model x/y.

        A planar face gives a clean sketch unless `project_edges=true`. A non-planar face
        fails with INVALID_PARAMS (`details.reason = "face_not_planar"`).

        Returns {"sketch": {ref}, "name", "plane", "component", "transform"}.

        Example: sketch_create(plane="origin:XY", name="Base") then sketch_draw(sketch=<ref>).
        """
        params = _given(plane=plane, name=name, component=component)
        params["project_edges"] = project_edges
        return await call_bridge(bridge, "sketch.create", params)

    @mcp.tool(name="sketch_list")
    async def sketch_list(
        component: Annotated[
            str | None, Field(min_length=1, description="Component ref; default: all components")
        ] = None,
    ) -> CallToolResult:
        """List sketches of the design (or of one component).

        Returns {"sketches": [{"sketch": {ref}, "name", "component", "plane",
        "fully_constrained", "curve_count", "profile_count", "visible"}]}.

        Example: sketch_list() -> pick the sketch ref, then sketch_get / sketch_profiles.
        """
        return await call_bridge(bridge, "sketch.list", _given(component=component))

    @mcp.tool(name="sketch_get")
    async def sketch_get(
        sketch: SketchRef,
        include: Annotated[
            list[GetSection] | None, Field(min_length=1, description="Sections; default: all")
        ] = None,
        limit: Annotated[int, Field(ge=1, le=5000, description="Max items per section")] = 500,
    ) -> CallToolResult:
        """Full state of a sketch, coordinates in sketch-space mm.

        Sections: `points` [{ref, xy, fixed, reference, fully_constrained}], `curves`
        [{ref, type: line|arc|circle|ellipse|elliptical_arc|fitted_spline|fixed_spline|conic,
        construction, fixed, reference (projected), fully_constrained, length_mm, start, end,
        geometry (line {start, end}; circle {center, radius}; arc {center, radius, start, end,
        start_angle, sweep}; ...)}], `constraints` [{ref, type, entities}], `dimensions`
        [{ref, type, entities, value (mm|deg), unit, expression, parameter, driving}],
        `texts`, `profiles` (as sketch_profiles). Each section is an object
        {items: [≤ limit entries as above], total, truncated} — `truncated: true` when cut.

        Returns {"sketch", the requested sections, "transform" (see sketch_create),
        "status": {fully_constrained, under_constrained_count, under_constrained: [≤ 50
        refs]}}.

        Example: sketch_get(sketch=<ref>, include=["dimensions"]) -> find a dimension ref for
        sketch_dimension_set.
        """
        params = _given(sketch=sketch, include=include)
        params["limit"] = limit
        return await call_bridge(bridge, "sketch.get", params)

    @mcp.tool(name="sketch_profiles")
    async def sketch_profiles(
        sketch: SketchRef,
        contains: Annotated[
            XY | None, Field(description="Only the profile containing this [x, y] sketch mm")
        ] = None,
    ) -> CallToolResult:
        """Closed regions (profiles) of a sketch — the input of extrude/revolve/sweep/loft.

        Each profile: {"ref": {"ref": "profile:<sketch token>@<x>,<y>", "type": "Profile",
        "name": "P1"}, "sketch", "point": [x, y] (an interior point of the region),
        "area_mm2", "centroid", "bbox": {min, max}, "outer_loop": {"curves": [refs]},
        "inner_loops": [{"curves": [refs]}] (holes)}. All coordinates sketch mm.

        A profile has no persistent id in Fusion, so its ref is the sketch plus a point inside
        the region: `profile:<sketch token>@x,y`. Pass this string as the profile of a feature
        (or {"sketch": ref, "point": [x, y]}). It survives moderate dimension changes; if the
        region moved away from the point the feature fails with ENTITY_NOT_FOUND listing the
        current profiles — call sketch_profiles(contains=[x, y]) again and use the new ref.
        `name` (P1, P2...) is only the order in this answer, never an address.

        Returns {"profiles": [...]}.

        Example: plate with 4 holes -> 5 profiles; the plate is the one with 4 `inner_loops`;
        sketch_profiles(sketch=<ref>, contains=[0, 0]) -> just the region around [0, 0].
        """
        return await call_bridge(
            bridge, "sketch.profiles", _given(sketch=sketch, contains=contains)
        )

    @mcp.tool(name="sketch_draw")
    async def sketch_draw(
        sketch: SketchRef,
        items: Annotated[
            list[DrawItem],
            Field(min_length=1, max_length=MAX_ITEMS, description="Geometry, drawn in order"),
        ],
        constraints: Annotated[
            list[ConstraintItem] | None,
            Field(max_length=MAX_ITEMS, description="Applied after all items, in order"),
        ] = None,
        dimensions: Annotated[
            list[DimensionItem] | None,
            Field(max_length=MAX_ITEMS, description="Applied after constraints, in order"),
        ] = None,
        units: Annotated[
            SketchUnits, Field(description="Unit of numeric coordinates and sizes")
        ] = "mm",
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Draw many sketch items, then constraints, then dimensions — one call, all or
        nothing (on any error the sketch is restored and `details` = {phase:
        geometry|constraints|dimensions, index, id}).

        Coordinates: `[x, y]` sketch mm (numbers only — expressions like "w/2" are refused:
        draw approximately and fix exact sizes with `dimensions`), {"model": [x, y, z]} model
        mm (projected on the sketch plane; mind sketch_create's XZ pitfall), or a ref of an
        existing point / curve end. A point given by ref is shared (no coincident needed).

        Local ids: give an item `id` (letters, digits, _; not `origin`) and later items,
        constraints and dimensions of THIS call can use `id` or `id.<sub>`; `origin` is the
        sketch origin point. Local ids live one call only: the result `refs` maps every local
        path to a real ref — use those refs in later calls.

        Every item: {"kind", "id"?, "construction": false, ...}:
        | kind / mode | fields | sub-ids |
        | point | at | — |
        | line | start, end | start, end |
        | polyline | points (≥2), closed=false | e0..e{n-1} (edges), v0..v{n-1} |
        | rectangle two_point (default) | corner1, corner2 | bottom,right,top,left, bl,br,tr,tl |
        | rectangle center | center, corner | as two_point + center, diagonal |
        | rectangle three_point | p1, p2, p3 | e0..e3, v0..v3 |
        | circle center_radius (default) | center, radius | center |
        | circle two_point | p1, p2 (diameter ends) | center |
        | circle three_point | p1, p2, p3 | center |
        | arc three_point (default) | start, mid, end | start, end, center |
        | arc center_start_sweep | center, start, sweep (deg, + = CCW) | start, end, center |
        | arc tangent | from (curve end, e.g. "l1.end"), to | start, end, center |
        | ellipse | center, major_radius, minor_radius, angle=0 | center |
        | polygon | center, radius, sides 3..64, mode=inscribed|circumscribed, angle=0,
          constrain=true | e0.., v0.., center, circle |
        | spline | points (≥2 fit points), closed=false | start, end, fp0.. |
        | slot center_to_center (default) / overall | p1, p2, width | side0, side1, end0,
          end1, axis, c0, c1 |
        | text | text, height, corner, size=[w,h]?, font?, h_align=left, v_align=bottom | — |

        constraints: [{"type": coincident (point, entity) | horizontal / vertical (line, or
        points [p1, p2]) | parallel / perpendicular / collinear (line1, line2) | tangent /
        equal / concentric (curve1, curve2) | midpoint (point, curve) | symmetry (entity1,
        entity2, line) | smooth (spline, curve) | fix (entity, fixed=true)}].
        dimensions: [{"type": distance (line, or point1 + point2; orientation=aligned|
        horizontal|vertical) | offset (line, entity) | radial / diameter (curve) | angular
        (line1, line2) | ellipse_major / ellipse_minor (ellipse), "value": 25 (mm/deg) or
        "plate_w * 0.6", "parameter_name"?, "driven"=false, "text_at"?}]. A named dimension
        (parameter_name) can be used by later dimensions' expressions in the same call.

        Returns {"sketch", "refs": {"r": {ref}, "r.bottom": {ref}, ...}, "constraints",
        "dimensions", "status" {fully_constrained, ...}, "profiles", "warnings"}.

        Example — fully constrained 100 x 60 plate centred on the origin with a hole:
        sketch_draw(sketch=<ref>, items=[
          {"kind": "rectangle", "mode": "center", "id": "r", "center": "origin",
           "corner": [50, 30]},
          {"kind": "circle", "id": "h", "center": [30, 15], "radius": 4}],
         dimensions=[
          {"type": "distance", "line": "r.bottom", "value": 100, "parameter_name": "plate_w"},
          {"type": "distance", "line": "r.left", "value": "plate_w * 0.6"},
          {"type": "diameter", "curve": "h", "value": 8},
          {"type": "distance", "point1": "origin", "point2": "h.center",
           "orientation": "horizontal", "value": 30},
          {"type": "distance", "point1": "origin", "point2": "h.center",
           "orientation": "vertical", "value": 15}])
        -> status.fully_constrained = true; the plate profile has one inner loop.
        """
        params = {
            "sketch": sketch,
            "items": [dump(item) for item in items],
            "constraints": [dump(c) for c in constraints or []],
            "dimensions": [dump(d) for d in dimensions or []],
            "units": units,
            "return_profiles": return_profiles,
        }
        return await call_bridge(bridge, "sketch.draw", params)

    @mcp.tool(name="sketch_constrain")
    async def sketch_constrain(
        sketch: SketchRef,
        constraints: Annotated[list[ConstraintItem], Field(min_length=1, max_length=MAX_ITEMS)],
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Add geometric constraints in order, all or nothing.

        Entities are refs from earlier results (sketch_draw `refs`, sketch_get) or `origin`.
        Types: coincident (point, entity) | horizontal / vertical (line, or points: [p1, p2])
        | parallel / perpendicular / collinear (line1, line2) | tangent / equal / concentric
        (curve1, curve2) | midpoint (point, curve) | symmetry (entity1, entity2, line) |
        smooth (spline, curve) | fix (entity, fixed=true — sets isFixed, no constraint ref).
        Wrong entity types fail with INVALID_PARAMS (`details.expected`); an over-constrained
        sketch fails with FUSION_API_ERROR and `details.index`; nothing is kept on failure.

        Returns {"constraints": [{ref, type}], "status", "profiles"}.

        Example: sketch_constrain(sketch=<ref>, constraints=[{"type": "horizontal",
        "points": [<p1 ref>, <p2 ref>]}, {"type": "equal", "curve1": <c1>, "curve2": <c2>}]).
        """
        params = {
            "sketch": sketch,
            "constraints": [dump(c) for c in constraints],
            "return_profiles": return_profiles,
        }
        return await call_bridge(bridge, "sketch.constrain", params)

    @mcp.tool(name="sketch_dimension")
    async def sketch_dimension(
        sketch: SketchRef,
        dimensions: Annotated[list[DimensionItem], Field(min_length=1, max_length=MAX_ITEMS)],
        units: Annotated[SketchUnits, Field(description="Unit of numeric `text_at`")] = "mm",
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Add driving or driven dimensions in order, all or nothing — this is how sizes
        become exact and parametric.

        `value`: a number is mm (degrees for angular) -> "25 mm"; a string is a Fusion
        expression kept as is ("plate_w * 0.6", "wall + 2 mm"); invalid expressions fail with
        INVALID_PARAMS before anything is created. `parameter_name` renames the dimension's
        parameter so later dimensions (in this call too) and parameter_update can use it.
        `driven=true` makes a reference dimension that only measures (no `value`).
        Types: distance (line, or point1 + point2; orientation aligned|horizontal|vertical) |
        offset (line, entity: parallel line or point) | radial / diameter (curve) | angular
        (line1, line2; `text_at` picks the measured quadrant) | ellipse_major / ellipse_minor
        (ellipse).

        Returns {"dimensions": [{ref, type, value, unit, expression, parameter, driving}],
        "status", "profiles"}.

        Example: sketch_dimension(sketch=<ref>, dimensions=[{"type": "distance", "line": <l>,
        "value": 100, "parameter_name": "plate_w"}, {"type": "radial", "curve": <arc>,
        "value": "plate_w / 20"}]).
        """
        params = {
            "sketch": sketch,
            "dimensions": [dump(d) for d in dimensions],
            "units": units,
            "return_profiles": return_profiles,
        }
        return await call_bridge(bridge, "sketch.dimension", params)

    @mcp.tool(name="sketch_dimension_set")
    async def sketch_dimension_set(
        sketch: SketchRef,
        updates: Annotated[list[DimensionUpdate], Field(min_length=1, max_length=MAX_ITEMS)],
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Change existing dimensions: value/expression, driven state or parameter name.
        All or nothing: if any update fails the earlier ones get their previous values back.

        `value`: number = mm/degrees, string = expression. Changing a dimension re-solves the
        sketch; profile refs taken before stay usable for moderate changes (re-read with
        sketch_profiles(contains=...) if a feature reports ENTITY_NOT_FOUND).

        Returns {"dimensions": [...as in sketch_dimension], "status", "profiles"}.

        Example: sketch_dimension_set(sketch=<ref>, updates=[{"dimension": <dim ref>,
        "value": "plate_w + 10"}]).
        """
        params = {
            "sketch": sketch,
            "updates": [dump(u) for u in updates],
            "return_profiles": return_profiles,
        }
        return await call_bridge(bridge, "sketch.dimension_set", params)

    @mcp.tool(name="sketch_set_construction")
    async def sketch_set_construction(
        sketch: SketchRef,
        curves: Refs,
        construction: Annotated[bool, Field(description="true = construction, false = normal")] = (
            True
        ),
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Turn curves into construction geometry (not part of profiles) or back. All or
        nothing.

        Returns {"curves": [...], "status", "profiles"}.

        Example: sketch_set_construction(sketch=<ref>, curves=[<line ref>]) -> the profiles
        bounded by that line merge.
        """
        params = {
            "sketch": sketch,
            "curves": curves,
            "construction": construction,
            "return_profiles": return_profiles,
        }
        return await call_bridge(bridge, "sketch.set_construction", params)

    @mcp.tool(name="sketch_delete")
    async def sketch_delete(
        sketch: SketchRef,
        entities: Annotated[
            Refs, Field(description="Curves, points, constraints, dimensions, texts")
        ],
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Delete sketch entities (not the sketch itself).

        All refs are checked first: a stale or foreign ref fails with ENTITY_NOT_FOUND /
        INVALID_PARAMS and nothing is deleted. Then dimensions, constraints, texts, curves and
        points are deleted in that order; entities Fusion already removed with another one
        (e.g. a constraint of a deleted line) are listed in `cascaded`.

        NOT ATOMIC (the only such sketch tool): Fusion cannot undo a deletion. If Fusion
        itself fails in the middle of the batch, the entities deleted so far stay deleted —
        the error is FUSION_API_ERROR with `details.deleted`; re-read the sketch with
        sketch_get before continuing.

        Returns {"deleted": [refs], "cascaded": [refs], "status", "profiles"}.

        Example: sketch_delete(sketch=<ref>, entities=[<line ref>, <constraint ref>]).
        """
        params = {"sketch": sketch, "entities": entities, "return_profiles": return_profiles}
        return await call_bridge(bridge, "sketch.delete", params)

    @mcp.tool(name="sketch_transform")
    async def sketch_transform(
        sketch: SketchRef,
        entities: Refs,
        translate: Annotated[
            XY | None,
            Field(description="[dx, dy] sketch mm, applied after rotation; default [0, 0]"),
        ] = None,
        rotate: Annotated[Angle, Field(description="Degrees, positive = counter-clockwise")] = 0,
        about: Annotated[Point | None, Field(description="Rotation centre; default [0, 0]")] = None,
        copy: Annotated[bool, Field(description="Transform a copy, keep the originals")] = False,
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Move/rotate sketch entities (or copies of them): rotate by `rotate` degrees about
        `about`, then translate by `translate` mm. All or nothing.

        Entities tied by constraints/dimensions may be pulled back by the solver: the result
        has the actual solved geometry, check it.

        Returns {"entities": [...] (moved) | "created": [...] (copy=true), "status",
        "profiles"}.

        Example: sketch_transform(sketch=<ref>, entities=[<circle ref>], translate=[30, 0],
        copy=true) -> a second circle 30 mm to the right.
        """
        params = {
            "sketch": sketch,
            "entities": entities,
            "translate": translate if translate is not None else [0, 0],
            "rotate": rotate,
            "about": _point(about) if about is not None else [0, 0],
            "copy": copy,
            "return_profiles": return_profiles,
        }
        return await call_bridge(bridge, "sketch.transform", params)

    @mcp.tool(name="sketch_offset")
    async def sketch_offset(
        sketch: SketchRef,
        curves: Refs,
        distance: Annotated[Length, Field(description="Offset distance: mm or expression")],
        side_point: Annotated[Point, Field(description="A point on the side to offset to")],
        chain: Annotated[
            bool, Field(description="Extend each curve to its connected chain")
        ] = True,
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Offset curves (with `chain=true` the whole connected loop) by `distance` toward
        `side_point`. All or nothing.

        When the Fusion version supports it the offset is parametric (`parametric: true`, a
        dimension carries the distance expression, e.g. "wall"); otherwise plain copies are
        made (`parametric: false`).

        Returns {"created": [refs], "parametric", "dimension"?, "status", "profiles"}.

        Example: inner wall of a closed outline: sketch_offset(sketch=<ref>, curves=[<one
        side>], distance="wall", side_point=[0, 0]).
        """
        params = {
            "sketch": sketch,
            "curves": curves,
            "distance": distance,
            "side_point": _point(side_point),
            "chain": chain,
            "return_profiles": return_profiles,
        }
        return await call_bridge(bridge, "sketch.offset", params)

    @mcp.tool(name="sketch_trim")
    async def sketch_trim(
        sketch: SketchRef,
        curve: Annotated[Ref, Field(description="Curve to trim")],
        point: Annotated[Point, Field(description="Near the segment to remove (≤ 5 mm)")],
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Trim at the segment nearest `point` (cut back to the neighbouring intersections).
        ONE curve per call: trimming replaces the curve and Fusion cannot undo it inside a
        batch, so the old ref becomes stale — use the refs in the result for the next trim.
        `point` farther than 5 mm from the curve fails with INVALID_PARAMS.

        Returns {"created": [refs of the remaining pieces], "deleted": [the original ref, if
        it was replaced], "status", "profiles"}.

        Example: sketch_trim(sketch=<ref>, curve=<line ref>, point=[10, 0]).
        """
        params = {
            "sketch": sketch,
            "curve": curve,
            "point": _point(point),
            "return_profiles": return_profiles,
        }
        return await call_bridge(bridge, "sketch.trim", params)

    @mcp.tool(name="sketch_extend")
    async def sketch_extend(
        sketch: SketchRef,
        curve: Annotated[Ref, Field(description="Curve to extend")],
        point: Annotated[Point, Field(description="Near the end to extend (≤ 5 mm)")],
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Extend the end nearest `point` to the next intersecting curve. ONE curve per call
        (the curve may be replaced, so use the refs in the result). `point`
        farther than 5 mm from the curve fails with INVALID_PARAMS.

        Returns {"created": [refs], "deleted": [the original ref, if replaced], "status",
        "profiles"}.

        Example: sketch_extend(sketch=<ref>, curve=<line ref>, point=[39, 0]).
        """
        params = {
            "sketch": sketch,
            "curve": curve,
            "point": _point(point),
            "return_profiles": return_profiles,
        }
        return await call_bridge(bridge, "sketch.extend", params)

    @mcp.tool(name="sketch_fillet")
    async def sketch_fillet(
        sketch: SketchRef,
        curve1: Ref,
        curve2: Ref,
        radius: Annotated[Length, Field(description="Fillet radius: mm or expression")],
        point1: Annotated[
            Point | None, Field(description="Pick point on curve1; default: shared end")
        ] = None,
        point2: Annotated[
            Point | None, Field(description="Pick point on curve2; default: shared end")
        ] = None,
        dimension: Annotated[
            bool, Field(description="Add a radial dimension with the radius expression")
        ] = True,
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Round ONE corner between two curves with a tangent arc of `radius`. One corner per
        call: Fusion trims both curves, so their old refs may become stale — take the new
        ones from the result or sketch_get before the next fillet. Pick points default to the
        curves' shared end (or nearest ends).

        Not fully atomic: Fusion cannot undo the fillet itself. If the radial dimension
        (`dimension=True`) cannot be added, only the dimension is rolled back — the arc stays
        and the call succeeds with warning `fillet_dimension_failed`. Dimension the arc
        yourself with sketch_dimension, or delete it with sketch_delete.

        Returns {"arc": {ref}, "dimension"?: {...}, "status", "profiles"}.

        Example: sketch_fillet(sketch=<ref>, curve1=<bottom>, curve2=<right>,
        radius="r_corner").
        """
        params = _given(
            sketch=sketch,
            curve1=curve1,
            curve2=curve2,
            radius=radius,
            point1=_point(point1),
            point2=_point(point2),
        )
        params["dimension"] = dimension
        params["return_profiles"] = return_profiles
        return await call_bridge(bridge, "sketch.fillet", params)

    @mcp.tool(name="sketch_mirror")
    async def sketch_mirror(
        sketch: SketchRef,
        entities: Annotated[Refs, Field(description="Points, lines, arcs, circles, splines")],
        axis: Annotated[Ref, Field(description="Mirror line (often a construction line)")],
        constrain: Annotated[
            bool, Field(description="Add symmetry constraints to keep the copies mirrored")
        ] = True,
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Mirror entities about a line, all or nothing. Shared points stay shared and points
        on the axis are reused; other curve types fail with INVALID_PARAMS.

        Returns {"mapping": {<source ref>: <mirrored ref>}, "constraints", "status",
        "profiles"}.

        Example: sketch_mirror(sketch=<ref>, entities=[<l1>, <l2>], axis=<centre line>).
        """
        params = {
            "sketch": sketch,
            "entities": entities,
            "axis": axis,
            "constrain": constrain,
            "return_profiles": return_profiles,
        }
        return await call_bridge(bridge, "sketch.mirror", params)

    @mcp.tool(name="sketch_pattern")
    async def sketch_pattern(
        sketch: SketchRef,
        entities: Refs,
        kind: Annotated[Literal["rectangular", "circular"], Field(description="Pattern type")],
        count_x: Annotated[
            int | None, Field(ge=1, description="rectangular: instances along X")
        ] = None,
        spacing_x: Annotated[Length | None, Field(description="rectangular: X step")] = None,
        count_y: Annotated[int | None, Field(ge=1, description="rectangular: default 1")] = None,
        spacing_y: Annotated[Length | None, Field(description="rectangular: default 0")] = None,
        direction: Annotated[
            str | None, Field(min_length=1, description="rectangular: line ref for X direction")
        ] = None,
        center: Annotated[Point | None, Field(description="circular: centre point")] = None,
        count: Annotated[int | None, Field(ge=2, description="circular: instances")] = None,
        angle: Annotated[
            Angle | None,
            Field(description="rectangular: X direction (default 0); circular: total (360)"),
        ] = None,
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Pattern sketch entities, all or nothing. Counts include the original.

        - rectangular: count_x (≥1), spacing_x (Length), count_y=1, spacing_y=0, angle=0
          (degrees from sketch X) or direction (a line ref).
        - circular: center (point), count (≥2), angle=360 (total, degrees).
        Spacing/angle: number = mm/deg, string = expression. When the Fusion version has the
        pattern constraint the result is parametric (`parametric: true`, `constraint`);
        otherwise plain copies (`parametric: false`).

        Returns {"instances": [[refs]...], "parametric", "constraint"?, "status", "profiles"}.

        Example: 6 holes on a circle: sketch_pattern(sketch=<ref>, entities=[<circle>],
        kind="circular", center="origin", count=6).
        """
        fields = _given(
            kind=kind,
            count_x=count_x,
            spacing_x=spacing_x,
            count_y=count_y,
            spacing_y=spacing_y,
            direction=direction,
            center=center,
            count=count,
            angle=angle,
        )
        try:
            pattern = PATTERN_ADAPTER.validate_python(fields)
        except ValidationError as exc:
            raise ToolError(f"invalid {kind} pattern: {exc}") from exc
        params = {"sketch": sketch, "entities": entities, **dump(pattern)}
        params["return_profiles"] = return_profiles
        return await call_bridge(bridge, "sketch.pattern", params)

    @mcp.tool(name="sketch_project")
    async def sketch_project(
        sketch: SketchRef,
        entities: Annotated[
            Refs,
            Field(description="Edges, faces, vertices, bodies, other sketches' curves, origin:*"),
        ],
        mode: Annotated[
            ProjectMode,
            Field(
                description="project | include (linked copy) | intersect (with the sketch "
                "plane) | cut_edges (body section)"
            ),
        ] = "project",
        return_profiles: ReturnProfiles = True,
    ) -> CallToolResult:
        """Bring model geometry into the sketch as reference curves (to dimension or
        constrain against, or to close profiles). All or nothing.

        Modes: `project` (projection onto the sketch plane; a body projects all its edges),
        `include` (linked copy of curves), `intersect` (intersection of the entities with the
        sketch plane), `cut_edges` (section of bodies by the sketch plane).

        Returns {"created": {<input ref>: [refs]}, "status", "profiles"}.

        Example: sketch_project(sketch=<ref>, entities=[<top face ref>]) -> the face outline
        as reference curves.
        """
        params = {
            "sketch": sketch,
            "entities": entities,
            "mode": mode,
            "return_profiles": return_profiles,
        }
        return await call_bridge(bridge, "sketch.project", params)


def _point(value: Any) -> Any:
    """Wire form of a validated ``Point`` (``{"model": [...]}`` stays a dict)."""
    if value is None or isinstance(value, str | list):
        return value
    return value.model_dump()

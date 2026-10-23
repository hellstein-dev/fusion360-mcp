"""``inspect_*`` tools: model tree, descriptions, bounding boxes, mass properties, geometric
queries (c03 D12). All read-only."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.server.app import call_bridge
from fusion_mcp.server.bridge import Bridge

TreeInclude = Literal["bodies", "sketches", "construction", "features", "occurrences"]
Detail = Literal["brief", "full"]
Accuracy = Literal["low", "medium", "high", "very_high"]
QueryKind = Literal["face", "edge", "vertex", "body"]
SurfaceType = Literal[
    "plane",
    "cylinder",
    "cone",
    "sphere",
    "torus",
    "elliptical_cylinder",
    "elliptical_cone",
    "nurbs",
    "other",
]
CurveType = Literal["line", "arc", "circle", "ellipse", "elliptical_arc", "nurbs", "other"]
SortBy = Literal["area", "length", "radius", "volume", "x", "y", "z"]
Vector = Annotated[list[float], Field(min_length=3, max_length=3)]
Refs = Annotated[list[str], Field(min_length=1)]


def _given(**params: Any) -> dict[str, Any]:
    """Only the parameters the agent set: ``None`` means "not given"."""
    return {k: v for k, v in params.items() if v is not None}


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="inspect_tree")
    async def inspect_tree(
        component: Annotated[
            str | None, Field(description="Component ref to start from; default: root")
        ] = None,
        depth: Annotated[
            int | None,
            Field(ge=0, description="Occurrence nesting levels to expand; 0 = only the start"),
        ] = None,
        include: Annotated[
            list[TreeInclude] | None, Field(description="Lists to include; default: all")
        ] = None,
        max_items: Annotated[int, Field(ge=1, description="Stop after this many items")] = 500,
    ) -> CallToolResult:
        """Model tree of the active design: bodies, sketches, construction geometry,
        timeline features (with health) and nested occurrences, each with a stable `ref`.

        Start here to find what to work on. Every node is a brief description (lengths mm,
        areas mm², volumes mm³). Origin planes/axes/point are addressed as `origin:XY`,
        `origin:XZ`, `origin:YZ`, `origin:X`, `origin:Y`, `origin:Z`, `origin:O` and are not
        listed. Direct (non-parametric) designs have `features: []`.

        Returns {"design_type": "parametric"|"direct", "root": {...component node with
        "bodies", "sketches", "construction", "features", "occurrences"}, "item_count",
        "truncated"}; when `truncated` is true narrow `component`/`depth`/`include`.

        Example: inspect_tree(include=["bodies", "features"]).
        """
        params = _given(component=component, depth=depth, include=include, max_items=max_items)
        return await call_bridge(bridge, "inspect.tree", params)

    @mcp.tool(name="inspect_describe")
    async def inspect_describe(
        refs: Annotated[
            list[str], Field(min_length=1, max_length=200, description="Refs to describe")
        ],
        detail: Annotated[Detail, Field(description="brief or full (adds geometry)")] = "full",
    ) -> CallToolResult:
        """Describe entities by ref: faces (surface type, area_mm2, centroid_mm, outward
        normal, geometry such as cylinder radius/axis), edges (curve type, length_mm, ends,
        adjacent faces), vertices, bodies (volume, area, bbox, material), components,
        occurrences (transform), sketches, construction geometry and features (health).

        Units: mm, mm², mm³, degrees; directions are unit vectors. Accepts `origin:XY` etc.
        A ref that does not resolve goes to `errors` and does not fail the call.

        Returns {"items": [...], "errors": [{"ref", "code", "message"}]}.

        Example: inspect_describe(refs=["origin:xy", "<face ref>"], detail="brief").
        """
        return await call_bridge(bridge, "inspect.describe", {"refs": refs, "detail": detail})

    @mcp.tool(name="inspect_bounding_box")
    async def inspect_bounding_box(
        refs: Annotated[
            list[str] | None,
            Field(
                min_length=1,
                description="Bodies, components, occurrences or faces; default: all visible bodies",
            ),
        ] = None,
    ) -> CallToolResult:
        """World-axis bounding boxes in mm. Tight (exact) boxes come from Fusion's measure
        manager; when it cannot compute one the looser `boundingBox` is used and the item
        has `"tight": false`. Components and occurrences are the union of their bodies.

        Returns {"items": [{"ref", "min_mm", "max_mm", "size_mm", "tight"}], "union":
        {"min_mm", "max_mm", "size_mm"} | null, "errors": [...]}.

        Example: inspect_bounding_box() -> overall size of the model in `union.size_mm`.
        """
        return await call_bridge(bridge, "inspect.bounding_box", _given(refs=refs))

    @mcp.tool(name="inspect_physical_properties")
    async def inspect_physical_properties(
        refs: Annotated[
            list[str] | None,
            Field(min_length=1, description="Bodies, components or occurrences; default: root"),
        ] = None,
        accuracy: Annotated[Accuracy, Field(description="Calculation accuracy")] = "high",
    ) -> CallToolResult:
        """Mass properties: mass_kg, volume_mm3, area_mm2, density_kg_m3,
        center_of_mass_mm (assembly coordinates for occurrences), inertia_kg_mm2
        {xx, yy, zz, xy, yz, xz} about the world origin, principal_moments_kg_mm2 and the
        material name. Moments Fusion cannot compute are null.

        Returns {"items": [...], "errors": [...]}.

        Example: inspect_physical_properties(refs=["<body ref>"]) -> check the volume.
        """
        params = _given(refs=refs, accuracy=accuracy)
        return await call_bridge(bridge, "inspect.physical_properties", params)

    @mcp.tool(name="inspect_query")
    async def inspect_query(
        kind: Annotated[QueryKind, Field(description="What to return")],
        scope: Annotated[
            list[str] | None,
            Field(
                min_length=1,
                description="Bodies, components, occurrences or faces to search; default: "
                "all visible bodies",
            ),
        ] = None,
        surface_type: Annotated[SurfaceType | None, Field(description="Faces only")] = None,
        curve_type: Annotated[CurveType | None, Field(description="Edges only")] = None,
        normal: Annotated[
            Vector | None, Field(description="Planar faces facing this direction")
        ] = None,
        normal_tolerance_deg: Annotated[
            float, Field(ge=0, le=180, description="Tolerance for normal/axis")
        ] = 1.0,
        direction: Annotated[
            Vector | None, Field(description="Line edges parallel to this (either way)")
        ] = None,
        direction_tolerance_deg: Annotated[
            float, Field(ge=0, le=180, description="Tolerance for direction")
        ] = 1.0,
        axis: Annotated[
            Vector | None, Field(description="Cylinder/cone faces with this axis (either way)")
        ] = None,
        area_min_mm2: float | None = None,
        area_max_mm2: float | None = None,
        length_min_mm: float | None = None,
        length_max_mm: float | None = None,
        radius_min_mm: Annotated[
            float | None,
            Field(description="Faces: cylinder/cone/sphere radius, torus tube; edges: arc/circle"),
        ] = None,
        radius_max_mm: float | None = None,
        within_box: Annotated[
            dict[str, list[float]] | None,
            Field(description='{"min_mm": [x, y, z], "max_mm": [x, y, z]}'),
        ] = None,
        sort_by: Annotated[SortBy | None, Field(description="Sort key")] = None,
        order: Literal["desc", "asc"] = "desc",
        limit: Annotated[int, Field(ge=1, le=1000)] = 50,
    ) -> CallToolResult:
        """Find faces, edges, vertices or bodies by geometry instead of writing code. All
        given filters must match; results are brief descriptions with `ref`s (lengths mm,
        areas mm²). Filters only apply to their kind (e.g. `surface_type` needs
        kind="face") — a wrong combination or min > max fails with INVALID_PARAMS.

        Returns {"items": [...], "total": <matches before limit>, "truncated": bool}.
        On large models narrow `scope` to one body.

        Examples:
        - largest face: inspect_query(kind="face", sort_by="area", limit=1)
        - planar faces facing +Z: inspect_query(kind="face", surface_type="plane",
          normal=[0, 0, 1])
        - edges longer than 25 mm of one body: inspect_query(kind="edge",
          scope=["<body ref>"], length_min_mm=25)
        - 10 mm holes: inspect_query(kind="face", surface_type="cylinder",
          radius_min_mm=4.9, radius_max_mm=5.1)
        - vertical edges: inspect_query(kind="edge", curve_type="line", direction=[0, 0, 1])
        """
        params = _given(
            kind=kind,
            scope=scope,
            surface_type=surface_type,
            curve_type=curve_type,
            normal=normal,
            direction=direction,
            axis=axis,
            area_min_mm2=area_min_mm2,
            area_max_mm2=area_max_mm2,
            length_min_mm=length_min_mm,
            length_max_mm=length_max_mm,
            radius_min_mm=radius_min_mm,
            radius_max_mm=radius_max_mm,
            within_box=within_box,
            sort_by=sort_by,
            order=order,
            limit=limit,
        )
        # Tolerances only make sense with their filter (the bridge rejects them otherwise).
        if normal is not None or axis is not None:
            params["normal_tolerance_deg"] = normal_tolerance_deg
        if direction is not None:
            params["direction_tolerance_deg"] = direction_tolerance_deg
        return await call_bridge(bridge, "inspect.query", params)

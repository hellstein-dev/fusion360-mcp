"""``material_*`` tools: physical material libraries, lookup and assignment (c12 D4, D5, D12)."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.server.app import call_bridge
from fusion_mcp.server.bridge import Bridge

Library = Annotated[
    str | None,
    Field(description="Library name or id (see material_list_libraries); omit for all libraries"),
]
Query = Annotated[
    str | None, Field(description="Case-insensitive substring of the name, e.g. 'steel'")
]
InDesign = Annotated[bool, Field(description="List only the items already in the active design")]
Limit = Annotated[int, Field(ge=1, le=1000, description="Page size (max 1000)")]
Offset = Annotated[int, Field(ge=0, description="Items to skip (pagination)")]
MaterialTarget = Annotated[str, Field(description="Ref of a body, a component or an occurrence")]


def list_params(
    library: str | None, query: str | None, in_design: bool, limit: int, offset: int
) -> dict[str, Any]:
    params: dict[str, Any] = {"in_design": in_design, "limit": limit, "offset": offset}
    if library is not None:
        params["library"] = library
    if query is not None:
        params["query"] = query
    return params


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="material_list_libraries")
    async def material_list_libraries() -> CallToolResult:
        """Loaded material libraries (physical materials and appearances).

        Returns {"libraries": [{"id", "name", "is_native", "material_count",
        "appearance_count"}]}.

        Example: material_list_libraries() -> pick a library for material_list(library=...).
        """
        return await call_bridge(bridge, "material.list_libraries", {})

    @mcp.tool(name="material_list")
    async def material_list(
        library: Library = None,
        query: Query = None,
        in_design: InDesign = False,
        limit: Limit = 100,
        offset: Offset = 0,
    ) -> CallToolResult:
        """Physical materials of one library, of every library (default) or of the design
        (in_design=true), filtered by a name substring and paginated.

        Returns {"items": [{"id", "name", "library"}], "total", "truncated"}; "library" is
        the library name or "design". Names are localized and not unique: pass the "id" to
        material_assign.

        Example: material_list(query="steel", limit=10).
        """
        params = list_params(library, query, in_design, limit, offset)
        return await call_bridge(bridge, "material.list", params)

    @mcp.tool(name="material_get")
    async def material_get(target: MaterialTarget) -> CallToolResult:
        """Physical material and appearance of a body, a component or an occurrence (an
        occurrence reports its component's material and its own appearance override).

        Returns {"target", "material": {"id", "name", "library"} | null, "appearance": {...} |
        null}.

        Example: material_get(target="<body ref>").
        """
        return await call_bridge(bridge, "material.get", {"target": target})

    @mcp.tool(name="material_assign")
    async def material_assign(
        target: MaterialTarget,
        material: Annotated[
            str,
            Field(description="Material id from material_list (preferred) or exact name"),
        ],
        library: Annotated[
            str | None, Field(description="Library name or id to resolve an ambiguous name")
        ] = None,
    ) -> CallToolResult:
        """Assign a physical material (density, mass) to a body, a component or an
        occurrence. An occurrence has no own material: the material is set on its component
        and so applies to all occurrences of that component. Take the id from material_list;
        a name found in several libraries fails with details.candidates. Check the new mass
        with inspect_physical_properties afterwards.

        Returns {"target", "applied_to", "material": {"id", "name", "library"}, "previous":
        {...} | null} (+ "note" for an occurrence).

        Example: material_assign(target="<body ref>", material="<id from material_list>").
        """
        params: dict[str, Any] = {"target": target, "material": material}
        if library is not None:
            params["library"] = library
        return await call_bridge(bridge, "material.assign", params)

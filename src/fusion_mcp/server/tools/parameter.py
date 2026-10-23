"""``parameter_*`` tools: list, create, update and delete design parameters (c04 D6, D9)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import BaseModel, Field

from fusion_mcp.server.app import call_bridge
from fusion_mcp.server.bridge import Bridge

NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"
MAX_BATCH = 100

ParameterName = Annotated[str, Field(pattern=NAME_PATTERN, description="Parameter name")]
Expression = Annotated[
    str | float,
    Field(description="Fusion expression ('40 mm', 'width / 2', '30 deg') or a number in `unit`"),
]


class ParameterCreate(BaseModel):
    """One user parameter to create."""

    name: ParameterName
    expression: Expression
    unit: Annotated[
        str,
        Field(description="Fusion unit: 'mm', 'cm', 'in', 'deg', '' (unitless)..."),
    ] = "mm"
    comment: str = ""


class ParameterUpdate(BaseModel):
    """Changes to one user or model parameter; give at least one field besides `name`."""

    name: ParameterName
    expression: Annotated[
        str | float | None,
        Field(
            description="New expression; a number is mm for lengths, degrees for angles, "
            "else the parameter's own unit"
        ),
    ] = None
    new_name: Annotated[str | None, Field(pattern=NAME_PATTERN)] = None
    comment: str | None = None
    is_favorite: bool | None = None


def _given(**params: Any) -> dict[str, Any]:
    """Only the parameters the agent set: ``None`` means "not given"."""
    return {k: v for k, v in params.items() if v is not None}


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="parameter_list")
    async def parameter_list(
        kind: Annotated[
            Literal["all", "user", "model"],
            Field(description="user (yours), model (created by features) or all"),
        ] = "all",
        name_contains: Annotated[
            str | None, Field(min_length=1, description="Case-insensitive name filter")
        ] = None,
        component: Annotated[
            str | None,
            Field(min_length=1, description="Component ref: only its model parameters"),
        ] = None,
        include_dependents: Annotated[
            bool, Field(description="Add the names of parameters that use each one")
        ] = False,
    ) -> CallToolResult:
        """List parameters of the active design.

        Each item: {"ref": "param:<name>", "name", "kind": "user"|"model", "expression",
        "unit", "value" (lengths in mm, angles in degrees, unitless as is; missing for
        other units), "value_unit": "mm"|"deg"|"", "display" (Fusion's formatted value),
        "comment", "is_favorite", "created_by": {"ref", "type", "name"} | null (the feature
        that owns a model parameter), "role", "component", "dependents" (only with
        include_dependents)}.

        Returns {"parameters": [...]}.

        Example: parameter_list(kind="model") -> find `d1` of an extrude to drive it.
        """
        params = _given(
            kind=kind,
            name_contains=name_contains,
            component=component,
            include_dependents=include_dependents,
        )
        return await call_bridge(bridge, "parameter.list", params)

    @mcp.tool(name="parameter_create")
    async def parameter_create(
        parameters: Annotated[list[ParameterCreate], Field(min_length=1, max_length=MAX_BATCH)],
    ) -> CallToolResult:
        """Create user parameters in order (later ones may use earlier ones). All or
        nothing: names are checked first (pattern, not existing, no duplicates) and a
        Fusion error on any item removes the ones already created.

        `expression` is a Fusion expression string ("40 mm", "width / 2") or a number in
        `unit` (default mm): 40 -> "40 mm". Use unit "deg" for angles and "" for counts.

        Returns {"parameters": [...as in parameter_list], "warnings": [features that newly
        show a warning]}.

        Example: parameter_create(parameters=[{"name": "width", "expression": 40},
        {"name": "half", "expression": "width / 2"}]).
        """
        items = [p.model_dump() for p in parameters]
        return await call_bridge(bridge, "parameter.create", {"parameters": items})

    @mcp.tool(name="parameter_update")
    async def parameter_update(
        parameters: Annotated[list[ParameterUpdate], Field(min_length=1, max_length=MAX_BATCH)],
    ) -> CallToolResult:
        """Change expression, name, comment or favourite flag of user and model parameters
        — e.g. drive an extrude by setting its model parameter `d1` to "width * 2".

        All or nothing: if any change fails, or puts a timeline feature into the error
        state, every parameter keeps its previous values and the call fails with
        FUSION_API_ERROR and `details.failed_features`. Renaming updates the expressions
        that use the parameter.

        Returns {"parameters": [...as in parameter_list], "warnings": [...]}.

        Example: parameter_update(parameters=[{"name": "d1", "expression": "width * 2"}]).
        """
        items = [p.model_dump(exclude_none=True) for p in parameters]
        return await call_bridge(bridge, "parameter.update", {"parameters": items})

    @mcp.tool(name="parameter_delete")
    async def parameter_delete(
        names: Annotated[
            list[Annotated[str, Field(pattern=NAME_PATTERN)]],
            Field(min_length=1, max_length=MAX_BATCH, description="User parameter names"),
        ],
    ) -> CallToolResult:
        """Delete user parameters, all or nothing. Model parameters cannot be deleted.
        A parameter still used by another parameter outside the list (or by features)
        is refused with INVALID_PARAMS and `details.dependents`; delete or change those
        first, or include them in `names`.

        Returns {"deleted": [names], "warnings": [...]}.

        Example: parameter_delete(names=["half", "width"]).
        """
        return await call_bridge(bridge, "parameter.delete", {"names": names})

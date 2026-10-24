"""``timeline_*`` tools: inspect and edit the timeline of a parametric design (c09 D9)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.server.app import call_bridge
from fusion_mcp.server.bridge import Bridge

MAX_ITEMS = 100
F = TypeVar("F", bound=Callable[..., Any])

ItemRef = Annotated[
    str,
    Field(
        min_length=1,
        description="Timeline item: the 'ref' of a feature/sketch/construction element from "
        "timeline_list, or 'timeline:<name>' (groups)",
    ),
]
FeatureRef = Annotated[
    str, Field(min_length=1, description="Feature ref from timeline_list (or 'timeline:<name>')")
]
LengthValue = Annotated[
    float | str | None,
    Field(description="Number in mm (or `units`), or a Fusion expression ('30 mm', 'width / 2')"),
]
AngleValue = Annotated[
    float | str | None,
    Field(description="Number in degrees, or a Fusion expression ('15 deg', 'a / 2')"),
]
AllowErrors = Annotated[
    bool,
    Field(
        description="Keep the change even if a feature falls into the error state (default: "
        "revert and fail)"
    ),
]
Units = Annotated[
    Literal["mm", "cm", "m", "in", "ft"], Field(description="Unit of plain length numbers")
]

ITEM_SHAPE = (
    'Item: {"index" (informational only, never an address), "name", "type" (ExtrudeFeature, '
    'Sketch, TimelineGroup...), "ref", "is_group", "group": "timeline:<name>" | null, '
    '"is_suppressed", "is_rolled_back", "health": "ok"|"warning"|"error"|"unknown", '
    '"message" | null, "component": "component:<name>" | null}.'
)
EDIT_RESULT = (
    'Returns {"feature": item, "changed": [{"key", "parameter", "old_expression", '
    '"new_expression", "value", "value_unit"}]} (values in mm / deg). All given keys are '
    "applied together; if the feature (or any other) falls into the error state everything "
    "is reverted and the call fails with FUSION_API_ERROR, details.reverted = true — unless "
    "allow_errors=true. A key that does not apply to this feature fails with INVALID_PARAMS "
    "and details.available_keys; no key at all fails with 'nothing to change'."
)


def _append_doc(extra: str) -> Callable[[F], F]:
    """Append a shared paragraph to a tool's docstring before ``mcp.tool`` reads it."""

    def decorate(fn: F) -> F:
        fn.__doc__ = (fn.__doc__ or "").rstrip() + "\n\n" + extra
        return fn

    return decorate


def _given(**params: Any) -> dict[str, Any]:
    """Only the parameters the agent set: ``None`` means "not given"."""
    return {k: v for k, v in params.items() if v is not None}


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    # --- reading ---------------------------------------------------------------------------

    @mcp.tool(name="timeline_list")
    @_append_doc(ITEM_SHAPE)
    async def timeline_list(
        only_problems: Annotated[bool, Field(description="Only items in warning or error")] = False,
        offset: Annotated[int, Field(ge=0, description="Skip this many items")] = 0,
        limit: Annotated[int, Field(ge=1, le=1000, description="Page size")] = 200,
    ) -> CallToolResult:
        """List the timeline of the active parametric design in order, with status.

        Group members follow their group. Returns {"count", "marker_position", "items":
        [item], "total", "truncated"}; items after the marker are rolled back (not part of
        the current model). Address items by "ref", never by index.

        Example: timeline_list(only_problems=true) -> which features are broken and why.
        """
        params = {"only_problems": only_problems, "offset": offset, "limit": limit}
        return await call_bridge(bridge, "timeline.list", params)

    @mcp.tool(name="timeline_health")
    async def timeline_health() -> CallToolResult:
        """Health report of the whole timeline. Returns {"ok" (no item in error), "errors": [item],
        "warnings": [item], "suppressed_count", "rolled_back_count", "marker_position",
        "count"}. Call it after a series of edits to check the model.

        Example: timeline_health() -> {"ok": false, "errors": [{"name": "Fillet1",
        "message": "..."}], ...}.
        """
        return await call_bridge(bridge, "timeline.health", {})

    @mcp.tool(name="timeline_compute_all")
    async def timeline_compute_all() -> CallToolResult:
        """Recompute the whole design (Design.computeAll) and return the timeline_health
        report (Returns the same shape). Slow on big designs; use it when the model looks
        stale or after edits made with execute_python.

        Example: timeline_compute_all() -> {"ok": true, "errors": [], ...}.
        """
        return await call_bridge(bridge, "timeline.compute_all", {})

    # --- marker, suppression, deletion, names ----------------------------------------------

    @mcp.tool(name="timeline_roll_to")
    async def timeline_roll_to(
        position: Annotated[
            Literal["start", "end", "before", "after"],
            Field(description="start/end of the timeline, or before/after `item`"),
        ],
        item: Annotated[
            str | None, Field(min_length=1, description="Item ref; required for before/after")
        ] = None,
    ) -> CallToolResult:
        """Move the timeline marker. Items after the marker are rolled back.

        WARNING: while the marker is not at the end, features created by other tools are
        inserted at the marker position, and they see the rolled-back model. Roll to
        "end" when done. Rolling into a collapsed group expands it.

        Returns {"marker_position", "count", "rolled_back_count"}.

        Example: timeline_roll_to(position="before", item=<extrude ref>) -> add a feature
        that comes before the extrude, then timeline_roll_to(position="end").
        """
        return await call_bridge(bridge, "timeline.roll_to", _given(position=position, item=item))

    @mcp.tool(name="timeline_set_suppressed")
    @_append_doc(ITEM_SHAPE)
    async def timeline_set_suppressed(
        items: Annotated[list[ItemRef], Field(min_length=1, max_length=MAX_ITEMS)],
        suppressed: Annotated[bool, Field(description="true suppresses, false restores")] = True,
        allow_errors: AllowErrors = False,
    ) -> CallToolResult:
        """Suppress (or unsuppress) timeline items: a suppressed feature is skipped when the
        model is computed but stays in the timeline.

        All or nothing: if any item fails, or the change puts a feature into the error
        state (e.g. unsuppressing a feature whose references are gone), every item gets
        its previous state back and the call fails with the feature's message and
        details.reverted = true. allow_errors=true keeps such a change.

        Returns {"items": [item], "marker_position", "count"}.

        Example: timeline_set_suppressed(items=[<fillet ref>]).
        """
        params = {"items": items, "suppressed": suppressed, "allow_errors": allow_errors}
        return await call_bridge(bridge, "timeline.set_suppressed", params)

    @mcp.tool(name="timeline_delete")
    async def timeline_delete(
        item: ItemRef,
        force: Annotated[
            bool, Field(description="Delete even if later features would break")
        ] = False,
    ) -> CallToolResult:
        """Delete a timeline item (feature, sketch, construction element; a group with its
        contents).

        Without force the item is first suppressed as a probe: if other items fall into the
        error state, nothing is deleted and the call fails with INVALID_PARAMS and
        details.dependents. The probe recomputes the model (slow on big designs). Items
        that cannot be suppressed are deleted without the check (dependents_checked =
        false). With force=true new problems are reported in problems_after.

        Returns {"deleted": {"name", "type"}, "dependents_checked", "problems_after":
        [item], "marker_position", "count"}.

        Example: timeline_delete(item=<fillet ref>).
        """
        return await call_bridge(bridge, "timeline.delete", {"item": item, "force": force})

    @mcp.tool(name="timeline_rename")
    async def timeline_rename(
        item: ItemRef,
        name: Annotated[str, Field(min_length=1, description="New name")],
    ) -> CallToolResult:
        """Rename a feature, sketch, construction element or timeline group. Entity refs stay
        valid; a group's ref becomes 'timeline:<new name>'. Bodies are renamed with the
        body_* tools, not here.

        Returns {"item": item, "marker_position", "count"}.

        Example: timeline_rename(item=<extrude ref>, name="Base").
        """
        return await call_bridge(bridge, "timeline.rename", {"item": item, "name": name})

    # --- model parameters --------------------------------------------------------------------

    @mcp.tool(name="timeline_feature_parameters")
    async def timeline_feature_parameters(feature: FeatureRef) -> CallToolResult:
        """Model parameters created by a feature, and the keys its timeline_edit_* tool
        accepts.

        Returns {"feature": ref, "parameters": [{"name", "expression", "value", "value_unit"
        ("mm", "deg", "" or "fusion_internal" for other units), "role", "comment"}],
        "editable_keys": [...]}.

        Example: timeline_feature_parameters(feature=<extrude ref>) -> [{"name": "d3",
        "expression": "20 mm", "value": 20.0, "value_unit": "mm", "role": "AlongDistance"}].
        """
        return await call_bridge(bridge, "timeline.feature_parameters", {"feature": feature})

    @mcp.tool(name="timeline_set_model_parameter")
    async def timeline_set_model_parameter(
        name: Annotated[str, Field(min_length=1, description="Model parameter name ('d3')")],
        value: Annotated[
            float | str,
            Field(
                description="Number (mm or `units` for lengths, degrees for angles, as is for "
                "unitless) or a Fusion expression"
            ),
        ],
        units: Units = "mm",
        allow_errors: AllowErrors = False,
    ) -> CallToolResult:
        """Set the expression of a model parameter (one created by a feature) by name.

        If the owning feature (or any other) falls into the error state, the old expression
        is restored and the call fails with the feature's message and details.reverted =
        true, unless allow_errors=true. User parameters are changed with parameter_update.

        Returns {"parameter": {"name", "expression", "old_expression", "value",
        "value_unit", "unit"}, "feature": item | null}.

        Example: timeline_set_model_parameter(name="d3", value=30) -> "30 mm".
        """
        params = {"name": name, "value": value, "units": units, "allow_errors": allow_errors}
        return await call_bridge(bridge, "timeline.set_model_parameter", params)

    # --- feature-specific edits ----------------------------------------------------------------

    @mcp.tool(name="timeline_edit_extrude")
    @_append_doc(EDIT_RESULT)
    async def timeline_edit_extrude(
        feature: FeatureRef,
        distance: LengthValue = None,
        distance_two: Annotated[
            float | str | None,
            Field(description="Second side of a two-sided extrude: mm (or `units`) or expression"),
        ] = None,
        taper_angle: AngleValue = None,
        units: Units = "mm",
        allow_errors: AllowErrors = False,
    ) -> CallToolResult:
        """Change an existing extrude: distance (only distance extents, not to-object/
        through-all), distance_two (two-sided extrudes) and taper_angle (degrees).

        Example: timeline_edit_extrude(feature=<extrude ref>, distance=30).
        """
        params = _given(
            feature=feature,
            distance=distance,
            distance_two=distance_two,
            taper_angle=taper_angle,
            units=units,
            allow_errors=allow_errors,
        )
        return await call_bridge(bridge, "timeline.edit_extrude", params)

    @mcp.tool(name="timeline_edit_revolve")
    @_append_doc(EDIT_RESULT)
    async def timeline_edit_revolve(
        feature: FeatureRef,
        angle: AngleValue = None,
        angle_two: AngleValue = None,
        allow_errors: AllowErrors = False,
    ) -> CallToolResult:
        """Change an existing revolve's angle (degrees; angle extents only) and angle_two
        (two-sided revolves).

        Example: timeline_edit_revolve(feature=<revolve ref>, angle=180).
        """
        params = _given(feature=feature, angle=angle, angle_two=angle_two)
        params["allow_errors"] = allow_errors
        return await call_bridge(bridge, "timeline.edit_revolve", params)

    @mcp.tool(name="timeline_edit_fillet")
    @_append_doc(EDIT_RESULT)
    async def timeline_edit_fillet(
        feature: FeatureRef,
        radius: Annotated[
            float | str, Field(description="New radius in mm (or `units`) or an expression")
        ],
        units: Units = "mm",
        allow_errors: AllowErrors = False,
    ) -> CallToolResult:
        """Set the radius of every constant-radius edge set of an existing fillet.

        Example: timeline_edit_fillet(feature=<fillet ref>, radius=3).
        """
        params = {"feature": feature, "radius": radius, "units": units}
        params["allow_errors"] = allow_errors
        return await call_bridge(bridge, "timeline.edit_fillet", params)

    @mcp.tool(name="timeline_edit_chamfer")
    @_append_doc(EDIT_RESULT)
    async def timeline_edit_chamfer(
        feature: FeatureRef,
        distance: Annotated[
            float | str, Field(description="New distance in mm (or `units`) or an expression")
        ],
        units: Units = "mm",
        allow_errors: AllowErrors = False,
    ) -> CallToolResult:
        """Set the distance of every equal-distance edge set of an existing chamfer.

        Example: timeline_edit_chamfer(feature=<chamfer ref>, distance=1.5).
        """
        params = {"feature": feature, "distance": distance, "units": units}
        params["allow_errors"] = allow_errors
        return await call_bridge(bridge, "timeline.edit_chamfer", params)

    @mcp.tool(name="timeline_edit_hole")
    @_append_doc(EDIT_RESULT)
    async def timeline_edit_hole(
        feature: FeatureRef,
        diameter: LengthValue = None,
        depth: LengthValue = None,
        tip_angle: AngleValue = None,
        counterbore_diameter: LengthValue = None,
        counterbore_depth: LengthValue = None,
        countersink_diameter: LengthValue = None,
        countersink_angle: AngleValue = None,
        units: Units = "mm",
        allow_errors: AllowErrors = False,
    ) -> CallToolResult:
        """Change an existing hole: diameter, depth (distance extents only), tip_angle,
        counterbore_diameter/depth (counterbore holes), countersink_diameter/angle
        (countersink holes). Lengths in mm (or `units`), angles in degrees.

        Example: timeline_edit_hole(feature=<hole ref>, diameter=6.6, depth=12).
        """
        params = _given(
            feature=feature,
            diameter=diameter,
            depth=depth,
            tip_angle=tip_angle,
            counterbore_diameter=counterbore_diameter,
            counterbore_depth=counterbore_depth,
            countersink_diameter=countersink_diameter,
            countersink_angle=countersink_angle,
            units=units,
            allow_errors=allow_errors,
        )
        return await call_bridge(bridge, "timeline.edit_hole", params)

    @mcp.tool(name="timeline_edit_shell")
    @_append_doc(EDIT_RESULT)
    async def timeline_edit_shell(
        feature: FeatureRef,
        inside_thickness: LengthValue = None,
        outside_thickness: LengthValue = None,
        units: Units = "mm",
        allow_errors: AllowErrors = False,
    ) -> CallToolResult:
        """Change the inside and/or outside wall thickness of an existing shell (mm or
        `units`).

        Example: timeline_edit_shell(feature=<shell ref>, inside_thickness=2).
        """
        params = _given(
            feature=feature,
            inside_thickness=inside_thickness,
            outside_thickness=outside_thickness,
            units=units,
            allow_errors=allow_errors,
        )
        return await call_bridge(bridge, "timeline.edit_shell", params)

    # --- groups ------------------------------------------------------------------------------

    @mcp.tool(name="timeline_create_group")
    async def timeline_create_group(
        first: Annotated[ItemRef, Field(description="First item of the range (top level)")],
        last: Annotated[ItemRef, Field(description="Last item of the range (top level)")],
        name: Annotated[str | None, Field(min_length=1, description="Group name")] = None,
    ) -> CallToolResult:
        """Group a contiguous range of top-level timeline items, first..last inclusive.
        Groups cannot be nested; the name must be unique in the timeline.

        Returns {"group": item (ref 'timeline:<name>'), "members": [item],
        "marker_position", "count"}.

        Example: timeline_create_group(first=<sketch ref>, last=<extrude ref>, name="Base").
        """
        params = _given(first=first, last=last, name=name)
        return await call_bridge(bridge, "timeline.create_group", params)

    @mcp.tool(name="timeline_set_group_collapsed")
    async def timeline_set_group_collapsed(
        group: Annotated[str, Field(min_length=1, description="Group ref 'timeline:<name>'")],
        collapsed: bool,
    ) -> CallToolResult:
        """Collapse or expand a timeline group (display only).

        Returns {"group": item + "is_collapsed", "marker_position", "count"}.

        Example: timeline_set_group_collapsed(group="timeline:Base", collapsed=true).
        """
        params = {"group": group, "collapsed": collapsed}
        return await call_bridge(bridge, "timeline.set_group_collapsed", params)

    @mcp.tool(name="timeline_ungroup")
    async def timeline_ungroup(
        group: Annotated[str, Field(min_length=1, description="Group ref 'timeline:<name>'")],
    ) -> CallToolResult:
        """Remove a timeline group but keep its members in the timeline.

        Returns {"members": [item], "marker_position", "count"}.

        Example: timeline_ungroup(group="timeline:Base").
        """
        return await call_bridge(bridge, "timeline.ungroup", {"group": group})

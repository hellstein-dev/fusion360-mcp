"""``assembly_*`` tools: components, occurrences, joints and interference (c10 D8).

Input models: ``JointGeometry`` (where a joint or joint origin snaps), ``Rotate`` and
``Placement`` (occurrence transforms, D3); ``JointType`` / ``Dof`` are the vocabularies of
the add-in (``protocol/assembly.py``, D5). Lengths are mm, angles degrees; refs of
entities inside an occurrence look like ``<token>@<occurrence path>``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field, model_validator

from fusion_mcp.server.app import call_bridge
from fusion_mcp.server.bridge import Bridge
from fusion_mcp.server.tools._feature_models import Number, Ref, _Model

JointType = Literal["rigid", "revolute", "slider", "cylindrical", "pin_slot", "planar", "ball"]
Dof = Literal["rotation", "slide", "primary_slide", "secondary_slide", "pitch", "yaw", "roll"]
JointGeometryKind = Literal[
    "point", "planar_face", "nonplanar_face", "curve", "profile", "joint_origin"
]
KeyPoint = Literal["start", "middle", "end", "center"]

Vector3 = Annotated[
    list[Number], Field(min_length=3, max_length=3, description="[x, y, z] direction (unitless)")
]
Point3 = Annotated[list[Number], Field(min_length=3, max_length=3, description="[x, y, z] in mm")]

# Optional fields allowed per geometry kind (``entity`` is always required).
_KIND_FIELDS: dict[str, tuple[str, ...]] = {
    "point": (),
    "planar_face": ("key_point", "edge"),
    "nonplanar_face": ("key_point",),
    "curve": ("key_point",),
    "profile": ("key_point", "curve"),
    "joint_origin": (),
}


def _nonzero(vector: list[float], name: str) -> None:
    if all(v == 0 for v in vector):
        raise ValueError(f"{name} must not be the zero vector")


class JointGeometry(_Model):
    """Where a joint (or joint origin) snaps: an entity plus a key point on it.

    kind: point (vertex, sketch/construction point), planar_face (+ key_point, optional
    edge), nonplanar_face (+ key_point), curve (edge or sketch curve, + key_point), profile
    (+ key_point, optional curve), joint_origin (an existing joint origin).
    """

    kind: JointGeometryKind
    entity: Annotated[Ref, Field(description="Entity ref ('<token>@<occurrence path>' in parts)")]
    key_point: Annotated[
        KeyPoint | None,
        Field(description="start|middle|end|center (default: center, middle for curves)"),
    ] = None
    edge: Annotated[
        str | None, Field(min_length=1, description="planar_face only: edge the key point is on")
    ] = None
    curve: Annotated[
        str | None, Field(min_length=1, description="profile only: curve the key point is on")
    ] = None

    @model_validator(mode="after")
    def _fields_for_kind(self) -> JointGeometry:
        allowed = _KIND_FIELDS[self.kind]
        for name in ("key_point", "edge", "curve"):
            if getattr(self, name) is not None and name not in allowed:
                raise ValueError(f"'{name}' does not apply to kind '{self.kind}'")
        return self


class Rotate(_Model):
    """Rotation by angle_deg (right-hand rule) about axis through origin_mm."""

    axis: Annotated[Vector3, Field(description="[x, y, z] axis direction (not zero)")]
    angle_deg: Annotated[Number, Field(description="Degrees, right-hand rule about axis")]
    origin_mm: Annotated[
        Point3, Field(default_factory=lambda: [0.0, 0.0, 0.0], description="Point on the axis, mm")
    ]

    @model_validator(mode="after")
    def _axis_not_zero(self) -> Rotate:
        _nonzero(self.axis, "axis")
        return self


class Placement(_Model):
    """Absolute placement: the occurrence's origin and X/Y axes in assembly space."""

    origin_mm: Annotated[Point3, Field(description="Origin in mm")]
    x_axis: Annotated[Vector3, Field(description="X axis direction")]
    y_axis: Annotated[Vector3, Field(description="Y axis direction, perpendicular to x_axis")]

    @model_validator(mode="after")
    def _axes_not_zero(self) -> Placement:
        _nonzero(self.x_axis, "x_axis")
        _nonzero(self.y_axis, "y_axis")
        return self


OccurrenceRef = Annotated[
    str,
    Field(
        min_length=1,
        description="Occurrence: 'occurrence:<fullPathName>' (e.g. 'occurrence:Box:1', nested "
        "'occurrence:Box:1+Hinge:1') as returned by assembly_list",
    ),
]
ComponentRef = Annotated[
    str,
    Field(
        min_length=1,
        description="Component: 'component:<name>' (an occurrence ref means its component)",
    ),
]
Name = Annotated[str, Field(min_length=1, description="Component name (unique in the design)")]
JointRef = Annotated[
    str, Field(min_length=1, description="Joint or as-built joint ref (assembly_list_joints)")
]
Length = Annotated[Number | str, Field(description="mm number or Fusion expression ('5 mm')")]
Angle = Annotated[Number | str, Field(description="Degrees number or Fusion expression")]
LimitValue = Annotated[
    Number | Literal["off"] | None,
    Field(description='Limit in degrees (rotations) or mm (slides); "off" disables it'),
]
TranslateMm = Annotated[
    list[Number] | None,
    Field(min_length=3, max_length=3, description="[dx, dy, dz] in mm, in assembly space"),
]


def _given(**params: Any) -> dict[str, Any]:
    """Only the parameters the agent set: ``None`` means "not given"."""
    return {k: v for k, v in params.items() if v is not None}


def _dump(model: _Model | None) -> dict[str, Any] | None:
    return model.model_dump(exclude_none=True) if model is not None else None


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    """Register the ``assembly_*`` tools (c10 D8)."""

    # --- components -----------------------------------------------------------------------

    @mcp.tool(name="assembly_list")
    async def assembly_list(
        limit: Annotated[
            int, Field(ge=1, le=5000, description="Max components and max occurrences")
        ] = 500,
    ) -> CallToolResult:
        """List the assembly structure of the active design: every component and every
        occurrence (nested ones by path) with transform, grounded, light bulb, visibility
        and joint count.

        Returns {"active_component": "component:<name>", "components": [{"ref":
        "component:<name>", "name", "is_root", "occurrence_count", "body_count",
        "part_number"}], "occurrences": [occurrence], "joint_count", "truncated"}.
        Occurrence object: {"ref": "occurrence:<path>", "component", "full_path_name",
        "is_grounded", "is_visible", "is_light_bulb_on", "transform": {"translation_mm",
        "rotation"}, "joint_count"}.

        Example: assembly_list() -> {"components": [{"ref": "component:Box", ...}],
        "occurrences": [{"ref": "occurrence:Box:1", ...}], ...}.
        """
        return await call_bridge(bridge, "assembly.list", {"limit": limit})

    @mcp.tool(name="assembly_new_component")
    async def assembly_new_component(
        name: Name,
        parent: Annotated[
            str | None,
            Field(min_length=1, description="Parent occurrence ref; default: the root component"),
        ] = None,
        translate_mm: TranslateMm = None,
    ) -> CallToolResult:
        """Create an empty component with one new occurrence, under root or inside the
        component of `parent`, optionally offset by translate_mm (mm). Build its geometry
        after activating it (assembly_activate) or by targeting the component.

        Returns {"component": "component:<name>", "occurrence": occurrence}. Occurrence
        object: {"ref": "occurrence:<path>", "transform", "is_grounded", ...}. The whole
        call is undone on failure.

        Example: assembly_new_component(name="Box") -> {"component": "component:Box",
        "occurrence": {"ref": "occurrence:Box:1", "transform": {"translation_mm": [0, 0, 0],
        ...}}}.
        """
        params = _given(name=name, parent=parent, translate_mm=translate_mm)
        return await call_bridge(bridge, "assembly.new_component", params)

    @mcp.tool(name="assembly_component_from_bodies")
    async def assembly_component_from_bodies(
        bodies: Annotated[
            list[Annotated[str, Field(min_length=1)]],
            Field(min_length=1, description="Body refs, all owned by the same component"),
        ],
        name: Name,
    ) -> CallToolResult:
        """Turn bodies into a new component: the bodies move into a new component (one new
        occurrence in their current owner) and keep their position. All bodies must belong
        to the same component, otherwise INVALID_PARAMS and nothing is moved.

        Returns {"component": "component:<name>", "occurrence": occurrence, "bodies":
        [new body refs, '<token>@<occurrence path>']}. Old body refs stop resolving; use
        the returned ones.

        Example: assembly_component_from_bodies(bodies=["Bd1=", "Bd2="], name="Lid") ->
        {"component": "component:Lid", "occurrence": {"ref": "occurrence:Lid:1", ...},
        "bodies": ["Xy=@Lid:1", "Zw=@Lid:1"]}.
        """
        params = {"bodies": bodies, "name": name}
        return await call_bridge(bridge, "assembly.component_from_bodies", params)

    @mcp.tool(name="assembly_activate")
    async def assembly_activate(
        occurrence: Annotated[
            str | None,
            Field(min_length=1, description="Occurrence ref; omit to activate the root component"),
        ] = None,
    ) -> CallToolResult:
        """Activate an occurrence (new sketches and features then go into its component) or,
        without argument, the root component again.

        Returns {"active_component": "component:<name>", "active_occurrence":
        "occurrence:<path>" | null}.

        Example: assembly_activate(occurrence="occurrence:Lid:1") -> {"active_component":
        "component:Lid", "active_occurrence": "occurrence:Lid:1"}; assembly_activate() ->
        back to root.
        """
        return await call_bridge(bridge, "assembly.activate", _given(occurrence=occurrence))

    @mcp.tool(name="assembly_rename_component")
    async def assembly_rename_component(component: ComponentRef, name: Name) -> CallToolResult:
        """Rename a component. The root component cannot be renamed (INVALID_PARAMS).

        WARNING: occurrence paths contain the component name ('Box:1' -> 'Case:1'), so every
        old 'occurrence:Box:1' ref and '<token>@Box:1' proxy ref stops resolving
        (ENTITY_NOT_FOUND); use the returned paths.

        Returns {"component": "component:<new name>", "occurrences": ["occurrence:<new
        path>", ...]}.

        Example: assembly_rename_component(component="component:Box", name="Case") ->
        {"component": "component:Case", "occurrences": ["occurrence:Case:1"]}.
        """
        params = {"component": component, "name": name}
        return await call_bridge(bridge, "assembly.rename_component", params)

    # --- occurrences ------------------------------------------------------------------------

    @mcp.tool(name="assembly_add_occurrence")
    async def assembly_add_occurrence(
        component: ComponentRef,
        parent: Annotated[
            str | None,
            Field(min_length=1, description="Parent occurrence ref; default: the root component"),
        ] = None,
        independent: Annotated[
            bool,
            Field(
                description="true: an independent copy (new component 'Name (1)'); false: "
                "another instance of the same component (edits affect all instances)"
            ),
        ] = False,
        translate_mm: TranslateMm = None,
    ) -> CallToolResult:
        """Insert another occurrence of an existing component (a referenced instance, or an
        independent copy), under root or inside `parent`, offset by translate_mm (mm).
        Independent copies need a Fusion version with Occurrences.addNewComponentCopy
        (otherwise FUSION_API_ERROR).

        Returns {"occurrence": occurrence}. Occurrence object: {"ref": "occurrence:<path>",
        "component", "transform", ...}.

        Example: assembly_add_occurrence(component="component:Bolt", translate_mm=[50, 0, 0])
        -> {"occurrence": {"ref": "occurrence:Bolt:2", ...}}.
        """
        params = _given(
            component=component, parent=parent, independent=independent, translate_mm=translate_mm
        )
        return await call_bridge(bridge, "assembly.add_occurrence", params)

    @mcp.tool(name="assembly_transform_occurrence")
    async def assembly_transform_occurrence(
        occurrence: OccurrenceRef,
        translate_mm: TranslateMm = None,
        rotate: Annotated[
            Rotate | None,
            Field(description="Rotation about an axis (applied before translate_mm)"),
        ] = None,
        absolute: Annotated[
            Placement | None,
            Field(description="Absolute placement; excludes translate_mm and rotate"),
        ] = None,
        capture_position: Annotated[
            bool,
            Field(description="Fix the new position with a timeline 'Position' snapshot"),
        ] = True,
    ) -> CallToolResult:
        """Move an occurrence in assembly space: relative (rotate about an axis through
        origin_mm, then translate by translate_mm, mm/degrees) or absolute (origin and X/Y
        axes). Grounded occurrences are refused (INVALID_PARAMS): unground them with
        assembly_set_occurrence_state first. Occurrences held by joints snap back to the
        joints on recompute (see "constrained_by_joints"); drive joints instead.

        Returns {"occurrence": occurrence, "captured": bool (a position snapshot was added
        in a parametric design), "constrained_by_joints": bool}.

        Example: assembly_transform_occurrence(occurrence="occurrence:Lid:1",
        translate_mm=[0, 0, 40]) -> {"occurrence": {"transform": {"translation_mm": [0, 0,
        40], ...}}, "captured": true, ...}; rotate={"axis": [0, 0, 1], "angle_deg": 90}.
        """
        params = _given(
            occurrence=occurrence,
            translate_mm=translate_mm,
            rotate=_dump(rotate),
            absolute=_dump(absolute),
            capture_position=capture_position,
        )
        return await call_bridge(bridge, "assembly.transform_occurrence", params)

    @mcp.tool(name="assembly_set_occurrence_state")
    async def assembly_set_occurrence_state(
        occurrence: OccurrenceRef,
        grounded: Annotated[
            bool | None, Field(description="Fix the occurrence in place (ground)")
        ] = None,
        light_bulb: Annotated[
            bool | None, Field(description="Show (true) or hide (false) the occurrence")
        ] = None,
    ) -> CallToolResult:
        """Ground/unground an occurrence and/or switch its light bulb (visibility). Pass at
        least one of grounded, light_bulb.

        Returns {"occurrence": occurrence} with "is_grounded" and "is_light_bulb_on".

        Example: assembly_set_occurrence_state(occurrence="occurrence:Box:1", grounded=true).
        """
        params = _given(occurrence=occurrence, grounded=grounded, light_bulb=light_bulb)
        return await call_bridge(bridge, "assembly.set_occurrence_state", params)

    @mcp.tool(name="assembly_delete")
    async def assembly_delete(
        item: Annotated[
            str,
            Field(
                min_length=1,
                description="Occurrence, joint, as-built joint, joint origin or rigid group ref",
            ),
        ],
    ) -> CallToolResult:
        """Delete an occurrence, a joint, an as-built joint, a joint origin or a rigid group.
        Other entities are refused (INVALID_PARAMS): use body_delete, sketch_delete or
        timeline_delete for them. Deleting the last occurrence of a component removes the
        component from the design.

        Returns {"deleted": {"name", "type"}}.

        Example: assembly_delete(item="occurrence:Box:2") -> {"deleted": {"name": "Box:2",
        "type": "Occurrence"}}.
        """
        return await call_bridge(bridge, "assembly.delete", {"item": item})

    # --- joint origins and joints -------------------------------------------------------------

    @mcp.tool(name="assembly_create_joint_origin")
    async def assembly_create_joint_origin(
        geometry: Annotated[
            JointGeometry, Field(description="Where the origin snaps (kind joint_origin refused)")
        ],
        name: Annotated[str | None, Field(min_length=1, description="Joint origin name")] = None,
        offset_mm: Annotated[
            list[Length] | None,
            Field(
                min_length=3,
                max_length=3,
                description="[x, y, z] offset of the origin along its own axes: mm numbers or "
                "Fusion expressions",
            ),
        ] = None,
        angle: Annotated[
            Angle | None, Field(description="Rotation about the origin's Z: degrees or expression")
        ] = None,
        flip: Annotated[bool, Field(description="Flip the origin's Z axis")] = False,
    ) -> CallToolResult:
        """Create a joint origin: a reusable snap point for joints, stored in the component
        that owns the geometry (so it moves with the part). Geometry refs seen through an
        occurrence ('<token>@Lid:1') are fine: the origin is still created in component Lid.
        Use it later as {"kind": "joint_origin", "entity": <ref>} in assembly_create_joint.

        Returns {"joint_origin": {"ref" (in the occurrence context, '<token>@<path>', when
        the component has one), "native_ref" (inside the component), "name", "component":
        "component:<name>"}}.

        Example: assembly_create_joint_origin(geometry={"kind": "planar_face", "entity":
        "Fc=@Lid:1", "key_point": "center"}, offset_mm=[0, 0, 5]) -> {"joint_origin": {"ref":
        "Jo=@Lid:1", "native_ref": "Jo=", "name": "Joint Origin1", "component":
        "component:Lid"}}.
        """
        params = _given(
            geometry=_dump(geometry), name=name, offset_mm=offset_mm, angle=angle, flip=flip
        )
        return await call_bridge(bridge, "assembly.create_joint_origin", params)

    @mcp.tool(name="assembly_create_joint")
    async def assembly_create_joint(
        type: Annotated[JointType, Field(description="Joint type (degrees of freedom)")],
        geometry_one: Annotated[
            JointGeometry, Field(description="Snap on the MOVING part (it moves to geometry_two)")
        ],
        geometry_two: Annotated[JointGeometry, Field(description="Snap on the part it joins")],
        motion: Annotated[
            dict[str, str] | None,
            Field(description="Motion axes of the type; values 'x'|'y'|'z' or a ref, see below"),
        ] = None,
        offset: Annotated[
            Length | None, Field(description="Offset along the joint Z: mm number or expression")
        ] = None,
        angle: Annotated[
            Angle | None, Field(description="Rotation about the joint Z: degrees or expression")
        ] = None,
        flip: Annotated[bool, Field(description="Flip the direction of geometry_one")] = False,
        name: Annotated[str | None, Field(min_length=1, description="Joint name")] = None,
    ) -> CallToolResult:
        """Join two parts with a joint (created in the root component): geometry_one's part
        moves so its snap point lands on geometry_two's.

        Getting refs: call inspect_query(kind="edge" or "face", scope=["occurrence:Lid:1"])
        and use the returned refs; entities inside occurrences look like '<token>@<occurrence
        path>' (e.g. 'AbC=@Lid:1'). A native ref of a component inserted once is accepted;
        if inserted several times you get INVALID_PARAMS with the candidate refs.

        motion keys per type (default in brackets; 'x'|'y'|'z' are axes of the joint, a ref
        of an edge/axis/face sets a custom direction): rigid - none; revolute - axis [z];
        slider - direction [z]; cylindrical - axis [z]; pin_slot - axis [z],
        slide_direction [x]; planar - normal [z], primary_slide_direction (ref); ball -
        pitch_direction [z], yaw_direction [x]. For a hinge pass the hinge edge as the axis.

        Returns {"joint": {"ref", "name", "type", "occurrence_one", "occurrence_two",
        "values": {"rotation_deg" | "slide_mm" | ...}, "limits": {dof: {"min", "max",
        "rest"}}, "is_suppressed", "health", "message"}}. Nothing is left on failure.

        Example: assembly_create_joint(type="revolute", geometry_one={"kind": "curve",
        "entity": "E1=@Lid:1"}, geometry_two={"kind": "curve", "entity": "E2=@Box:1"},
        motion={"axis": "E2=@Box:1"}) -> {"joint": {"type": "revolute", "values":
        {"rotation_deg": 0.0}, "occurrence_one": "occurrence:Lid:1", ...}}.
        """
        params = _given(
            type=type,
            geometry_one=_dump(geometry_one),
            geometry_two=_dump(geometry_two),
            motion=motion,
            offset=offset,
            angle=angle,
            flip=flip,
            name=name,
        )
        return await call_bridge(bridge, "assembly.create_joint", params)

    @mcp.tool(name="assembly_create_as_built_joint")
    async def assembly_create_as_built_joint(
        type: Annotated[JointType, Field(description="Joint type (degrees of freedom)")],
        occurrence_one: OccurrenceRef,
        occurrence_two: OccurrenceRef,
        geometry: Annotated[
            JointGeometry | None,
            Field(description="Joint position/axis; required for every type except rigid"),
        ] = None,
        motion: Annotated[
            dict[str, str] | None,
            Field(description="Motion axes, same keys as assembly_create_joint"),
        ] = None,
        name: Annotated[str | None, Field(min_length=1, description="Joint name")] = None,
    ) -> CallToolResult:
        """Join two occurrences where they already are (nothing moves), in the root
        component. geometry gives the joint position and axis (required except for rigid;
        refs inside occurrences look like '<token>@<path>'). motion keys as in
        assembly_create_joint.

        Returns {"joint": joint object as in assembly_create_joint}.

        Example: assembly_create_as_built_joint(type="revolute",
        occurrence_one="occurrence:Lid:1", occurrence_two="occurrence:Box:1",
        geometry={"kind": "curve", "entity": "E2=@Box:1"}) -> {"joint": {"type": "revolute",
        ...}}.
        """
        params = _given(
            type=type,
            occurrence_one=occurrence_one,
            occurrence_two=occurrence_two,
            geometry=_dump(geometry),
            motion=motion,
            name=name,
        )
        return await call_bridge(bridge, "assembly.create_as_built_joint", params)

    # --- limits and driving ---------------------------------------------------------------------

    @mcp.tool(name="assembly_set_joint_limits")
    async def assembly_set_joint_limits(
        joint: JointRef,
        dof: Annotated[Dof, Field(description="Degree of freedom of the joint's type")],
        min: LimitValue = None,
        max: LimitValue = None,
        rest: LimitValue = None,
    ) -> CallToolResult:
        """Set the motion limits of one degree of freedom: rotation/pitch/yaw/roll in
        degrees, slide/primary_slide/secondary_slide in mm. A number enables the limit,
        "off" disables it, omitted keeps it. dof must exist for the joint type (revolute:
        rotation; slider: slide; cylindrical/pin_slot: rotation, slide; planar:
        primary_slide, secondary_slide, rotation; ball: pitch, yaw, roll), and min must not
        exceed max, otherwise INVALID_PARAMS.

        Returns {"joint": joint object (see assembly_create_joint) with "limits"}.

        Example: assembly_set_joint_limits(joint="Jt=", dof="rotation", min=0, max=110) ->
        {"joint": {"limits": {"rotation": {"min": 0.0, "max": 110.0, "rest": null}}, ...}}.
        """
        params = _given(joint=joint, dof=dof, min=min, max=max, rest=rest)
        return await call_bridge(bridge, "assembly.set_joint_limits", params)

    @mcp.tool(name="assembly_drive_joint")
    async def assembly_drive_joint(
        joint: JointRef,
        values: Annotated[
            dict[Dof, Number],
            Field(
                min_length=1,
                description="Target value per degree of freedom: degrees for rotation/pitch/"
                "yaw/roll, mm for slides",
            ),
        ],
        capture_position: Annotated[
            bool, Field(description="Fix the new position with a timeline 'Position' snapshot")
        ] = True,
    ) -> CallToolResult:
        """Move a joint to the given values (e.g. open a hinge to 90 degrees). Fusion keeps
        values inside the joint limits: dofs whose actual value differs from the request
        are listed in "clamped" (the actual value is in joint.values). Values are restored
        if Fusion fails.

        Returns {"joint": joint object (see assembly_create_joint), "requested": {dof:
        value}, "clamped": [dof], "captured": bool}.

        Example: assembly_drive_joint(joint="Jt=", values={"rotation": 90}) -> {"joint":
        {"values": {"rotation_deg": 90.0}, ...}, "requested": {"rotation": 90.0},
        "clamped": [], "captured": true}.
        """
        params = {"joint": joint, "values": values, "capture_position": capture_position}
        return await call_bridge(bridge, "assembly.drive_joint", params)

"""``form_*`` tools (c14 D10): T-spline forms - list, primitives, TSM in and out, control-mesh
inspection and edits, and an independent B-Rep copy.

Inputs are the models of ``_form_models.py``; the add-in handlers live in
``fusion_addin/handlers/form.py`` and re-check everything. Cheap rules (sizes are numbers
only, parameters of another shape, ``tsm`` xor ``path``, the TSM paths) are checked here too,
so such a mistake fails without a bridge round trip.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.form import SHAPE_FACES, SHAPE_SIZES, TSM_EXTENSIONS
from fusion_mcp.protocol.paths import check_input_path, check_output_path
from fusion_mcp.server.app import call_bridge, error_result
from fusion_mcp.server.bridge import Bridge
from fusion_mcp.server.tools._feature_models import Ref, Units, to_params
from fusion_mcp.server.tools._form_models import (
    Direction,
    FaceCount,
    InsertionMode,
    InspectPart,
    MoveKind,
    Point3,
    Shape,
    Symmetry,
    Vector3,
)
from fusion_mcp.server.tools.export import EXPORT_TIMEOUT_S

UnitsArg = Annotated[Units, Field(description="Unit of lengths and points (default mm)")]
BodyArg = Annotated[Ref, Field(description="T-spline body ref (form_list / form_create_*)")]
FormArg = Annotated[
    Ref | None,
    Field(description="Existing form feature to add the body to (default: a new form feature)"),
]
ComponentArg = Annotated[
    Ref | None,
    Field(description="Component or occurrence for a new form feature (default: the root)"),
]
NameArg = Annotated[str | None, Field(min_length=1, description="Name of the new body")]
NearArg = Annotated[
    list[Point3],
    Field(min_length=1, description="Points in `units` near the elements to pick (form_inspect)"),
]
ToleranceArg = Annotated[
    float, Field(gt=0, description="Max distance from a point to its element, in `units`")
]
Size = Annotated[
    float | str | None,
    Field(description="Number in `units`; expressions are not supported (not parametric)"),
]


def _invalid(message: str, param: str, **details: Any) -> CallToolResult:
    return error_result(BridgeError(ErrorCode.INVALID_PARAMS, message, {"param": param, **details}))


def _shape_error(shape: str, given: dict[str, Any]) -> CallToolResult | None:
    """Early D5 checks: sizes are numbers, no parameter of another shape."""
    allowed = (*SHAPE_SIZES[shape], *SHAPE_FACES[shape])
    for name, value in given.items():
        if value is None:
            continue
        if name not in allowed:
            return _invalid(
                f"parameter '{name}' does not apply to shape '{shape}' (sizes: "
                f"{', '.join(SHAPE_SIZES[shape])}; face counts: {', '.join(SHAPE_FACES[shape])})",
                name,
                reason="not_for_shape",
            )
        if isinstance(value, str):
            return _invalid(
                f"T-spline primitives are not parametric: parameter '{name}' must be a number, "
                "not an expression",
                name,
                reason="not_parametric",
            )
    return None


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="form_list")
    async def form_list(component: ComponentArg = None) -> CallToolResult:
        """List the form (T-spline) features of the design or one component with their
        T-spline bodies; changes nothing.

        Returns {"forms": [{"feature": ref, "name", "component", "health": {"state",
        "message"}, "bodies": [<body>]}], "count"};
        <body> = {"ref", "type": "TSplineBody", "name", "form", "vertex_count", "edge_count",
        "face_count", "bbox": {"min", "max"} mm, "brep_body": ref | null, "is_visible"}.
        """
        return await call_bridge(bridge, "form.list", to_params(component=component))

    @mcp.tool(name="form_create_primitive")
    async def form_create_primitive(
        shape: Annotated[Shape, Field(description="box, plane, cylinder, sphere, torus, quadball")],
        x_size: Size = None,
        y_size: Size = None,
        z_size: Size = None,
        radius: Size = None,
        height: Size = None,
        major_radius: Size = None,
        minor_radius: Size = None,
        x_faces: FaceCount | None = None,
        y_faces: FaceCount | None = None,
        z_faces: FaceCount | None = None,
        radial_faces: FaceCount | None = None,
        length_faces: FaceCount | None = None,
        longitude_faces: FaceCount | None = None,
        latitude_faces: FaceCount | None = None,
        major_faces: FaceCount | None = None,
        minor_faces: FaceCount | None = None,
        faces: FaceCount | None = None,
        origin: Annotated[
            Point3 | None,
            Field(description="Corner of box / plane, centre of the others (default 0,0,0)"),
        ] = None,
        x_direction: Annotated[
            Direction | None, Field(description="Local X axis (default [1,0,0])")
        ] = None,
        y_direction: Annotated[
            Direction | None,
            Field(description="Local Y axis, perpendicular to X (default [0,1,0])"),
        ] = None,
        symmetry: Annotated[
            Symmetry | None,
            Field(
                description='{"x", "y", "z": bool} mirror symmetry, or "circular" (cylinder, '
                "sphere, torus)"
            ),
        ] = None,
        symmetric_faces: Annotated[
            int | None, Field(ge=1, description="Faces per symmetric segment (circular only)")
        ] = None,
        name: NameArg = None,
        form: FormArg = None,
        component: ComponentArg = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Create a T-spline primitive body (Form environment) with its control mesh.

        Sizes per shape (numbers in `units`):
        box: x_size, y_size, z_size (x_faces, y_faces, z_faces);
        plane: x_size, y_size (x_faces, y_faces);
        cylinder: radius, height (radial_faces, length_faces);
        sphere: radius (longitude_faces, latitude_faces);
        torus: major_radius, minor_radius < major_radius (major_faces, minor_faces);
        quadball: radius (faces).
        Face counts default to Fusion's. A parameter of another shape -> INVALID_PARAMS.

        T-spline primitives are NOT parametric: sizes are numbers, not expressions or user
        parameters (a string -> INVALID_PARAMS), and the body does not follow parameter
        changes. Shape it afterwards with form_move / form_crease / form_insert_edge, get a
        solid with form_convert_to_brep. Pull, Bridge, Fill Hole, Weld, Subdivide and the
        like have no Fusion API: read the TSM (form_get_tsm), edit it and create the body
        again (form_create_from_tsm).

        Returns {"form": ref, "body": <body>, "created_form": bool}; the body has a B-Rep
        ("brep_body") once Fusion regenerated it - a body without one fails (rolled back).

        Example: form_create_primitive(shape="box", x_size=40, y_size=20, z_size=10,
        x_faces=4)
        """
        error = _shape_error(
            shape,
            {
                "x_size": x_size,
                "y_size": y_size,
                "z_size": z_size,
                "radius": radius,
                "height": height,
                "major_radius": major_radius,
                "minor_radius": minor_radius,
                "x_faces": x_faces,
                "y_faces": y_faces,
                "z_faces": z_faces,
                "radial_faces": radial_faces,
                "length_faces": length_faces,
                "longitude_faces": longitude_faces,
                "latitude_faces": latitude_faces,
                "major_faces": major_faces,
                "minor_faces": minor_faces,
                "faces": faces,
            },
        )
        if error is not None:
            return error
        params = to_params(
            shape=shape,
            x_size=x_size,
            y_size=y_size,
            z_size=z_size,
            radius=radius,
            height=height,
            major_radius=major_radius,
            minor_radius=minor_radius,
            x_faces=x_faces,
            y_faces=y_faces,
            z_faces=z_faces,
            radial_faces=radial_faces,
            length_faces=length_faces,
            longitude_faces=longitude_faces,
            latitude_faces=latitude_faces,
            major_faces=major_faces,
            minor_faces=minor_faces,
            faces=faces,
            origin=origin,
            x_direction=x_direction,
            y_direction=y_direction,
            symmetry=symmetry,
            symmetric_faces=symmetric_faces,
            name=name,
            form=form,
            component=component,
            units=units,
        )
        return await call_bridge(bridge, "form.create_primitive", params)

    @mcp.tool(name="form_create_from_tsm")
    async def form_create_from_tsm(
        tsm: Annotated[
            str | None, Field(min_length=1, description="TSM text (up to 8 MiB), e.g. edited")
        ] = None,
        path: Annotated[str | None, Field(description="Absolute path of a .tsm file")] = None,
        name: NameArg = None,
        form: FormArg = None,
        component: ComponentArg = None,
    ) -> CallToolResult:
        """Create a T-spline body from TSM (T-spline mesh) text or a .tsm file - exactly one
        of `tsm` and `path`.

        This is the way to complex control-mesh edits the Fusion API lacks: form_get_tsm ->
        change the text -> form_create_from_tsm (then hide or delete the old form).

        Errors: both / neither input, a path that is not absolute / missing / not .tsm, text
        over 8 MiB -> INVALID_PARAMS; TSM Fusion cannot read -> FUSION_API_ERROR "invalid
        TSM" (nothing is left).

        Returns {"form": ref, "body": <body>, "created_form": bool}.
        """
        if (tsm is None) == (path is None):
            return _invalid("pass exactly one of 'tsm' (text) and 'path' (.tsm file)", "tsm")
        if path is not None:
            try:
                check_input_path(path, TSM_EXTENSIONS)
            except BridgeError as error:
                return error_result(error)
        params = to_params(tsm=tsm, path=path, name=name, form=form, component=component)
        return await call_bridge(bridge, "form.create_from_tsm", params, EXPORT_TIMEOUT_S)

    @mcp.tool(name="form_get_tsm")
    async def form_get_tsm(
        body: BodyArg,
        path: Annotated[
            str | None,
            Field(description="Absolute .tsm path to write to (default: return the text)"),
        ] = None,
        overwrite: Annotated[bool, Field(description="Replace an existing file")] = False,
    ) -> CallToolResult:
        """Read the TSM (T-spline mesh) description of a T-spline body: the text (up to
        1 MiB) or, with `path`, a .tsm file. Changes nothing in the design.

        Errors: text over 1 MiB without path -> INVALID_PARAMS "use path"; a path that is not
        absolute / not .tsm / whose folder is missing, an existing file without
        overwrite=true -> INVALID_PARAMS.

        Returns {"body", "tsm", "size_bytes"} or {"body", "files": [{"path", "size_bytes"}]}.
        """
        if path is not None:
            try:
                check_output_path(path, TSM_EXTENSIONS, overwrite)
            except BridgeError as error:
                return error_result(error)
        params = to_params(body=body, path=path, overwrite=overwrite)
        return await call_bridge(bridge, "form.get_tsm", params, EXPORT_TIMEOUT_S)

    @mcp.tool(name="form_inspect")
    async def form_inspect(
        body: BodyArg,
        include: Annotated[
            list[InspectPart] | None,
            Field(min_length=1, description="vertices, edges, faces (default: all)"),
        ] = None,
        limit: Annotated[int, Field(ge=1, description="Max entries per list")] = 500,
    ) -> CallToolResult:
        """Read the control mesh of a T-spline body in mm; changes nothing.

        The positions are what form_move / form_crease / form_insert_edge match their `near`
        points to: vertex "position" (control point), edge "midpoint", face "center".

        Returns {"body": <body>, "vertices": [{"position", "smooth_position"}], "edges":
        [{"midpoint", "start", "end", "is_creased", "is_degenerate"}], "faces": [{"center",
        "vertex_count"}], "<list>_truncated": bool, "truncated": bool}.
        """
        params = to_params(body=body, include=include, limit=limit)
        return await call_bridge(bridge, "form.inspect", params)

    @mcp.tool(name="form_move")
    async def form_move(
        body: BodyArg,
        kind: Annotated[
            MoveKind, Field(description="vertex, face (picked by `near`) or body (whole body)")
        ],
        vector: Annotated[Vector3, Field(description="Translation in `units`")],
        near: Annotated[
            list[Point3] | None,
            Field(
                min_length=1,
                description="Points near the vertices / faces to move (positions from "
                "form_inspect); ignored for kind=body",
            ),
        ] = None,
        tolerance: ToleranceArg = 0.5,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Move control-mesh vertices or faces of a T-spline body, or the whole body, by a
        vector; the B-Rep is regenerated.

        Control-mesh vertices and faces have no stable ids: pick them by points (mm by
        default) - take the exact positions for `near` from form_inspect (vertex position,
        face center). A point with no element within `tolerance`, or equally near two
        elements (symmetric meshes), fails with INVALID_PARAMS and details.nearest; nothing
        is changed. On a symmetric body Fusion mirrors the move to the symmetric elements.

        Errors: zero vector, missing `near` for vertex/face -> INVALID_PARAMS. If a move fails
        midway, the elements already moved are moved back.

        Returns {"body": <body>, "changed": n}.

        Example: form_move(body="<ref>", kind="face", near=[[5, 5, 20]], vector=[0, 0, 5])
        """
        if kind != "body" and not near:
            return _invalid(f"kind '{kind}' needs 'near' points (from form_inspect)", "near")
        params = to_params(
            body=body, kind=kind, vector=vector, near=near, tolerance=tolerance, units=units
        )
        return await call_bridge(bridge, "form.move", params)

    @mcp.tool(name="form_crease")
    async def form_crease(
        body: BodyArg,
        near: Annotated[
            list[Point3], Field(min_length=1, description="Points near edge midpoints")
        ],
        creased: Annotated[bool, Field(description="true = crease, false = smooth")] = True,
        tolerance: ToleranceArg = 0.5,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Crease (sharp) or uncrease (smooth) control-mesh edges of a T-spline body; the
        B-Rep is regenerated.

        Control-mesh edges have no stable ids: pick them by points (mm by default) - take
        the exact edge midpoints from form_inspect. A point with no edge within `tolerance`,
        or equally near two edges (symmetric meshes), fails with INVALID_PARAMS and
        details.nearest; nothing is changed. On error the crease flags already changed are
        restored.

        Returns {"body": <body>, "changed": number of edges whose flag changed}.
        """
        params = to_params(body=body, near=near, creased=creased, tolerance=tolerance, units=units)
        return await call_bridge(bridge, "form.crease", params)

    @mcp.tool(name="form_insert_edge")
    async def form_insert_edge(
        body: BodyArg,
        near: Annotated[
            list[Point3],
            Field(min_length=1, description="Points near midpoints of edges of one loop"),
        ],
        position: Annotated[
            float, Field(ge=-1.0, le=1.0, description="Where the new edge goes, -1..1")
        ] = 0.5,
        mode: Annotated[InsertionMode, Field(description="simple (default) or exact")] = "simple",
        both_sides: Annotated[bool, Field(description="Insert on both sides")] = False,
        tolerance: ToleranceArg = 0.5,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Insert edges next to control-mesh edges of a T-spline body (more faces where
        detail is needed); the B-Rep is regenerated.

        Uses the preview Fusion API (FormFeature.insertEdge): when it is missing the tool
        fails with FUSION_API_ERROR reason=api_unavailable (nothing changed). The edges must
        be of one loop, else Fusion refuses (FUSION_API_ERROR).

        Control-mesh edges have no stable ids: pick them by points (mm by default) - take
        the exact edge midpoints from form_inspect. A point with no edge within `tolerance`,
        or equally near two edges (symmetric meshes), fails with INVALID_PARAMS and
        details.nearest; nothing is changed.

        Returns {"body": <body>, "changed": selected edges, "new_edges": n}.
        """
        params = to_params(
            body=body,
            near=near,
            position=position,
            mode=mode,
            both_sides=both_sides,
            tolerance=tolerance,
            units=units,
        )
        return await call_bridge(bridge, "form.insert_edge", params)

    @mcp.tool(name="form_convert_to_brep")
    async def form_convert_to_brep(
        body: BodyArg,
        hide_form: Annotated[
            bool, Field(description="Hide the T-spline body and its B-Rep afterwards")
        ] = True,
        name: NameArg = None,
    ) -> CallToolResult:
        """Make an independent B-Rep copy of a T-spline body's B-Rep (in a base feature in a
        parametric design) - the solid for further features (shell, fillet, join).

        The copy is independent: later edits of the form do NOT change it, and the form
        stays in the timeline (hidden by default). The Fusion API has no "Convert" command.

        Errors: a T-spline body without a B-Rep (e.g. an open or broken mesh) ->
        FUSION_API_ERROR, nothing left.

        Returns {"body": ref of the new B-Rep body, "name", "source": T-spline body ref,
        "base_feature": ref | null, "is_solid": bool}.
        """
        params = to_params(body=body, hide_form=hide_form, name=name)
        return await call_bridge(bridge, "form.convert_to_brep", params)

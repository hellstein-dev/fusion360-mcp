"""``feature_hole``, ``feature_thread`` and ``feature_thread_options`` tools (c07 D15, D16).

Inputs are the shared models of ``_feature_models.py`` (D20); the add-in handlers live in
``fusion_addin/handlers/feature_hole.py``. Cheap cross-field rules (which fields a hole kind
takes, thread length vs full length) are checked here too, so such a mistake fails without a
bridge round trip; the add-in re-checks everything. Thread types, sizes, designations and
classes are Fusion strings and are sent unchanged.
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
    DEFAULT_THREAD_TYPE,
    DEFAULT_TIP_ANGLE,
    Angle,
    HoleExtent,
    HoleKind,
    HolePlacement,
    Length,
    Ref,
    ThreadLocation,
    ThreadSpec,
    Units,
    to_params,
)

UnitsArg = Annotated[Units, Field(description="Unit of plain-number lengths and points")]
Participants = Annotated[
    list[Ref] | None,
    Field(description="Body refs the hole may cut (default: every body it meets)"),
]

_KIND_FIELDS = {
    "simple": (),
    "counterbore": ("cb_diameter", "cb_depth"),
    "countersink": ("cs_diameter", "cs_angle"),
}
_REQUIRED = {"counterbore": ("cb_diameter", "cb_depth"), "countersink": ("cs_diameter",)}


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _is_zero(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and value == 0


def _check_hole(kind: str, given: dict[str, Any]) -> None:
    """Kind-dependent fields of ``feature_hole`` (D15); the add-in applies the same rules."""
    for other, names in _KIND_FIELDS.items():
        for name in names:
            if other != kind and given[name] is not None:
                raise _invalid(
                    f"parameter '{name}' applies only to kind '{other}', not '{kind}'",
                    param=name,
                    kind=kind,
                )
    for name in _REQUIRED.get(kind, ()):
        if given[name] is None:
            raise _invalid(f"kind '{kind}' needs '{name}'", param=name, kind=kind)


def _check_thread_length(full_length: bool, length: Any, offset: Any) -> None:
    """``length``/``offset`` only with ``full_length = false``, which needs ``length``."""
    if full_length:
        if length is not None:
            raise _invalid("parameter 'length' needs full_length=false", param="length")
        if not _is_zero(offset):
            raise _invalid("parameter 'offset' needs full_length=false", param="offset")
    elif length is None:
        raise _invalid("full_length=false needs 'length'", param="length")


def register(mcp: MCPServer[Any], bridge: Bridge) -> None:
    @mcp.tool(name="feature_hole")
    async def feature_hole(
        placement: Annotated[
            HolePlacement,
            Field(
                description='Where: {"type": "point", "face", "point": [x,y,z]} | '
                '{"type": "sketch_points", "points": [refs]} (one hole per point) | '
                '{"type": "face_offsets", "face", "point", "edge1", "offset1", "edge2", '
                '"offset2"} | {"type": "center", "face", "edge"} (circular edge)'
            ),
        ],
        diameter: Annotated[Length, Field(description="Hole (bore) diameter")],
        extent: Annotated[
            HoleExtent,
            Field(
                description='How deep: {"type": "distance", "depth"} | {"type": "through_all"} '
                '| {"type": "to_entity", "entity"}'
            ),
        ],
        kind: Annotated[
            HoleKind, Field(description="simple (default), counterbore or countersink")
        ] = "simple",
        cb_diameter: Annotated[
            Length | None, Field(description="counterbore only: counterbore diameter")
        ] = None,
        cb_depth: Annotated[
            Length | None, Field(description="counterbore only: counterbore depth")
        ] = None,
        cs_diameter: Annotated[
            Length | None, Field(description="countersink only: countersink diameter")
        ] = None,
        cs_angle: Annotated[
            Angle | None,
            Field(description="countersink only: included angle, degrees (default 90)"),
        ] = None,
        tip_angle: Annotated[
            Angle, Field(description="Drill point angle at the bottom, degrees")
        ] = DEFAULT_TIP_ANGLE,
        flip: Annotated[bool, Field(description="Drill the other way from the face")] = False,
        participant_bodies: Participants = None,
        thread: Annotated[
            ThreadSpec | None,
            Field(
                description="Tapped hole: thread on the bore in the same call, e.g. "
                '{"designation": "M6x1"}; omit designation to let Fusion pick it from the bore '
                "diameter"
            ),
        ] = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Drill simple, counterbore or countersink holes - optionally tapped - into a body.

        Units: diameters, depths, offsets and points are in `units` (default mm); angles in
        degrees; a string is a Fusion expression ("bolt_d + 0.5"). Refs: planar faces and
        edges from inspect_query (refs go stale after later features - use the latest
        results), sketch points from sketch_draw (all points of one call in one sketch on the
        face), construction planes from construction_plane.

        Kinds: simple needs only diameter; counterbore needs cb_diameter and cb_depth;
        countersink needs cs_diameter (cs_angle default 90). Fields of another kind are
        refused. tip_angle defaults to 118 deg (180 = flat bottom). The hole always cuts;
        participant_bodies limits which bodies.

        Tapped hole: thread={"designation": "M6x1"} (ThreadSpec: thread_type default "ISO
        Metric profile", designation, thread_class, modeled=false (cosmetic), full_length=true
        or length + offset, location high|low). Drill the tap-drill diameter (M6x1 -> 5).
        If the thread cannot be created the whole call fails and neither the hole nor the
        thread stays. See feature_thread_options for valid designations.

        Errors: a missing kind field, a non-planar face, a non-circular center edge, an unknown
        designation (details.available) -> INVALID_PARAMS (nothing changed); a hole or thread
        Fusion computes with an error -> FUSION_API_ERROR and the timeline is rolled back.

        Returns {"feature": {ref, type, name}, "health": {"state", "message"},
        "created_bodies": [], "modified_bodies": [{ref, type, name}] (the drilled body),
        "removed_bodies": [], "holes": <number of holes>, "thread"?: {"type", "designation",
        "class", "internal": true, "modeled", "feature": {ref, type, name}, "health", "faces"}}.

        Examples:
        - feature_hole(placement={"type": "sketch_points", "points": [p1, p2, p3, p4]},
          kind="counterbore", diameter=5.5, cb_diameter=10, cb_depth=5.5,
          extent={"type": "through_all"})
        - feature_hole(placement={"type": "point", "face": <top face>, "point": [10, 10, 20]},
          diameter=5, extent={"type": "distance", "depth": 12}, thread={"designation": "M6x1"})
        """
        try:
            _check_hole(
                kind,
                {
                    "cb_diameter": cb_diameter,
                    "cb_depth": cb_depth,
                    "cs_diameter": cs_diameter,
                    "cs_angle": cs_angle,
                },
            )
        except BridgeError as error:
            return error_result(error)
        params = to_params(
            placement=placement,
            diameter=diameter,
            extent=extent,
            kind=kind,
            cb_diameter=cb_diameter,
            cb_depth=cb_depth,
            cs_diameter=cs_diameter,
            cs_angle=cs_angle,
            tip_angle=tip_angle,
            flip=flip,
            participant_bodies=participant_bodies,
            thread=thread,
            units=units,
        )
        return await call_bridge(bridge, "feature.hole", params)

    @mcp.tool(name="feature_thread")
    async def feature_thread(
        faces: Annotated[
            list[Ref],
            Field(
                min_length=1,
                description="Cylindrical face refs (inspect_query): hole walls or shafts",
            ),
        ],
        thread_type: Annotated[
            str, Field(min_length=1, description="Fusion thread type (feature_thread_options)")
        ] = DEFAULT_THREAD_TYPE,
        designation: Annotated[
            str | None,
            Field(min_length=1, description="e.g. 'M6x1'; omitted = recommended from diameter"),
        ] = None,
        thread_class: Annotated[
            str | None,
            Field(min_length=1, description="e.g. '6H' / '6g'; omitted = first offered class"),
        ] = None,
        internal: Annotated[
            bool | None,
            Field(description="Override hole (true) / shaft (false); default: from the face"),
        ] = None,
        modeled: Annotated[
            bool, Field(description="Real thread geometry instead of cosmetic (default false)")
        ] = False,
        full_length: Annotated[bool, Field(description="Thread the whole face")] = True,
        length: Annotated[
            Length | None, Field(description="Thread length; needs full_length=false")
        ] = None,
        offset: Annotated[
            Length, Field(description="Distance of the thread from its end; full_length=false")
        ] = 0.0,
        location: Annotated[
            ThreadLocation, Field(description="End the thread starts from: high or low")
        ] = "high",
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Add a cosmetic (default) or modeled thread to cylindrical faces - bolts, shafts,
        tapped holes made earlier.

        Hole wall vs shaft is detected from the face orientation (override with `internal`).
        Without designation Fusion recommends one from the cylinder diameter (a 10 mm shaft
        -> M10x1.5 class 6g) and the result says which; without thread_class the first class
        offered for the designation is used. Strings (type, designation, class) are Fusion's
        own names - list them with feature_thread_options. Units: length/offset in `units`
        (default mm).

        Errors: a planar or conical face, faces mixing holes and shafts, an unknown
        type/designation/class (details.available lists valid ones), length with
        full_length=true -> INVALID_PARAMS (nothing changed); a thread Fusion computes with
        an error -> FUSION_API_ERROR and the timeline is rolled back.

        Returns {"feature": {ref, type, name}, "health": {"state", "message"},
        "created_bodies": [], "modified_bodies": [...] (modeled threads), "removed_bodies": [],
        "thread": {"type", "designation", "class", "internal", "modeled"}}.

        Examples:
        - feature_thread(faces=[<shaft face>])
        - feature_thread(faces=[<face>], designation="M8x1.25", modeled=true,
          full_length=false, length=12)
        """
        try:
            _check_thread_length(full_length, length, offset)
        except BridgeError as error:
            return error_result(error)
        params = to_params(
            faces=faces,
            thread_type=thread_type,
            designation=designation,
            thread_class=thread_class,
            internal=internal,
            modeled=modeled,
            full_length=full_length,
            length=length,
            offset=None if full_length else offset,
            location=location,
            units=units,
        )
        return await call_bridge(bridge, "feature.thread", params)

    @mcp.tool(name="feature_thread_options")
    async def feature_thread_options(
        thread_type: Annotated[
            str | None, Field(min_length=1, description="e.g. 'ISO Metric profile'")
        ] = None,
        size: Annotated[str | None, Field(min_length=1, description="e.g. '6'")] = None,
        designation: Annotated[str | None, Field(min_length=1, description="e.g. 'M6x1'")] = None,
        internal: Annotated[
            bool, Field(description="Classes / recommendation for a hole (true) or shaft")
        ] = False,
        diameter: Annotated[
            Length | None,
            Field(description="Recommend a designation and class for this model diameter"),
        ] = None,
        units: UnitsArg = "mm",
    ) -> CallToolResult:
        """Look up Fusion's thread data (read-only) - which strings feature_thread and
        feature_hole(thread=...) accept.

        Levels: no arguments -> {"thread_types"}; thread_type -> {"sizes"}; + size ->
        {"designations"}; + designation -> {"classes"} (internal or external); diameter (in
        `units`, with internal=true for a hole's bore / tap drill, false for a shaft) ->
        {"recommended": {"designation", "class"} or null}. size, designation and diameter
        without thread_type use "ISO Metric profile". diameter cannot be combined with size
        or designation.

        Errors: an unknown type, size or designation -> INVALID_PARAMS with
        details.available.

        Examples:
        - feature_thread_options(thread_type="ISO Metric profile")
        - feature_thread_options(diameter=8, internal=false)
        - feature_thread_options(designation="M6x1", internal=true)
        """
        if diameter is not None and (size is not None or designation is not None):
            return error_result(
                _invalid(
                    "parameter 'diameter' cannot be combined with 'size' or 'designation'",
                    param="diameter",
                )
            )
        params = to_params(
            thread_type=thread_type,
            size=size,
            designation=designation,
            internal=internal,
            diameter=diameter,
            units=units,
        )
        return await call_bridge(bridge, "feature.thread_options", params)

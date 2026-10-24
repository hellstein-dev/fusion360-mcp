"""``export.*``: CAD, mesh and sketch DXF export through ``ExportManager`` (c12 D7-D10).

Paths are absolute, the extension must match the format and an existing file is replaced
only with ``overwrite`` (``fusion_mcp.protocol.paths``). Exports change neither the timeline
nor the document's saved state (saving is c04), so the handlers are read-only
(``mutating=False``). Mesh lengths are in the request ``units`` (default mm) and angles in
degrees, converted by ``ctx.units`` (INV-6).
"""

from __future__ import annotations

import os
from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.exchange import (
    CAD_EXPORT_FORMATS,
    EXTENSIONS,
    FILE_UNITS,
    MESH_EXPORT_FORMATS,
    REFINEMENTS,
    detect_format,
)
from fusion_mcp.protocol.features import check_choice

from ..core.exchange import run_export
from ..core.features import resolve_one
from ..core.refs import same_entity, type_name
from ..core.registry import handler

CAD_FACTORIES = {
    "step": "createSTEPExportOptions",
    "iges": "createIGESExportOptions",
    "sat": "createSATExportOptions",
    "smt": "createSMTExportOptions",
    "f3d": "createFusionArchiveExportOptions",
    "usdz": "createUSDExportOptions",
}
MESH_FACTORIES = {
    "stl": "createSTLExportOptions",
    "3mf": "createC3MFExportOptions",
    "obj": "createOBJExportOptions",
}
REFINEMENT_ENUMS = {
    "low": "MeshRefinementLow",
    "medium": "MeshRefinementMedium",
    "high": "MeshRefinementHigh",
    "custom": "MeshRefinementCustom",
}
DISTANCE_UNITS = {
    "mm": "MillimeterDistanceUnits",
    "cm": "CentimeterDistanceUnits",
    "m": "MeterDistanceUnits",
    "in": "InchDistanceUnits",
    "ft": "FootDistanceUnits",
}
# Custom refinement values: name -> (Fusion option, kind).
CUSTOM_VALUES = {
    "surface_deviation": ("surfaceDeviation", "length"),
    "normal_deviation": ("normalDeviation", "angle"),
    "max_edge_length": ("maximumEdgeLength", "length"),
    "aspect_ratio": ("aspectRatio", "ratio"),
}


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _not_available(what: str) -> BridgeError:
    return BridgeError(
        ErrorCode.FUSION_API_ERROR,
        f"{what} not available in this Fusion version",
        {"reason": "not_available"},
    )


def _format(params: dict[str, Any], formats: tuple[str, ...]) -> str:
    value = params.get("format")
    if value is None:
        path = params.get("path")
        return detect_format(path if isinstance(path, str) else "", formats)
    return check_choice("format", value, formats)


def _scope(ctx: Any, params: dict[str, Any], allow_body: bool) -> tuple[Any, str]:
    """``(geometry, scope ref)``: the root component, an occurrence, the first occurrence of
    a component or (mesh only) a body."""
    ref = params.get("scope")
    root = ctx.root
    if ref is None or ref == "root":
        return root, "root"
    entity = resolve_one(ctx, ref, ("Component", "Occurrence", "BRepBody"), "scope")
    kind = type_name(entity)
    if kind == "BRepBody":
        if not allow_body:
            raise _invalid(
                "a body cannot be exported to a CAD format: use export_mesh or move the body "
                "to a component",
                param="scope",
                actual="BRepBody",
            )
        return entity, ctx.refs.to_ref(entity)
    if kind == "Component":
        if same_entity(entity, root):
            return root, "root"
        occurrences = root.allOccurrencesByComponent(entity)
        if occurrences is None or occurrences.count == 0:
            raise _invalid(
                f"component {entity.name!r} has no occurrence to export",
                param="scope",
                reason="no_occurrence",
            )
        return occurrences.item(0), ctx.refs.to_ref(entity)
    return entity, ctx.refs.to_ref(entity)


def _factory(ctx: Any, name: str, what: str) -> Any:
    manager = ctx.design.exportManager
    if not hasattr(manager, name):
        raise _not_available(what)
    return getattr(manager, name)


@handler("export.cad")
def export_cad(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    fmt = _format(params, CAD_EXPORT_FORMATS)
    overwrite = ctx.require(params, "overwrite", bool, False)
    geometry, scope = _scope(ctx, params, allow_body=False)
    create = _factory(ctx, CAD_FACTORIES[fmt], "USD export" if fmt == "usdz" else f"{fmt} export")
    return run_export(
        ctx,
        lambda path: create(path, geometry),
        params.get("path"),
        overwrite,
        extensions=EXTENSIONS[fmt],
        fmt=fmt,
        scope=scope,
    )


def _custom_values(ctx: Any, params: dict[str, Any], refinement: str) -> dict[str, float]:
    """Fusion option -> value (cm / radians / ratio) of the custom refinement values given."""
    values: dict[str, float] = {}
    for name, (option, kind) in CUSTOM_VALUES.items():
        value = params.get(name)
        if value is None:
            continue
        if refinement != "custom":
            raise _invalid(
                f"parameter '{name}' needs refinement='custom'", param=name, refinement=refinement
            )
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            raise _invalid(
                f"parameter '{name}' must be a positive number (expressions are not supported)",
                param=name,
                value=value if isinstance(value, str) else repr(value),
            )
        if kind == "length":
            values[option] = float(ctx.units.length(value))
        elif kind == "angle":
            values[option] = float(ctx.units.angle(value))
        else:
            values[option] = float(value)
    if refinement == "custom" and not values:
        raise _invalid(
            "refinement='custom' needs at least one of " + ", ".join(CUSTOM_VALUES),
            param="refinement",
        )
    return values


@handler("export.mesh")
def export_mesh(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    fmt = _format(params, MESH_EXPORT_FORMATS)
    overwrite = ctx.require(params, "overwrite", bool, False)
    refinement = check_choice("refinement", params.get("refinement", "medium"), REFINEMENTS)
    file_units = check_choice("file_units", params.get("file_units", "mm"), FILE_UNITS)
    binary = ctx.require(params, "binary", bool, True)
    custom = _custom_values(ctx, params, refinement)
    geometry, scope = _scope(ctx, params, allow_body=True)
    create = _factory(ctx, MESH_FACTORIES[fmt], f"{fmt} export")
    settings = adsk.fusion.MeshRefinementSettings
    warnings: list[str] = []

    def options(path: str) -> Any:
        opts = create(geometry, path)
        opts.meshRefinement = getattr(settings, REFINEMENT_ENUMS[refinement])
        for option, value in custom.items():
            setattr(opts, option, value)
        opts.isOneFilePerBody = False
        opts.sendToPrintUtility = False
        if hasattr(opts, "unitType"):
            opts.unitType = getattr(adsk.fusion.DistanceUnits, DISTANCE_UNITS[file_units])
        elif "file_units" in params:
            warnings.append(f"{fmt} has no file units option: 'file_units' was ignored")
        if fmt == "stl":
            opts.isBinaryFormat = binary
        return opts

    result = run_export(
        ctx,
        options,
        params.get("path"),
        overwrite,
        extensions=EXTENSIONS[fmt],
        fmt=fmt,
        scope=scope,
        extra_files=(lambda path: [os.path.splitext(path)[0] + ".mtl"])
        if fmt == "obj"
        else (lambda _: ()),
    )
    if warnings:
        result["warnings"] = warnings
    return result


@handler("export.sketch_dxf")
def export_sketch_dxf(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    sketch = resolve_one(ctx, params.get("sketch"), ("Sketch",), "sketch")
    overwrite = ctx.require(params, "overwrite", bool, False)
    manager = ctx.design.exportManager
    factory = None
    execute = None
    if hasattr(manager, "createDXFSketchExportOptions"):
        factory = lambda path: manager.createDXFSketchExportOptions(path, sketch)  # noqa: E731
    elif hasattr(sketch, "saveAsDXF"):
        execute = sketch.saveAsDXF
    else:
        raise _not_available("sketch DXF export")
    return run_export(
        ctx,
        factory,
        params.get("path"),
        overwrite,
        extensions=EXTENSIONS["dxf"],
        fmt="dxf",
        scope=ctx.refs.to_ref(sketch),
        execute=execute,
    )

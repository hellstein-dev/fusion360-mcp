"""``import.file``: CAD and mesh import into the active design (c12 D11). The module is not
named ``import`` (a keyword).

CAD files (STEP, IGES, SAT, SMT, F3D) go through ``ImportManager.importToTarget2`` (or
``importToTarget`` on an older Fusion) into a target component (default root); meshes (STL,
OBJ, 3MF) through ``component.meshBodies.add`` in ``mesh_units``. What was created is found
by comparing snapshots of the component before and after, not from the importer's return
value. The handler is mutating: the dispatcher rolls the timeline back on failure; in a
direct design (no timeline, R7) a ``Compensation`` deletes what the import created.
"""

from __future__ import annotations

from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.exchange import (
    CAD_IMPORT_FORMATS,
    EXTENSIONS,
    FILE_UNITS,
    IMPORT_FORMATS,
    detect_format,
    extensions_of,
)
from fusion_mcp.protocol.features import check_choice
from fusion_mcp.protocol.paths import check_input_path

from ..core.compensate import Compensation
from ..core.exchange import created_since, snapshot
from ..core.features import resolve_one
from ..core.registry import handler

IMPORT_OPTIONS = {
    "step": "createSTEPImportOptions",
    "iges": "createIGESImportOptions",
    "sat": "createSATImportOptions",
    "smt": "createSMTImportOptions",
    "f3d": "createFusionArchiveImportOptions",
}
MESH_UNITS = {
    "mm": "MillimeterMeshUnit",
    "cm": "CentimeterMeshUnit",
    "m": "MeterMeshUnit",
    "in": "InchMeshUnit",
    "ft": "FootMeshUnit",
}


def _failed(message: str, path: str) -> BridgeError:
    return BridgeError(ErrorCode.FUSION_API_ERROR, message, {"path": path})


def _direct(ctx: Any) -> bool:
    try:
        return bool(ctx.design.designType == adsk.fusion.DesignTypes.DirectDesignType)
    except Exception:
        return False


def _delete_created(component: Any, before: dict[str, set[Any]]) -> None:
    for entities in created_since(component, before).values():
        for entity in entities:
            entity.deleteMe()


def _import_cad(ctx: Any, fmt: str, path: str, component: Any) -> None:
    manager = ctx.app.importManager
    options = getattr(manager, IMPORT_OPTIONS[fmt])(path)
    if hasattr(manager, "importToTarget2"):
        result = manager.importToTarget2(options, component)
    else:
        result = manager.importToTarget(options, component)
    if result is None or result is False:
        raise _failed(f"Fusion could not import {path!r}", path)


def _import_mesh(path: str, component: Any, unit: str) -> None:
    mesh_unit = getattr(adsk.fusion.MeshUnits, MESH_UNITS[unit])
    if component.meshBodies.add(path, mesh_unit) is None:
        raise _failed(f"Fusion could not import the mesh {path!r}", path)


@handler("import.file", mutating=True)
def import_file(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    raw_format = params.get("format")
    if raw_format is None:
        path = check_input_path(params.get("path"), extensions_of(IMPORT_FORMATS))
        fmt = detect_format(path, IMPORT_FORMATS)
    else:
        fmt = check_choice("format", raw_format, IMPORT_FORMATS)
        path = check_input_path(params.get("path"), EXTENSIONS[fmt])
    mesh_units = check_choice("mesh_units", params.get("mesh_units", "mm"), FILE_UNITS)
    target = params.get("target")
    component = ctx.root if target is None else resolve_one(ctx, target, ("Component",), "target")
    before = snapshot(component)
    with Compensation() as undo:
        if _direct(ctx):
            undo.add(lambda: _delete_created(component, before), "delete imported entities")
        if fmt in CAD_IMPORT_FORMATS:
            _import_cad(ctx, fmt, path, component)
        else:
            _import_mesh(path, component, mesh_units)
        created = created_since(component, before)
        if not any(created.values()):
            raise _failed(f"import of {path!r} produced no geometry", path)
    return {
        "format": fmt,
        "target": ctx.refs.to_ref(component),
        "created": {
            kind: [ctx.refs.to_ref(e) for e in entities] for kind, entities in created.items()
        },
    }

"""``document.*``: list, create, open, activate, save and close documents (c04 D3).

Documents are addressed by ``core.documents`` refs; an omitted ``document`` is the active
one. None of these handlers is mutating: they do not add timeline items. Nothing is saved
or closed unless the agent calls ``document.save``/``document.close`` explicitly.
"""

from __future__ import annotations

import os
from typing import Any

import adsk.core
import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

from ..core import documents as docs
from ..core.registry import handler

DESIGN_TYPES = ("parametric", "direct")
LOCAL_EXTENSION = ".f3d"


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _design_type_value(name: str) -> Any:
    types = adsk.fusion.DesignTypes
    return types.ParametricDesignType if name == "parametric" else types.DirectDesignType


def _choice(
    ctx: Any, params: dict[str, Any], name: str, allowed: tuple[str, ...], default: str
) -> str:
    value = ctx.require(params, name, str, default)
    if value not in allowed:
        raise _invalid(
            f"parameter '{name}' must be one of {', '.join(allowed)}", param=name, value=value
        )
    return str(value)


def _document(ctx: Any, params: dict[str, Any]) -> Any:
    return docs.resolve_document(ctx.app, params.get("document"))


@handler("document.list")
def document_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    app = ctx.app
    return {
        "documents": [
            docs.document_info(app.documents.item(i), app) for i in range(app.documents.count)
        ]
    }


@handler("document.activate")
def document_activate(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    ref = ctx.require(params, "document", str)
    doc = docs.resolve_document(ctx.app, ref)
    if not doc.activate():
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR, f"Fusion did not activate {ref!r}", {"ref": ref}
        )
    return docs.document_info(doc, ctx.app)


@handler("document.new")
def document_new(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    design_type = _choice(ctx, params, "design_type", DESIGN_TYPES, "parametric")
    units = docs.units_to_fusion(ctx.require(params, "units", str, "mm"))
    doc = ctx.app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    try:
        design = docs.design_of(doc)
        if design is None:
            raise BridgeError(ErrorCode.FUSION_API_ERROR, "the new document has no design")
        design.designType = _design_type_value(design_type)
        design.fusionUnitsManager.distanceDisplayUnits = units
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, BridgeError)
            else BridgeError(
                ErrorCode.FUSION_API_ERROR,
                f"setting up the new document failed: {exc}",
                {"exception_type": type(exc).__name__},
            )
        )
        # The document is new and empty: close it without saving, then report.
        try:
            doc.close(False)
        except Exception as close_exc:
            error.details["close_error"] = str(close_exc) or type(close_exc).__name__
        if error is exc:
            raise
        raise error from exc
    return docs.document_info(doc, ctx.app)


def _open_document_with_file(app: Any, file_id: str) -> Any:
    for i in range(app.documents.count):
        doc = app.documents.item(i)
        data = docs.data_file(doc)
        if data is not None and data.id == file_id:
            return doc
    return None


@handler("document.open")
def document_open(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    file_id = ctx.require(params, "file_id", str, None)
    local_path = ctx.require(params, "local_path", str, None)
    if (file_id is None) == (local_path is None):
        raise _invalid("pass exactly one of 'file_id' or 'local_path'")
    app = ctx.app
    if file_id is not None:
        if not file_id:
            raise _invalid("parameter 'file_id' must not be empty", param="file_id")
        doc = _open_document_with_file(app, file_id)
        if doc is not None:
            doc.activate()
            return {**docs.document_info(doc, app), "already_open": True}
        data_file = app.data.findFileById(file_id)
        if data_file is None:
            raise BridgeError(
                ErrorCode.ENTITY_NOT_FOUND,
                f"no cloud file with id {file_id!r}",
                {"file_id": file_id},
            )
        doc = app.documents.open(data_file, True)
    else:
        assert local_path is not None
        if os.path.splitext(local_path)[1].lower() != LOCAL_EXTENSION:
            raise _invalid(
                "only local Fusion archives (.f3d) can be opened; use import tools for other "
                "formats",
                param="local_path",
                value=local_path,
            )
        if not os.path.isfile(local_path):
            raise _invalid(f"file {local_path!r} does not exist", param="local_path")
        manager = app.importManager
        options = manager.createFusionArchiveImportOptions(local_path)
        doc = manager.importToNewDocument(options)
    if doc is None:
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "Fusion did not open the document")
    return {**docs.document_info(doc, app), "already_open": False}


def _folder(app: Any, folder_path: str | None) -> Any:
    """The active project's root folder, then each ``A/B`` segment by name (none created)."""
    folder = app.data.activeProject.rootFolder
    if not folder_path:
        return folder
    walked: list[str] = []
    for segment in (s for s in folder_path.replace("\\", "/").split("/") if s):
        child = folder.dataFolders.itemByName(segment)
        walked.append(segment)
        if child is None:
            raise BridgeError(
                ErrorCode.ENTITY_NOT_FOUND,
                f"folder {'/'.join(walked)!r} does not exist in the active project",
                {"folder_path": folder_path, "missing": "/".join(walked)},
            )
        folder = child
    return folder


@handler("document.save")
def document_save(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    app = ctx.app
    doc = _document(ctx, params)
    description = ctx.require(params, "description", str, "")
    name = ctx.require(params, "name", str, None)
    folder_path = ctx.require(params, "folder_path", str, None)
    if docs.data_file(doc) is not None:
        if name is not None or folder_path is not None:
            raise _invalid(
                "the document is already saved: 'name'/'folder_path' are only for the first "
                "save (Save As is not supported)",
                ref=docs.document_ref(doc)["ref"],
            )
        saved = doc.save(description)
    else:
        if not name:
            raise _invalid(
                "the document has never been saved: pass 'name' (and optionally "
                "'folder_path' in the active project)",
                param="name",
            )
        folder = _folder(app, folder_path)
        saved = doc.saveAs(name, folder, description, "")
    if not saved:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            "Fusion did not save the document",
            {"ref": docs.document_ref(doc)["ref"]},
        )
    return {**docs.document_info(doc, app), "upload": "pending"}


@handler("document.close")
def document_close(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    app = ctx.app
    doc = _document(ctx, params)
    save_changes = ctx.require(params, "save_changes", bool, False)
    discard_changes = ctx.require(params, "discard_changes", bool, False)
    if save_changes and discard_changes:
        raise _invalid("pass at most one of 'save_changes' or 'discard_changes'")
    ref = docs.document_ref(doc)
    if doc.isModified and not (save_changes or discard_changes):
        raise _invalid(
            "unsaved changes: pass save_changes=true or discard_changes=true",
            ref=ref["ref"],
        )
    if save_changes and docs.data_file(doc) is None:
        raise _invalid(
            "the document has never been saved: call document_save with a name first",
            ref=ref["ref"],
        )
    if not doc.close(save_changes):
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR, "Fusion did not close the document", {"ref": ref["ref"]}
        )
    active = app.activeDocument
    return {
        "closed": ref,
        "active": docs.document_info(active, app) if active is not None else None,
    }

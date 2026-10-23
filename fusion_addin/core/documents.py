"""Document references and information (c04 D2) and the distance-unit mapping (D4).

A saved document is ``document:file:<dataFile id>``, an unsaved one ``document:<name>``;
collection indices never go out (INV-7).
"""

from __future__ import annotations

from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

DOCUMENT_PREFIX = "document:"
FILE_PREFIX = "document:file:"
DESIGN_PRODUCT = "DesignProductType"

UNIT_NAMES = ("mm", "cm", "m", "in", "ft")


def _distance_units() -> dict[str, Any]:
    units = adsk.fusion.DistanceUnits
    return {
        "mm": units.MillimeterDistanceUnits,
        "cm": units.CentimeterDistanceUnits,
        "m": units.MeterDistanceUnits,
        "in": units.InchDistanceUnits,
        "ft": units.FootDistanceUnits,
    }


def units_to_fusion(units: str) -> Any:
    """``"mm"`` ... ``"ft"`` -> ``adsk.fusion.DistanceUnits`` value; else ``INVALID_PARAMS``."""
    mapping = _distance_units()
    if not isinstance(units, str) or units not in mapping:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"parameter 'units' must be one of {', '.join(UNIT_NAMES)}",
            {"param": "units", "value": units},
        )
    return mapping[units]


def units_from_fusion(value: Any) -> str | None:
    """``DistanceUnits`` value -> ``"mm"`` ...; an unknown value -> ``None``."""
    return next((name for name, v in _distance_units().items() if v == value), None)


def data_file(doc: Any) -> Any:
    """``doc.dataFile`` for a saved document; ``None`` for an unsaved one, including when
    Fusion raises instead of returning ``None``."""
    try:
        if not doc.isSaved:
            return None
        return doc.dataFile
    except Exception:
        return None


def document_ref(doc: Any) -> dict[str, str]:
    data = data_file(doc)
    ref = FILE_PREFIX + data.id if data is not None else DOCUMENT_PREFIX + doc.name
    return {"ref": ref, "type": "Document", "name": doc.name}


def design_of(doc: Any) -> Any:
    """The design product of ``doc``, or ``None`` for a non-design document."""
    try:
        product = doc.products.itemByProductType(DESIGN_PRODUCT)
    except Exception:
        return None
    if product is None:
        return None
    return adsk.fusion.Design.cast(product) or None


def design_type(design: Any) -> str | None:
    if design is None:
        return None
    types = adsk.fusion.DesignTypes
    if design.designType == types.ParametricDesignType:
        return "parametric"
    if design.designType == types.DirectDesignType:
        return "direct"
    return None


def design_units(design: Any) -> str | None:
    if design is None:
        return None
    return units_from_fusion(design.fusionUnitsManager.distanceDisplayUnits)


def _documents(app: Any) -> list[Any]:
    docs = app.documents
    return [docs.item(i) for i in range(docs.count)]


def document_info(doc: Any, app: Any) -> dict[str, Any]:
    data = data_file(doc)
    design = design_of(doc)
    return {
        **document_ref(doc),
        "is_active": doc == app.activeDocument,
        "is_saved": data is not None,
        "is_modified": bool(doc.isModified),
        "file_id": data.id if data is not None else None,
        "version": data.versionNumber if data is not None else None,
        "design_type": design_type(design),
        "units": design_units(design),
    }


def resolve_document(app: Any, ref: Any = None) -> Any:
    """The open document for ``ref``; ``None`` means the active document."""
    if ref is None:
        doc = app.activeDocument
        if doc is None:
            raise BridgeError(
                ErrorCode.NO_ACTIVE_DESIGN, "no active document: open or create one first"
            )
        return doc
    if isinstance(ref, dict):
        ref = ref.get("ref")
    if not isinstance(ref, str) or not ref.startswith(DOCUMENT_PREFIX) or ref == DOCUMENT_PREFIX:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            "a document ref is 'document:file:<id>' or 'document:<name>'",
            {"param": "document", "value": ref},
        )
    if ref.startswith(FILE_PREFIX):
        file_id = ref[len(FILE_PREFIX) :]
        matches = [d for d in _documents(app) if _file_id(d) == file_id]
    else:
        name = ref[len(DOCUMENT_PREFIX) :]
        matches = [d for d in _documents(app) if d.name == name]
    if not matches:
        raise BridgeError(
            ErrorCode.ENTITY_NOT_FOUND,
            f"document {ref!r} is not open",
            {"ref": ref, "open": [document_ref(d)["ref"] for d in _documents(app)]},
        )
    if len(matches) > 1:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"document {ref!r} is ambiguous: {len(matches)} open documents match",
            {"ref": ref, "candidates": [document_ref(d)["ref"] for d in matches]},
        )
    return matches[0]


def _file_id(doc: Any) -> str | None:
    data = data_file(doc)
    return data.id if data is not None else None

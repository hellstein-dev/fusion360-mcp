"""``core/documents.py``: document refs, resolution, info and unit mapping (c04 D2, D4)."""

import pytest

import fusion_addin
from fusion_addin.core import documents as d
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.documents import FakeDocsApp

adsk = fusion_addin.adsk


def test_ref_saved_and_unsaved() -> None:
    app = FakeDocsApp()
    saved = app.open_document("Bracket", file_id="urn:42")
    unsaved = app.open_document("Untitled")
    assert d.document_ref(saved) == {
        "ref": "document:file:urn:42",
        "type": "Document",
        "name": "Bracket",
    }
    assert d.document_ref(unsaved)["ref"] == "document:Untitled"


def test_ref_when_data_file_raises() -> None:
    app = FakeDocsApp()
    # isSaved says yes, but reading dataFile raises: the document counts as unsaved.
    doc = app.open_document("Untitled", file_id="urn:9")
    assert doc.isSaved is True
    doc.data_file_raises = True
    assert d.document_ref(doc)["ref"] == "document:Untitled"
    assert d.document_info(doc, app)["is_saved"] is False


def test_resolve_active_and_none_open() -> None:
    app = FakeDocsApp()
    with pytest.raises(BridgeError) as info:
        d.resolve_document(app, None)
    assert info.value.code is ErrorCode.NO_ACTIVE_DESIGN
    doc = app.open_document("A")
    assert d.resolve_document(app) is doc


def test_resolve_by_file_and_name() -> None:
    app = FakeDocsApp()
    saved = app.open_document("Bracket", file_id="urn:1")
    other = app.open_document("Shaft")
    assert d.resolve_document(app, "document:file:urn:1") is saved
    assert d.resolve_document(app, "document:Shaft") is other
    assert d.resolve_document(app, {"ref": "document:Bracket"}) is saved


def test_resolve_not_found() -> None:
    app = FakeDocsApp()
    app.open_document("A")
    for ref in ("document:B", "document:file:urn:none"):
        with pytest.raises(BridgeError) as info:
            d.resolve_document(app, ref)
        assert info.value.code is ErrorCode.ENTITY_NOT_FOUND
        assert info.value.details["ref"] == ref


def test_resolve_ambiguous_lists_candidates() -> None:
    app = FakeDocsApp()
    app.open_document("Untitled")
    app.open_document("Untitled")
    with pytest.raises(BridgeError) as info:
        d.resolve_document(app, "document:Untitled")
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["candidates"] == ["document:Untitled", "document:Untitled"]


@pytest.mark.parametrize("ref", ["", "document:", "Untitled", 3, "body:1"])
def test_resolve_malformed(ref: object) -> None:
    app = FakeDocsApp()
    app.open_document("A")
    with pytest.raises(BridgeError) as info:
        d.resolve_document(app, ref)
    assert info.value.code is ErrorCode.INVALID_PARAMS


def test_document_info() -> None:
    app = FakeDocsApp()
    saved = app.open_document("Bracket", file_id="urn:1", activate=False)
    active = app.open_document("Draft", modified=True)
    active.design.designType = adsk.fusion.DesignTypes.DirectDesignType
    active.design.fusionUnitsManager.distanceDisplayUnits = (
        adsk.fusion.DistanceUnits.InchDistanceUnits
    )
    assert d.document_info(saved, app) == {
        "ref": "document:file:urn:1",
        "type": "Document",
        "name": "Bracket",
        "is_active": False,
        "is_saved": True,
        "is_modified": False,
        "file_id": "urn:1",
        "version": 1,
        "design_type": "parametric",
        "units": "mm",
    }
    info = d.document_info(active, app)
    assert info["is_active"] is True
    assert info["is_modified"] is True
    assert (info["file_id"], info["version"]) == (None, None)
    assert (info["design_type"], info["units"]) == ("direct", "in")


def test_document_info_non_design() -> None:
    app = FakeDocsApp()
    doc = app.documents.add(adsk.core.DocumentTypes.FusionDrawingDocumentType)
    info = d.document_info(doc, app)
    assert info["design_type"] is None and info["units"] is None


@pytest.mark.parametrize("name", ["mm", "cm", "m", "in", "ft"])
def test_units_round_trip(name: str) -> None:
    assert d.units_from_fusion(d.units_to_fusion(name)) == name


def test_units_invalid() -> None:
    with pytest.raises(BridgeError) as info:
        d.units_to_fusion("yd")
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert d.units_from_fusion(99) is None

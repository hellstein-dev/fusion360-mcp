"""``document.*`` handlers on fake documents (c04 D3, tasks 2.1-2.5)."""

from pathlib import Path
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import document as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.documents import FakeDataFolder, FakeDocsApp

adsk = fusion_addin.adsk


def run(fn: Any, app: FakeDocsApp, params: dict[str, Any] | None = None) -> Any:
    params = params or {}
    return fn(Context(app, params), params)


def fails(fn: Any, app: FakeDocsApp, params: dict[str, Any], code: ErrorCode) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        run(fn, app, params)
    assert info.value.code is code, info.value
    return info.value


def test_registered_not_mutating() -> None:
    for name in (
        "document.list",
        "document.new",
        "document.open",
        "document.activate",
        "document.save",
        "document.close",
    ):
        spec = REGISTRY.get(name)
        assert spec is not None, name
        assert spec.mutating is False


# --- 2.1 list / activate -----------------------------------------------------------------------


def test_list_two_documents_one_active() -> None:
    app = FakeDocsApp()
    app.open_document("Bracket", file_id="urn:1")
    app.open_document("Draft", modified=True)
    out = run(h.document_list, app)
    assert [d["name"] for d in out["documents"]] == ["Bracket", "Draft"]
    assert [d["is_active"] for d in out["documents"]] == [False, True]
    assert set(out["documents"][0]) == {
        "ref",
        "type",
        "name",
        "is_active",
        "is_saved",
        "is_modified",
        "file_id",
        "version",
        "design_type",
        "units",
    }


def test_list_empty() -> None:
    assert run(h.document_list, FakeDocsApp()) == {"documents": []}


def test_activate_switches_document() -> None:
    app = FakeDocsApp()
    bracket = app.open_document("Bracket", file_id="urn:1")
    app.open_document("Draft")
    out = run(h.document_activate, app, {"document": "document:file:urn:1"})
    assert app.activeDocument is bracket
    assert app.activeProduct is bracket.design
    assert out["is_active"] is True and out["ref"] == "document:file:urn:1"


def test_activate_errors() -> None:
    app = FakeDocsApp()
    app.open_document("Untitled")
    app.open_document("Untitled")
    error = fails(
        h.document_activate, app, {"document": "document:Untitled"}, ErrorCode.INVALID_PARAMS
    )
    assert len(error.details["candidates"]) == 2
    fails(h.document_activate, app, {"document": "document:Nope"}, ErrorCode.ENTITY_NOT_FOUND)
    fails(h.document_activate, app, {}, ErrorCode.INVALID_PARAMS)


# --- 2.2 new -----------------------------------------------------------------------------------


def test_new_defaults() -> None:
    app = FakeDocsApp()
    previous = app.open_document("Old", file_id="urn:1")
    out = run(h.document_new, app)
    assert app.documents.added_types == [adsk.core.DocumentTypes.FusionDesignDocumentType]
    assert app.activeDocument is not previous
    assert out["is_active"] is True
    assert out["design_type"] == "parametric"
    assert out["units"] == "mm"
    assert out["is_saved"] is False
    assert app.documents.item(1).saved_with == []


def test_new_direct_in_inches() -> None:
    app = FakeDocsApp()
    out = run(h.document_new, app, {"design_type": "direct", "units": "in"})
    design = app.activeProduct
    assert design.designType == adsk.fusion.DesignTypes.DirectDesignType
    assert (
        design.fusionUnitsManager.distanceDisplayUnits
        == adsk.fusion.DistanceUnits.InchDistanceUnits
    )
    assert (out["design_type"], out["units"]) == ("direct", "in")


def test_new_rejects_bad_arguments_before_creating() -> None:
    app = FakeDocsApp()
    fails(h.document_new, app, {"design_type": "sheet"}, ErrorCode.INVALID_PARAMS)
    with pytest.raises(BridgeError):
        run(h.document_new, app, {"units": "yd"})
    assert app.documents.count == 0


def test_new_closes_document_when_setup_fails() -> None:
    app = FakeDocsApp()
    old = app.open_document("Old")
    original_add = app.documents.add

    def add(document_type: int) -> Any:
        doc = original_add(document_type)
        doc.design.fusionUnitsManager.fail_set_units = True
        return doc

    app.documents.add = add  # type: ignore[method-assign]
    error = fails(h.document_new, app, {"units": "cm"}, ErrorCode.FUSION_API_ERROR)
    assert "cannot change the distance units" in error.message
    assert list(app.documents) == [old]
    assert app.activeDocument is old


# --- 2.3 open ----------------------------------------------------------------------------------


def test_open_cloud_file() -> None:
    app = FakeDocsApp()
    data_file = app.data.add_file("urn:7", "Hinge")
    out = run(h.document_open, app, {"file_id": "urn:7"})
    assert app.documents.opened == [(data_file, True)]
    assert out["ref"] == "document:file:urn:7"
    assert out["is_active"] is True
    assert out["already_open"] is False


def test_open_already_open_activates() -> None:
    app = FakeDocsApp()
    hinge = app.open_document("Hinge", file_id="urn:7")
    app.open_document("Other")
    out = run(h.document_open, app, {"file_id": "urn:7"})
    assert app.activeDocument is hinge
    assert app.documents.opened == []
    assert out["already_open"] is True and out["is_active"] is True


def test_open_unknown_file() -> None:
    fails(h.document_open, FakeDocsApp(), {"file_id": "urn:x"}, ErrorCode.ENTITY_NOT_FOUND)


def test_open_needs_exactly_one_source(tmp_path: Path) -> None:
    app = FakeDocsApp()
    fails(h.document_open, app, {}, ErrorCode.INVALID_PARAMS)
    fails(
        h.document_open,
        app,
        {"file_id": "urn:1", "local_path": str(tmp_path / "a.f3d")},
        ErrorCode.INVALID_PARAMS,
    )
    fails(h.document_open, app, {"file_id": ""}, ErrorCode.INVALID_PARAMS)


def test_open_local_f3d(tmp_path: Path) -> None:
    path = tmp_path / "Bracket.F3D"
    path.write_bytes(b"archive")
    app = FakeDocsApp()
    out = run(h.document_open, app, {"local_path": str(path)})
    assert app.importManager.imported == [str(path)]
    assert out["name"] == "Bracket"
    assert out["is_active"] is True and out["already_open"] is False


def test_open_local_rejects_other_formats_and_missing(tmp_path: Path) -> None:
    step = tmp_path / "part.step"
    step.write_text("ISO-10303-21;")
    app = FakeDocsApp()
    error = fails(h.document_open, app, {"local_path": str(step)}, ErrorCode.INVALID_PARAMS)
    assert ".f3d" in error.message
    fails(
        h.document_open, app, {"local_path": str(tmp_path / "none.f3d")}, ErrorCode.INVALID_PARAMS
    )
    assert app.importManager.imported == []


def test_open_local_import_error(tmp_path: Path) -> None:
    path = tmp_path / "a.f3d"
    path.write_bytes(b"x")
    app = FakeDocsApp()
    app.importManager.fail = "version conflict"
    with pytest.raises(RuntimeError, match="version conflict"):
        run(h.document_open, app, {"local_path": str(path)})


# --- 2.4 save ----------------------------------------------------------------------------------


def test_first_save_into_root_folder() -> None:
    app = FakeDocsApp()
    doc = app.open_document("Untitled", modified=True)
    out = run(h.document_save, app, {"name": "Bracket", "description": "v1"})
    root = app.data.activeProject.rootFolder
    assert doc.saved_with == [("saveAs", "Bracket", root, "v1", "")]
    assert out["is_saved"] is True and out["name"] == "Bracket"
    assert out["upload"] == "pending"
    assert out["ref"].startswith("document:file:")


def test_first_save_into_folder_path() -> None:
    app = FakeDocsApp()
    inner = FakeDataFolder("B")
    app.data.activeProject.rootFolder = FakeDataFolder("root", [FakeDataFolder("A", [inner])])
    doc = app.open_document("Untitled")
    run(h.document_save, app, {"name": "Part", "folder_path": "A/B"})
    assert doc.saved_with[0][2] is inner


def test_first_save_missing_folder() -> None:
    app = FakeDocsApp()
    app.data.activeProject.rootFolder = FakeDataFolder("root", [FakeDataFolder("A")])
    doc = app.open_document("Untitled")
    error = fails(
        h.document_save, app, {"name": "Part", "folder_path": "A/Z/Q"}, ErrorCode.ENTITY_NOT_FOUND
    )
    assert error.details["missing"] == "A/Z"
    assert doc.saved_with == []
    assert app.data.activeProject.rootFolder.dataFolders.itemByName("A").dataFolders.count == 0


def test_first_save_needs_name() -> None:
    app = FakeDocsApp()
    doc = app.open_document("Untitled")
    fails(h.document_save, app, {}, ErrorCode.INVALID_PARAMS)
    assert doc.saved_with == []


def test_save_of_saved_document() -> None:
    app = FakeDocsApp()
    doc = app.open_document("Bracket", file_id="urn:1", modified=True)
    out = run(h.document_save, app, {"document": "document:file:urn:1", "description": "fix"})
    assert doc.saved_with == [("save", "fix")]
    assert out["version"] == 2 and out["is_modified"] is False
    assert out["upload"] == "pending"


def test_save_of_saved_document_rejects_name_and_folder() -> None:
    app = FakeDocsApp()
    doc = app.open_document("Bracket", file_id="urn:1")
    fails(h.document_save, app, {"name": "Copy"}, ErrorCode.INVALID_PARAMS)
    fails(h.document_save, app, {"folder_path": "A"}, ErrorCode.INVALID_PARAMS)
    assert doc.saved_with == []


def test_save_false_is_fusion_error() -> None:
    app = FakeDocsApp()
    doc = app.open_document("Bracket", file_id="urn:1")
    doc.save_result = False
    fails(h.document_save, app, {}, ErrorCode.FUSION_API_ERROR)
    unsaved = app.open_document("Untitled")
    unsaved.save_result = False
    fails(h.document_save, app, {"name": "X"}, ErrorCode.FUSION_API_ERROR)


# --- 2.5 close ---------------------------------------------------------------------------------


def test_close_unmodified_returns_new_active() -> None:
    app = FakeDocsApp()
    other = app.open_document("Other", file_id="urn:2")
    doc = app.open_document("Bracket", file_id="urn:1")
    out = run(h.document_close, app)
    assert doc.closed_with is False
    assert out["closed"] == {"ref": "document:file:urn:1", "type": "Document", "name": "Bracket"}
    assert out["active"]["ref"] == "document:file:urn:2"
    assert app.activeDocument is other


def test_close_last_document() -> None:
    app = FakeDocsApp()
    app.open_document("Only")
    out = run(h.document_close, app)
    assert out["active"] is None


def test_close_guard_unsaved_changes() -> None:
    app = FakeDocsApp()
    doc = app.open_document("Draft", modified=True)
    error = fails(h.document_close, app, {}, ErrorCode.INVALID_PARAMS)
    assert "save_changes=true or discard_changes=true" in error.message
    assert list(app.documents) == [doc]


def test_close_discard_changes() -> None:
    app = FakeDocsApp()
    doc = app.open_document("Draft", modified=True)
    run(h.document_close, app, {"discard_changes": True})
    assert doc.closed_with is False
    assert app.documents.count == 0


def test_close_save_changes() -> None:
    app = FakeDocsApp()
    doc = app.open_document("Bracket", file_id="urn:1", modified=True)
    run(h.document_close, app, {"document": "document:Bracket", "save_changes": True})
    assert doc.closed_with is True
    assert doc.saved_with == [("save", "")]


def test_close_flags_exclusive() -> None:
    app = FakeDocsApp()
    doc = app.open_document("Bracket", file_id="urn:1", modified=True)
    fails(
        h.document_close,
        app,
        {"save_changes": True, "discard_changes": True},
        ErrorCode.INVALID_PARAMS,
    )
    assert list(app.documents) == [doc]


def test_close_save_changes_of_never_saved() -> None:
    app = FakeDocsApp()
    doc = app.open_document("Draft", modified=True)
    error = fails(h.document_close, app, {"save_changes": True}, ErrorCode.INVALID_PARAMS)
    assert "document_save" in error.message
    assert list(app.documents) == [doc]


def test_close_no_document() -> None:
    fails(h.document_close, FakeDocsApp(), {}, ErrorCode.NO_ACTIVE_DESIGN)

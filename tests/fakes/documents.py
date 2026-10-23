"""Fake Fusion documents (c04 D2, D3): ``app.documents``, ``activeDocument``, ``app.data``
and ``app.importManager``.

``FakeDocsApp`` keeps ``activeDocument``/``activeProduct`` in step with the open documents,
like Fusion: a new or opened document becomes active; closing the active one activates the
last remaining document.
"""

from __future__ import annotations

import itertools
from typing import Any

import fusion_addin

from .design import FakeCollection, FakeDesign, FakeUI
from .parameters import attach_parameters

adsk = fusion_addin.adsk

_ids = itertools.count(1)


class FakeDataFile:
    def __init__(self, id: str, name: str, folder: Any = None, version: int = 1) -> None:
        self.id = id
        self.name = name
        self.parentFolder = folder
        self.versionNumber = version


class FakeDataFolder:
    def __init__(self, name: str, children: list[FakeDataFolder] | None = None) -> None:
        self.name = name
        self.id = f"folder-{name}"
        self.dataFolders = FakeCollection(children or [])


class FakeProject:
    def __init__(self, root: FakeDataFolder | None = None) -> None:
        self.name = "Default Project"
        self.rootFolder = root if root is not None else FakeDataFolder("root")


class FakeData:
    """``app.data``: the active project and ``findFileById`` over known cloud files."""

    def __init__(self, project: FakeProject | None = None) -> None:
        self.activeProject = project if project is not None else FakeProject()
        self.files: dict[str, FakeDataFile] = {}

    def add_file(self, id: str, name: str) -> FakeDataFile:
        data_file = FakeDataFile(id, name, self.activeProject.rootFolder)
        self.files[id] = data_file
        return data_file

    def findFileById(self, id: str) -> FakeDataFile | None:
        return self.files.get(id)


class FakeProducts:
    def __init__(self, design: Any) -> None:
        self._design = design

    def itemByProductType(self, product_type: str) -> Any:
        return self._design if product_type == "DesignProductType" else None


class FakeDocument:
    """``adsk.core.Document`` with a design product (``design=None``: a non-design document)."""

    objectType = "adsk::fusion::FusionDocument"

    def __init__(
        self,
        app: FakeDocsApp,
        name: str = "Untitled",
        design: Any = "new",
        data_file: FakeDataFile | None = None,
        modified: bool = False,
    ) -> None:
        self._app = app
        self.name = name
        if design == "new":
            design = FakeDesign(name=name)
            attach_parameters(design)
        self.design = design
        if design is not None:
            design.parentDocument = self
        self.products = FakeProducts(design)
        self._data_file = data_file
        self.isModified = modified
        # Set to make ``dataFile`` raise, as Fusion does for some unsaved documents.
        self.data_file_raises = False
        self.save_result = True
        self.saved_with: list[tuple[Any, ...]] = []
        self.closed_with: bool | None = None

    @property
    def isSaved(self) -> bool:
        return self._data_file is not None

    @property
    def dataFile(self) -> FakeDataFile | None:
        if self.data_file_raises:
            raise RuntimeError("the document has not been saved")
        return self._data_file

    def activate(self) -> bool:
        self._app.active = self
        return True

    def save(self, description: str) -> bool:
        self.saved_with.append(("save", description))
        if not self.save_result:
            return False
        assert self._data_file is not None, "save() on a never-saved document"
        self._data_file.versionNumber += 1
        self.isModified = False
        return True

    def saveAs(self, name: str, folder: Any, description: str, tag: str) -> bool:
        self.saved_with.append(("saveAs", name, folder, description, tag))
        if not self.save_result:
            return False
        self.name = name
        self._data_file = FakeDataFile(f"urn:file:{next(_ids)}", name, folder)
        self.isModified = False
        return True

    def close(self, saveChanges: bool) -> bool:
        if saveChanges and self.isModified:
            self.save("")
        self.closed_with = saveChanges
        self._app.documents.remove(self)
        return True

    def __repr__(self) -> str:
        return f"<FakeDocument {self.name}>"


class FakeDocuments:
    """``app.documents``."""

    def __init__(self, app: FakeDocsApp) -> None:
        self._app = app
        self._items: list[FakeDocument] = []
        self.added_types: list[int] = []
        self.opened: list[tuple[Any, bool]] = []

    @property
    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> FakeDocument:
        return self._items[index]

    def __iter__(self) -> Any:
        return iter(list(self._items))

    def adopt(self, doc: FakeDocument, activate: bool = True) -> FakeDocument:
        self._items.append(doc)
        if activate:
            doc.activate()
        return doc

    def add(self, document_type: int) -> FakeDocument:
        self.added_types.append(document_type)
        design = None
        if document_type == adsk.core.DocumentTypes.FusionDesignDocumentType:
            design = "new"
        return self.adopt(FakeDocument(self._app, "Untitled", design))

    def open(self, data_file: FakeDataFile, visible: bool = True) -> FakeDocument:
        self.opened.append((data_file, visible))
        return self.adopt(FakeDocument(self._app, data_file.name, data_file=data_file))

    def remove(self, doc: FakeDocument) -> None:
        self._items.remove(doc)
        if self._app.active is doc:
            self._app.active = self._items[-1] if self._items else None


class FakeArchiveOptions:
    def __init__(self, filename: str) -> None:
        self.filename = filename


class FakeImportManager:
    def __init__(self, app: FakeDocsApp) -> None:
        self._app = app
        self.imported: list[str] = []
        self.fail: str | None = None

    def createFusionArchiveImportOptions(self, filename: str) -> FakeArchiveOptions:
        return FakeArchiveOptions(filename)

    def importToNewDocument(self, options: FakeArchiveOptions) -> FakeDocument:
        if self.fail is not None:
            raise RuntimeError(self.fail)
        self.imported.append(options.filename)
        stem = options.filename.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return self._app.documents.adopt(FakeDocument(self._app, stem))


class FakeDocsApp:
    """``adsk.core.Application`` with documents; ``activeProduct`` is the active design."""

    def __init__(self, version: str = "2.0.21000") -> None:
        self.userInterface = FakeUI()
        self.version = version
        self.active: FakeDocument | None = None
        self.documents = FakeDocuments(self)
        self.data = FakeData()
        self.importManager = FakeImportManager(self)

    @property
    def activeDocument(self) -> FakeDocument | None:
        return self.active

    @property
    def activeProduct(self) -> Any:
        return self.active.design if self.active is not None else None

    def open_document(
        self,
        name: str = "Untitled",
        file_id: str | None = None,
        modified: bool = False,
        design: Any = "new",
        activate: bool = True,
    ) -> FakeDocument:
        """Test helper: an already open document, saved when ``file_id`` is given."""
        data_file = self.data.add_file(file_id, name) if file_id is not None else None
        doc = FakeDocument(self, name, design, data_file, modified)
        return self.documents.adopt(doc, activate)

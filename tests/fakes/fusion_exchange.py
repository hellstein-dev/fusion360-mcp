"""Fake ``ExportManager``, ``ImportManager`` and ``MeshBodies`` for ``export.*`` / ``import.*``
handler tests (c12 D7-D11).

- ``FakeExportManager`` (``design.exportManager``): every ``create*Options`` method returns a
  ``FakeExportOptions`` recording its kind, file name and geometry (mesh options carry the
  mesh settings; STL/OBJ also ``unitType``, STL ``isBinaryFormat``; 3MF has no ``unitType``).
  ``execute(options)`` records the options and writes ``size`` bytes to the file (OBJ also a
  ``<stem>.mtl`` when ``write_mtl``); ``result=False`` returns ``False`` without writing,
  ``write_file=False`` returns ``True`` without writing. Names in ``missing`` (e.g.
  ``"createUSDExportOptions"``) are absent, like an older Fusion.
- ``FakeImportManager`` (``app.importManager``): ``create<X>ImportOptions(filename)``,
  ``importToTarget2`` / ``importToTarget`` call ``produce(options, component)`` (default: a new
  occurrence in ``component``) and return an ``ObjectCollection`` / ``True``; ``result`` overrides
  the return value; ``missing`` hides methods.
- ``FakeMeshBodies`` (``component.meshBodies``): ``add(fullFilename, units, base=None)`` records
  the call and adds a ``FakeMeshBody`` (``return_none`` makes it return ``None``).
- ``install_exchange(app, design)`` wires everything to the root component (plus
  ``allOccurrencesByComponent``) and returns ``(export_manager, import_manager)``.
"""

from __future__ import annotations

import itertools
import os
from typing import Any

import fusion_addin

from .brep import FakeComponent, FakeOccurrence
from .design import FakeCollection

adsk = fusion_addin.adsk

_ids = itertools.count(1)

MESH_KINDS = ("stl", "3mf", "obj")


class FakeExportOptions:
    def __init__(self, kind: str, filename: str, geometry: Any) -> None:
        self.kind = kind
        self.filename = filename
        self.geometry = geometry
        if kind in MESH_KINDS:
            self.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementMedium
            self.surfaceDeviation = 0.0
            self.normalDeviation = 0.0
            self.maximumEdgeLength = 0.0
            self.aspectRatio = 0.0
            self.isOneFilePerBody = False
            self.sendToPrintUtility = False
        if kind in ("stl", "obj"):
            self.unitType = adsk.fusion.DistanceUnits.CentimeterDistanceUnits
        if kind == "stl":
            self.isBinaryFormat = True


class _Hiding:
    """Hides the attribute names listed in ``missing`` (an older Fusion without them)."""

    missing: set[str]

    def __getattribute__(self, name: str) -> Any:
        if name != "missing" and name in object.__getattribute__(self, "missing"):
            raise AttributeError(name)
        return object.__getattribute__(self, name)


class FakeExportManager(_Hiding):
    def __init__(self) -> None:
        self.missing = set()
        self.created: list[FakeExportOptions] = []
        self.executed: list[FakeExportOptions] = []
        self.result = True
        self.write_file = True
        self.size = 128
        self.write_mtl = True

    def _make(self, kind: str, filename: str, geometry: Any) -> FakeExportOptions:
        options = FakeExportOptions(kind, filename, geometry)
        self.created.append(options)
        return options

    def createSTEPExportOptions(self, filename: str, geometry: Any = None) -> FakeExportOptions:
        return self._make("step", filename, geometry)

    def createIGESExportOptions(self, filename: str, geometry: Any = None) -> FakeExportOptions:
        return self._make("iges", filename, geometry)

    def createSATExportOptions(self, filename: str, geometry: Any = None) -> FakeExportOptions:
        return self._make("sat", filename, geometry)

    def createSMTExportOptions(self, filename: str, geometry: Any = None) -> FakeExportOptions:
        return self._make("smt", filename, geometry)

    def createFusionArchiveExportOptions(
        self, filename: str, geometry: Any = None
    ) -> FakeExportOptions:
        return self._make("f3d", filename, geometry)

    def createUSDExportOptions(self, filename: str, geometry: Any = None) -> FakeExportOptions:
        return self._make("usdz", filename, geometry)

    def createSTLExportOptions(self, geometry: Any, filename: str = "") -> FakeExportOptions:
        return self._make("stl", filename, geometry)

    def createC3MFExportOptions(self, geometry: Any, filename: str = "") -> FakeExportOptions:
        return self._make("3mf", filename, geometry)

    def createOBJExportOptions(self, geometry: Any, filename: str = "") -> FakeExportOptions:
        return self._make("obj", filename, geometry)

    def createDXFSketchExportOptions(self, filename: str, sketch: Any) -> FakeExportOptions:
        return self._make("dxf", filename, sketch)

    def execute(self, options: FakeExportOptions) -> bool:
        self.executed.append(options)
        if not self.result:
            return False
        if self.write_file:
            write_file(options.filename, self.size)
            if options.kind == "obj" and self.write_mtl:
                write_file(os.path.splitext(options.filename)[0] + ".mtl", 16)
        return True


def write_file(path: str, size: int) -> None:
    with open(path, "wb") as handle:
        handle.write(b"x" * size)


class FakeImportOptions:
    def __init__(self, kind: str, filename: str) -> None:
        self.kind = kind
        self.filename = filename


class FakeMeshBody:
    objectType = "adsk::fusion::MeshBody"

    def __init__(self, name: str, owner: Any = None) -> None:
        self.name = name
        self.entityToken = f"mesh-{next(_ids)}"
        self.owner = owner
        self.deleted = False

    def deleteMe(self) -> bool:
        self.deleted = True
        if self.owner is not None:
            self.owner.remove(self)
        return True


class FakeMeshBodies(FakeCollection):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[Any, ...]] = []
        self.return_none = False

    def add(self, *args: Any) -> Any:
        """``add(fullFilename, units, baseOrFormFeature=None)`` like ``MeshBodies.add``."""
        self.calls.append(args)
        if self.return_none:
            return None
        body = FakeMeshBody(os.path.splitext(os.path.basename(args[0]))[0], self)
        self._items.append(body)
        return body

    def remove(self, item: Any) -> None:
        self._items.remove(item)


class FakeImportManager(_Hiding):
    def __init__(self, root: Any) -> None:
        self.missing = set()
        self.root = root
        self.created: list[FakeImportOptions] = []
        self.calls: list[tuple[str, Any, Any]] = []
        self.result: Any = None
        self.produce: Any = self.add_occurrence

    def add_occurrence(self, options: FakeImportOptions, component: Any) -> list[Any]:
        name = os.path.splitext(os.path.basename(options.filename))[0]
        occurrence = FakeOccurrence(FakeComponent(name), f"{name}:1")
        component.occurrences.add(occurrence)
        self.root.allOccurrences.add(occurrence)
        return [occurrence]

    def _options(self, kind: str, filename: str) -> FakeImportOptions:
        options = FakeImportOptions(kind, filename)
        self.created.append(options)
        return options

    def createSTEPImportOptions(self, filename: str) -> FakeImportOptions:
        return self._options("step", filename)

    def createIGESImportOptions(self, filename: str) -> FakeImportOptions:
        return self._options("iges", filename)

    def createSATImportOptions(self, filename: str) -> FakeImportOptions:
        return self._options("sat", filename)

    def createSMTImportOptions(self, filename: str) -> FakeImportOptions:
        return self._options("smt", filename)

    def createFusionArchiveImportOptions(self, filename: str) -> FakeImportOptions:
        return self._options("f3d", filename)

    def importToTarget2(self, options: FakeImportOptions, target: Any) -> Any:
        self.calls.append(("importToTarget2", options, target))
        created = self.produce(options, target)
        if self.result is not None:
            return self.result
        return adsk.core.ObjectCollection.createWithArray(created)

    def importToTarget(self, options: FakeImportOptions, target: Any) -> Any:
        self.calls.append(("importToTarget", options, target))
        self.produce(options, target)
        return True if self.result is None else self.result


def install_exchange(app: Any, design: Any) -> tuple[FakeExportManager, FakeImportManager]:
    root = design.rootComponent
    design.exportManager = FakeExportManager()
    app.importManager = FakeImportManager(root)
    root.meshBodies = FakeMeshBodies()

    def by_component(component: Any) -> FakeCollection:
        return FakeCollection([o for o in root.allOccurrences if o.component is component])

    root.allOccurrencesByComponent = by_component
    return design.exportManager, app.importManager

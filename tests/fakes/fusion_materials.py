"""Fake material libraries, materials and appearances for ``material.*`` / ``appearance.*``
handler tests (c12 D4-D6).

- ``FakeMaterialLibraries`` (``app.materialLibraries``): ``item``, ``count``, ``itemById``,
  ``itemByName`` over ``FakeMaterialLibrary`` (``name``, ``id``, ``isNative``,
  ``materials``, ``appearances``).
- ``FakeMaterials`` / ``FakeAppearances``: ``item``, ``count``, ``itemById``,
  ``itemByName``; the design collections also have ``addByCopy(src, name)``. A copy keeps
  the source ``id`` when it keeps its name (Fusion's copy of a library item is found by the
  library id), otherwise gets a new one. ``fail_add_by_copy`` makes ``addByCopy`` raise.
- ``FakeAppearance``: ``color`` (an ``adsk.core.Color``; absent with ``has_color=False``, an
  older API), ``appearanceProperties`` (``itemById`` -> ``FakeColorProperty`` with ``value``),
  ``isUsed`` (true while an entity in the design collection's ``users`` has it as
  ``appearance``), ``deleteMe`` (removes it from its collection, records ``deleted``).
- ``install_materials(app, design, libraries)`` hangs the libraries on ``app`` and empty
  design collections on ``design``; ``standard_libraries()`` is a small default set.
"""

from __future__ import annotations

import itertools
from typing import Any

import fusion_addin

adsk = fusion_addin.adsk

_ids = itertools.count(1)


class FakeColorProperty:
    objectType = "adsk::core::ColorProperty"

    def __init__(self, id: str, rgba: tuple[int, int, int, int]) -> None:
        self.id = id
        self.value = adsk.core.Color.create(*rgba)


class FakeProperties:
    def __init__(self, items: list[Any] | None = None) -> None:
        self._items = list(items or [])

    @property
    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> Any:
        return self._items[index]

    def itemById(self, id: str) -> Any:
        return next((p for p in self._items if p.id == id), None)


class FakeMaterial:
    objectType = "adsk::core::Material"

    def __init__(self, id: str, name: str, appearance: Any = None) -> None:
        self.id = id
        self.name = name
        self.appearance = appearance
        self.owner: Any = None

    def copy(self, name: str) -> FakeMaterial:
        return FakeMaterial(self.id if name == self.name else f"copy-{next(_ids)}", name)


class FakeAppearance:
    objectType = "adsk::core::Appearance"

    def __init__(
        self,
        id: str,
        name: str,
        rgba: tuple[int, int, int, int] | None = (128, 128, 128, 255),
        has_color: bool = True,
        properties: list[Any] | None = None,
    ) -> None:
        self.id = id
        self.name = name
        if has_color:
            self.color = adsk.core.Color.create(*(rgba or (0, 0, 0, 255)))
        self.appearanceProperties = FakeProperties(properties)
        self.owner: Any = None
        self.deleted = False

    @property
    def isUsed(self) -> bool:
        users = getattr(self.owner, "users", [])
        return any(getattr(u, "appearance", None) is self for u in users)

    def deleteMe(self) -> bool:
        self.deleted = True
        if self.owner is not None:
            self.owner.remove(self)
        return True

    def copy(self, name: str) -> FakeAppearance:
        new_id = self.id if name == self.name else f"copy-{next(_ids)}"
        has_color = hasattr(self, "color")
        rgba = None
        if has_color:
            c = self.color
            rgba = (c.red, c.green, c.blue, c.opacity)
        properties = [
            FakeColorProperty(p.id, (p.value.red, p.value.green, p.value.blue, p.value.opacity))
            for p in self.appearanceProperties._items
        ]
        return FakeAppearance(new_id, name, rgba, has_color, properties)


class FakeItems:
    """``Materials`` / ``Appearances`` of a library or of the design."""

    def __init__(self, items: list[Any] | None = None, design: bool = False) -> None:
        self._items: list[Any] = []
        self.design = design
        self.users: list[Any] = []
        self.fail_add_by_copy = False
        self.copies: list[tuple[Any, str]] = []
        for item in items or []:
            self._append(item)

    def _append(self, item: Any) -> Any:
        item.owner = self
        self._items.append(item)
        return item

    @property
    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> Any:
        return self._items[index]

    def itemById(self, id: str) -> Any:
        return next((i for i in self._items if i.id == id), None)

    def itemByName(self, name: str) -> Any:
        return next((i for i in self._items if i.name == name), None)

    def addByCopy(self, source: Any, name: str) -> Any:
        if not self.design:
            raise RuntimeError("library collections are read-only")
        if self.fail_add_by_copy:
            raise RuntimeError("addByCopy failed")
        self.copies.append((source, name))
        return self._append(source.copy(name))

    def remove(self, item: Any) -> None:
        self._items.remove(item)


class FakeMaterialLibrary:
    objectType = "adsk::core::MaterialLibrary"

    def __init__(
        self,
        name: str,
        id: str,
        materials: list[FakeMaterial] | None = None,
        appearances: list[FakeAppearance] | None = None,
        is_native: bool = True,
    ) -> None:
        self.name = name
        self.id = id
        self.isNative = is_native
        self.materials = FakeItems(materials)
        self.appearances = FakeItems(appearances)


class FakeMaterialLibraries:
    def __init__(self, libraries: list[FakeMaterialLibrary]) -> None:
        self._items = list(libraries)

    @property
    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> FakeMaterialLibrary:
        return self._items[index]

    def itemById(self, id: str) -> FakeMaterialLibrary | None:
        return next((i for i in self._items if i.id == id), None)

    def itemByName(self, name: str) -> FakeMaterialLibrary | None:
        return next((i for i in self._items if i.name == name), None)


def standard_libraries() -> list[FakeMaterialLibrary]:
    """Material library (Steel, Aluminum, ABS Plastic), appearance library (red paint,
    satin steel, legacy blue without ``color``) and a custom library that also has a
    material and an appearance named like the standard ones."""
    red = FakeAppearance("app-paint-red", "Paint - Enamel Glossy (Red)", (200, 0, 0, 255))
    satin = FakeAppearance("app-steel-satin", "Steel - Satin", (180, 180, 180, 255))
    legacy = FakeAppearance(
        "app-legacy-blue",
        "Legacy Blue",
        has_color=False,
        properties=[FakeColorProperty("generic_diffuse", (0, 0, 200, 255))],
    )
    return [
        FakeMaterialLibrary(
            "Fusion Material Library",
            "lib-materials",
            [
                FakeMaterial("mat-steel", "Steel", satin),
                FakeMaterial("mat-aluminum", "Aluminum"),
                FakeMaterial("mat-abs", "ABS Plastic"),
            ],
        ),
        FakeMaterialLibrary(
            "Fusion Appearance Library", "lib-appearances", [], [red, satin, legacy]
        ),
        FakeMaterialLibrary(
            "Custom Library",
            "lib-custom",
            [FakeMaterial("mat-custom-steel", "Steel")],
            [FakeAppearance("app-custom-satin", "Steel - Satin", (10, 10, 10, 255))],
            is_native=False,
        ),
    ]


def install_materials(
    app: Any, design: Any, libraries: list[FakeMaterialLibrary] | None = None
) -> FakeMaterialLibraries:
    """``app.materialLibraries`` and empty ``design.materials`` / ``design.appearances``."""
    app.materialLibraries = FakeMaterialLibraries(
        libraries if libraries is not None else standard_libraries()
    )
    design.materials = FakeItems(design=True)
    design.appearances = FakeItems(design=True)
    return app.materialLibraries

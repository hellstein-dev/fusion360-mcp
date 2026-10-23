"""Fake Fusion ``Application``/``Design`` for add-in tests.

``FakeDesign`` subclasses the stub ``adsk.fusion.Design`` (reached through the add-in
package, INV-1), so ``Design.cast`` accepts it as Fusion would.
"""

from __future__ import annotations

from typing import Any

import fusion_addin

from .timeline import FakeTimeline

adsk = fusion_addin.adsk


class FakeCollection:
    def __init__(self, items: list[Any] | None = None) -> None:
        self._items = list(items or [])

    @property
    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> Any:
        return self._items[index]

    def itemByName(self, name: str) -> Any:
        return next((i for i in self._items if i.name == name), None)

    def add(self, item: Any) -> Any:
        self._items.append(item)
        return item

    def __iter__(self) -> Any:
        return iter(self._items)


class FakeEntity:
    def __init__(
        self,
        object_type: str,
        name: str | None = None,
        token: str | None = None,
        **attrs: Any,
    ) -> None:
        self.objectType = object_type
        if name is not None:
            self.name = name
        self.entityToken = token
        for key, value in attrs.items():
            setattr(self, key, value)


class FakeDesign(adsk.fusion.Design):
    def __init__(
        self,
        parametric: bool = True,
        timeline: FakeTimeline | None = None,
        name: str = "Untitled",
    ) -> None:
        types = adsk.fusion.DesignTypes
        self.designType = types.ParametricDesignType if parametric else types.DirectDesignType
        self._timeline = timeline if timeline is not None else FakeTimeline()
        self.parametric = parametric
        self.by_token: dict[str, Any] = {}
        self.allParameters = FakeCollection()
        self.allComponents = FakeCollection()
        self.rootComponent = FakeEntity(
            "adsk::fusion::Component", "root", allOccurrences=FakeCollection()
        )
        self.allComponents.add(self.rootComponent)
        self.fusionUnitsManager = FakeEntity(
            "adsk::fusion::FusionUnitsManager", defaultLengthUnits="mm"
        )
        self.parentDocument = FakeEntity("adsk::core::Document", name)

    @property
    def timeline(self) -> FakeTimeline:
        if not self.parametric:
            raise RuntimeError("direct designs have no timeline")
        return self._timeline

    def add_entity(self, entity: Any) -> Any:
        if entity.entityToken:
            self.by_token[entity.entityToken] = entity
        return entity

    def findEntityByToken(self, token: str) -> list[Any]:
        if token.startswith("bad"):
            raise RuntimeError("invalid entity token")
        entity = self.by_token.get(token)
        return [entity] if entity is not None else []


class FakeUI:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def messageBox(self, text: str, *args: Any) -> int:
        self.messages.append(text)
        return 0


class FakeApp:
    def __init__(self, design: Any = None, version: str = "2.0.21000") -> None:
        self.activeProduct = design
        self.userInterface = FakeUI()
        self.version = version
        self.activeDocument = getattr(design, "parentDocument", None)

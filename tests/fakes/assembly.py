"""Fake assembly objects for the c10 tests (design D9), on top of tests/fakes/brep.py.

- ``FakeProxy(native, occurrence)``: an entity seen through an occurrence. It has its own
  token, ``nativeObject`` and ``assemblyContext``; other attributes come from the native
  entity. Native fakes build it with ``createForAssemblyContext`` (``brep.Proxyable``).
- ``FakeOccurrences``: ``Component.occurrences`` with ``addNewComponent`` /
  ``addExistingComponent`` / ``addNewComponentCopy``; new occurrences are registered in
  ``root.allOccurrences`` (nested ones as root-context copies, like Fusion's proxies).
- ``FakeSnapshots``: ``Design.snapshots``.
- ``fake_assembly_design()``: ``fake_part_design`` plus the above, ``activateRootComponent``
  and ``add_component``. Matrices are the stub ``adsk.core.Matrix3D``.
"""

from __future__ import annotations

import itertools
from typing import Any

from .brep import FakeComponent, FakeOccurrence, fake_part_design, register_body
from .design import FakeCollection, FakeDesign

_ids = itertools.count(1)


class FakeProxy:
    """``native`` in the context of ``occurrence`` (a ``FakeOccurrence``)."""

    def __init__(self, native: Any, occurrence: Any) -> None:
        self.nativeObject = native
        self.assemblyContext = occurrence
        self.objectType = native.objectType
        self.entityToken = f"proxy-{next(_ids)}"

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes not set in __init__: delegate to the native entity.
        return getattr(self.__dict__["nativeObject"], name)

    def createForAssemblyContext(self, occurrence: Any) -> FakeProxy:
        return FakeProxy(self.nativeObject, occurrence)


class FakeSnapshots:
    """``Design.snapshots``: ``add()`` captures a pending position change."""

    def __init__(self) -> None:
        self.hasPendingSnapshot = False
        self.added = 0

    def add(self) -> bool:
        self.added += 1
        self.hasPendingSnapshot = False
        return True


class FakeOccurrences(FakeCollection):
    """``Component.occurrences`` of ``owner`` in ``design``; records the matrices it got."""

    def __init__(self, owner: FakeComponent, design: FakeDesign) -> None:
        super().__init__()
        self.owner = owner
        self.design = design
        self.calls: list[tuple[str, Any]] = []
        self.copy_supported = True

    def _insert(self, component: FakeComponent, transform: Any) -> FakeOccurrence:
        root = self.design.rootComponent
        index = 1 + sum(1 for o in self if o.component is component)
        occurrence = FakeOccurrence(component, None, transform=transform.copy(), index=index)
        self.add(occurrence)
        if self.owner is root:
            root.allOccurrences.add(occurrence)
        else:
            # Seen from root: one copy per occurrence of the owner component.
            for parent in list(root.allOccurrences):
                if parent.component is self.owner:
                    copy = occurrence.createForAssemblyContext(parent)
                    parent.childOccurrences.add(copy)
                    root.allOccurrences.add(copy)
        return occurrence

    def addNewComponent(self, transform: Any) -> FakeOccurrence:
        self.calls.append(("addNewComponent", transform))
        component = new_component(self.design, f"Component{len(self.design.allComponents._items)}")
        return self._insert(component, transform)

    def addExistingComponent(self, component: FakeComponent, transform: Any) -> FakeOccurrence:
        self.calls.append(("addExistingComponent", transform))
        return self._insert(component, transform)

    def addNewComponentCopy(self, component: FakeComponent, transform: Any) -> FakeOccurrence:
        if not self.copy_supported:
            raise AttributeError("'Occurrences' object has no attribute 'addNewComponentCopy'")
        self.calls.append(("addNewComponentCopy", transform))
        copy = new_component(self.design, f"{component.name} (1)")
        for body in component.bRepBodies:
            copy.bRepBodies.add(body)
        return self._insert(copy, transform)


def new_component(design: FakeDesign, name: str) -> FakeComponent:
    """A component registered in ``design.allComponents`` with its own ``FakeOccurrences``."""
    component = FakeComponent(name)
    component.occurrences = FakeOccurrences(component, design)
    component.sketches.design = design
    design.allComponents.add(component)
    design.add_entity(component)
    for element in component.origin_elements().values():
        design.add_entity(element)
    return component


def add_component(
    design: FakeDesign, name: str, *bodies: Any, parent: Any = None, transform: Any = None
) -> tuple[FakeComponent, FakeOccurrence]:
    """New component ``name`` holding ``bodies``, inserted once under ``parent`` (an
    occurrence; default root). Returns the component and its (root-context) occurrence."""
    import fusion_addin

    component = new_component(design, name)
    for body in bodies:
        component.add_body(body)
        register_body(design, body)
    matrix = transform if transform is not None else fusion_addin.adsk.core.Matrix3D.create()
    root = design.rootComponent
    if parent is None:
        return component, root.occurrences.addExistingComponent(component, matrix)
    parent.component.occurrences.addExistingComponent(component, matrix)
    found = [o for o in root.allOccurrences if o.component is component]
    return component, found[-1]


def fake_assembly_design(*bodies: Any, parametric: bool = True) -> Any:
    """``fake_part_design`` with ``FakeOccurrences`` on root, ``snapshots`` and
    ``activateRootComponent``."""
    design = fake_part_design(*bodies, parametric=parametric)
    root = design.rootComponent
    root.occurrences = FakeOccurrences(root, design)
    design.add_entity(root)
    design.snapshots = FakeSnapshots()  # type: ignore[attr-defined]
    design.activateRootComponent = lambda: _activate_root(design)  # type: ignore[attr-defined]
    return design


def _activate_root(design: FakeDesign) -> bool:
    design.activeComponent = None
    return True

"""Fake construction collections and inputs for ``construction.*`` handler tests (c06 D6).

``FakeConstructionPlanes/Axes/Points`` stand in for ``component.constructionPlanes`` etc.:
``createInput(occurrence=None)`` hands out an input that records every ``setBy…`` call with
its arguments; ``add(input)`` returns a c03 ``FakeConstructionPlane/Axis/Point`` with
``healthState``, adds it to the design's timeline and makes it findable by token. Deleting
the element (``deleteMe``, as the dispatcher's rollback does through the timeline item)
removes it from the collection and the timeline.

Knobs on a collection: ``set_result`` (what ``setBy…`` returns), ``set_error`` / ``add_error``
(message of a ``RuntimeError`` raised there), ``health`` / ``message`` (state of the next
element) and ``geometry`` (override of the computed geometry). Geometry is computed for a few
methods (offset by a real value, axis of two planes, center of a circle/arc, vertex);
otherwise it is the origin with +Z.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import fusion_addin

from .brep import (
    FakeConstructionAxis,
    FakeConstructionPlane,
    FakeConstructionPoint,
    fake_part_design,
)
from .design import FakeCollection, FakeDesign

adsk = fusion_addin.adsk
P = adsk.core.Point3D.create
V = adsk.core.Vector3D.create
HEALTH = adsk.fusion.FeatureHealthStates

PLANE_SETTERS = (
    "setByOffset",
    "setByAngle",
    "setByTwoPlanes",
    "setByTwoEdges",
    "setByThreePoints",
    "setByTangent",
    "setByTangentAtPoint",
    "setByDistanceOnPath",
)
AXIS_SETTERS = (
    "setByTwoPoints",
    "setByEdge",
    "setByCircularFace",
    "setByPerpendicularAtPoint",
    "setByTwoPlanes",
)
POINT_SETTERS = ("setByPoint", "setByTwoEdges", "setByThreePlanes", "setByEdgePlane", "setByCenter")


class _FakeConstructionInput:
    """Base of the three input fakes; the ``setBy…`` methods are added per kind below."""

    objectType = ""

    def __init__(self, collection: _FakeConstructionCollection, occurrence: Any) -> None:
        self.collection = collection
        self.occurrence = occurrence
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @property
    def call(self) -> tuple[str, tuple[Any, ...]] | None:
        """The last ``setBy…`` call: ``(name, args)``."""
        return self.calls[-1] if self.calls else None

    def _set(self, name: str, args: tuple[Any, ...]) -> bool:
        self.calls.append((name, args))
        if self.collection.set_error is not None:
            raise RuntimeError(self.collection.set_error)
        return self.collection.set_result


def _setter(name: str) -> Callable[..., bool]:
    def set_by(self: _FakeConstructionInput, *args: Any) -> bool:
        return self._set(name, args)

    set_by.__name__ = name
    return set_by


class FakeConstructionPlaneInput(_FakeConstructionInput):
    objectType = "adsk::fusion::ConstructionPlaneInput"


class FakeConstructionAxisInput(_FakeConstructionInput):
    objectType = "adsk::fusion::ConstructionAxisInput"


class FakeConstructionPointInput(_FakeConstructionInput):
    objectType = "adsk::fusion::ConstructionPointInput"


for _cls, _names in (
    (FakeConstructionPlaneInput, PLANE_SETTERS),
    (FakeConstructionAxisInput, AXIS_SETTERS),
    (FakeConstructionPointInput, POINT_SETTERS),
):
    for _name in _names:
        setattr(_cls, _name, _setter(_name))


# --- computed geometry ---------------------------------------------------------------------


def _offset_plane(args: tuple[Any, ...]) -> tuple[Any, Any] | None:
    plane, value = args
    if getattr(value, "kind", None) != "real":
        return None
    geom = plane.geometry
    n = geom.normal
    o = geom.origin
    d = value.value
    return P(o.x + n.x * d, o.y + n.y * d, o.z + n.z * d), V(n.x, n.y, n.z)


def _plane_intersection(args: tuple[Any, ...]) -> tuple[Any, Any] | None:
    one, two = (a.geometry.normal for a in args)
    direction = one.crossProduct(two)
    if not direction.normalize():
        return None
    return P(0, 0, 0), direction


def _center(args: tuple[Any, ...]) -> Any:
    center = getattr(args[0].geometry, "center", None) or getattr(args[0].geometry, "origin", None)
    return center.copy() if center is not None else None


def _vertex(args: tuple[Any, ...]) -> Any:
    geometry: Any = getattr(args[0], "geometry", None)
    return geometry.copy() if hasattr(geometry, "copy") else None


# --- collections -----------------------------------------------------------------------------


class _FakeConstructionCollection(FakeCollection):
    input_class: type[_FakeConstructionInput] = _FakeConstructionInput
    prefix = ""

    def __init__(self, component: Any, design: FakeDesign | None = None) -> None:
        super().__init__()
        self.component = component
        self.design = design
        self.inputs: list[_FakeConstructionInput] = []
        self.added: list[_FakeConstructionInput] = []
        self.set_result = True
        self.set_error: str | None = None
        self.add_error: str | None = None
        self.health = HEALTH.HealthyFeatureHealthState
        self.message = ""
        self.geometry: Any = None

    def createInput(self, occurrence: Any = None) -> _FakeConstructionInput:
        inp = self.input_class(self, occurrence)
        self.inputs.append(inp)
        return inp

    def _computed(self, inp: _FakeConstructionInput) -> Any:
        raise NotImplementedError

    def _element(self, name: str, geometry: Any) -> Any:
        raise NotImplementedError

    def add(self, inp: Any) -> Any:
        if self.add_error is not None:
            raise RuntimeError(self.add_error)
        if inp.call is None:
            raise RuntimeError("the input has no definition: call a setBy method first")
        self.added.append(inp)
        geometry = self.geometry if self.geometry is not None else self._computed(inp)
        element = self._element(f"{self.prefix}{self.count + 1}", geometry)
        element.healthState = self.health
        element.errorOrWarningMessage = self.message
        element.parentComponent = self.component
        element.definition_call = inp.call
        self._items.append(element)
        self._attach(element)
        return element

    def _attach(self, element: Any) -> None:
        """Timeline item + token lookup; deleting the element undoes both."""
        item = None
        if self.design is not None:
            self.design.add_entity(element)
            if self.design.parametric:
                item = self.design.timeline.add(element.name)
                item.entity = element

        def delete_me() -> bool:
            self._items.remove(element)
            if item is not None:
                self.design.timeline._remove(item)  # type: ignore[union-attr]
            if self.design is not None:
                self.design.by_token.pop(element.entityToken, None)
            return True

        element.deleteMe = delete_me


class FakeConstructionPlanes(_FakeConstructionCollection):
    input_class = FakeConstructionPlaneInput
    prefix = "Plane"

    def _computed(self, inp: _FakeConstructionInput) -> Any:
        name, args = inp.call  # type: ignore[misc]
        found = _offset_plane(args) if name == "setByOffset" else None
        return found or (P(0, 0, 0), V(0, 0, 1))

    def _element(self, name: str, geometry: Any) -> Any:
        origin, normal = geometry
        return FakeConstructionPlane(name, origin, normal)


class FakeConstructionAxes(_FakeConstructionCollection):
    input_class = FakeConstructionAxisInput
    prefix = "Axis"

    def _computed(self, inp: _FakeConstructionInput) -> Any:
        name, args = inp.call  # type: ignore[misc]
        found = _plane_intersection(args) if name == "setByTwoPlanes" else None
        return found or (P(0, 0, 0), V(0, 0, 1))

    def _element(self, name: str, geometry: Any) -> Any:
        origin, direction = geometry
        return FakeConstructionAxis(name, origin, direction)


class FakeConstructionPoints(_FakeConstructionCollection):
    input_class = FakeConstructionPointInput
    prefix = "Point"

    def _computed(self, inp: _FakeConstructionInput) -> Any:
        name, args = inp.call  # type: ignore[misc]
        found = None
        if name == "setByCenter":
            found = _center(args)
        elif name == "setByPoint":
            found = _vertex(args)
        return found or P(0, 0, 0)

    def _element(self, name: str, geometry: Any) -> Any:
        return FakeConstructionPoint(name, geometry)


def install_construction(component: Any, design: FakeDesign | None = None) -> Any:
    """Give ``component`` construction collections with ``createInput``; returns it."""
    component.constructionPlanes = FakeConstructionPlanes(component, design)
    component.constructionAxes = FakeConstructionAxes(component, design)
    component.constructionPoints = FakeConstructionPoints(component, design)
    return component


def fake_construction_design(*bodies: Any, parametric: bool = True) -> FakeDesign:
    """``fake_part_design`` whose root (the default ``activeComponent``) has construction
    collections."""
    design = fake_part_design(*bodies, parametric=parametric)
    install_construction(design.rootComponent, design)
    return design

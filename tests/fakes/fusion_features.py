"""Fake feature collections and inputs for ``feature.*`` handler tests (c07 D21).

Builds on the c03 fakes (``tests/fakes/brep.py``: ``FakeComponent``, ``FakeBody``,
``FakeFeature``) and the c05 sketch fakes (``fake_sketch_design``); c08 extends this module.

- ``FakeFeatureCollection`` stands in for ``component.features.<x>Features``. Each creation
  method (``createInput`` and, per kind, ``createInput2`` / ``createSimpleInput`` / ...)
  hands out a ``FakeFeatureInput`` that remembers the method and its arguments. ``add(input)``
  returns a c03 ``FakeFeature`` with the collection's ``health``/``message``, ``bodies``
  and ``input``, adds a timeline item and makes the feature findable by token. Deleting the
  feature (``deleteMe``, as the dispatcher's rollback does through the timeline item) undoes
  all of it, including the body changes below.
- Body changes of the next ``add``: ``will_create(*bodies, component=None)`` (new bodies, in
  ``feature.bodies``), ``will_modify(*bodies)`` (existing bodies, in ``feature.bodies``),
  ``will_remove(*bodies)`` (bodies consumed by a join/cut, gone from their component);
  ``will_set(**attrs)`` sets attributes of the next feature (``sideFaces`` of a hole).
- ``threadFeatures.threadDataQuery`` is a ``FakeThreadDataQuery`` over ``THREAD_TABLE``;
  ``cylinder_face(radius, internal=...)`` is a cylindrical face with a point and normal that
  tell a hole wall from a shaft (D16).
- ``FakeFeatureInput`` records every attribute set and every method call in order; values
  set are readable back. ``provide(name, value)`` gives it a sub-object or a property value
  without recording it; ``returns[name]`` is what a method call returns (default ``True``);
  names in ``missing`` behave as absent (``hasattr`` is false) to test fallbacks.
- ``install_features(component, design, omit=())`` hangs ``component.features`` with all
  collections (``omit`` drops some, e.g. ``ribFeatures`` of an older Fusion API) and
  ``createPath`` (-> ``FakePath``, recorded in ``features.paths``). A loft input comes with
  ``loftSections`` (``FakeLoftSections``) and a recording ``centerLineOrRails``; a fillet
  input with ``edgeSetInputs`` and a ``createInput2`` chamfer input with ``chamferEdgeSets``
  (``edge_set_container``: each ``add*`` returns a recording edge set). A collection's
  ``on_input(inp)`` hook runs on every new input (e.g. ``inp.missing.add(...)``).
- c08 (D15): pattern, mirror, combine, split, move, scale, thicken, boundary fill, delete /
  replace face collections; ``copyPasteBodies``, ``deleteFaceFeatures`` and
  ``removeFeatures`` are ``FakeDirectCollection`` (``add(entities)``). A boundary fill input
  has ``bRepCells`` (``FakeBRepCell`` over the collection's ``cells`` - ``FakeCellBody``
  boxes answering ``pointContainment``). ``on_add(feature)`` runs after every ``add``.
- ``make_ctx(design, params, refs)`` is a ``Context`` over a fake app whose ref resolver
  first looks refs up in ``refs`` (ref -> entity, for entities without a token of their own),
  then in the design, and records every resolved ref.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.refs import Refs

from .brep import (
    FakeBody,
    FakeEvaluator,
    FakeFeature,
    add_face,
    fake_cylinder_face,
    register_body,
)
from .design import FakeApp, FakeCollection, FakeDesign
from .fusion_sketch import fake_sketch_design

adsk = fusion_addin.adsk
HEALTH = adsk.fusion.FeatureHealthStates

# ``Features`` attribute -> (feature object type, name prefix, creation methods).
FEATURE_COLLECTIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "extrudeFeatures": ("ExtrudeFeature", "Extrude", ("createInput",)),
    "revolveFeatures": ("RevolveFeature", "Revolve", ("createInput",)),
    "sweepFeatures": ("SweepFeature", "Sweep", ("createInput",)),
    "loftFeatures": ("LoftFeature", "Loft", ("createInput",)),
    "ribFeatures": ("RibFeature", "Rib", ("createInput",)),
    "webFeatures": ("WebFeature", "Web", ("createInput",)),
    "holeFeatures": (
        "HoleFeature",
        "Hole",
        ("createSimpleInput", "createCounterboreInput", "createCountersinkInput"),
    ),
    "threadFeatures": ("ThreadFeature", "Thread", ("createInput", "createThreadInfo")),
    "filletFeatures": ("FilletFeature", "Fillet", ("createInput",)),
    "ruleFilletFeatures": ("RuleFilletFeature", "RuleFillet", ("createInput",)),
    "chamferFeatures": ("ChamferFeature", "Chamfer", ("createInput", "createInput2")),
    "shellFeatures": ("ShellFeature", "Shell", ("createInput",)),
    "draftFeatures": ("DraftFeature", "Draft", ("createInput",)),
    "offsetFacesFeatures": ("OffsetFacesFeature", "OffsetFaces", ("createInput",)),
    # c08 D15: patterns, mirror and body operations.
    "rectangularPatternFeatures": (
        "RectangularPatternFeature",
        "RectangularPattern",
        ("createInput",),
    ),
    "circularPatternFeatures": ("CircularPatternFeature", "CircularPattern", ("createInput",)),
    "pathPatternFeatures": ("PathPatternFeature", "PathPattern", ("createInput",)),
    "mirrorFeatures": ("MirrorFeature", "Mirror", ("createInput",)),
    "combineFeatures": ("CombineFeature", "Combine", ("createInput",)),
    "splitBodyFeatures": ("SplitBodyFeature", "SplitBody", ("createInput",)),
    "splitFaceFeatures": ("SplitFaceFeature", "SplitFace", ("createInput",)),
    "moveFeatures": ("MoveFeature", "Move", ("createInput", "createInput2")),
    "copyPasteBodies": ("CopyPasteBody", "CopyPasteBody", ()),
    "scaleFeatures": ("ScaleFeature", "Scale", ("createInput",)),
    "thickenFeatures": ("ThickenFeature", "Thicken", ("createInput",)),
    "boundaryFillFeatures": ("BoundaryFillFeature", "BoundaryFill", ("createInput",)),
    "deleteFaceFeatures": ("DeleteFaceFeature", "DeleteFace", ()),
    "replaceFaceFeatures": ("ReplaceFaceFeature", "ReplaceFace", ("createInput",)),
    "removeFeatures": ("RemoveFeature", "Remove", ()),
}
# Collections whose ``add`` takes the entities themselves, not a feature input.
DIRECT_COLLECTIONS = frozenset({"copyPasteBodies", "deleteFaceFeatures", "removeFeatures"})

_OWN = frozenset(
    {
        "collection",
        "create_method",
        "create_args",
        "log",
        "attrs",
        "returns",
        "missing",
    }
)


class FakeFeatureInput:
    """Any ``*FeatureInput`` (or other object a creation method returns, e.g. ``ThreadInfo``).

    ``log`` holds ``("set", name, value)`` and ``("call", name, args)`` in order.
    """

    def __init__(
        self,
        collection: FakeFeatureCollection | None = None,
        create_method: str = "",
        create_args: tuple[Any, ...] = (),
    ) -> None:
        self.collection = collection
        self.create_method = create_method
        self.create_args = create_args
        self.log: list[tuple[str, str, Any]] = []
        self.attrs: dict[str, Any] = {}
        self.returns: dict[str, Any] = {}
        self.missing: set[str] = set()

    @property
    def objectType(self) -> str:
        kind = self.collection.kind if self.collection is not None else "Feature"
        return f"adsk::fusion::{kind}Input"

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _OWN:
            object.__setattr__(self, name, value)
            return
        self.attrs[name] = value
        self.log.append(("set", name, value))

    def __getattr__(self, name: str) -> Any:
        # Only reached for names that are not real attributes.
        if name.startswith("__") or name in _OWN:
            raise AttributeError(name)
        if name in self.missing:
            raise AttributeError(f"{type(self).__name__} has no attribute {name!r}")
        if name in self.attrs:
            return self.attrs[name]
        return _Method(self, name)

    def provide(self, name: str, value: Any) -> FakeFeatureInput:
        """Make ``name`` read as ``value`` without logging a set."""
        self.attrs[name] = value
        return self

    @property
    def sets(self) -> dict[str, Any]:
        """Last value of every attribute set (not ``provide``-d ones)."""
        return {name: value for kind, name, value in self.log if kind == "set"}

    @property
    def calls(self) -> list[tuple[str, tuple[Any, ...]]]:
        return [(name, args) for kind, name, args in self.log if kind == "call"]

    def call(self, name: str) -> tuple[Any, ...]:
        """Arguments of the last call of ``name``; ``KeyError`` if it was never called."""
        for kind, logged, args in reversed(self.log):
            if kind == "call" and logged == name:
                return tuple(args)
        raise KeyError(f"{name} was not called; calls: {[n for n, _ in self.calls]}")

    def called(self, name: str) -> bool:
        return any(kind == "call" and logged == name for kind, logged, _ in self.log)


class _Method:
    """A recorded method of a ``FakeFeatureInput``; returns ``returns[name]`` (a callable
    there is called with the arguments) or ``True``."""

    def __init__(self, owner: FakeFeatureInput, name: str) -> None:
        self._owner = owner
        self._name = name

    def __call__(self, *args: Any) -> Any:
        owner = self._owner
        owner.log.append(("call", self._name, args))
        result = owner.returns.get(self._name, True)
        return result(*args) if callable(result) else result


class FakeFeatureCollection(FakeCollection):
    """``component.features.<x>Features`` of one feature ``kind`` (``"ExtrudeFeature"``)."""

    def __init__(
        self,
        component: Any,
        design: FakeDesign | None = None,
        kind: str = "ExtrudeFeature",
        prefix: str = "Extrude",
        create_methods: Iterable[str] = ("createInput",),
    ) -> None:
        super().__init__()
        self.component = component
        self.design = design
        self.kind = kind
        self.prefix = prefix
        self.create_methods: set[str] = set(create_methods)
        self.inputs: list[FakeFeatureInput] = []
        self.added: list[FakeFeatureInput] = []
        self.health = HEALTH.HealthyFeatureHealthState
        self.message = ""
        self.add_error: str | None = None
        self.create_error: str | None = None
        # Called with every new input after its sub-objects are made (e.g. to mark names
        # ``missing`` and test a fallback for an older Fusion API).
        self.on_input: Callable[[FakeFeatureInput], None] | None = None
        # Called with every added feature after its bodies are placed (c08: new faces of a
        # split, new occurrences of a pattern).
        self.on_add: Callable[[Any], None] | None = None
        self._created: list[tuple[FakeBody, Any]] = []
        self._modified: list[FakeBody] = []
        self._removed: list[FakeBody] = []
        self._attrs: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Callable[..., FakeFeatureInput]:
        # Only reached for names that are not real attributes: the creation methods.
        if name.startswith("_") or name not in self.__dict__.get("create_methods", ()):
            raise AttributeError(f"{type(self).__name__} has no attribute {name!r}")

        def create(*args: Any) -> FakeFeatureInput:
            if self.create_error is not None:
                raise RuntimeError(self.create_error)
            inp = FakeFeatureInput(self, name, args)
            _prepare_input(self, name, inp)
            self.inputs.append(inp)
            return inp

        create.__name__ = name
        return create

    @property
    def last_input(self) -> FakeFeatureInput:
        return self.inputs[-1]

    # --- body changes of the next add ---------------------------------------------------------

    def will_create(self, *bodies: FakeBody, component: Any = None) -> FakeFeatureCollection:
        self._created.extend((body, component) for body in bodies)
        return self

    def will_modify(self, *bodies: FakeBody) -> FakeFeatureCollection:
        self._modified.extend(bodies)
        return self

    def will_remove(self, *bodies: FakeBody) -> FakeFeatureCollection:
        self._removed.extend(bodies)
        return self

    def will_set(self, **attrs: Any) -> FakeFeatureCollection:
        """Attributes of the next added feature (e.g. ``sideFaces`` of a hole)."""
        self._attrs.update(attrs)
        return self

    # --- add ----------------------------------------------------------------------------------

    def add(self, item: Any) -> Any:
        if not isinstance(item, FakeFeatureInput):
            raise RuntimeError(f"{self.kind}s.add expects a feature input, got {item!r}")
        if self.add_error is not None:
            raise RuntimeError(self.add_error)
        self.added.append(item)
        created, modified, removed = self._created, self._modified, self._removed
        self._created, self._modified, self._removed = [], [], []
        attrs, self._attrs = self._attrs, {}

        feature: Any = FakeFeature(
            self.kind,
            f"{self.prefix}{len(self.added)}",
            health=self.health,
            message=self.message,
            component=self.component,
        )
        feature.input = item
        for name, value in attrs.items():
            setattr(feature, name, value)
        new_bodies = [self._place(body, owner) for body, owner in created]
        feature.bodies = FakeCollection([*new_bodies, *modified])
        removed_from = [(body, body.parentComponent) for body in removed]
        for body, owner in removed_from:
            self._unregister(body, owner)
        self._items.append(feature)
        self._attach(feature, new_bodies, removed_from)
        if self.on_add is not None:
            self.on_add(feature)
        return feature

    def _place(self, body: FakeBody, owner: Any) -> FakeBody:
        owner = owner if owner is not None else self.component
        owner.add_body(body)
        if self.design is not None:
            register_body(self.design, body)
        return body

    def _unregister(self, body: FakeBody, owner: Any) -> None:
        if owner is not None:
            owner.bRepBodies._items = [b for b in owner.bRepBodies if b is not body]
        if self.design is not None:
            self.design.by_token.pop(body.entityToken, None)

    def _attach(
        self, feature: Any, new_bodies: list[FakeBody], removed: list[tuple[FakeBody, Any]]
    ) -> None:
        """Timeline item + token lookup; deleting the feature undoes them and the bodies."""
        item = None
        if self.design is not None:
            self.design.add_entity(feature)
            if self.design.parametric:
                item = self.design.timeline.add(feature.name)
                item.entity = feature

        def delete_me() -> bool:
            if item is not None:
                self.design.timeline._remove(item)  # type: ignore[union-attr]
            self._items.remove(feature)
            for body in new_bodies:
                self._unregister(body, body.parentComponent)
            for body, owner in removed:
                if owner is not None:
                    owner.add_body(body)
                if self.design is not None:
                    register_body(self.design, body)
            if self.design is not None:
                self.design.by_token.pop(feature.entityToken, None)
            return True

        feature.deleteMe = delete_me


class FakeDirectCollection(FakeFeatureCollection):
    """A collection whose ``add(entities)`` takes the entities directly (c08 D15):
    ``copyPasteBodies``, ``deleteFaceFeatures``, ``removeFeatures``. The argument is kept as
    ``feature.input.create_args[0]``. ``removeFeatures.add(body)`` removes ``body`` from its
    component (undone with the feature)."""

    def add(self, item: Any) -> Any:
        if self.add_error is not None:
            raise RuntimeError(self.add_error)
        if self.kind == "RemoveFeature" and isinstance(item, FakeBody):
            self.will_remove(item)
        inp = FakeFeatureInput(self, "add", (item,))
        self.inputs.append(inp)
        return super().add(inp)


class FakeCellBody(FakeBody):
    """``BRepCell.cellBody``: a box ``lo``..``hi`` (cm) answering ``pointContainment``."""

    def __init__(
        self,
        lo: tuple[float, float, float],
        hi: tuple[float, float, float],
        name: str = "Cell",
    ) -> None:
        from .brep import FakeBoundingBox

        volume = (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])
        super().__init__(name, volume=volume, bounding_box=FakeBoundingBox(lo, hi))
        self.lo, self.hi = lo, hi
        self.queries: list[Any] = []

    def pointContainment(self, point: Any) -> int:
        self.queries.append(point)
        inside = all(
            lo < c < hi for lo, c, hi in zip(self.lo, point.asArray(), self.hi, strict=True)
        )
        containment = adsk.fusion.PointContainment
        if inside:
            return int(containment.PointInsidePointContainment)
        return int(containment.PointOutsidePointContainment)


class FakeBRepCell:
    """``BRepCell`` of a boundary fill input: ``cellBody`` and a settable ``isSelected``."""

    objectType = "adsk::fusion::BRepCell"

    def __init__(self, cell_body: FakeCellBody) -> None:
        self.cellBody = cell_body
        self.isSelected = False


class FakeBRepCells(FakeCollection):
    """``BoundaryFillFeatureInput.bRepCells``."""


class FakeLoftSections(FakeCollection):
    """``LoftFeatureInput.loftSections``: ``add(entity)`` -> a recording ``FakeFeatureInput``
    (the ``LoftSection``; its ``create_args`` is ``(entity,)``)."""

    def add(self, entity: Any) -> FakeFeatureInput:
        section = FakeFeatureInput(None, "add", (entity,))
        section.provide("entity", entity)
        self._items.append(section)
        return section


class FakePath:
    """``Path`` built by ``Features.createPath(curves, isChain)``."""

    objectType = "adsk::fusion::Path"

    def __init__(self, curves: Any, is_chain: bool) -> None:
        self.curves = curves
        self.isChain = is_chain

    @property
    def entities(self) -> list[Any]:
        if isinstance(self.curves, adsk.core.ObjectCollection):
            return list(self.curves)
        return [self.curves]


# Thread type -> size -> designation -> (major diameter cm, minor diameter cm).
THREAD_TABLE: dict[str, dict[str, dict[str, tuple[float, float]]]] = {
    "ISO Metric profile": {
        "5": {"M5x0.8": (0.5, 0.42), "M5x0.5": (0.5, 0.45)},
        "6": {"M6x1": (0.6, 0.5), "M6x0.75": (0.6, 0.52)},
        "8": {"M8x1.25": (0.8, 0.67), "M8x1": (0.8, 0.69)},
        "10": {"M10x1.5": (1.0, 0.84), "M10x1.25": (1.0, 0.86)},
    },
    "ANSI Unified Screw Threads": {
        "1/4": {"1/4-20 UNC": (0.635, 0.513), "1/4-28 UNF": (0.635, 0.551)},
    },
}
THREAD_CLASSES = {
    "ISO Metric profile": {True: ["6H", "4H", "5H"], False: ["6g", "4g6g", "6e"]},
    "ANSI Unified Screw Threads": {True: ["2B", "1B", "3B"], False: ["2A", "1A", "3A"]},
}


class FakeThreadDataQuery:
    """``ThreadFeatures.threadDataQuery`` over ``THREAD_TABLE`` (or ``table``).

    ``recommendThreadData(d, internal, type)`` picks the designation whose minor (internal,
    the tap drill of a hole) or major (external, a shaft) diameter is nearest to ``d`` cm,
    within ``tolerance`` cm, with the first class; ``(False, "", "")`` otherwise. ``calls``
    records ``(method, args)``.
    """

    objectType = "adsk::fusion::ThreadDataQuery"

    def __init__(
        self,
        table: dict[str, dict[str, dict[str, tuple[float, float]]]] | None = None,
        classes: dict[str, dict[bool, list[str]]] | None = None,
        tolerance: float = 0.06,
    ) -> None:
        self.table = table if table is not None else THREAD_TABLE
        self.classes = classes if classes is not None else THREAD_CLASSES
        self.tolerance = tolerance
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    @property
    def allThreadTypes(self) -> list[str]:
        self.calls.append(("allThreadTypes", ()))
        return list(self.table)

    def allSizes(self, threadType: str) -> list[str]:
        self.calls.append(("allSizes", (threadType,)))
        return list(self.table.get(threadType, {}))

    def allDesignations(self, threadType: str, size: str) -> list[str]:
        self.calls.append(("allDesignations", (threadType, size)))
        return list(self.table.get(threadType, {}).get(size, {}))

    def _designations(self, threadType: str) -> dict[str, tuple[float, float]]:
        out: dict[str, tuple[float, float]] = {}
        for designations in self.table.get(threadType, {}).values():
            out.update(designations)
        return out

    def allClasses(self, isInternal: bool, threadType: str, designation: str) -> list[str]:
        self.calls.append(("allClasses", (isInternal, threadType, designation)))
        if designation not in self._designations(threadType):
            return []
        return list(self.classes.get(threadType, {}).get(bool(isInternal), []))

    def recommendThreadData(
        self, modelDiameter: float, isInternal: bool, threadType: str
    ) -> tuple[bool, str, str]:
        self.calls.append(("recommendThreadData", (modelDiameter, isInternal, threadType)))
        best: tuple[float, str] | None = None
        for designation, (major, minor) in self._designations(threadType).items():
            gap = abs((minor if isInternal else major) - modelDiameter)
            if gap <= self.tolerance and (best is None or gap < best[0]):
                best = (gap, designation)
        if best is None:
            return False, "", ""
        classes = self.classes.get(threadType, {}).get(bool(isInternal), [""])
        return True, best[1], classes[0]


def cylinder_face(
    radius: float,
    height: float = 1.0,
    internal: bool = False,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    axis: tuple[float, float, float] = (0.0, 0.0, 1.0),
    body: Any = None,
) -> Any:
    """c03 ``fake_cylinder_face`` (cm) with a point on the face and its normal: pointing away
    from the axis for an outer (shaft) face, towards it for an ``internal`` (hole) face.
    Added to ``body`` when given."""
    face = fake_cylinder_face(radius, height, origin, axis)
    a = adsk.core.Vector3D.create(*axis)
    a.normalize()
    helper = adsk.core.Vector3D.create(1.0, 0.0, 0.0)
    if abs(a.dotProduct(helper)) > 0.9:
        helper = adsk.core.Vector3D.create(0.0, 1.0, 0.0)
    radial = a.crossProduct(helper)
    radial.normalize()
    point = adsk.core.Point3D.create(
        origin[0] + a.x * height / 2 + radial.x * radius,
        origin[1] + a.y * height / 2 + radial.y * radius,
        origin[2] + a.z * height / 2 + radial.z * radius,
    )
    normal = radial.copy()
    if internal:
        normal.scaleBy(-1.0)
    face.pointOnFace = point
    face.evaluator = FakeEvaluator(normal)
    if body is not None:
        add_face(body, face)
    return face


def edge_set_container(name: str) -> FakeFeatureInput:
    """``FilletFeatureInput.edgeSetInputs`` / ``ChamferFeatureInput.chamferEdgeSets``: every
    ``add*`` call is recorded and returns a new recording edge set (``create_method`` = the
    call, ``create_args`` = its arguments); ``edge_sets`` lists them in order."""
    container = FakeFeatureInput(None, name)
    made: list[FakeFeatureInput] = []
    container.provide("edge_sets", made)
    for method in (
        "addConstantRadiusEdgeSet",
        "addVariableRadiusEdgeSet",
        "addChordLengthEdgeSet",
        "addEqualDistanceChamferEdgeSet",
        "addTwoDistancesChamferEdgeSet",
        "addDistanceAndAngleChamferEdgeSet",
    ):

        def add(*args: Any, _method: str = method) -> FakeFeatureInput:
            edge_set = FakeFeatureInput(None, _method, args)
            made.append(edge_set)
            return edge_set

        container.returns[method] = add
    return container


def _prepare_input(collection: FakeFeatureCollection, method: str, inp: FakeFeatureInput) -> None:
    """Sub-objects a real input has right after creation (not logged as sets)."""
    if collection.kind == "LoftFeature" and method == "createInput":
        inp.provide("loftSections", FakeLoftSections())
        inp.provide("centerLineOrRails", FakeFeatureInput(None, "centerLineOrRails"))
    if collection.kind == "FilletFeature" and method == "createInput":
        inp.provide("edgeSetInputs", edge_set_container("edgeSetInputs"))
    if collection.kind == "ChamferFeature" and method == "createInput2":
        inp.provide("chamferEdgeSets", edge_set_container("chamferEdgeSets"))
    if collection.kind == "BoundaryFillFeature" and method == "createInput":
        cells = [FakeBRepCell(body) for body in getattr(collection, "cells", [])]
        inp.provide("bRepCells", FakeBRepCells(cells))
    if collection.on_input is not None:
        collection.on_input(inp)


class FakeFeatures:
    """``component.features``: one ``FakeFeatureCollection`` per ``FEATURE_COLLECTIONS``
    attribute, except the omitted ones (absent, like in an older Fusion API), and
    ``createPath`` (records ``paths``; ``path_error`` makes it raise ``RuntimeError``)."""

    def __init__(
        self, component: Any, design: FakeDesign | None = None, omit: Iterable[str] = ()
    ) -> None:
        self.component = component
        self.paths: list[FakePath] = []
        self.path_error: str | None = None
        skipped = set(omit)
        unknown = skipped - set(FEATURE_COLLECTIONS)
        if unknown:
            raise ValueError(f"unknown feature collections: {sorted(unknown)}")
        for attr, (kind, prefix, methods) in FEATURE_COLLECTIONS.items():
            if attr not in skipped:
                cls = FakeDirectCollection if attr in DIRECT_COLLECTIONS else FakeFeatureCollection
                setattr(self, attr, cls(component, design, kind, prefix, methods))
        threads = getattr(self, "threadFeatures", None)
        if threads is not None:
            threads.threadDataQuery = FakeThreadDataQuery()

    def createPath(self, curves: Any, isChain: bool = True) -> FakePath:
        if self.path_error is not None:
            raise RuntimeError(self.path_error)
        path = FakePath(curves, isChain)
        self.paths.append(path)
        return path

    def collections(self) -> dict[str, FakeFeatureCollection]:
        return {attr: getattr(self, attr) for attr in FEATURE_COLLECTIONS if hasattr(self, attr)}


def install_features(
    component: Any, design: FakeDesign | None = None, omit: Iterable[str] = ()
) -> Any:
    """Give ``component`` (a c03 ``FakeComponent``) fake feature collections; returns it."""
    component.features = FakeFeatures(component, design, omit)
    return component


def fake_feature_design(*bodies: FakeBody, parametric: bool = True) -> FakeDesign:
    """``fake_sketch_design`` (c03 bodies + c05 sketches) whose root has feature collections."""
    design: FakeDesign = fake_sketch_design(*bodies, parametric=parametric)
    install_features(design.rootComponent, design)
    return design


class FakeRefs(Refs):
    """``ctx.refs`` that resolves ``aliases`` (ref -> entity) first, then the design;
    ``resolved`` lists every ref resolved, in order."""

    def __init__(self, ctx: Any, aliases: dict[str, Any] | None = None) -> None:
        super().__init__(ctx)
        self.aliases: dict[str, Any] = dict(aliases or {})
        self.resolved: list[str] = []

    def resolve(self, ref: Any, component: Any = None) -> Any:
        key = ref.get("ref") if isinstance(ref, dict) else ref
        if isinstance(key, str):
            self.resolved.append(key)
            if key in self.aliases:
                return self.aliases[key]
        return super().resolve(ref, component)

    def to_ref(self, entity: Any) -> str:
        for ref, known in self.aliases.items():
            if known is entity:
                return ref
        return super().to_ref(entity)


def make_ctx(
    design: FakeDesign | None = None,
    params: dict[str, Any] | None = None,
    refs: dict[str, Any] | None = None,
) -> Context:
    """``Context`` over ``design`` (default: a new ``fake_feature_design()``) with a
    ``FakeRefs`` resolver; ``params`` is the request (e.g. ``{"units": "in"}``)."""
    design = design if design is not None else fake_feature_design()
    ctx = Context(FakeApp(design), params)
    ctx.refs = FakeRefs(ctx, refs)
    return ctx

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
  ``will_remove(*bodies)`` (bodies consumed by a join/cut, gone from their component).
- ``FakeFeatureInput`` records every attribute set and every method call in order; values
  set are readable back. ``provide(name, value)`` gives it a sub-object or a property value
  without recording it; ``returns[name]`` is what a method call returns (default ``True``);
  names in ``missing`` behave as absent (``hasattr`` is false) to test fallbacks.
- ``install_features(component, design, omit=())`` hangs ``component.features`` with all
  collections (``omit`` drops some, e.g. ``ribFeatures`` of an older Fusion API).
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

from .brep import FakeBody, FakeFeature, register_body
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
}

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
        self._created: list[tuple[FakeBody, Any]] = []
        self._modified: list[FakeBody] = []
        self._removed: list[FakeBody] = []

    def __getattr__(self, name: str) -> Callable[..., FakeFeatureInput]:
        # Only reached for names that are not real attributes: the creation methods.
        if name.startswith("_") or name not in self.__dict__.get("create_methods", ()):
            raise AttributeError(f"{type(self).__name__} has no attribute {name!r}")

        def create(*args: Any) -> FakeFeatureInput:
            if self.create_error is not None:
                raise RuntimeError(self.create_error)
            inp = FakeFeatureInput(self, name, args)
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

    # --- add ----------------------------------------------------------------------------------

    def add(self, item: Any) -> Any:
        if not isinstance(item, FakeFeatureInput):
            raise RuntimeError(f"{self.kind}s.add expects a feature input, got {item!r}")
        if self.add_error is not None:
            raise RuntimeError(self.add_error)
        self.added.append(item)
        created, modified, removed = self._created, self._modified, self._removed
        self._created, self._modified, self._removed = [], [], []

        feature: Any = FakeFeature(
            self.kind,
            f"{self.prefix}{len(self.added)}",
            health=self.health,
            message=self.message,
            component=self.component,
        )
        feature.input = item
        new_bodies = [self._place(body, owner) for body, owner in created]
        feature.bodies = FakeCollection([*new_bodies, *modified])
        removed_from = [(body, body.parentComponent) for body in removed]
        for body, owner in removed_from:
            self._unregister(body, owner)
        self._items.append(feature)
        self._attach(feature, new_bodies, removed_from)
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


class FakeFeatures:
    """``component.features``: one ``FakeFeatureCollection`` per ``FEATURE_COLLECTIONS``
    attribute, except the omitted ones (absent, like in an older Fusion API)."""

    def __init__(
        self, component: Any, design: FakeDesign | None = None, omit: Iterable[str] = ()
    ) -> None:
        self.component = component
        skipped = set(omit)
        unknown = skipped - set(FEATURE_COLLECTIONS)
        if unknown:
            raise ValueError(f"unknown feature collections: {sorted(unknown)}")
        for attr, (kind, prefix, methods) in FEATURE_COLLECTIONS.items():
            if attr not in skipped:
                setattr(self, attr, FakeFeatureCollection(component, design, kind, prefix, methods))

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

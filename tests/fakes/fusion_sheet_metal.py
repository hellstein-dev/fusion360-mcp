"""Fake sheet metal API for ``sheet_metal.*`` handler tests (c13 D1).

Builds on the c07 feature fakes (``tests/fakes/fusion_features.py``) and the c12 export fakes.

- ``FakeSheetMetalRuleValue`` (``value`` in cm, ``expression``; an expression of numbers with
  ``mm``/``cm`` and ``Thickness`` is evaluated), ``FakeSheetMetalRule`` (every
  ``SheetMetalRule`` property, ``deleteMe``; ``set_error`` makes value setters raise; a used
  rule refuses ``deleteMe``), ``FakeSheetMetalRules`` (``item``/``itemByName``/``addByCopy``;
  ``copy_error`` makes ``addByCopy`` raise).
- ``install_sheet_metal(design, preview=True)``: ``designSheetMetalRules`` (empty) and
  ``librarySheetMetalRules`` (``"Steel"`` 1 mm, default, and ``"Aluminum"`` 2 mm) on the
  design; ``activeSheetMetalRule`` (``None``), ``flatPattern`` (``None``) and
  ``createFlatPattern(face)`` on the root; ``flangeFeatures`` / ``unfoldFeatures`` /
  ``refoldFeatures`` on ``root.features``; a ``FakeSheetMetalExportManager`` as
  ``design.exportManager``. ``preview=False`` is a Fusion without the preview API: the
  collections have no creation methods and the export manager no
  ``createSTEPExportOptionsForFlatPattern``.
- Edge flange inputs come with ``edgeDefinitions`` (each ``add*Edge`` is recorded and returns
  a recording definition); flange inputs with ``bendRadiusOverride`` (recording
  ``setOverride``).
- ``FakeFlatPattern``: ``flatBody`` (a box of the folded body's size), ``bendLinesBody`` with
  one edge per bend of ``component.flat_bends`` (``(is_up, angle rad)``), ``getBendInfo``,
  ``deleteMe``. Creating one switches ``app.activeProduct`` to a ``FakeFlatPatternProduct``
  (design R4); ``install_sheet_metal_app(app, design)`` gives the app the workspaces whose
  ``FusionSolidEnvironment.activate()`` switches back to the design.
- ``make_sheet_metal(body, ...)`` marks a body as sheet metal and (``preview``) gives it
  ``convertToSheetMetal`` / ``getBendFaces`` with ``bends`` inner/outer cylinder face pairs.
"""

from __future__ import annotations

import math
import re
from typing import Any

import fusion_addin

from .brep import FakeBody, FakeBoundingBox, add_face, fake_cylinder_face, register_body
from .design import FakeCollection, FakeDesign, FakeEntity
from .fusion_exchange import FakeExportManager, FakeExportOptions
from .fusion_features import FakeFeatureCollection, FakeFeatureInput, fake_feature_design

adsk = fusion_addin.adsk
RELIEF = adsk.fusion.BendReliefShapes

_NUMBER = re.compile(r"(\d+(?:\.\d+)?)\s*(mm|cm)?")
NO_DATA_ERROR = "2 : InternalValidationError : smDataRoot"
# Flange creation methods whose first argument is a ``std::vector`` on the live Fusion.
VECTOR_FIRST = {
    "createBaseFlangeInput": "Profile",
    "createContourFlangeInput": "Profile",
    "createEdgeFlangeInput": "BRepEdge",
}


class FakeSheetMetalRuleValue:
    objectType = "adsk::fusion::SheetMetalRuleValue"

    def __init__(self, rule: FakeSheetMetalRule, cm: float, expression: str | None = None) -> None:
        self._rule = rule
        self._value = float(cm)
        self._expression = expression if expression is not None else f"{cm * 10:g} mm"
        self.sets: list[tuple[str, Any]] = []

    def _check(self) -> None:
        if self._rule.set_error is not None:
            raise RuntimeError(self._rule.set_error)

    @property
    def value(self) -> float:
        return self._value

    @value.setter
    def value(self, cm: float) -> None:
        self._check()
        self.sets.append(("value", cm))
        self._value = float(cm)
        self._expression = f"{cm * 10:g} mm"

    @property
    def expression(self) -> str:
        return self._expression

    @expression.setter
    def expression(self, text: str) -> None:
        self._check()
        self.sets.append(("expression", text))
        self._expression = text
        evaluated = self._rule.evaluate(text, self)
        if evaluated is not None:
            self._value = evaluated


class FakeSheetMetalRule:
    objectType = "adsk::fusion::SheetMetalRule"

    def __init__(
        self,
        name: str,
        thickness: float = 0.1,
        k_factor: float = 0.44,
        relief_shape: int = RELIEF.RoundBendReliefShape,
        is_default: bool = False,
        units: str = "mm",
        owner: FakeSheetMetalRules | None = None,
    ) -> None:
        self.name = name
        self.owner = owner
        self.set_error: str | None = None
        self.thickness = FakeSheetMetalRuleValue(self, thickness)
        self.bendRadius = FakeSheetMetalRuleValue(self, thickness, "Thickness")
        self.gap = FakeSheetMetalRuleValue(self, thickness, "Thickness")
        self.reliefWidth = FakeSheetMetalRuleValue(self, thickness, "Thickness")
        self.reliefDepth = FakeSheetMetalRuleValue(self, thickness * 0.5, "Thickness * 0.5")
        self.reliefRemnant = FakeSheetMetalRuleValue(self, thickness * 0.5, "Thickness * 0.5")
        self.kFactor = k_factor
        self.reliefShape = relief_shape
        self.isDefault = is_default
        self.isUsed = False
        self.units = units
        self.deleted = False
        self.entityToken = f"rule-{name}-{id(self)}"

    def evaluate(self, text: str, target: FakeSheetMetalRuleValue) -> float | None:
        """cm value of a simple expression (numbers with mm/cm, ``Thickness``, + - * /)."""
        thickness = self.thickness.value if target is not self.thickness else None
        source = text
        if "Thickness" in source:
            if thickness is None:
                return None
            source = source.replace("Thickness", repr(thickness))
        source = _NUMBER.sub(
            lambda m: (
                repr(float(m.group(1)) * (0.1 if m.group(2) == "mm" else 1.0))
                if m.group(2)
                else m.group(1)
            ),
            source,
        )
        if not re.fullmatch(r"[0-9.eE+\-*/() ]+", source):
            return None
        try:
            return float(eval(source, {"__builtins__": {}}, {}))
        except Exception:
            return None

    def copy(self, name: str, owner: FakeSheetMetalRules) -> FakeSheetMetalRule:
        rule = FakeSheetMetalRule(
            name, self.thickness.value, self.kFactor, self.reliefShape, False, self.units, owner
        )
        for attr in ("bendRadius", "gap", "reliefWidth", "reliefDepth", "reliefRemnant"):
            mine = getattr(self, attr)
            setattr(rule, attr, FakeSheetMetalRuleValue(rule, mine.value, mine.expression))
        return rule

    def deleteMe(self) -> bool:
        if self.isUsed:
            raise RuntimeError("the rule is in use")
        if self.owner is not None:
            self.owner._items.remove(self)
        self.deleted = True
        return True


class FakeSheetMetalRules(FakeCollection):
    """``designSheetMetalRules`` / ``librarySheetMetalRules``."""

    objectType = "adsk::fusion::SheetMetalRules"

    def __init__(self) -> None:
        super().__init__()
        self.copy_error: str | None = None
        self.copies: list[FakeSheetMetalRule] = []
        # Like the design rules of a fresh live design (c15 6.1): every read raises
        # ``smDataRoot``; the first ``addByCopy`` raises it too but adds the rule and clears it.
        self.no_data = False

    def _check_data(self) -> None:
        if self.no_data:
            raise RuntimeError(NO_DATA_ERROR)

    @property
    def count(self) -> int:
        self._check_data()
        return len(self._items)

    def item(self, index: int) -> Any:
        self._check_data()
        return super().item(index)

    def itemByName(self, name: str) -> Any:
        self._check_data()
        return next((rule for rule in self._items if rule.name == name), None)

    def __iter__(self) -> Any:
        self._check_data()
        return iter(list(self._items))

    def add_rule(self, rule: FakeSheetMetalRule) -> FakeSheetMetalRule:
        rule.owner = self
        self._items.append(rule)
        return rule

    def addByCopy(self, rule: FakeSheetMetalRule, name: str) -> FakeSheetMetalRule:
        if self.copy_error is not None:
            raise RuntimeError(self.copy_error)
        copy = rule.copy(name, self)
        self._items.append(copy)
        self.copies.append(copy)
        if self.no_data:
            self.no_data = False
            raise RuntimeError(NO_DATA_ERROR)
        return copy

    @property
    def names(self) -> list[str]:
        return [rule.name for rule in self._items]


# --- feature collections -----------------------------------------------------------------------


def _recorder(name: str) -> FakeFeatureInput:
    """A recording sub-object whose ``add*`` calls return recording definitions (kept in
    ``made``)."""
    container = FakeFeatureInput(None, name)
    made: list[FakeFeatureInput] = []
    container.provide("made", made)
    for method in ("addFullLengthEdge", "addSymmetricEdge", "addTwoSidedEdge", "addTwoOffsetsEdge"):

        def add(*args: Any, _method: str = method) -> FakeFeatureInput:
            definition = FakeFeatureInput(None, _method, args)
            made.append(definition)
            return definition

        container.returns[method] = add
    return container


class FakeSheetMetalFeatures(FakeFeatureCollection):
    """``flangeFeatures`` / ``unfoldFeatures`` / ``refoldFeatures``: flange inputs come with
    ``edgeDefinitions`` and ``bendRadiusOverride``."""

    def __getattr__(self, name: str) -> Any:
        create = super().__getattr__(name)

        def prepared(*args: Any) -> FakeFeatureInput:
            item_type = VECTOR_FIRST.get(name) if self.kind == "FlangeFeature" else None
            if item_type is not None and not isinstance(args[0] if args else None, list):
                raise TypeError(
                    f"in method 'FlangeFeatures_{name}', argument 2 of type 'std::vector< "
                    f"adsk::core::Ptr< adsk::fusion::{item_type} > > const &'"
                )
            inp = create(*args)
            if self.kind == "FlangeFeature":
                inp.provide("bendRadiusOverride", FakeFeatureInput(None, "bendRadiusOverride"))
                if name == "createEdgeFlangeInput":
                    inp.provide("edgeDefinitions", _recorder("edgeDefinitions"))
            return inp

        return prepared


SHEET_METAL_COLLECTIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "flangeFeatures": (
        "FlangeFeature",
        "Flange",
        ("createBaseFlangeInput", "createContourFlangeInput", "createEdgeFlangeInput"),
    ),
    "unfoldFeatures": ("UnfoldFeature", "Unfold", ("createInput",)),
    "refoldFeatures": ("RefoldFeature", "Refold", ("createInput",)),
}


# --- flat pattern ------------------------------------------------------------------------------


class FakeFlatPatternProduct:
    objectType = "adsk::fusion::FlatPatternProduct"

    def __init__(self, flat: FakeFlatPattern) -> None:
        self.flatPattern = flat


class FakeFlatPattern:
    objectType = "adsk::fusion::FlatPattern"

    def __init__(self, component: Any, face: Any) -> None:
        self.component = component
        self.stationaryFace = face
        self.foldedBody = getattr(face, "body", None)
        box = getattr(self.foldedBody, "boundingBox", None)
        lo = (0.0, 0.0, 0.0)
        hi = (10.0, 5.0, 0.1)
        if box is not None:
            span = [box.maxPoint.x - box.minPoint.x, box.maxPoint.y - box.minPoint.y]
            if span[0] > 0 and span[1] > 0:
                hi = (span[0], span[1], 0.1)
        self.flatBody = FakeBody("Flat", bounding_box=FakeBoundingBox(lo, hi))
        self.bends: list[tuple[bool, float]] = list(getattr(component, "flat_bends", []))
        edges = [
            FakeEntity("adsk::fusion::BRepEdge", token=f"bend-{i}") for i in range(len(self.bends))
        ]
        self.bendLinesBody = FakeEntity(
            "adsk::fusion::BRepBody", "BendLines", edges=FakeCollection(edges)
        )
        self.extentLinesBody = FakeEntity("adsk::fusion::BRepBody", "ExtentLines")
        self.topFace = face
        self.isValid = True
        self.deleted = False
        self.delete_error: str | None = None

    def getBendInfo(self, edge: Any) -> tuple[bool, bool, float]:
        index = int(edge.entityToken.rsplit("-", 1)[1])
        is_up, angle = self.bends[index]
        return True, is_up, angle

    def deleteMe(self) -> bool:
        if self.delete_error is not None:
            raise RuntimeError(self.delete_error)
        self.deleted = True
        self.isValid = False
        if self.component.flatPattern is self:
            self.component.flatPattern = None
        return True


def _flat_pattern_maker(component: Any, design: FakeDesign) -> Any:
    def createFlatPattern(face: Any) -> FakeFlatPattern:
        component.flat_pattern_calls.append(face)
        if component.flat_pattern_error is not None:
            raise RuntimeError(component.flat_pattern_error)
        flat = FakeFlatPattern(component, face)
        component.flatPattern = flat
        app = getattr(design, "sheet_app", None)
        if app is not None:
            app.activeProduct = FakeFlatPatternProduct(flat)
        return flat

    return createFlatPattern


class FakeAttribute:
    def __init__(self, group: str, name: str, value: str) -> None:
        self.groupName = group
        self.name = name
        self.value = value


class FakeAttributes:
    """``Component.attributes``: ``add`` replaces an attribute of the same group and name."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], FakeAttribute] = {}

    def add(self, group: str, name: str, value: str) -> FakeAttribute:
        attribute = FakeAttribute(group, name, value)
        attribute.deleteMe = lambda: self.items.pop((group, name), None) is not None  # type: ignore[attr-defined]
        self.items[(group, name)] = attribute
        return attribute

    def itemByName(self, group: str, name: str) -> FakeAttribute | None:
        return self.items.get((group, name))


class FakeWorkspace:
    def __init__(self, workspace_id: str, app: Any, design: Any) -> None:
        self.id = workspace_id
        self.app = app
        self.design = design
        self.activations = 0

    def activate(self) -> bool:
        self.activations += 1
        if self.id == "FusionSolidEnvironment":
            if self.app.activeProduct == self.design:
                return False  # live Fusion: activating the active workspace answers False
            self.app.activeProduct = self.design
        return True


class FakeWorkspaces:
    def __init__(self, app: Any, design: Any) -> None:
        self.app = app
        self.design = design
        self.by_id: dict[str, FakeWorkspace] = {}

    def itemById(self, workspace_id: str) -> FakeWorkspace:
        return self.by_id.setdefault(
            workspace_id, FakeWorkspace(workspace_id, self.app, self.design)
        )


class FakeSheetMetalExportManager(FakeExportManager):
    def createDXFFlatPatternExportOptions(self, filename: str, flat: Any) -> FakeExportOptions:
        options = self._make("dxf", filename, flat)
        options.units = adsk.fusion.DistanceUnits.CentimeterDistanceUnits  # type: ignore[attr-defined]
        options.isCenterLinesExported = False  # type: ignore[attr-defined]
        options.isExtentLinesExported = False  # type: ignore[attr-defined]
        options.isSplineConvertedToPolyline = False  # type: ignore[attr-defined]
        options.convertToPolylineTolerance = 0.0  # type: ignore[attr-defined]
        return options

    def createSTEPExportOptionsForFlatPattern(self, filename: str, flat: Any) -> FakeExportOptions:
        return self._make("step", filename, flat)


# --- install -----------------------------------------------------------------------------------


def install_sheet_metal(design: FakeDesign, preview: bool = True) -> FakeDesign:
    """Sheet metal rules, flat pattern, sheet metal collections and the flat pattern export on
    ``design`` (``preview=False``: a Fusion without the preview API)."""
    design.designSheetMetalRules = FakeSheetMetalRules()  # type: ignore[attr-defined]
    library = FakeSheetMetalRules()
    library.add_rule(FakeSheetMetalRule("Steel", 0.1, is_default=True))
    library.add_rule(FakeSheetMetalRule("Aluminum", 0.2, k_factor=0.4))
    design.librarySheetMetalRules = library  # type: ignore[attr-defined]
    root = design.rootComponent
    root.activeSheetMetalRule = None
    root.flatPattern = None
    root.flat_bends = [(True, math.pi / 2)]
    root.flat_pattern_calls = []
    root.flat_pattern_error = None
    root.attributes = FakeAttributes()
    root.createFlatPattern = _flat_pattern_maker(root, design)
    for attr, (kind, prefix, methods) in SHEET_METAL_COLLECTIONS.items():
        collection = FakeSheetMetalFeatures(root, design, kind, prefix, methods if preview else ())
        setattr(root.features, attr, collection)
    manager = FakeSheetMetalExportManager()
    if not preview:
        manager.missing.add("createSTEPExportOptionsForFlatPattern")
    design.exportManager = manager  # type: ignore[attr-defined]
    return design


def install_sheet_metal_app(app: Any, design: FakeDesign) -> Any:
    """Workspaces on ``app.userInterface``; flat pattern creation switches ``app``'s product."""
    app.userInterface.workspaces = FakeWorkspaces(app, design)
    design.sheet_app = app  # type: ignore[attr-defined]
    return app


def fake_sheet_metal_design(
    *bodies: FakeBody, parametric: bool = True, preview: bool = True
) -> Any:
    """``fake_feature_design`` with the sheet metal fakes installed."""
    return install_sheet_metal(fake_feature_design(*bodies, parametric=parametric), preview)


def make_sheet_metal(
    body: FakeBody,
    is_sheet_metal: bool = True,
    preview: bool = True,
    bends: int = 0,
    radius: float = 0.1,
    thickness: float = 0.1,
    design: FakeDesign | None = None,
) -> Any:
    """Mark ``body`` (not) sheet metal; with ``preview`` give it ``convertToSheetMetal`` and
    ``getBendFaces`` over ``bends`` inner (``radius`` cm) / outer (``radius + thickness``)
    cylinder face pairs. ``body.convert_result`` is what a conversion returns."""
    body.isSheetMetal = is_sheet_metal  # type: ignore[attr-defined]
    body.bend_pairs = []  # type: ignore[attr-defined]
    for index in range(bends):
        inner = add_face(body, fake_cylinder_face(radius, 1.0, (index * 2.0, 0, 0)))
        outer = add_face(body, fake_cylinder_face(radius + thickness, 1.0, (index * 2.0, 0, 0)))
        body.bend_pairs.append((inner, outer))  # type: ignore[attr-defined]
    if design is not None:
        register_body(design, body)
    if not preview:
        return body
    body.convert_result = True  # type: ignore[attr-defined]
    body.convert_calls = []  # type: ignore[attr-defined]

    def convertToSheetMetal(face: Any, rule: Any) -> bool:
        body.convert_calls.append((face, rule))  # type: ignore[attr-defined]
        if not body.convert_result:  # type: ignore[attr-defined]
            return False
        body.isSheetMetal = True  # type: ignore[attr-defined]
        return True

    def getBendFaces() -> tuple[bool, list[Any], list[Any]]:
        pairs = body.bend_pairs  # type: ignore[attr-defined]
        return True, [p[0] for p in pairs], [p[1] for p in pairs]

    body.convertToSheetMetal = convertToSheetMetal  # type: ignore[attr-defined]
    body.getBendFaces = getBendFaces  # type: ignore[attr-defined]
    return body

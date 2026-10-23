"""Fake Fusion parameters: ``UserParameters``, ``Parameter``, ``ModelParameter`` and
``UnitsManager.formatInternalValue`` (c04 D6).

Like Fusion, values are kept in internal units (cm, radians) and recomputed from the
expressions: ``"40 mm"``, ``"width / 2"``, ``"d1 * 2"``. A number without a unit and without
parameter names is read in the parameter's own unit. An expression Fusion would reject
raises ``RuntimeError``. Renaming a parameter rewrites the expressions that use it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any

import fusion_addin

adsk = fusion_addin.adsk

# Internal units per unit string (cm for lengths, radians for angles).
UNIT_SCALE: dict[str, float] = {
    "mm": 0.1,
    "cm": 1.0,
    "m": 100.0,
    "in": 2.54,
    "ft": 30.48,
    "um": 0.0001,
    "yd": 91.44,
    "mil": 0.00254,
    "deg": math.pi / 180,
    "rad": 1.0,
    "": 1.0,
}
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUM = r"\d+(?:\.\d*)?(?:[eE][+-]?\d+)?"
_TOKEN = re.compile(rf"{_NUM}|[A-Za-z_][A-Za-z0-9_]*")
# "40 mm" -> "40*mm": a number (not part of a name) directly followed by a word.
_NUMBER_THEN_WORD = re.compile(rf"(?<![A-Za-z0-9_.])({_NUM})\s*([A-Za-z_])")
# A name or unit word, not the exponent of a number ("1e5").
_WORD = re.compile(r"(?<![A-Za-z0-9_.])[A-Za-z_][A-Za-z0-9_]*")


def unit_scale(unit: str) -> float:
    return UNIT_SCALE.get(unit.strip().lower(), 1.0)


class FakeParameter:
    """``adsk.fusion.Parameter`` (a user parameter unless built as ``FakeModelParameter``)."""

    object_type = "adsk::fusion::UserParameter"

    def __init__(
        self,
        owner: FakeParameterSet,
        name: str,
        expression: str,
        unit: str,
        comment: str = "",
    ) -> None:
        self._owner = owner
        self._name = name
        self._expression = expression
        self.unit = unit
        self.comment = comment
        self.isFavorite = False
        self.objectType = self.object_type
        self.entityToken = None
        self.deleted = False

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, new: str) -> None:
        self._owner.rename(self, new)

    @property
    def expression(self) -> str:
        return self._expression

    @expression.setter
    def expression(self, text: str) -> None:
        self._owner.set_expression(self, text)

    @property
    def value(self) -> float:
        return self._owner.evaluate(self._expression, self.unit, exclude=self)

    @property
    def dependentParameters(self) -> FakeParameterList:
        return FakeParameterList(self._owner.dependents_of(self))

    @property
    def isDeletable(self) -> bool:
        return not self._owner.dependents_of(self)

    def deleteMe(self) -> bool:
        return self._owner.delete(self)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self._name}={self._expression!r}>"


class FakeModelParameter(FakeParameter):
    object_type = "adsk::fusion::ModelParameter"

    def __init__(
        self,
        owner: FakeParameterSet,
        name: str,
        expression: str,
        unit: str,
        created_by: Any = None,
        role: str = "",
        component: Any = None,
    ) -> None:
        super().__init__(owner, name, expression, unit)
        self.createdBy = created_by
        self.role = role
        self.component = component

    @property
    def isDeletable(self) -> bool:
        return False

    def deleteMe(self) -> bool:
        raise RuntimeError("model parameters cannot be deleted")


class FakeParameterList:
    def __init__(self, items: list[FakeParameter]) -> None:
        self._items = list(items)

    @property
    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> FakeParameter:
        return self._items[index]

    def itemByName(self, name: str) -> FakeParameter | None:
        return next((p for p in self._items if p.name == name), None)

    def __iter__(self) -> Any:
        return iter(self._items)


class FakeParameterSet:
    """Every parameter of one design; ``user``/``all``/``model_of`` are live views."""

    def __init__(self) -> None:
        self.params: list[FakeParameter] = []
        # Name of a parameter whose ``add``/``expression`` set should fail like Fusion would.
        self.fail_on: set[str] = set()
        # Called after every change (create, update, rename, delete): lets a test drive
        # feature health from parameter values.
        self.on_change: Callable[[FakeParameterSet], None] | None = None

    # --- evaluation --------------------------------------------------------------------

    def get(self, name: str) -> FakeParameter | None:
        return next((p for p in self.params if p.name == name), None)

    def __getitem__(self, name: str) -> FakeParameter:
        param = self.get(name)
        if param is None:
            raise KeyError(name)
        return param

    def evaluate(self, expression: str, unit: str, exclude: Any = None) -> float:
        words = [t for t in _TOKEN.findall(expression) if NAME.fullmatch(t)]
        names = [w for w in words if w.lower() not in UNIT_SCALE]
        units_used = [w for w in words if w.lower() in UNIT_SCALE]
        scope: dict[str, float] = {}
        for name in names:
            param = self.get(name)
            if param is None or param is exclude:
                raise RuntimeError(f"invalid expression {expression!r}: unknown {name!r}")
            scope["p_" + name] = param.value
        for name in units_used:
            scope["u_" + name] = UNIT_SCALE[name.lower()]

        def rename(match: re.Match[str]) -> str:
            word = match.group(0)
            return ("u_" if word.lower() in UNIT_SCALE else "p_") + word

        code = _NUMBER_THEN_WORD.sub(r"\1*\2", expression)
        code = _WORD.sub(rename, code)
        try:
            value = float(eval(code, {"__builtins__": {}}, scope))
        except Exception as exc:
            raise RuntimeError(f"invalid expression {expression!r}") from exc
        if not names and not units_used:
            value *= unit_scale(unit)
        return value

    def _check(self, name: str, expression: str, unit: str, exclude: Any = None) -> None:
        if name in self.fail_on:
            raise RuntimeError(f"Fusion rejected parameter {name!r}")
        self.evaluate(expression, unit, exclude)

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change(self)

    # --- changes -----------------------------------------------------------------------

    def add_user(self, name: str, expression: str, unit: str, comment: str) -> FakeParameter:
        if not NAME.fullmatch(name):
            raise RuntimeError(f"invalid parameter name {name!r}")
        if self.get(name) is not None:
            raise RuntimeError(f"parameter {name!r} already exists")
        self._check(name, expression, unit)
        param = FakeParameter(self, name, expression, unit, comment)
        self.params.append(param)
        self._changed()
        return param

    def add_model(
        self,
        name: str,
        expression: str,
        unit: str = "mm",
        created_by: Any = None,
        role: str = "",
        component: Any = None,
    ) -> FakeModelParameter:
        param = FakeModelParameter(self, name, expression, unit, created_by, role, component)
        self.params.append(param)
        return param

    def set_expression(self, param: FakeParameter, text: str) -> None:
        self._check(param.name, text, param.unit, exclude=param)
        param._expression = text
        self._changed()

    def rename(self, param: FakeParameter, new: str) -> None:
        if not NAME.fullmatch(new):
            raise RuntimeError(f"invalid parameter name {new!r}")
        if new != param.name and self.get(new) is not None:
            raise RuntimeError(f"parameter {new!r} already exists")
        pattern = re.compile(rf"\b{re.escape(param.name)}\b")
        for other in self.dependents_of(param):
            other._expression = pattern.sub(new, other._expression)
        param._name = new
        self._changed()

    def dependents_of(self, param: FakeParameter) -> list[FakeParameter]:
        pattern = re.compile(rf"\b{re.escape(param.name)}\b")
        return [p for p in self.params if p is not param and pattern.search(p._expression)]

    def delete(self, param: FakeParameter) -> bool:
        if self.dependents_of(param):
            return False
        self.params.remove(param)
        param.deleted = True
        self._changed()
        return True

    # --- views -------------------------------------------------------------------------

    def user(self) -> list[FakeParameter]:
        return [p for p in self.params if not isinstance(p, FakeModelParameter)]

    def model_of(self, component: Any) -> list[FakeParameter]:
        return [
            p for p in self.params if isinstance(p, FakeModelParameter) and p.component is component
        ]


class FakeUserParameters:
    """``design.userParameters``."""

    def __init__(self, params: FakeParameterSet) -> None:
        self._params = params
        self.added: list[tuple[str, str, str, str]] = []

    @property
    def count(self) -> int:
        return len(self._params.user())

    def item(self, index: int) -> FakeParameter:
        return self._params.user()[index]

    def itemByName(self, name: str) -> FakeParameter | None:
        return next((p for p in self._params.user() if p.name == name), None)

    def add(self, name: str, value: Any, units: str, comment: str) -> FakeParameter:
        """``value`` is an ``adsk.core.ValueInput``; a real one is in internal units."""
        if value.kind == "string":
            expression = value.value
        else:
            expression = repr(value.value / unit_scale(units))
        self.added.append((name, expression, units, comment))
        return self._params.add_user(name, expression, units, comment)

    def __iter__(self) -> Any:
        return iter(self._params.user())


class FakeAllParameters:
    """``design.allParameters`` (``ParameterList``)."""

    def __init__(self, params: FakeParameterSet) -> None:
        self._params = params

    @property
    def count(self) -> int:
        return len(self._params.params)

    def item(self, index: int) -> FakeParameter:
        return self._params.params[index]

    def itemByName(self, name: str) -> FakeParameter | None:
        return self._params.get(name)

    def __iter__(self) -> Any:
        return iter(list(self._params.params))


class FakeModelParameters:
    """``component.modelParameters``."""

    def __init__(self, params: FakeParameterSet, component: Any) -> None:
        self._params = params
        self._component = component

    @property
    def count(self) -> int:
        return len(self._params.model_of(self._component))

    def item(self, index: int) -> FakeParameter:
        return self._params.model_of(self._component)[index]

    def itemByName(self, name: str) -> FakeParameter | None:
        return next((p for p in self._params.model_of(self._component) if p.name == name), None)


DISTANCE_UNIT_NAMES = {
    0: "mm",
    1: "cm",
    2: "m",
    3: "in",
    4: "ft",
}


class FakeUnitsManager:
    """``FusionUnitsManager``: distance display units and ``formatInternalValue``."""

    objectType = "adsk::fusion::FusionUnitsManager"

    def __init__(self) -> None:
        self._distance = adsk.fusion.DistanceUnits.MillimeterDistanceUnits
        # Units whose formatting raises, like some compound units in Fusion.
        self.unformattable: set[str] = set()
        self.fail_set_units = False

    @property
    def distanceDisplayUnits(self) -> int:
        return self._distance

    @distanceDisplayUnits.setter
    def distanceDisplayUnits(self, value: int) -> None:
        if self.fail_set_units:
            raise RuntimeError("cannot change the distance units")
        self._distance = value

    @property
    def defaultLengthUnits(self) -> str:
        return DISTANCE_UNIT_NAMES[self._distance]

    def formatInternalValue(self, value: float, units: str = "", showUnits: bool = True) -> str:
        if units in self.unformattable:
            raise RuntimeError(f"cannot format {units!r}")
        number = value / unit_scale(units)
        text = f"{number:.2f}".rstrip("0").rstrip(".")
        return f"{text} {units}" if showUnits and units else text


def attach_parameters(design: Any) -> FakeParameterSet:
    """Give a ``FakeDesign`` user/model parameters and a units manager; return the set."""
    params = FakeParameterSet()
    manager = FakeUnitsManager()
    design.parameter_set = params
    design.userParameters = FakeUserParameters(params)
    design.allParameters = FakeAllParameters(params)
    design.unitsManager = manager
    design.fusionUnitsManager = manager
    for component in list(design.allComponents):
        component.modelParameters = FakeModelParameters(params, component)
    return params

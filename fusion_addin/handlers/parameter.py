"""``parameter.*``: list, create, update and delete design parameters (c04 D6, D7).

Parameters are addressed by name (``param:<name>``, INV-7). Values go out in mm, degrees or
unitless (``fusion_mcp.protocol.units``, INV-6). Batches are atomic: every change goes
through a ``ParameterTransaction``; an error, or a timeline feature that newly falls into
the error state, rolls the whole batch back (INV-5).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from functools import partial
from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.units import (
    FUSION_INTERNAL,
    classify_unit,
    number_expression,
    parameter_value_to_user,
)

from ..core import health, refs
from ..core.dispatcher import to_bridge_error
from ..core.param_tx import ParameterTransaction
from ..core.registry import handler

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_BATCH = 100
KINDS = ("all", "user", "model")
DEFAULT_UNIT = "mm"


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


# --- serialisation (D6) --------------------------------------------------------------------


def is_model(param: Any) -> bool:
    return refs.type_name(param) == "ModelParameter"


def _brief(entity: Any) -> dict[str, Any] | None:
    if entity is None:
        return None
    try:
        ref = refs.to_ref(entity)
    except Exception:
        return None
    return {"ref": ref, "type": refs.type_name(entity), "name": getattr(entity, "name", None)}


def _display(design: Any, param: Any) -> str:
    try:
        text = design.unitsManager.formatInternalValue(param.value, param.unit, True)
    except Exception:
        return str(param.expression)
    return text if isinstance(text, str) and text else str(param.expression)


def serialize(design: Any, param: Any, include_dependents: bool = False) -> dict[str, Any]:
    model = is_model(param)
    out: dict[str, Any] = {
        "ref": refs.PARAM_PREFIX + param.name,
        "name": param.name,
        "kind": "model" if model else "user",
        "expression": param.expression,
        "unit": param.unit,
    }
    try:
        value = parameter_value_to_user(param.value, param.unit)
    except Exception:
        value = None
    # A unit that is neither length, angle nor unitless: no value, only display (spec).
    if value is not None and value["value_unit"] != FUSION_INTERNAL:
        out.update(value)
    out.update(
        {
            "display": _display(design, param),
            "comment": param.comment,
            "is_favorite": bool(param.isFavorite),
            "created_by": _brief(param.createdBy) if model else None,
            "role": param.role if model else None,
            "component": _brief(param.component) if model else None,
        }
    )
    if include_dependents:
        out["dependents"] = [p.name for p in param.dependentParameters]
    return out


def _items(collection: Any) -> list[Any]:
    return [collection.item(i) for i in range(collection.count)]


# --- shared validation and atomic apply ------------------------------------------------------


def _batch(ctx: Any, params: dict[str, Any], name: str) -> list[Any]:
    items = ctx.require(params, name, list)
    if not 1 <= len(items) <= MAX_BATCH:
        raise _invalid(f"'{name}' must have 1..{MAX_BATCH} items, got {len(items)}", param=name)
    return items


def _check_name(name: Any, where: str) -> str:
    if not isinstance(name, str) or not NAME.match(name):
        raise _invalid(
            f"{where}: parameter name {name!r} must match {NAME.pattern}", param=where, value=name
        )
    return name


def _find(design: Any, name: str, where: str) -> Any:
    param = design.allParameters.itemByName(name)
    if param is None:
        raise BridgeError(
            ErrorCode.ENTITY_NOT_FOUND,
            f"{where}: parameter {name!r} not found",
            {"ref": refs.PARAM_PREFIX + name, "param": where},
        )
    return param


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _expression(value: Any, unit: str, where: str) -> str:
    """A Fusion expression string as is; a number becomes ``"<number> <unit>"``."""
    if isinstance(value, str):
        if not value.strip():
            raise _invalid(f"{where}: expression must not be empty", param=where)
        return value
    if _number(value):
        return number_expression(value, unit)
    raise _invalid(
        f"{where}: expression must be a string or a finite number", param=where, value=value
    )


def _with_item(where: str, name: str, fn: Callable[[], Any]) -> Any:
    """Run one Fusion call; tag a failure with the batch item it belongs to."""
    try:
        return fn()
    except Exception as exc:
        error = to_bridge_error(exc)
        error.details.setdefault("item", where)
        error.details.setdefault("name", name)
        if error is exc:
            raise
        raise error from exc


def _apply(design: Any, changes: Callable[[ParameterTransaction], Any]) -> tuple[Any, list[Any]]:
    """Apply ``changes`` in a transaction between two health snapshots (D7).

    A feature that newly falls into ``error`` rolls everything back with
    ``FUSION_API_ERROR`` and ``details.failed_features``; new warnings are returned.
    """
    errors_before = health.timeline_errors(design)
    warnings_before = health.timeline_warnings(design)
    with ParameterTransaction(design) as tx:
        result = changes(tx)
        failed = health.new_items(errors_before, health.timeline_errors(design))
        if failed:
            names = ", ".join(f["name"] for f in failed)
            raise BridgeError(
                ErrorCode.FUSION_API_ERROR,
                f"the change puts features into the error state: {names}; nothing was changed",
                {"failed_features": failed},
            )
        warnings = health.new_items(warnings_before, health.timeline_warnings(design))
    return result, warnings


# --- handlers --------------------------------------------------------------------------------


@handler("parameter.list")
def parameter_list(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    kind = ctx.require(params, "kind", str, "all")
    if kind not in KINDS:
        raise _invalid(f"parameter 'kind' must be one of {', '.join(KINDS)}", param="kind")
    contains = ctx.require(params, "name_contains", str, None)
    component_ref = ctx.require(params, "component", str, None)
    include_dependents = ctx.require(params, "include_dependents", bool, False)
    design = ctx.design

    found: list[Any] = []
    if kind in ("all", "user"):
        found.extend(_items(design.userParameters))
    if kind in ("all", "model"):
        if component_ref is not None:
            component = ctx.refs.resolve(component_ref)
            found.extend(_items(component.modelParameters))
        else:
            found.extend(p for p in _items(design.allParameters) if is_model(p))
    if contains:
        needle = contains.lower()
        found = [p for p in found if needle in p.name.lower()]
    return {"parameters": [serialize(design, p, include_dependents) for p in found]}


@handler("parameter.create", mutating=True)
def parameter_create(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    items = _batch(ctx, params, "parameters")
    design = ctx.design
    planned: list[tuple[str, str, str, str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        where = f"parameters[{index}]"
        if not isinstance(item, dict):
            raise _invalid(f"{where} must be an object", param=where)
        name = _check_name(item.get("name"), where)
        if name in seen:
            raise _invalid(f"{where}: duplicate name {name!r} in the batch", param=where, name=name)
        if design.allParameters.itemByName(name) is not None:
            raise _invalid(f"{where}: parameter {name!r} already exists", param=where, name=name)
        seen.add(name)
        unit = item.get("unit", DEFAULT_UNIT)
        comment = item.get("comment", "")
        if not isinstance(unit, str) or not isinstance(comment, str):
            raise _invalid(f"{where}: 'unit' and 'comment' must be strings", param=where)
        if "expression" not in item:
            raise _invalid(f"{where}: missing 'expression'", param=where)
        expression = _expression(item["expression"], unit, where)
        planned.append((where, name, expression, unit.strip(), comment))

    def create(tx: ParameterTransaction) -> list[Any]:
        return [
            _with_item(where, name, partial(tx.create, name, expr, unit, comment))
            for where, name, expr, unit, comment in planned
        ]

    created, warnings = _apply(design, create)
    return {"parameters": [serialize(design, p) for p in created], "warnings": warnings}


UPDATE_FIELDS = ("expression", "new_name", "comment", "is_favorite")


def _update_number_unit(param: Any) -> str:
    """Numbers for an update are in mm for lengths, degrees for angles (INV-6); otherwise
    in the parameter's own unit."""
    kind = classify_unit(param.unit)
    if kind == "length":
        return "mm"
    if kind == "angle":
        return "deg"
    return str(param.unit)


@handler("parameter.update", mutating=True)
def parameter_update(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    items = _batch(ctx, params, "parameters")
    design = ctx.design
    planned: list[tuple[str, Any, dict[str, Any]]] = []
    names: set[str] = set()
    new_names: set[str] = set()
    for index, item in enumerate(items):
        where = f"parameters[{index}]"
        if not isinstance(item, dict):
            raise _invalid(f"{where} must be an object", param=where)
        name = _check_name(item.get("name"), where)
        if name in names:
            raise _invalid(f"{where}: {name!r} appears twice in the batch", param=where, name=name)
        names.add(name)
        param = _find(design, name, where)
        changes: dict[str, Any] = {}
        if item.get("expression") is not None:
            changes["expression"] = _expression(
                item["expression"], _update_number_unit(param), where
            )
        if item.get("new_name") is not None:
            new_name = _check_name(item["new_name"], where)
            if new_name != name:
                if new_name in new_names or design.allParameters.itemByName(new_name) is not None:
                    raise _invalid(
                        f"{where}: parameter {new_name!r} already exists",
                        param=where,
                        name=new_name,
                    )
                new_names.add(new_name)
                changes["name"] = new_name
        if item.get("comment") is not None:
            if not isinstance(item["comment"], str):
                raise _invalid(f"{where}: 'comment' must be a string", param=where)
            changes["comment"] = item["comment"]
        if item.get("is_favorite") is not None:
            if not isinstance(item["is_favorite"], bool):
                raise _invalid(f"{where}: 'is_favorite' must be a boolean", param=where)
            changes["is_favorite"] = item["is_favorite"]
        if not any(item.get(f) is not None for f in UPDATE_FIELDS):
            raise _invalid(
                f"{where}: pass at least one of {', '.join(UPDATE_FIELDS)}", param=where, name=name
            )
        planned.append((where, param, changes))

    def update(tx: ParameterTransaction) -> list[Any]:
        return [
            _with_item(where, param.name, partial(tx.update, param, **changes))
            for where, param, changes in planned
        ]

    updated, warnings = _apply(design, update)
    return {"parameters": [serialize(design, p) for p in updated], "warnings": warnings}


@handler("parameter.delete", mutating=True)
def parameter_delete(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    names = _batch(ctx, params, "names")
    design = ctx.design
    targets: dict[str, Any] = {}
    for index, name in enumerate(names):
        where = f"names[{index}]"
        _check_name(name, where)
        if name in targets:
            raise _invalid(f"{where}: {name!r} appears twice", param=where, name=name)
        param = _find(design, name, where)
        if is_model(param):
            raise _invalid(
                f"{where}: {name!r} is a model parameter; only user parameters can be deleted",
                param=where,
                name=name,
            )
        targets[name] = param
    # A parameter still referenced from outside the batch (or by features) cannot go.
    for name, param in targets.items():
        if param.isDeletable:
            continue
        dependents = [p.name for p in param.dependentParameters]
        outside = [d for d in dependents if d not in targets]
        if outside or not dependents:
            raise _invalid(
                f"parameter {name!r} is still used"
                + (f" by {', '.join(outside)}" if outside else " by features of the model"),
                name=name,
                dependents=outside,
            )

    def delete(tx: ParameterTransaction) -> list[str]:
        # Dependents first: delete a parameter only when nothing left in the batch uses it.
        remaining = dict(targets)
        order: list[str] = []
        while remaining:
            ready = next(
                n
                for n, p in remaining.items()
                if not any(d.name in remaining for d in p.dependentParameters)
            )
            param = remaining.pop(ready)
            _with_item(ready, ready, partial(tx.delete, param))
            order.append(ready)
        return order

    _, warnings = _apply(design, delete)
    return {"deleted": list(targets), "warnings": warnings}

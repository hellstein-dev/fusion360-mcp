"""``ParameterTransaction`` (c04 D7): parameter changes with a journal of inverse operations.

Parameters are not timeline items, so the dispatcher's timeline rollback (c02 D5) does not
cover them. Every change goes through the transaction; ``rollback`` undoes the journal in
reverse order: create -> ``deleteMe()``; update -> previous name/expression/comment/
favourite; delete -> ``userParameters.add`` with the old expression, unit and comment.

Used as a context manager: an exception inside the block rolls the journal back and is
re-raised as a ``BridgeError``; errors of the rollback itself go to
``details.rollback_error``.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Any

import adsk.core

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

from .dispatcher import to_bridge_error

_UNSET = object()


@dataclass
class _Entry:
    action: str  # "create" | "update" | "delete"
    param: Any
    state: dict[str, Any]


def _state(param: Any) -> dict[str, Any]:
    return {
        "name": param.name,
        "expression": param.expression,
        "comment": param.comment,
        "is_favorite": param.isFavorite,
    }


class ParameterTransaction:
    def __init__(self, design: Any) -> None:
        self._design = design
        self.journal: list[_Entry] = []

    # --- operations --------------------------------------------------------------------

    def create(self, name: str, expression: str, unit: str, comment: str = "") -> Any:
        value = adsk.core.ValueInput.createByString(expression)
        param = self._design.userParameters.add(name, value, unit, comment)
        if param is None:
            raise BridgeError(
                ErrorCode.FUSION_API_ERROR,
                f"Fusion did not create parameter {name!r}",
                {"name": name, "expression": expression},
            )
        self.journal.append(_Entry("create", param, {"name": name}))
        return param

    def update(
        self,
        param: Any,
        expression: Any = _UNSET,
        name: Any = _UNSET,
        comment: Any = _UNSET,
        is_favorite: Any = _UNSET,
    ) -> Any:
        """Apply the given fields; the previous state is journaled before any change."""
        self.journal.append(_Entry("update", param, _state(param)))
        if name is not _UNSET and name != param.name:
            param.name = name
        if expression is not _UNSET:
            param.expression = expression
        if comment is not _UNSET:
            param.comment = comment
        if is_favorite is not _UNSET:
            param.isFavorite = is_favorite
        return param

    def delete(self, param: Any) -> None:
        state = {**_state(param), "unit": param.unit}
        if not param.deleteMe():
            raise BridgeError(
                ErrorCode.FUSION_API_ERROR,
                f"Fusion did not delete parameter {state['name']!r}",
                {"name": state["name"]},
            )
        self.journal.append(_Entry("delete", param, state))

    # --- rollback ----------------------------------------------------------------------

    def rollback(self) -> list[str]:
        """Undo the journal in reverse order; return the errors (empty if all undone)."""
        errors: list[str] = []
        while self.journal:
            entry = self.journal.pop()
            try:
                self._undo(entry)
            except Exception as exc:
                name = entry.state.get("name")
                errors.append(f"{entry.action} {name}: {str(exc) or type(exc).__name__}")
        return errors

    def _undo(self, entry: _Entry) -> None:
        state = entry.state
        if entry.action == "create":
            if not entry.param.deleteMe():
                raise RuntimeError("deleteMe returned False")
        elif entry.action == "update":
            param = entry.param
            if param.name != state["name"]:
                param.name = state["name"]
            if param.expression != state["expression"]:
                param.expression = state["expression"]
            if param.comment != state["comment"]:
                param.comment = state["comment"]
            if param.isFavorite != state["is_favorite"]:
                param.isFavorite = state["is_favorite"]
        else:
            value = adsk.core.ValueInput.createByString(state["expression"])
            restored = self._design.userParameters.add(
                state["name"], value, state["unit"], state["comment"]
            )
            if restored is None:
                raise RuntimeError("userParameters.add returned None")
            if state["is_favorite"]:
                restored.isFavorite = True

    def fail(self, error: BridgeError) -> BridgeError:
        """Roll back and return ``error`` with ``details.rollback_error`` if undo failed."""
        errors = self.rollback()
        if errors:
            error.details["rollback_error"] = "; ".join(errors)
        return error

    def __enter__(self) -> ParameterTransaction:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is None or not isinstance(exc, Exception):
            return
        error = self.fail(to_bridge_error(exc))
        if error is not exc:
            raise error from exc
        # A BridgeError propagates as is, with the rollback details added in place.

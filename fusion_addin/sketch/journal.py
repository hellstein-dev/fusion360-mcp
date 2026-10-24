"""``SketchJournal`` (c05 D6): atomic edits of an existing sketch (INV-5).

Curves, points, constraints, dimensions and texts added to an existing sketch create no
timeline items, so the dispatcher's timeline rollback (c02 D5) does not undo them. Every
mutating sketch handler works inside ``with SketchJournal(sketch) as j:``:

- ``j.created(entity)`` journals what was created; on an exception it is deleted in reverse
  order (``deleteMe()`` of entities that are still ``isValid``: deleting a curve may already
  have cascaded to its constraints and points).
- ``j.on_undo(fn)`` journals a restore closure for a property change (old ``expression`` of
  a dimension parameter, ``isDriving``, ``isConstruction``, ``isFixed``, the inverse matrix of
  ``Sketch.move``); closures and deletions run interleaved, newest first.
- ``j.defer()`` sets ``Sketch.isComputeDeferred = True``; ``j.undefer()`` (called by the
  handler before it computes profiles) and the end of the block always restore the previous
  value, before any rollback.

An exception inside the block rolls the journal back and propagates as a ``BridgeError``
(Fusion's ``RuntimeError`` -> ``FUSION_API_ERROR``); rollback failures are collected in
``details.rollback_error`` and never mask the original error.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import TracebackType
from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

from ..core.dispatcher import to_bridge_error
from ..core.refs import type_name


@dataclass
class _Entry:
    kind: str  # "created" | "undo"
    target: Any
    label: str


def _describe(entity: Any) -> str:
    try:
        token = entity.entityToken
    except Exception:
        token = None
    kind = type_name(entity)
    return f"{kind} {token}" if isinstance(token, str) and token else kind


def _is_valid(entity: Any) -> bool:
    try:
        return bool(getattr(entity, "isValid", True))
    except Exception:
        return False


def _message(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


class SketchJournal:
    def __init__(self, sketch: Any) -> None:
        self.sketch = sketch
        self.entries: list[_Entry] = []
        self._deferred_before: bool | None = None

    # --- journaling --------------------------------------------------------------------

    def created(self, entity: Any) -> Any:
        """Journal a created entity and return it; ``None`` (Fusion created nothing) is
        ``FUSION_API_ERROR``."""
        if entity is None:
            raise BridgeError(ErrorCode.FUSION_API_ERROR, "Fusion did not create the entity")
        self.entries.append(_Entry("created", entity, _describe(entity)))
        return entity

    def created_all(self, entities: Iterable[Any]) -> list[Any]:
        """Journal every entity of a collection (``ObjectCollection``/list); returns them."""
        items = list(entities)
        for entity in items:
            self.created(entity)
        return items

    def on_undo(self, fn: Callable[[], object], label: str = "undo") -> None:
        """Journal a restore closure, run on rollback (newest first)."""
        self.entries.append(_Entry("undo", fn, label))

    # --- compute deferral --------------------------------------------------------------

    def defer(self) -> None:
        """``isComputeDeferred = True``; the previous value is kept for ``undefer``."""
        if self._deferred_before is None:
            self._deferred_before = bool(self.sketch.isComputeDeferred)
        self.sketch.isComputeDeferred = True

    def undefer(self) -> None:
        """Restore ``isComputeDeferred`` to its value before ``defer`` (no-op otherwise)."""
        before = self._deferred_before
        if before is None:
            return
        self._deferred_before = None
        self.sketch.isComputeDeferred = before

    # --- rollback ----------------------------------------------------------------------

    def rollback(self) -> list[str]:
        """Undo the journal newest first; return the errors (empty if all undone)."""
        errors: list[str] = []
        while self.entries:
            entry = self.entries.pop()
            try:
                if entry.kind == "created":
                    if _is_valid(entry.target) and entry.target.deleteMe() is False:
                        raise RuntimeError("deleteMe returned False")
                else:
                    entry.target()
            except Exception as exc:
                action = "delete" if entry.kind == "created" else "undo"
                errors.append(f"{action} {entry.label}: {_message(exc)}")
        return errors

    def fail(self, error: BridgeError) -> BridgeError:
        """Restore the deferral flag, roll back, add ``details.rollback_error`` if needed."""
        errors: list[str] = []
        try:
            self.undefer()
        except Exception as exc:
            errors.append(f"restore isComputeDeferred: {_message(exc)}")
        errors.extend(self.rollback())
        if errors:
            error.details["rollback_error"] = "; ".join(errors)
        return error

    def __enter__(self) -> SketchJournal:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is None:
            try:
                self.undefer()
            except Exception as restore_exc:
                raise self.fail(to_bridge_error(restore_exc)) from restore_exc
            self.entries.clear()
            return
        if not isinstance(exc, Exception):
            # KeyboardInterrupt/SystemExit: only put the flag back.
            with contextlib.suppress(Exception):
                self.undefer()
            return
        error = self.fail(to_bridge_error(exc))
        if error is not exc:
            raise error from exc
        # A BridgeError propagates as is, with the rollback details added in place.

"""``Compensation`` (c09 D4): undo journal for changes that add no timeline items.

The dispatcher's timeline rollback (c02 D5) removes items a failed handler added, but not
property changes: suppression, parameter expressions, names, group collapse, the marker.
A handler registers an undo **before** each change; when the block raises, the undos run
in reverse order. Undo failures go to ``details.compensation_errors``; an exception that
is not a ``BridgeError`` becomes ``FUSION_API_ERROR``. The error is re-raised either way, so
the dispatcher's D5 rollback still runs after the compensation. Reused by c10.
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType

from .dispatcher import to_bridge_error


class Compensation:
    def __init__(self) -> None:
        self._undo: list[tuple[str, Callable[[], object]]] = []

    def add(self, undo: Callable[[], object], label: str) -> None:
        """Register ``undo`` (call it before the change it reverts)."""
        self._undo.append((label, undo))

    def __len__(self) -> int:
        return len(self._undo)

    def run(self) -> list[str]:
        """Run the undos in reverse order; return ``"<label>: <error>"`` for each failure."""
        errors: list[str] = []
        while self._undo:
            label, undo = self._undo.pop()
            try:
                undo()
            except Exception as exc:
                errors.append(f"{label}: {str(exc) or type(exc).__name__}")
        return errors

    def __enter__(self) -> Compensation:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc is None or not isinstance(exc, Exception):
            self._undo.clear()
            return
        errors = self.run()
        error = to_bridge_error(exc)
        if errors:
            error.details["compensation_errors"] = errors
        if error is not exc:
            raise error from exc
        # A BridgeError propagates as is, with the compensation details added in place.

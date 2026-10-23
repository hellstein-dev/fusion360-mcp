"""Handler registry (design D4): ``@handler("group.action", mutating=False)``.

Handler signature: ``fn(ctx: Context, params: dict) -> dict``. ``mutating=True`` makes the
dispatcher roll the timeline back if the handler raises (D5).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

HandlerFn = Callable[[Any, dict[str, Any]], Any]

METHOD_NAME = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class DuplicateHandlerError(ValueError):
    """Two handlers registered under one method name."""


@dataclass(frozen=True)
class HandlerSpec:
    name: str
    fn: HandlerFn
    mutating: bool


class Registry:
    def __init__(self) -> None:
        self._handlers: dict[str, HandlerSpec] = {}

    def register(self, name: str, fn: HandlerFn, mutating: bool = False) -> HandlerSpec:
        if not METHOD_NAME.match(name):
            raise ValueError(f"method name {name!r} must be '<group>.<action>' in snake_case")
        if name in self._handlers:
            raise DuplicateHandlerError(f"handler {name!r} is already registered")
        spec = HandlerSpec(name, fn, mutating)
        self._handlers[name] = spec
        return spec

    def handler(self, name: str, mutating: bool = False) -> Callable[[HandlerFn], HandlerFn]:
        def decorate(fn: HandlerFn) -> HandlerFn:
            self.register(name, fn, mutating)
            return fn

        return decorate

    def get(self, name: str) -> HandlerSpec | None:
        return self._handlers.get(name)

    def names(self) -> list[str]:
        return sorted(self._handlers)

    def __contains__(self, name: object) -> bool:
        return name in self._handlers

    def __len__(self) -> int:
        return len(self._handlers)


# The add-in's registry: handler modules in ``handlers/`` register here on import.
REGISTRY = Registry()


def handler(name: str, mutating: bool = False) -> Callable[[HandlerFn], HandlerFn]:
    """Register ``fn`` in the add-in registry under ``name``."""
    return REGISTRY.handler(name, mutating)

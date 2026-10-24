"""``dev.reload``: hot-reload the add-in's code without restarting it (c15 design D1).

Development only: the ``fusion-mcp reload-addin`` CLI calls it; it is not an MCP tool.

The registry is cleared in place (the running ``Dispatcher`` holds that object), every module
of the add-in package and of ``fusion_mcp.protocol`` is dropped from ``sys.modules``, the
``handlers`` package (and every other dropped module) is imported again, so the decorators
refill the same registry. Globals of the kept modules that pointed to an old module, class or
function (the entry point's ``Context``, say) are rebound to the new ones, so old and new
classes never meet in one request.

Not reloaded (they hold the socket, the job queue and the registry of the running bridge, or
their classes cross the bridge boundary): the package / entry point, ``core``, ``core.server``,
``core.dispatcher``, ``core.registry`` and ``fusion_mcp.protocol.errors`` / ``wire`` /
``discovery``. Changing one of them still needs a manual restart of the add-in
(Utilities > Add-ins > FusionMCP > Stop, Run).
"""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Callable, Iterable, MutableMapping
from typing import Any

from ..core.registry import REGISTRY, Registry, handler

PACKAGE = __name__.rpartition(".handlers.")[0]
PROTOCOL = "fusion_mcp.protocol"

KEEP_ADDIN = ("", ".FusionMCP", ".core", ".core.server", ".core.dispatcher", ".core.registry")
KEEP_PROTOCOL = ("", ".errors", ".wire", ".discovery")
# Handlers put back from before the reload when the new import fails.
SURVIVORS = ("dev.reload", "status.get")


def kept_modules(package: str = PACKAGE, protocol: str = PROTOCOL) -> frozenset[str]:
    """Modules ``reload_addin`` never drops from ``sys.modules``."""
    return frozenset(
        [package + suffix for suffix in KEEP_ADDIN] + [protocol + s for s in KEEP_PROTOCOL]
    )


def reloadable(name: str, package: str = PACKAGE, protocol: str = PROTOCOL) -> bool:
    in_scope = any(name == root or name.startswith(root + ".") for root in (package, protocol))
    return in_scope and name not in kept_modules(package, protocol)


def rebind(
    kept: Iterable[str], reloaded: Iterable[str], modules: MutableMapping[str, Any]
) -> list[str]:
    """Point globals of the ``kept`` modules at the reloaded module, class or function of the
    same name. Returns ``"<module>.<global>"`` of every rebound name."""
    fresh = {name: modules[name] for name in reloaded if name in modules}
    rebound: list[str] = []
    for kept_name in sorted(kept):
        module = modules.get(kept_name)
        if module is None:
            continue
        for attr, value in list(vars(module).items()):
            new: Any = None
            if isinstance(value, types.ModuleType):
                new = fresh.get(value.__name__)
            elif isinstance(value, (type, types.FunctionType)):
                owner = fresh.get(getattr(value, "__module__", ""))
                new = getattr(owner, value.__name__, None) if owner is not None else None
            if new is not None and new is not value:
                setattr(module, attr, new)
                rebound.append(f"{kept_name}.{attr}")
    return rebound


def reload_addin(
    registry: Registry = REGISTRY,
    package: str = PACKAGE,
    protocol: str = PROTOCOL,
    modules: MutableMapping[str, Any] | None = None,
    importer: Callable[[str], Any] = importlib.import_module,
) -> dict[str, Any]:
    """Clear ``registry``, drop the reloadable modules and import them (``handlers`` first)
    again, then rebind the kept modules' globals.

    If an import fails the error propagates (the dispatcher maps it to ``FUSION_API_ERROR``
    with the traceback); the previous ``dev.reload`` and ``status.get`` are registered back if
    the failed import did not, so the bridge keeps answering and a fixed file can be reloaded
    without a restart.
    """
    modules = sys.modules if modules is None else modules
    names = sorted(name for name in list(modules) if reloadable(name, package, protocol))
    survivors = [spec for spec in map(registry.get, SURVIVORS) if spec is not None]
    registry.clear()
    for name in names:
        modules.pop(name, None)
    try:
        importer(package + ".handlers")
        for name in names:
            if name not in modules:
                importer(name)
    except BaseException:
        for spec in survivors:
            if spec.name not in registry:
                registry.register(spec.name, spec.fn, spec.mutating)
        raise
    rebind(kept_modules(package, protocol), names, modules)
    return {"reloaded_modules": names, "handlers": len(registry)}


@handler("dev.reload")
def dev_reload(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    return reload_addin()

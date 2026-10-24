"""``dev.reload`` (c15 task 1.1): hot reload of the add-in's handler modules.

The real reload runs on a throwaway package in ``tmp_path`` (its own registry copy), so the
add-in modules of this test session are never dropped from ``sys.modules``.
"""

import importlib
import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

import fusion_addin.handlers
from fusion_addin.core import registry as registry_module
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY, Registry
from fusion_addin.handlers import dev
from fusion_mcp.protocol.errors import ErrorCode
from fusion_mcp.protocol.wire import Request

PKG = "rl_addin"
PROTO = "rl_proto"

STATUS = (
    "from ..core.registry import handler\n"
    "VERSION = {version!r}\n"
    "@handler('status.get')\n"
    "def status_get(ctx, params):\n"
    "    return {{'version': VERSION}}\n"
)
DEV = (
    "from fusion_addin.handlers.dev import reload_addin\n"
    "from ..core.registry import REGISTRY, handler\n"
    "@handler('dev.reload')\n"
    "def dev_reload(ctx, params):\n"
    "    return reload_addin(REGISTRY, {pkg!r}, {proto!r})\n"
)


@pytest.fixture
def addin(tmp_path: Path):
    root = tmp_path / PKG
    for sub in ("core", "handlers"):
        (root / sub).mkdir(parents=True)
        (root / sub / "__init__.py").write_text("")
    (root / "__init__.py").write_text("")
    # Like the real entry point: holds ``Context`` and builds contexts with it.
    (root / "FusionMCP.py").write_text(
        "from . import handlers\nfrom .core.context import Context\nfrom .core import geometry\n"
    )
    (root / "core" / "registry.py").write_text(Path(registry_module.__file__).read_text())
    for name in ("server", "dispatcher"):
        (root / "core" / f"{name}.py").write_text("from .registry import REGISTRY\n")
    (root / "core" / "context.py").write_text("class Context:\n    pass\n")
    (root / "core" / "geometry.py").write_text("")
    (root / "handlers" / "__init__.py").write_text("from . import dev, status\n")
    (root / "handlers" / "status.py").write_text(STATUS.format(version=1))
    (root / "handlers" / "dev.py").write_text(DEV.format(pkg=PKG, proto=PROTO))
    sys.path.insert(0, str(tmp_path))
    importlib.import_module(f"{PKG}.FusionMCP")
    importlib.import_module(f"{PKG}.core.server")
    importlib.import_module(f"{PKG}.core.dispatcher")
    importlib.import_module(f"{PKG}.core.context")
    importlib.import_module(f"{PKG}.core.geometry")
    try:
        yield root
    finally:
        sys.path.remove(str(tmp_path))
        for name in [n for n in sys.modules if n == PKG or n.startswith(PKG + ".")]:
            del sys.modules[name]
        importlib.invalidate_caches()


def _rewrite(path: Path, text: str) -> None:
    """Edit a module the way an editor does: a later mtime, so no stale ``.pyc`` is used."""
    stamp = path.stat().st_mtime + 10
    path.write_text(text)
    os.utime(path, (stamp, stamp))
    importlib.invalidate_caches()


def _registry() -> Registry:
    return sys.modules[f"{PKG}.core.registry"].REGISTRY


def _call(registry: Registry, method: str):
    return Dispatcher("t", registry).dispatch(Request("1", "t", method))


def test_dev_reload_is_registered_non_mutating() -> None:
    assert "dev" in fusion_addin.handlers.__all__
    spec = REGISTRY.get("dev.reload")
    assert spec is not None
    assert spec.fn is dev.dev_reload
    assert spec.mutating is False


def test_registry_clear_keeps_object() -> None:
    reg = Registry()
    reg.register("a.b", lambda ctx, params: {})
    reg.clear()
    assert len(reg) == 0
    assert reg.get("a.b") is None


def test_kept_modules_default_package() -> None:
    kept = dev.kept_modules()
    assert dev.PACKAGE == "fusion_addin"
    for name in (
        "fusion_addin",
        "fusion_addin.FusionMCP",
        "fusion_addin.core.server",
        "fusion_addin.core.dispatcher",
        "fusion_addin.core.registry",
        "fusion_mcp.protocol.errors",
        "fusion_mcp.protocol.wire",
    ):
        assert name in kept
    assert dev.reloadable("fusion_addin.handlers.sketch")
    assert dev.reloadable("fusion_addin.core.context")
    assert dev.reloadable("fusion_mcp.protocol.units")
    assert not dev.reloadable("fusion_mcp.server.app")
    assert not dev.reloadable("fusion_addin_other.x")
    assert not dev.reloadable("adsk.core")


def test_reload_restores_registry_with_new_code(addin: Path) -> None:
    registry = _registry()
    kept = {
        name: sys.modules[f"{PKG}{name}"]
        for name in ("", ".FusionMCP", ".core.server", ".core.dispatcher", ".core.registry")
    }
    old_spec = registry.get("status.get")
    assert old_spec is not None
    old_status = old_spec.fn
    assert old_status(None, {}) == {"version": 1}
    _rewrite(addin / "handlers" / "status.py", STATUS.format(version=2))

    response = _call(registry, "dev.reload")

    assert response.ok, response.error
    result = response.result
    assert set(registry.names()) == {"dev.reload", "status.get"}
    assert result["handlers"] == 2
    assert f"{PKG}.handlers.status" in result["reloaded_modules"]
    assert f"{PKG}.core.geometry" in result["reloaded_modules"]
    assert not set(result["reloaded_modules"]) & set(dev.kept_modules(PKG, PROTO))
    assert registry is _registry()
    for name, module in kept.items():
        assert sys.modules[f"{PKG}{name}"] is module
    new_spec = registry.get("status.get")
    assert new_spec is not None
    assert new_spec.fn is not old_status
    assert _call(registry, "status.get").result == {"version": 2}


def test_reload_rebinds_globals_of_kept_modules(addin: Path) -> None:
    entry = sys.modules[f"{PKG}.FusionMCP"]
    old_context, old_geometry = entry.Context, entry.geometry

    response = _call(_registry(), "dev.reload")

    assert response.ok, response.error
    assert f"{PKG}.core.context" in response.result["reloaded_modules"]
    # core.geometry is imported only by the entry point: re-imported all the same.
    assert entry.geometry is sys.modules[f"{PKG}.core.geometry"] is not old_geometry
    assert entry.Context is sys.modules[f"{PKG}.core.context"].Context is not old_context
    assert sys.modules[f"{PKG}.FusionMCP"] is entry


def test_import_error_is_fusion_api_error_and_bridge_keeps_serving(addin: Path) -> None:
    registry = _registry()
    # The broken module is imported before dev and status: neither registers itself again.
    broken = addin / "handlers" / "broken.py"
    broken.write_text("raise RuntimeError('broken handler module')\n")
    _rewrite(addin / "handlers" / "__init__.py", "from . import broken, dev, status\n")
    _rewrite(addin / "handlers" / "status.py", STATUS.format(version=3))

    response = _call(registry, "dev.reload")

    assert not response.ok
    assert response.error is not None
    assert response.error.code == ErrorCode.FUSION_API_ERROR
    assert "broken handler module" in response.error.message
    assert "broken handler module" in response.error.details["traceback"]
    assert registry.names() == ["dev.reload", "status.get"]
    assert _call(registry, "status.get").result == {"version": 1}

    _rewrite(broken, "")
    assert _call(registry, "dev.reload").ok
    assert _call(registry, "status.get").result == {"version": 3}


def test_injected_modules_and_importer() -> None:
    registry = Registry()
    registry.register("old.handler", lambda ctx, params: {})
    names = [
        "pkg",
        "pkg.core.registry",
        "pkg.handlers",
        "pkg.handlers.sketch",
        "proto.units",
        "proto.errors",
        "other",
    ]
    old = {name: types.ModuleType(name) for name in names}
    old["pkg"].__dict__["units"] = old["proto.units"]  # a kept module's global: rebound
    modules: dict[str, Any] = dict(old)
    imported: list[str] = []

    def importer(name: str) -> None:
        imported.append(name)
        modules[name] = types.ModuleType(name)
        if name == "pkg.handlers":
            registry.register("status.get", lambda ctx, params: {})

    result = dev.reload_addin(registry, "pkg", "proto", modules, importer)

    assert imported == ["pkg.handlers", "pkg.handlers.sketch", "proto.units"]
    assert result == {
        "reloaded_modules": ["pkg.handlers", "pkg.handlers.sketch", "proto.units"],
        "handlers": 1,
    }
    assert modules["pkg.core.registry"] is old["pkg.core.registry"]
    assert modules["proto.errors"] is old["proto.errors"]
    assert modules["pkg.handlers.sketch"] is not old["pkg.handlers.sketch"]
    assert old["pkg"].units is modules["proto.units"]
    assert registry.names() == ["status.get"]


def test_rebind_classes_and_functions() -> None:
    old_mod, new_mod, kept = (types.ModuleType(n) for n in ("m", "m", "k"))
    exec("class C: pass\ndef f(): pass\nX = 1", old_mod.__dict__)
    exec("class C: pass\ndef f(): pass\nX = 2", new_mod.__dict__)
    for obj in (old_mod.C, old_mod.f, new_mod.C, new_mod.f):
        obj.__module__ = "m"
    kept.__dict__.update(C=old_mod.C, alias=old_mod.f, X=old_mod.X)

    rebound = dev.rebind(["k"], ["m"], {"k": kept, "m": new_mod})

    assert sorted(rebound) == ["k.C", "k.alias"]
    assert kept.C is new_mod.C
    assert kept.alias is new_mod.f
    assert kept.X == 1

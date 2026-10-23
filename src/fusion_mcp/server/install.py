"""Install the FusionMCP add-in into Fusion's AddIns folder (design D9).

Default: a link named ``FusionMCP`` to ``mcp-plugin/fusion_addin`` (a junction on Windows,
a symlink on macOS), so the add-in finds ``../src/fusion_mcp/protocol``. ``copy=True``
copies the add-in and vendors ``fusion_mcp.protocol`` into ``FusionMCP/_vendor/`` for
machines where links cannot be created. A target the installer did not create is never
touched.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path

import fusion_mcp

ADDIN_NAME = "FusionMCP"
# Written into copied installs so uninstall/reinstall can tell them from foreign folders.
MARKER_FILE = ".fusion-mcp-install"
MARKER_TEXT = "installed by fusion-mcp install-addin --copy\n"
FUSION_API_SUBDIR = Path("Autodesk") / "Autodesk Fusion 360" / "API" / "AddIns"


class InstallError(RuntimeError):
    """The add-in cannot be (un)installed; the message says why."""


def plugin_dir() -> Path:
    """``mcp-plugin/``: ``src/fusion_mcp/server/install.py`` is four levels below it."""
    return Path(__file__).resolve().parents[3]


def addin_source() -> Path:
    return plugin_dir() / "fusion_addin"


def protocol_source() -> Path:
    return Path(fusion_mcp.__file__).resolve().parent / "protocol"


def addins_dir(
    platform: str = sys.platform,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Fusion's per-user AddIns folder."""
    env = os.environ if env is None else env
    if platform == "win32":
        appdata = env.get("APPDATA")
        if not appdata:
            raise InstallError("APPDATA is not set; cannot locate Fusion's AddIns folder")
        return Path(appdata) / FUSION_API_SUBDIR
    if platform == "darwin":
        base = home if home is not None else Path.home()
        return base / "Library" / "Application Support" / FUSION_API_SUBDIR
    raise InstallError(f"Fusion 360 runs on Windows and macOS only, not on {platform!r}")


def is_link(path: Path) -> bool:
    """Symlink or Windows junction."""
    return path.is_symlink() or bool(getattr(os.path, "isjunction", lambda p: False)(path))


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def is_ours(target: Path, source: Path) -> bool:
    if is_link(target):
        return _same(target, source)
    marker = target / MARKER_FILE
    return marker.is_file() and marker.read_text(encoding="utf-8") == MARKER_TEXT


def _exists(path: Path) -> bool:
    return path.exists() or is_link(path)


def _remove(target: Path) -> None:
    if is_link(target):
        # A junction is removed like an empty directory; neither form touches the source.
        if target.is_symlink():
            target.unlink()
        else:
            os.rmdir(target)
    else:
        shutil.rmtree(target)


def _link(source: Path, target: Path, platform: str) -> None:
    if platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(source), str(target))  # type: ignore[attr-defined,unused-ignore]
    else:
        os.symlink(source, target, target_is_directory=True)


def _copy(source: Path, target: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "_vendor")
    shutil.copytree(source, target, ignore=ignore)
    vendor = target / "_vendor" / "fusion_mcp"
    shutil.copytree(protocol_source(), vendor / "protocol", ignore=ignore)
    (vendor / "__init__.py").write_text(
        f'"""Vendored fusion_mcp.protocol for the FusionMCP add-in."""\n\n'
        f'__version__ = "{fusion_mcp.__version__}"\n',
        encoding="utf-8",
    )
    (target / MARKER_FILE).write_text(MARKER_TEXT, encoding="utf-8")


def install(
    copy: bool = False,
    addins: Path | None = None,
    source: Path | None = None,
    platform: str = sys.platform,
) -> Path:
    """Install (or reinstall over our own previous install); return the target path."""
    addins = addins if addins is not None else addins_dir(platform)
    source = source if source is not None else addin_source()
    if not (source / f"{ADDIN_NAME}.py").is_file():
        raise InstallError(f"add-in sources not found in {source}")
    target = addins / ADDIN_NAME
    if _exists(target):
        if not is_ours(target, source):
            raise InstallError(
                f"{target} exists and was not created by fusion-mcp; remove it yourself "
                "if it is an old copy of the add-in"
            )
        _remove(target)
    addins.mkdir(parents=True, exist_ok=True)
    if copy:
        _copy(source, target)
    else:
        try:
            _link(source, target, platform)
        except OSError as exc:
            raise InstallError(
                f"cannot create a link at {target} ({exc}); retry with --copy"
            ) from exc
    return target


def uninstall(addins: Path | None = None, source: Path | None = None) -> bool:
    """Remove our install; ``False`` if nothing was installed."""
    addins = addins if addins is not None else addins_dir()
    source = source if source is not None else addin_source()
    target = addins / ADDIN_NAME
    if not _exists(target):
        return False
    if not is_ours(target, source):
        raise InstallError(f"{target} was not created by fusion-mcp; not removing it")
    _remove(target)
    return True

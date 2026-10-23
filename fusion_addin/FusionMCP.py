"""FusionMCP add-in entry point: Fusion calls ``run(context)`` on load and ``stop(context)``.

``run`` makes ``fusion_mcp.protocol`` importable (``_vendor/`` of a copied install, else
``../src`` of a linked one, design D9), registers the CustomEvent that carries bridge jobs
to the main thread (INV-3), starts the ``BridgeServer`` and publishes the discovery file.
Nothing raised here reaches Fusion: failures are shown with ``ui.messageBox``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ADDIN_DIR = Path(__file__).resolve().parent


def protocol_root(addin_dir: Path = ADDIN_DIR) -> Path | None:
    """Directory to put on ``sys.path`` so ``import fusion_mcp.protocol`` works."""
    for candidate in (addin_dir / "_vendor", addin_dir.parent / "src"):
        if (candidate / "fusion_mcp" / "protocol" / "__init__.py").is_file():
            return candidate
    return None


def ensure_protocol_path(addin_dir: Path = ADDIN_DIR) -> Path | None:
    root = protocol_root(addin_dir)
    if root is not None and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


# Before the core imports below: they import fusion_mcp.protocol.
ensure_protocol_path()

import contextlib  # noqa: E402
import os  # noqa: E402
import secrets  # noqa: E402
import traceback  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from typing import Any  # noqa: E402

import adsk.core  # noqa: E402

from fusion_mcp.protocol.discovery import (  # noqa: E402
    BridgeInfo,
    remove_discovery,
    write_discovery,
)

from . import handlers  # noqa: E402, F401  (registers the handler groups)
from .core.context import Context  # noqa: E402
from .core.dispatcher import Dispatcher  # noqa: E402
from .core.registry import REGISTRY, Registry  # noqa: E402
from .core.server import DEFAULT_PORT, BridgeServer  # noqa: E402
from .core.ui import BridgeUI  # noqa: E402
from .core.version import ADDIN_VERSION  # noqa: E402

EVENT_ID = "FusionMCP.BridgeJob"


class _JobEventHandler(adsk.core.CustomEventHandler):
    """Main thread: run the next queued bridge job."""

    def __init__(self, bridge: Bridge) -> None:
        super().__init__()
        self._bridge = bridge

    def notify(self, args: Any) -> None:
        server = self._bridge.server
        if server is None:
            return
        with contextlib.suppress(Exception):
            server.process_next(getattr(args, "additionalInfo", None))


class Bridge:
    """The running bridge: CustomEvent + ``BridgeServer`` + discovery file."""

    def __init__(
        self,
        app: Any,
        registry: Registry = REGISTRY,
        home: Path | None = None,
        port: int = DEFAULT_PORT,
    ) -> None:
        self.app = app
        self._registry = registry
        self._home = home
        self._port = port
        self.server: BridgeServer | None = None
        self._event: Any = None
        self._handler: _JobEventHandler | None = None

    @property
    def running(self) -> bool:
        return self.server is not None and self.server.running

    @property
    def port(self) -> int | None:
        return self.server.port if self.server is not None else None

    def start(self) -> int:
        if self.server is not None and self.server.running:
            return self.server.port or 0
        token = secrets.token_hex(16)
        self._event = self.app.registerCustomEvent(EVENT_ID)
        self._handler = _JobEventHandler(self)
        self._event.add(self._handler)
        try:
            dispatcher = Dispatcher(token, self._registry, lambda params: Context(self.app, params))
            self.server = BridgeServer(token, dispatcher.dispatch, self._fire, self._port)
            port = self.server.start()
            write_discovery(
                BridgeInfo(
                    port=port,
                    token=token,
                    pid=os.getpid(),
                    addin_version=ADDIN_VERSION,
                    started_at=datetime.now(UTC).isoformat(timespec="seconds"),
                ),
                self._home,
            )
        except BaseException:
            self.stop()
            raise
        return port

    def _fire(self, job_id: str) -> None:
        self.app.fireCustomEvent(EVENT_ID, job_id)

    def stop(self) -> None:
        server, self.server = self.server, None
        errors: list[BaseException] = []
        if server is not None:
            try:
                server.stop()
            except Exception as exc:
                errors.append(exc)
        try:
            remove_discovery(self._home)
        except Exception as exc:
            errors.append(exc)
        if self._event is not None:
            try:
                if self._handler is not None:
                    self._event.remove(self._handler)
                self.app.unregisterCustomEvent(EVENT_ID)
            except Exception as exc:
                errors.append(exc)
            self._event = None
            self._handler = None
        if errors:
            raise errors[0]


_bridge: Bridge | None = None
_ui: BridgeUI | None = None


def _message(app: Any, text: str) -> None:
    with contextlib.suppress(Exception):
        app.userInterface.messageBox(text)


def run(context: Any, app: Any = None, home: Path | None = None) -> None:
    global _bridge, _ui
    app = app if app is not None else adsk.core.Application.get()
    try:
        _bridge = Bridge(app, home=home)
        _ui = BridgeUI(app.userInterface, _bridge)
        _ui.install()
        _bridge.start()
        _ui.refresh()
    except Exception:
        _message(app, "FusionMCP failed to start:\n" + traceback.format_exc())


def stop(context: Any, app: Any = None) -> None:
    global _bridge, _ui
    app = app if app is not None else adsk.core.Application.get()
    try:
        if _ui is not None:
            _ui.uninstall()
        if _bridge is not None:
            _bridge.stop()
    except Exception:
        _message(app, "FusionMCP failed to stop cleanly:\n" + traceback.format_exc())
    finally:
        _bridge = None
        _ui = None

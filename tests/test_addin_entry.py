"""``fusion_addin/FusionMCP.py``: ``run``/``stop`` lifecycle on fake Fusion objects (task 3.1)."""

import json
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import fusion_mcp
from fusion_addin import FusionMCP as entry
from fusion_addin.core.version import ADDIN_VERSION
from fusion_mcp.protocol.discovery import read_discovery
from fusion_mcp.protocol.wire import Request, encode_request
from tests.fakes.design import FakeDesign
from tests.fakes.fusion_app import FakeFusionApp

ADDIN_DIR = Path(entry.__file__).resolve().parent


def test_manifest() -> None:
    manifest = json.loads((ADDIN_DIR / "FusionMCP.manifest").read_text(encoding="utf-8"))
    assert manifest["type"] == "addin"
    assert manifest["runOnStartup"] is True
    assert manifest["version"] == fusion_mcp.__version__
    assert fusion_mcp.__version__ == ADDIN_VERSION


def test_protocol_root_prefers_vendor(tmp_path: Path) -> None:
    addin = tmp_path / "FusionMCP"
    for base in (addin / "_vendor", tmp_path / "src"):
        (base / "fusion_mcp" / "protocol").mkdir(parents=True)
        (base / "fusion_mcp" / "protocol" / "__init__.py").write_text("")
    assert entry.protocol_root(addin) == addin / "_vendor"


def test_protocol_root_linked_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    addin = tmp_path / "fusion_addin"
    addin.mkdir()
    (tmp_path / "src" / "fusion_mcp" / "protocol").mkdir(parents=True)
    (tmp_path / "src" / "fusion_mcp" / "protocol" / "__init__.py").write_text("")
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert entry.ensure_protocol_path(addin) == tmp_path / "src"
    assert sys.path[0] == str(tmp_path / "src")
    entry.ensure_protocol_path(addin)
    assert sys.path.count(str(tmp_path / "src")) == 1


def test_protocol_root_missing(tmp_path: Path) -> None:
    assert entry.protocol_root(tmp_path / "x") is None


@pytest.fixture
def app() -> FakeFusionApp:
    return FakeFusionApp(FakeDesign(name="Bracket"))


@pytest.fixture
def running(app: FakeFusionApp, tmp_path: Path) -> Iterator[FakeFusionApp]:
    entry.run({}, app=app, home=tmp_path)
    yield app
    entry.stop({}, app=app)


def test_run_starts_bridge_and_writes_discovery(running: FakeFusionApp, tmp_path: Path) -> None:
    assert running.userInterface.messages == []
    assert entry.EVENT_ID in running.events
    assert len(running.events[entry.EVENT_ID].handlers) == 1
    info = read_discovery(tmp_path)
    assert info is not None
    assert entry._bridge is not None
    assert info.port == entry._bridge.port
    assert len(info.token) == 32
    assert info.addin_version == fusion_mcp.__version__
    assert info.started_at


def test_request_runs_through_custom_event(running: FakeFusionApp, tmp_path: Path) -> None:
    info = read_discovery(tmp_path)
    assert info is not None
    stop = threading.Event()

    def main_loop() -> None:
        while not stop.is_set():
            if not running.pump():
                time.sleep(0.005)

    thread = threading.Thread(target=main_loop, daemon=True)
    thread.start()
    try:
        with socket.create_connection(("127.0.0.1", info.port), timeout=5) as sock:
            sock.sendall(encode_request(Request("1", info.token, "status.get", {}, 5)))
            reply = json.loads(sock.makefile("rb").readline())
    finally:
        stop.set()
        thread.join(2)
    assert reply["ok"] is True
    assert reply["result"]["document"] == "Bracket"


def test_stop_removes_discovery_and_handlers(app: FakeFusionApp, tmp_path: Path) -> None:
    entry.run({}, app=app, home=tmp_path)
    entry.stop({}, app=app)
    assert read_discovery(tmp_path) is None
    assert entry.EVENT_ID not in app.events
    assert entry._bridge is None
    ui: Any = app.userInterface
    assert ui.commandDefinitions.items == {}
    assert app.userInterface.messages == []


def test_start_failure_shows_message_box(app: FakeFusionApp, tmp_path: Path) -> None:
    app.fail_register = True
    entry.run({}, app=app, home=tmp_path)
    try:
        assert len(app.userInterface.messages) == 1
        assert "FusionMCP failed to start" in app.userInterface.messages[0]
        assert "registerCustomEvent failed" in app.userInterface.messages[0]
        assert read_discovery(tmp_path) is None
    finally:
        entry.stop({}, app=app)


def test_stop_failure_shows_message_box(app: FakeFusionApp, tmp_path: Path) -> None:
    entry.run({}, app=app, home=tmp_path)
    app.unregisterCustomEvent = None  # type: ignore[assignment,method-assign]
    entry.stop({}, app=app)
    assert any("failed to stop" in m for m in app.userInterface.messages)
    assert entry._bridge is None


def test_bridge_restart_publishes_new_token(app: FakeFusionApp, tmp_path: Path) -> None:
    bridge = entry.Bridge(app, home=tmp_path, port=0)
    bridge.start()
    first = read_discovery(tmp_path)
    bridge.stop()
    bridge.start()
    second = read_discovery(tmp_path)
    bridge.stop()
    assert first is not None
    assert second is not None
    assert first.token != second.token

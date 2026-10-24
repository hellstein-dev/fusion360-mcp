"""``fusion-mcp reload-addin`` (c15 task 1.2): calls ``dev.reload`` and prints the result."""

import json
from pathlib import Path

import pytest

from fusion_mcp import cli
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server import bridge as bridge_module
from fusion_mcp.server.testing import FakeBridge

RESULT = {"reloaded_modules": ["fusion_addin.handlers.sketch"], "handlers": 42}


def test_prints_result_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeBridge()
    fake.respond("dev.reload", RESULT)
    assert cli.reload_addin(fake) == 0
    assert [call.method for call in fake.calls] == ["dev.reload"]
    assert json.loads(capsys.readouterr().out) == RESULT


def test_bridge_error_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeBridge()
    fake.respond(
        "dev.reload",
        BridgeError(ErrorCode.FUSION_API_ERROR, "broken", {"traceback": "Traceback ..."}),
    )
    assert cli.reload_addin(fake) == 1
    err = capsys.readouterr().err
    assert "FUSION_API_ERROR: broken" in err
    assert "Traceback ..." in err


def test_unknown_method_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeBridge()
    fake.respond("dev.reload", BridgeError(ErrorCode.UNKNOWN_METHOD, "unknown method 'dev.reload'"))
    assert cli.reload_addin(fake) == 1
    assert "UNKNOWN_METHOD" in capsys.readouterr().err


class ClosingBridge(FakeBridge):
    closed = False

    async def close(self) -> None:
        self.closed = True


def test_main_dispatches_and_closes_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = ClosingBridge()
    fake.respond("dev.reload", RESULT)
    monkeypatch.setattr(bridge_module, "BridgeClient", lambda: fake)
    assert cli.main(["reload-addin"]) == 0
    assert fake.closed
    assert json.loads(capsys.readouterr().out)["handlers"] == 42


def test_not_connected_exits_nonzero(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    client = bridge_module.BridgeClient(home=tmp_path)  # no discovery file there
    assert cli.reload_addin(client) == 1
    assert "NOT_CONNECTED" in capsys.readouterr().err

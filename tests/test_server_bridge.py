"""``BridgeClient`` against the add-in's real ``BridgeServer`` in a background thread (4.1)."""

import asyncio
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from fusion_addin.core.server import BridgeServer
from fusion_mcp.protocol.discovery import BridgeInfo, remove_discovery, write_discovery
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request, Response
from fusion_mcp.server.bridge import BridgeClient


def execute(request: Request) -> Response:
    if request.method == "echo.run":
        return Response(request.id, True, result={"params": request.params})
    if request.method == "big.run":
        return Response(request.id, True, result={"data": "x" * 200_000})
    if request.method == "screenshot.run":
        # ~3 MB on one JSON line: a 2048 px viewport PNG as base64 (c03 5.7).
        return Response(request.id, True, result={"image_base64": "QUJD" * 750_000})
    if request.method == "slow.run":
        time.sleep(0.5)
        return Response(request.id, True, result={})
    error = BridgeError(ErrorCode.UNKNOWN_METHOD, f"unknown method {request.method!r}")
    return Response(request.id, False, error=error)


class Addin:
    """A BridgeServer with a pump thread playing Fusion's main thread."""

    def __init__(self, home: Path, token: str) -> None:
        self.home = home
        self.server = BridgeServer(token, execute, lambda job_id: None, port=0)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._pump, daemon=True)

    def _pump(self) -> None:
        while not self._stop.is_set():
            if not self.server.process_next():
                time.sleep(0.002)

    def start(self) -> "Addin":
        port = self.server.start()
        write_discovery(BridgeInfo(port, self.server.token, 1, "0.1.0", "now"), self.home)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2)
        self.server.stop()
        remove_discovery(self.home)


@pytest.fixture
def addin(tmp_path: Path) -> Iterator[Addin]:
    a = Addin(tmp_path, "tok-1").start()
    yield a
    a.stop()


def run(coro):
    return asyncio.run(coro)


def test_call_returns_result(addin: Addin, tmp_path: Path) -> None:
    async def go():
        client = BridgeClient(home=tmp_path)
        try:
            first = await client.call("echo.run", {"a": 1})
            second = await client.call("echo.run", {"b": 2})
            return first, second, client.connected
        finally:
            await client.close()

    first, second, connected = run(go())
    assert first == {"params": {"a": 1}}
    assert second == {"params": {"b": 2}}
    assert connected


def test_large_result_line(addin: Addin, tmp_path: Path) -> None:
    async def go():
        client = BridgeClient(home=tmp_path)
        try:
            return await client.call("big.run")
        finally:
            await client.close()

    assert len(run(go())["data"]) == 200_000


def test_three_megabyte_result_line(addin: Addin, tmp_path: Path) -> None:
    async def go():
        client = BridgeClient(home=tmp_path)
        try:
            return await client.call("screenshot.run")
        finally:
            await client.close()

    assert len(run(go())["image_base64"]) == 3_000_000


def test_bridge_error_is_raised(addin: Addin, tmp_path: Path) -> None:
    async def go():
        client = BridgeClient(home=tmp_path)
        try:
            await client.call("nope.run")
        finally:
            await client.close()

    with pytest.raises(BridgeError) as exc:
        run(go())
    assert exc.value.code is ErrorCode.UNKNOWN_METHOD


def test_missing_discovery_is_not_connected(tmp_path: Path) -> None:
    with pytest.raises(BridgeError) as exc:
        run(BridgeClient(home=tmp_path).call("echo.run"))
    assert exc.value.code is ErrorCode.NOT_CONNECTED
    assert "FusionMCP add-in" in exc.value.message
    assert "discovery file" in exc.value.message


def test_refused_port_is_not_connected(tmp_path: Path) -> None:
    server = BridgeServer("t", execute, lambda job_id: None, port=0)
    port = server.start()
    server.stop()  # nothing listens there any more
    write_discovery(BridgeInfo(port, "t", 1, "0.1.0", "now"), tmp_path)
    with pytest.raises(BridgeError) as exc:
        run(BridgeClient(home=tmp_path, connect_timeout_s=2).call("echo.run"))
    assert exc.value.code is ErrorCode.NOT_CONNECTED
    assert exc.value.details["port"] == port


def test_reconnects_after_addin_restart(tmp_path: Path) -> None:
    async def go():
        client = BridgeClient(home=tmp_path)
        first_addin = Addin(tmp_path, "tok-old").start()
        try:
            before = await client.call("echo.run", {"n": 1})
        finally:
            first_addin.stop()
        second_addin = Addin(tmp_path, "tok-new").start()
        try:
            after = await client.call("echo.run", {"n": 2})
        finally:
            second_addin.stop()
            await client.close()
        return before, after

    before, after = run(go())
    assert before == {"params": {"n": 1}}
    assert after == {"params": {"n": 2}}


def test_wrong_token_then_rereads_discovery(addin: Addin, tmp_path: Path) -> None:
    async def go():
        client = BridgeClient(home=tmp_path)
        write_discovery(BridgeInfo(addin.server.port or 0, "stale", 1, "0.1.0", "x"), tmp_path)
        try:
            with pytest.raises(BridgeError) as exc:
                await client.call("echo.run")
            assert exc.value.code is ErrorCode.UNAUTHORIZED
            write_discovery(
                BridgeInfo(addin.server.port or 0, addin.server.token, 1, "0.1.0", "x"), tmp_path
            )
            return await client.call("echo.run", {"ok": True})
        finally:
            await client.close()

    assert run(go()) == {"params": {"ok": True}}


def test_requests_are_serialised(addin: Addin, tmp_path: Path) -> None:
    async def go():
        client = BridgeClient(home=tmp_path)
        try:
            return await asyncio.gather(*(client.call("echo.run", {"i": i}) for i in range(5)))
        finally:
            await client.close()

    assert [r["params"]["i"] for r in run(go())] == [0, 1, 2, 3, 4]


def test_timeout_reported_by_addin(addin: Addin, tmp_path: Path) -> None:
    async def go():
        client = BridgeClient(home=tmp_path)
        try:
            await client.call("slow.run", timeout_s=0.1)
        finally:
            await client.close()

    with pytest.raises(BridgeError) as exc:
        run(go())
    assert exc.value.code is ErrorCode.TIMEOUT

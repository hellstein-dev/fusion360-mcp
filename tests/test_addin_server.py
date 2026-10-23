"""``fusion_addin.core.server.BridgeServer`` over a real socket.

The "main thread" is simulated synchronously: ``fire`` records job ids and the test (or a
pump thread standing in for Fusion's event loop) calls ``process_next``.
"""

import json
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from fusion_addin.core.server import HOST, BridgeServer
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request, Response, encode_request

TOKEN = "t0k"


class MainThread:
    """Executes jobs when pumped, records which thread ran them."""

    def __init__(self) -> None:
        self.fired: list[str] = []
        self.ran: list[str] = []
        self.threads: set[str] = set()
        self.gate = threading.Event()
        self.gate.set()

    def fire(self, job_id: str) -> None:
        self.fired.append(job_id)

    def execute(self, request: Request) -> Response:
        self.gate.wait(5)
        self.ran.append(request.id)
        self.threads.add(threading.current_thread().name)
        if request.method == "echo.run":
            return Response(request.id, True, result=request.params)
        error = BridgeError(ErrorCode.UNKNOWN_METHOD, f"unknown method {request.method!r}")
        return Response(request.id, False, error=error)


@pytest.fixture
def main() -> MainThread:
    return MainThread()


@pytest.fixture
def server(main: MainThread) -> Iterator[BridgeServer]:
    srv = BridgeServer(TOKEN, main.execute, main.fire, port=0)
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def pump(server: BridgeServer) -> Iterator[None]:
    """A thread named like Fusion's main thread draining the job queue."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.is_set():
            if not server.process_next():
                time.sleep(0.005)

    thread = threading.Thread(target=loop, name="FusionMain", daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(2)


class Client:
    def __init__(self, port: int) -> None:
        self.sock = socket.create_connection((HOST, port), timeout=5)
        self.reader = self.sock.makefile("rb")

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def send(
        self,
        method: str,
        params: dict | None = None,
        *,
        id: str = "1",
        token: str = TOKEN,
        timeout_s: float = 5,
    ) -> None:
        self.send_raw(encode_request(Request(id, token, method, params or {}, timeout_s)))

    def recv(self) -> dict[str, Any]:
        line = self.reader.readline()
        assert line, "connection closed"
        return json.loads(line)

    def closed(self) -> bool:
        return self.reader.readline() == b""

    def close(self) -> None:
        self.reader.close()
        self.sock.close()


@pytest.fixture
def client(server: BridgeServer) -> Iterator[Client]:
    assert server.port is not None
    c = Client(server.port)
    yield c
    c.close()


def test_listens_on_localhost(server: BridgeServer) -> None:
    assert server.running
    assert server._listener is not None
    assert server._listener.getsockname()[0] == "127.0.0.1"


def test_request_runs_on_main_thread(
    server: BridgeServer, client: Client, main: MainThread, pump: None
) -> None:
    client.send("echo.run", {"x": 1}, id="a")
    assert client.recv() == {"id": "a", "ok": True, "result": {"x": 1}}
    assert main.threads == {"FusionMain"}
    assert len(main.fired) == 1


def test_job_queued_until_main_thread_pumps(
    server: BridgeServer, client: Client, main: MainThread
) -> None:
    client.send("echo.run", {"x": 2}, id="q")
    deadline = time.monotonic() + 5
    while not main.fired and time.monotonic() < deadline:
        time.sleep(0.005)
    assert main.ran == []
    assert server.pending() == 1
    assert server.process_next(main.fired[0]) is True
    assert client.recv()["result"] == {"x": 2}
    assert server.process_next() is False


def test_wrong_token_closes_connection(client: Client, main: MainThread) -> None:
    client.send("echo.run", token="wrong", id="w")
    reply = client.recv()
    assert reply["ok"] is False
    assert reply["id"] == "w"
    assert reply["error"]["code"] == "UNAUTHORIZED"
    assert client.closed()
    assert main.fired == []


def test_unknown_method(client: Client, pump: None) -> None:
    client.send("nope.run")
    assert client.recv()["error"]["code"] == "UNKNOWN_METHOD"


def test_malformed_line_keeps_connection_open(client: Client, pump: None) -> None:
    client.send_raw(b"{not json\n")
    reply = client.recv()
    assert reply["ok"] is False
    assert reply["error"]["code"] == "INVALID_PARAMS"
    client.send_raw(b'{"id": "m", "method": "echo.run"}\n')
    reply = client.recv()
    assert reply["id"] == "m"
    assert reply["error"]["code"] == "INVALID_PARAMS"
    client.send("echo.run", {"still": "open"})
    assert client.recv()["result"] == {"still": "open"}


def test_timeout_then_later_requests_in_order(
    server: BridgeServer, client: Client, main: MainThread, pump: None
) -> None:
    main.gate.clear()  # the first handler hangs on the main thread
    client.send("echo.run", {"n": 1}, id="slow", timeout_s=0.2)
    reply = client.recv()
    assert reply["id"] == "slow"
    assert reply["error"]["code"] == "TIMEOUT"
    assert "main thread" in reply["error"]["details"]["note"]
    client.send("echo.run", {"n": 2}, id="next")
    main.gate.set()
    assert client.recv() == {"id": "next", "ok": True, "result": {"n": 2}}
    assert main.ran == ["slow", "next"]


def test_fifo_across_connections(server: BridgeServer, main: MainThread) -> None:
    assert server.port is not None
    clients = [Client(server.port) for _ in range(3)]
    try:
        for i, c in enumerate(clients):
            c.send("echo.run", {"i": i}, id=f"c{i}")
            deadline = time.monotonic() + 5
            while len(main.fired) <= i and time.monotonic() < deadline:
                time.sleep(0.005)
        while server.process_next():
            pass
        assert main.ran == ["c0", "c1", "c2"]
        assert [c.recv()["result"]["i"] for c in clients] == [0, 1, 2]
    finally:
        for c in clients:
            c.close()


def test_port_fallback_when_busy(main: MainThread) -> None:
    first = BridgeServer(TOKEN, main.execute, main.fire, port=0)
    port = first.start()
    second = BridgeServer(TOKEN, main.execute, main.fire, port=port)
    try:
        other = second.start()
        assert other != port
        assert other > 0
    finally:
        second.stop()
        first.stop()


def test_stop_fails_pending_and_closes(
    server: BridgeServer, client: Client, main: MainThread
) -> None:
    client.send("echo.run", id="p", timeout_s=5)
    deadline = time.monotonic() + 5
    while not main.fired and time.monotonic() < deadline:
        time.sleep(0.005)
    server.stop()
    assert not server.running
    assert server.port is None
    assert main.ran == []


def test_executor_crash_is_internal_error(server: BridgeServer, main: MainThread) -> None:
    def boom(request: Request) -> Response:
        raise RuntimeError("bug")

    server._executor = boom
    assert server.port is not None
    c = Client(server.port)
    try:
        c.send("echo.run")
        deadline = time.monotonic() + 5
        while not main.fired and time.monotonic() < deadline:
            time.sleep(0.005)
        assert server.process_next()
        assert c.recv()["error"]["code"] == "INTERNAL"
    finally:
        c.close()

"""Bridge server (design D2, D3): TCP on 127.0.0.1, newline-delimited JSON, main-thread jobs.

Background threads only accept connections, parse lines and queue jobs (INV-3). Each job
is announced with ``fire(job_id)`` (in Fusion: ``app.fireCustomEvent``); the CustomEvent
handler calls ``process_next()`` on the main thread, which runs the executor and resolves
the job's Future. Jobs run one at a time in FIFO order.
"""

from __future__ import annotations

import contextlib
import queue
import socket
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request, Response, decode_line, encode_response

from .dispatcher import token_matches, unauthorized

HOST = "127.0.0.1"  # INV-4: never any other interface.
DEFAULT_PORT = 7432
MAX_LINE_BYTES = 16 * 1024 * 1024
TIMEOUT_NOTE = (
    "the handler keeps running on Fusion's main thread (the Fusion API cannot be "
    "interrupted); call fusion_status before retrying"
)

Executor = Callable[[Request], Response]
Fire = Callable[[str], None]


@dataclass
class Job:
    request: Request
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    future: Future[Response] = field(default_factory=Future)


class BridgeServer:
    def __init__(
        self,
        token: str,
        executor: Executor,
        fire: Fire,
        port: int = DEFAULT_PORT,
    ) -> None:
        self.token = token
        self._executor = executor
        self._fire = fire
        self._wanted_port = port
        self._jobs: queue.Queue[Job] = queue.Queue()
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._connections: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._stopping = threading.Event()
        self.port: int | None = None

    # --- lifecycle --------------------------------------------------------------------

    def start(self) -> int:
        """Bind ``127.0.0.1:<port>`` (falls back to an OS-chosen port if busy); return it."""
        listener = self._bind(self._wanted_port)
        listener.listen()
        self._listener = listener
        self.port = listener.getsockname()[1]
        self._stopping.clear()
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="FusionMCP-accept", daemon=True
        )
        self._accept_thread.start()
        return self.port

    @staticmethod
    def _bind(port: int) -> socket.socket:
        for candidate in dict.fromkeys((port, 0)):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                sock.bind((HOST, candidate))
            except OSError:
                sock.close()
                if candidate == 0:
                    raise
                continue
            return sock
        raise OSError("could not bind the bridge socket")  # pragma: no cover

    def stop(self) -> None:
        self._stopping.set()
        if self._listener is not None:
            with contextlib.suppress(OSError):
                self._listener.close()
            self._listener = None
        with self._lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            with contextlib.suppress(OSError):
                conn.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                conn.close()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=2)
            self._accept_thread = None
        stopped = BridgeError(ErrorCode.NOT_CONNECTED, "the FusionMCP add-in was stopped")
        while True:
            try:
                job = self._jobs.get_nowait()
            except queue.Empty:
                break
            if not job.future.done():
                job.future.set_result(Response(job.request.id, False, error=stopped))
        self.port = None

    @property
    def running(self) -> bool:
        return self._listener is not None

    # --- main thread ------------------------------------------------------------------

    def process_next(self, job_id: str | None = None) -> bool:
        """Run the oldest queued job; call from the main thread (CustomEvent handler).

        ``job_id`` is the event payload; jobs still run strictly FIFO. Returns ``False``
        if the queue was empty. Never raises.
        """
        try:
            job = self._jobs.get_nowait()
        except queue.Empty:
            return False
        try:
            response = self._executor(job.request)
        except Exception as exc:
            error = BridgeError(ErrorCode.INTERNAL, f"executor failed: {exc}")
            response = Response(job.request.id, False, error=error)
        if not job.future.done():
            job.future.set_result(response)
        return True

    def pending(self) -> int:
        return self._jobs.qsize()

    # --- background threads -----------------------------------------------------------

    def _accept_loop(self) -> None:
        listener = self._listener
        while listener is not None and not self._stopping.is_set():
            try:
                conn, _addr = listener.accept()
            except OSError:
                break
            with self._lock:
                if self._stopping.is_set():
                    conn.close()
                    break
                self._connections.add(conn)
            threading.Thread(
                target=self._serve_connection, args=(conn,), name="FusionMCP-conn", daemon=True
            ).start()

    def _serve_connection(self, conn: socket.socket) -> None:
        try:
            with conn, conn.makefile("rb") as reader:
                while not self._stopping.is_set():
                    line = reader.readline(MAX_LINE_BYTES + 1)
                    if not line:
                        break
                    if not line.strip():
                        continue
                    response, keep_open = self._handle_line(line)
                    conn.sendall(encode_response(response))
                    if not keep_open:
                        break
        except OSError:
            pass
        finally:
            with self._lock:
                self._connections.discard(conn)

    def _handle_line(self, line: bytes) -> tuple[Response, bool]:
        if len(line) > MAX_LINE_BYTES:
            error = BridgeError(ErrorCode.INVALID_PARAMS, "message exceeds the size limit")
            return Response(None, False, error=error), False
        try:
            message = decode_line(line)
        except BridgeError as exc:
            request_id = exc.details.get("id")
            return Response(
                request_id if isinstance(request_id, str) else None, False, error=exc
            ), True
        if not isinstance(message, Request):
            error = BridgeError(ErrorCode.INVALID_PARAMS, "expected a request, got a response")
            return Response(message.id, False, error=error), True
        if not token_matches(self.token, message.token):
            return unauthorized(message.id), False
        return self._submit(message), True

    def _submit(self, request: Request) -> Response:
        job = Job(request)
        self._jobs.put(job)
        try:
            self._fire(job.id)
        except Exception as exc:
            error = BridgeError(ErrorCode.INTERNAL, f"could not reach Fusion's main thread: {exc}")
            return Response(request.id, False, error=error)
        try:
            return job.future.result(timeout=request.timeout_s)
        except FutureTimeout:
            error = BridgeError(
                ErrorCode.TIMEOUT,
                f"{request.method} did not finish within {request.timeout_s:g} s",
                {"timeout_s": request.timeout_s, "note": TIMEOUT_NOTE},
            )
            return Response(request.id, False, error=error)

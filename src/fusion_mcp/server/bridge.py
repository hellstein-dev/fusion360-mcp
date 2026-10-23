"""``BridgeClient``: asyncio client of the add-in bridge (design D10).

One connection, one request at a time (lock). The discovery file is read on every
(re)connect, so an add-in restarted with a new port/token is picked up without
restarting the MCP server. Failures to reach the add-in raise ``NOT_CONNECTED``.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from pathlib import Path
from typing import Any, Protocol

from fusion_mcp.protocol.discovery import discovery_path, read_discovery
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import (
    DEFAULT_TIMEOUT_S,
    Request,
    Response,
    decode_line,
    encode_request,
)

HOST = "127.0.0.1"
CONNECT_TIMEOUT_S = 5.0
# Extra wait over the request's own timeout: the add-in answers TIMEOUT itself first.
READ_GRACE_S = 10.0
STREAM_LIMIT = 32 * 1024 * 1024

NOT_CONNECTED_HINT = (
    "Fusion 360 is not reachable: start Fusion and make sure the FusionMCP add-in is "
    "running (Utilities > Add-ins > FusionMCP: Start). If the add-in is not installed, "
    "run `fusion-mcp install-addin` and restart Fusion."
)


class Bridge(Protocol):
    """What tools need from a bridge: ``BridgeClient`` in production, ``FakeBridge`` in tests."""

    async def call(
        self, method: str, params: dict[str, Any] | None = None, timeout_s: float | None = None
    ) -> Any: ...


def not_connected(reason: str, **details: Any) -> BridgeError:
    return BridgeError(
        ErrorCode.NOT_CONNECTED, f"{reason}. {NOT_CONNECTED_HINT}", {"reason": reason, **details}
    )


class BridgeClient:
    def __init__(
        self,
        home: Path | None = None,
        default_timeout_s: float = DEFAULT_TIMEOUT_S,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
    ) -> None:
        self._home = home
        self._default_timeout_s = default_timeout_s
        self._connect_timeout_s = connect_timeout_s
        self._lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._token = ""

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def call(
        self, method: str, params: dict[str, Any] | None = None, timeout_s: float | None = None
    ) -> Any:
        """Send one request and return its ``result``; a bridge error raises ``BridgeError``."""
        timeout = timeout_s if timeout_s is not None else self._default_timeout_s
        async with self._lock:
            response = await self._roundtrip(method, params or {}, timeout)
        if response.ok:
            return response.result
        assert response.error is not None
        if response.error.code is ErrorCode.UNAUTHORIZED:
            # The add-in closed the connection; the next call re-reads the discovery file.
            await self.close()
        raise response.error

    async def _roundtrip(self, method: str, params: dict[str, Any], timeout: float) -> Response:
        reused = self.connected
        if not reused:
            await self._connect()
        try:
            return await self._exchange(method, params, timeout)
        except (ConnectionError, OSError, asyncio.IncompleteReadError, EOFError) as exc:
            await self.close()
            if not reused:
                raise not_connected(
                    "the connection to the add-in was lost", error=str(exc)
                ) from exc
        # A reused connection may be stale (add-in restarted): reconnect once.
        await self._connect()
        try:
            return await self._exchange(method, params, timeout)
        except (ConnectionError, OSError, asyncio.IncompleteReadError, EOFError) as exc:
            await self.close()
            raise not_connected("the connection to the add-in was lost", error=str(exc)) from exc

    async def _connect(self) -> None:
        info = read_discovery(self._home)
        if info is None:
            raise not_connected(
                "the add-in discovery file is missing", path=str(discovery_path(self._home))
            )
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(HOST, info.port, limit=STREAM_LIMIT),
                self._connect_timeout_s,
            )
        except (OSError, TimeoutError) as exc:
            raise not_connected(
                f"cannot connect to the add-in on {HOST}:{info.port}", port=info.port
            ) from exc
        self._token = info.token

    async def _exchange(self, method: str, params: dict[str, Any], timeout: float) -> Response:
        assert self._reader is not None
        assert self._writer is not None
        request = Request(uuid.uuid4().hex, self._token, method, params, timeout)
        self._writer.write(encode_request(request))
        await self._writer.drain()
        try:
            line = await asyncio.wait_for(self._reader.readline(), timeout + READ_GRACE_S)
        except TimeoutError as exc:
            await self.close()
            raise BridgeError(
                ErrorCode.TIMEOUT,
                f"{method} got no answer within {timeout + READ_GRACE_S:g} s",
                {"timeout_s": timeout},
            ) from exc
        if not line:
            raise EOFError("the add-in closed the connection")
        message = decode_line(line)
        if not isinstance(message, Response):
            raise BridgeError(ErrorCode.INTERNAL, "the add-in sent a request instead of a response")
        return message

    async def close(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

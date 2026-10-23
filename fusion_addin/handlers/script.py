"""``script.execute``: run agent-supplied Python on Fusion's main thread (design D11).

Mutating, so the dispatcher rolls back timeline items the code added if it raises (D5).
"""

from __future__ import annotations

import contextlib
import io
import json
import traceback
from typing import Any

import adsk
import adsk.core
import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

from ..core.registry import handler

OUTPUT_LIMIT_BYTES = 64 * 1024
TRUNCATION_MARKER = "\n...[output truncated at 64 KiB]"
SCRIPT_FILENAME = "<execute_python>"


class LimitedBuffer(io.TextIOBase):
    """Text sink that keeps the first ``limit`` UTF-8 bytes and drops the rest."""

    def __init__(self, limit: int = OUTPUT_LIMIT_BYTES) -> None:
        super().__init__()
        self._limit = limit
        self._parts: list[str] = []
        self._size = 0
        self.truncated = False

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        if self.truncated:
            return len(text)
        data = text.encode("utf-8", errors="replace")
        room = self._limit - self._size
        if len(data) <= room:
            self._parts.append(text)
            self._size += len(data)
        else:
            self._parts.append(data[:room].decode("utf-8", errors="ignore"))
            self._size = self._limit
            self.truncated = True
        return len(text)

    def getvalue(self) -> str:
        value = "".join(self._parts)
        return value + TRUNCATION_MARKER if self.truncated else value


def _json_or_repr(value: Any) -> Any:
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return repr(value)
    return value


def _user_traceback(exc: BaseException) -> str:
    """Traceback without the handler's own frames: starts at the agent's code."""
    tb = exc.__traceback__
    while tb is not None and tb.tb_frame.f_code.co_filename != SCRIPT_FILENAME:
        tb = tb.tb_next
    return "".join(traceback.format_exception(type(exc), exc, tb or exc.__traceback__))


@handler("script.execute", mutating=True)
def script_execute(ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
    code = ctx.require(params, "code", str)
    script_params = ctx.require(params, "params", dict, default={})
    design = ctx.design_or_none
    namespace: dict[str, Any] = {
        "__name__": "__fusion_mcp_script__",
        "adsk": adsk,
        "app": ctx.app,
        "ui": ctx.ui,
        "design": design,
        "root": design.rootComponent if design is not None else None,
        "params": script_params,
    }
    stdout, stderr = LimitedBuffer(), LimitedBuffer()
    try:
        compiled = compile(code, SCRIPT_FILENAME, "exec")
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(compiled, namespace)
    except (Exception, SystemExit) as exc:
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"{type(exc).__name__}: {exc}",
            {
                "traceback": _user_traceback(exc),
                "stdout": stdout.getvalue(),
                "stderr": stderr.getvalue(),
            },
        ) from exc
    return {
        "result": _json_or_repr(namespace.get("result")),
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
    }

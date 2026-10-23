"""Wire format (design D1): one UTF-8 JSON object per line, terminated by ``\\n``.

Request:  ``{"id", "token", "method", "params", "timeout_s"}``.
Response: ``{"id", "ok": true, "result"}`` or ``{"id", "ok": false, "error": {...}}``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

DEFAULT_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class Request:
    id: str
    token: str
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    timeout_s: float = DEFAULT_TIMEOUT_S


@dataclass(frozen=True)
class Response:
    id: str | None
    ok: bool
    result: Any = None
    error: BridgeError | None = None


def _dumps(obj: dict[str, Any]) -> bytes:
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return (text + "\n").encode("utf-8")


def encode_request(request: Request) -> bytes:
    return _dumps(
        {
            "id": request.id,
            "token": request.token,
            "method": request.method,
            "params": request.params,
            "timeout_s": request.timeout_s,
        }
    )


def encode_response(response: Response) -> bytes:
    payload: dict[str, Any] = {"id": response.id, "ok": response.ok}
    if response.ok:
        payload["result"] = response.result
    else:
        error = response.error or BridgeError(ErrorCode.INTERNAL, "error without details")
        payload["error"] = error.to_dict()
    return _dumps(payload)


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def _parse_object(line: bytes | str) -> dict[str, Any]:
    try:
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        obj = json.loads(text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise _invalid(f"malformed message: {exc}") from exc
    if not isinstance(obj, dict):
        raise _invalid("message must be a JSON object")
    return obj


def _field(obj: dict[str, Any], name: str, types: type | tuple[type, ...]) -> Any:
    if name not in obj:
        raise _invalid(f"missing required field '{name}'", field=name, id=obj.get("id"))
    value = obj[name]
    if not isinstance(value, types) or (isinstance(value, bool) and bool not in _as_tuple(types)):
        raise _invalid(f"field '{name}' has wrong type", field=name, id=obj.get("id"))
    return value


def _as_tuple(types: type | tuple[type, ...]) -> tuple[type, ...]:
    return types if isinstance(types, tuple) else (types,)


def decode_line(line: bytes | str) -> Request | Response:
    """Parse one line into a ``Request`` or ``Response``.

    A line with ``method`` is a request, a line with ``ok`` is a response. Malformed JSON
    or a missing/mistyped required field raises ``BridgeError(INVALID_PARAMS)``; its
    ``details["id"]`` carries the request id when it could be read.
    """
    obj = _parse_object(line)
    if "method" in obj:
        return decode_request_obj(obj)
    if "ok" in obj:
        return decode_response_obj(obj)
    raise _invalid("message is neither a request nor a response", id=obj.get("id"))


def decode_request_obj(obj: dict[str, Any]) -> Request:
    request_id = _field(obj, "id", str)
    token = _field(obj, "token", str)
    method = _field(obj, "method", str)
    params = obj.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise _invalid("field 'params' must be an object", field="params", id=request_id)
    timeout_s = obj.get("timeout_s", DEFAULT_TIMEOUT_S)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise _invalid(
            "field 'timeout_s' must be a positive number", field="timeout_s", id=request_id
        )
    return Request(request_id, token, method, params, float(timeout_s))


def decode_response_obj(obj: dict[str, Any]) -> Response:
    response_id = obj.get("id")
    if response_id is not None and not isinstance(response_id, str):
        raise _invalid("field 'id' has wrong type", field="id")
    ok = _field(obj, "ok", bool)
    if ok:
        return Response(response_id, True, result=obj.get("result"))
    error = _field(obj, "error", dict)
    return Response(response_id, False, error=BridgeError.from_dict(error))

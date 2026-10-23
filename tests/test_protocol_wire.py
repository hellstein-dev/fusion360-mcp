"""``fusion_mcp.protocol.wire``: newline-delimited JSON encoding and validation."""

import json

import pytest

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import (
    DEFAULT_TIMEOUT_S,
    Request,
    Response,
    decode_line,
    encode_request,
    encode_response,
)


def test_request_is_one_line_of_utf8_json() -> None:
    line = encode_request(Request("1", "tok", "status.get", {"name": "Деталь"}, 5))
    assert line.endswith(b"\n")
    assert line.count(b"\n") == 1
    assert json.loads(line.decode("utf-8")) == {
        "id": "1",
        "token": "tok",
        "method": "status.get",
        "params": {"name": "Деталь"},
        "timeout_s": 5,
    }


def test_request_round_trip() -> None:
    request = Request("abc", "tok", "sketch.create", {"plane": "xy"}, 12.5)
    assert decode_line(encode_request(request)) == request


def test_ok_response_round_trip() -> None:
    response = Response("abc", True, result={"count": 2})
    line = encode_response(response)
    assert json.loads(line) == {"id": "abc", "ok": True, "result": {"count": 2}}
    assert decode_line(line) == response


def test_error_response_round_trip() -> None:
    error = BridgeError(ErrorCode.FUSION_API_ERROR, "boom", {"traceback": "tb"})
    line = encode_response(Response("abc", False, error=error))
    assert json.loads(line)["error"] == {
        "code": "FUSION_API_ERROR",
        "message": "boom",
        "details": {"traceback": "tb"},
    }
    decoded = decode_line(line)
    assert isinstance(decoded, Response)
    assert decoded.ok is False
    assert decoded.error == error


def test_decode_accepts_str_and_defaults() -> None:
    decoded = decode_line('{"id": "1", "token": "t", "method": "m"}')
    assert decoded == Request("1", "t", "m", {}, DEFAULT_TIMEOUT_S)


@pytest.mark.parametrize(
    "line",
    [
        b"not json\n",
        b"\xff\xfe\n",
        b"[1, 2]\n",
        b'{"id": "1"}\n',
        b'{"token": "t", "method": "m"}\n',
        b'{"id": "1", "method": "m"}\n',
        b'{"id": 1, "token": "t", "method": "m"}\n',
        b'{"id": "1", "token": "t", "method": 5}\n',
        b'{"id": "1", "token": "t", "method": "m", "params": [1]}\n',
        b'{"id": "1", "token": "t", "method": "m", "timeout_s": -1}\n',
        b'{"id": "1", "token": "t", "method": "m", "timeout_s": true}\n',
        b'{"id": "1", "ok": "yes"}\n',
        b'{"id": "1", "ok": false}\n',
    ],
)
def test_broken_lines_raise_invalid_params(line: bytes) -> None:
    with pytest.raises(BridgeError) as exc:
        decode_line(line)
    assert exc.value.code is ErrorCode.INVALID_PARAMS


def test_invalid_params_keeps_request_id() -> None:
    with pytest.raises(BridgeError) as exc:
        decode_line(b'{"id": "42", "method": "m"}')
    assert exc.value.details["id"] == "42"
    assert exc.value.details["field"] == "token"

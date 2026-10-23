"""``fusion_mcp.protocol.errors``: codes of design D8 and dict round-trip."""

import pytest

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

D8_CODES = {
    "INVALID_PARAMS",
    "UNKNOWN_METHOD",
    "UNAUTHORIZED",
    "NO_ACTIVE_DESIGN",
    "ENTITY_NOT_FOUND",
    "FUSION_API_ERROR",
    "TIMEOUT",
    "NOT_CONNECTED",
    "INTERNAL",
}


def test_error_codes_match_design() -> None:
    assert {code.value for code in ErrorCode} == D8_CODES


@pytest.mark.parametrize("code", list(ErrorCode))
def test_round_trip(code: ErrorCode) -> None:
    error = BridgeError(code, "something failed", {"traceback": "line 1", "n": 3})
    data = error.to_dict()
    assert data == {
        "code": code.value,
        "message": "something failed",
        "details": {"traceback": "line 1", "n": 3},
    }
    assert BridgeError.from_dict(data) == error


def test_details_default_to_empty_dict() -> None:
    error = BridgeError(ErrorCode.TIMEOUT, "slow")
    assert error.to_dict()["details"] == {}
    assert BridgeError.from_dict({"code": "TIMEOUT", "message": "slow"}) == error


def test_unknown_code_becomes_internal() -> None:
    error = BridgeError.from_dict({"code": "WHAT", "message": "x", "details": None})
    assert error.code is ErrorCode.INTERNAL
    assert error.details == {"original_code": "WHAT"}


def test_is_exception_with_readable_text() -> None:
    error = BridgeError(ErrorCode.ENTITY_NOT_FOUND, "no such face")
    assert isinstance(error, Exception)
    assert str(error) == "ENTITY_NOT_FOUND: no such face"
    with pytest.raises(BridgeError) as exc:
        raise error
    assert exc.value.code is ErrorCode.ENTITY_NOT_FOUND

"""Error model of the bridge: codes shared by the add-in, the MCP server and the agent."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Machine-readable error codes (design D8). The value is what goes over the wire."""

    INVALID_PARAMS = "INVALID_PARAMS"
    UNKNOWN_METHOD = "UNKNOWN_METHOD"
    UNAUTHORIZED = "UNAUTHORIZED"
    NO_ACTIVE_DESIGN = "NO_ACTIVE_DESIGN"
    ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
    FUSION_API_ERROR = "FUSION_API_ERROR"
    TIMEOUT = "TIMEOUT"
    NOT_CONNECTED = "NOT_CONNECTED"
    INTERNAL = "INTERNAL"


class BridgeError(Exception):
    """An error that crosses the bridge as ``{"code", "message", "details"}``.

    Handlers raise it to report a failure the agent can act on; it is sent as is.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details) if details else {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "details": self.details}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BridgeError:
        """Rebuild an error from its wire form; an unknown code becomes ``INTERNAL``."""
        raw_code = data.get("code")
        try:
            code = ErrorCode(str(raw_code))
        except ValueError:
            code = ErrorCode.INTERNAL
        details = data.get("details")
        details = dict(details) if isinstance(details, dict) else {}
        if code is ErrorCode.INTERNAL and raw_code != ErrorCode.INTERNAL.value:
            details.setdefault("original_code", raw_code)
        return cls(code, str(data.get("message", "")), details)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BridgeError):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __hash__(self) -> int:
        return hash((self.code, self.message))

    def __repr__(self) -> str:
        return f"BridgeError({self.code.value!r}, {self.message!r}, {self.details!r})"

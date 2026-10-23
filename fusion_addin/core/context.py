"""Per-request ``Context`` handed to handlers (design D4)."""

from __future__ import annotations

from typing import Any

import adsk.core
import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.units import CM_PER_UNIT, DEFAULT_LENGTH_UNIT, Units

from .refs import Refs

_MISSING = object()


class Context:
    """Access to Fusion for one request: ``app``, ``ui``, ``design``, ``root``, ``units``, ``refs``.

    ``design``/``root`` are looked up on access, so handlers that do not need a design
    (``status.get``) work with no document open. ``units`` converts inputs given in the
    request's ``units`` (default mm) to Fusion's cm/radians.
    """

    def __init__(self, app: Any = None, params: dict[str, Any] | None = None) -> None:
        self.app = app if app is not None else adsk.core.Application.get()
        self.params: dict[str, Any] = params if params is not None else {}
        unit = self.params.get("units", DEFAULT_LENGTH_UNIT)
        if not isinstance(unit, str) or unit not in CM_PER_UNIT:
            raise BridgeError(
                ErrorCode.INVALID_PARAMS,
                f"parameter 'units' must be one of {', '.join(CM_PER_UNIT)}",
                {"param": "units", "value": unit},
            )
        self.units = Units(unit)
        self.refs = Refs(self)

    @property
    def ui(self) -> Any:
        return self.app.userInterface

    @property
    def design_or_none(self) -> Any:
        product = self.app.activeProduct
        if product is None:
            return None
        return adsk.fusion.Design.cast(product) or None

    @property
    def design(self) -> Any:
        design = self.design_or_none
        if design is None:
            raise BridgeError(
                ErrorCode.NO_ACTIVE_DESIGN,
                "no active Fusion design: open or create a design document first",
            )
        return design

    @property
    def root(self) -> Any:
        return self.design.rootComponent

    def require(
        self,
        params: dict[str, Any],
        name: str,
        expected: type | tuple[type, ...] = object,
        default: Any = _MISSING,
    ) -> Any:
        """Return ``params[name]`` checked against ``expected``; else ``INVALID_PARAMS``.

        ``bool`` is not accepted where a number is expected. With ``default`` the
        parameter is optional.
        """
        if name not in params or params[name] is None:
            if default is not _MISSING:
                return default
            raise BridgeError(
                ErrorCode.INVALID_PARAMS, f"missing required parameter '{name}'", {"param": name}
            )
        value = params[name]
        types = expected if isinstance(expected, tuple) else (expected,)
        bad_bool = isinstance(value, bool) and bool not in types and object not in types
        if not isinstance(value, types) or bad_bool:
            names = " | ".join(t.__name__ for t in types)
            raise BridgeError(
                ErrorCode.INVALID_PARAMS,
                f"parameter '{name}' must be {names}, got {type(value).__name__}",
                {"param": name, "expected": names},
            )
        return value

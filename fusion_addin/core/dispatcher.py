"""Dispatcher: token check, handler lookup, call, error mapping and rollback (D4, D5, D8).

Runs on Fusion's main thread only (INV-3); the bridge server calls ``dispatch`` from the
CustomEvent handler. No exception leaves ``dispatch`` (INV-5).
"""

from __future__ import annotations

import hmac
import traceback
from collections.abc import Callable
from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request, Response

from .context import Context
from .registry import REGISTRY, HandlerSpec, Registry

ROLLBACK_UNAVAILABLE_DIRECT = "unavailable_direct_design"

ContextFactory = Callable[[dict[str, Any]], Any]


def token_matches(expected: str, given: str) -> bool:
    return hmac.compare_digest(expected.encode("utf-8"), given.encode("utf-8"))


def unauthorized(request_id: str | None) -> Response:
    return Response(
        request_id, False, error=BridgeError(ErrorCode.UNAUTHORIZED, "invalid bridge token")
    )


def to_bridge_error(exc: BaseException) -> BridgeError:
    """``BridgeError`` as is; anything else (Fusion raises ``RuntimeError``) is
    ``FUSION_API_ERROR`` with the original message and the traceback in details."""
    if isinstance(exc, BridgeError):
        return exc
    message = str(exc) or type(exc).__name__
    return BridgeError(
        ErrorCode.FUSION_API_ERROR,
        message,
        {
            "exception_type": type(exc).__name__,
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        },
    )


def rollback_timeline(timeline: Any, m0: int, n0: int) -> None:
    """Delete items added since ``(m0, n0)`` and restore the marker (D5).

    Fusion inserts new items at the marker, so the added ones are
    ``[m0, m0 + max(0, n1 - n0))``; they are deleted from the end of the range so earlier
    deletions never shift later indices. A group is deleted without its contents.
    """
    added = max(0, timeline.count - n0)
    for index in range(m0 + added - 1, m0 - 1, -1):
        item = timeline.item(index)
        if item.isGroup:
            item.deleteMe(False)
        else:
            item.entity.deleteMe()
    # A handler that deleted items (n1 < n0) leaves fewer than m0 items: clamp.
    timeline.markerPosition = min(m0, timeline.count)


class Dispatcher:
    def __init__(
        self,
        token: str,
        registry: Registry = REGISTRY,
        context_factory: ContextFactory | None = None,
    ) -> None:
        self._token = token
        self._registry = registry
        self._context_factory: ContextFactory = context_factory or (
            lambda params: Context(params=params)
        )

    def dispatch(self, request: Request) -> Response:
        if not token_matches(self._token, request.token):
            return unauthorized(request.id)
        spec = self._registry.get(request.method)
        if spec is None:
            error = BridgeError(
                ErrorCode.UNKNOWN_METHOD,
                f"unknown method {request.method!r}",
                {"method": request.method, "known": self._registry.names()},
            )
            return Response(request.id, False, error=error)
        try:
            ctx = self._context_factory(request.params)
            result = self._call(spec, ctx, request.params)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return Response(request.id, False, error=to_bridge_error(exc))
        return Response(request.id, True, result=result)

    def _call(self, spec: HandlerSpec, ctx: Any, params: dict[str, Any]) -> Any:
        if not spec.mutating:
            return spec.fn(ctx, params)
        design = ctx.design_or_none
        if design is None:
            # Nothing to roll back; a handler that needs a design raises NO_ACTIVE_DESIGN.
            return spec.fn(ctx, params)
        parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType
        timeline = design.timeline if parametric else None
        m0 = timeline.markerPosition if timeline is not None else 0
        n0 = timeline.count if timeline is not None else 0
        try:
            return spec.fn(ctx, params)
        except Exception as exc:
            error = to_bridge_error(exc)
            if timeline is None:
                error.details["rollback"] = ROLLBACK_UNAVAILABLE_DIRECT
            else:
                try:
                    rollback_timeline(timeline, m0, n0)
                except Exception as rollback_exc:
                    error.details["rollback_error"] = (
                        str(rollback_exc) or type(rollback_exc).__name__
                    )
            raise error from exc

"""Health of features and timeline items (c03 D14): the one ``healthState`` mapping.

Later changes import this module instead of keeping their own tables.
"""

from __future__ import annotations

from typing import Any

import adsk.fusion

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

from . import refs

_MISSING = object()


def _states() -> dict[Any, str]:
    states = adsk.fusion.FeatureHealthStates
    return {
        states.HealthyFeatureHealthState: "ok",
        states.WarningFeatureHealthState: "warning",
        states.ErrorFeatureHealthState: "error",
    }


def health_of(obj: Any) -> str:
    """``"ok" | "warning" | "error"``; any other state or no ``healthState`` -> ``"unknown"``."""
    try:
        state = getattr(obj, "healthState", _MISSING)
    except Exception:
        return "unknown"
    if state is _MISSING:
        return "unknown"
    return _states().get(state, "unknown")


def _message(obj: Any) -> str:
    try:
        message = getattr(obj, "errorOrWarningMessage", "")
    except Exception:
        return ""
    return message if isinstance(message, str) else ""


def entity_health(entity: Any) -> dict[str, str]:
    return {"state": health_of(entity), "message": _message(entity)}


def _timeline_ref(item: Any) -> str:
    entity = getattr(item, "entity", None)
    if entity is not None:
        try:
            return refs.to_ref(entity)
        except Exception:
            pass
    return f"timeline:{item.name}"


def timeline_errors(design: Any) -> list[dict[str, str]]:
    """Timeline items in the ``error`` state; a direct design has none."""
    if design.designType != adsk.fusion.DesignTypes.ParametricDesignType:
        return []
    timeline = design.timeline
    errors = []
    for index in range(timeline.count):
        item = timeline.item(index)
        if health_of(item) == "error":
            errors.append(
                {"ref": _timeline_ref(item), "name": item.name, "message": _message(item)}
            )
    return errors


def check_health(entity: Any) -> dict[str, str]:
    """Raise ``FUSION_API_ERROR`` for an entity in the ``error`` state (the dispatcher then
    rolls the timeline back); ``warning`` and the rest are returned as is."""
    health = entity_health(entity)
    if health["state"] == "error":
        kind = refs.type_name(entity)
        message = health["message"] or "the feature is in the error state"
        raise BridgeError(
            ErrorCode.FUSION_API_ERROR,
            f"{kind} failed: {message}",
            {"health": health, "feature_type": kind},
        )
    return health

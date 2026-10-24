"""Running exports and snapshotting imports (c12 D7, D11).

``run_export`` re-checks the output path (the add-in's check is authoritative), removes the old
file(s) only with ``overwrite``, executes the export and verifies that a non-empty file
appeared — ``ExportManager.execute`` may report success without writing (no rights, cloud
path). ``snapshot`` / ``created_since`` find what an import added by entity tokens, not by the
importer's return value (empty for some formats).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.paths import check_output_path

# File systems with a coarse mtime (FAT: 2 s) must still count a fresh side file as new.
MTIME_SLACK_S = 2.0


# Fusion's message when the user's license does not allow the format (c15 D3), e.g. "3 : Cannot
# execute the export operation ... as the user is restricted."
LICENSE_RESTRICTED_MARK = "user is restricted"


def _export_failed(path: str, why: str) -> BridgeError:
    return BridgeError(ErrorCode.FUSION_API_ERROR, f"export failed: {why}", {"path": path})


def _license_restricted(path: str, fmt: str, why: str) -> BridgeError:
    return BridgeError(
        ErrorCode.FUSION_API_ERROR,
        f"export of {fmt} is not allowed by this Fusion license",
        {"path": path, "reason": "license_restricted", "format": fmt, "fusion_message": why},
    )


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def run_export(
    ctx: Any,
    options_factory: Callable[[str], Any] | None,
    path: Any,
    overwrite: bool,
    *,
    extensions: Sequence[str],
    fmt: str,
    scope: str,
    extra_files: Callable[[str], Iterable[str]] = lambda _: (),
    execute: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Export to ``path`` and return ``{"files": [{"path", "size_bytes"}], "format", "scope"}``.

    ``options_factory(path)`` builds the export options for ``design.exportManager.execute``;
    ``execute(path)`` replaces both (an export that is not an ``ExportManager`` call).
    ``extra_files(path)`` are side files the export writes (OBJ -> ``.mtl``): removed with
    ``overwrite`` and reported when written by this call.
    """
    target = check_output_path(path, extensions, overwrite)
    extras = list(extra_files(target))
    if overwrite:
        for name in (target, *extras):
            if os.path.isfile(name):
                os.remove(name)
    started = time.time()
    try:
        if execute is not None:
            ok = execute(target)
        else:
            assert options_factory is not None
            ok = ctx.design.exportManager.execute(options_factory(target))
    except BridgeError:
        raise
    except Exception as exc:
        why = str(exc) or type(exc).__name__
        if LICENSE_RESTRICTED_MARK in why:
            raise _license_restricted(target, fmt, why) from exc
        raise _export_failed(target, why) from exc
    if ok is False:
        raise _export_failed(target, "Fusion reported failure")
    if not os.path.isfile(target):
        raise _export_failed(target, "no file was written")
    size = _size(target)
    if size == 0:
        raise _export_failed(target, "the written file is empty")
    files = [{"path": target, "size_bytes": size}]
    for name in extras:
        if os.path.isfile(name) and os.path.getmtime(name) >= started - MTIME_SLACK_S:
            files.append({"path": name, "size_bytes": _size(name)})
    return {"files": files, "format": fmt, "scope": scope}


SNAPSHOT_KINDS = (
    ("occurrences", "occurrences"),
    ("bodies", "bRepBodies"),
    ("mesh_bodies", "meshBodies"),
)


def _entities(component: Any, attr: str) -> list[Any]:
    collection = getattr(component, attr, None)
    if collection is None:
        return []
    return [collection.item(i) for i in range(collection.count)]


def _key(entity: Any) -> Any:
    token = getattr(entity, "entityToken", None)
    return token if isinstance(token, str) and token else id(entity)


def snapshot(component: Any) -> dict[str, set[Any]]:
    """Keys (entity tokens) of the occurrences, B-Rep bodies and mesh bodies of
    ``component``."""
    return {kind: {_key(e) for e in _entities(component, attr)} for kind, attr in SNAPSHOT_KINDS}


def created_since(component: Any, before: dict[str, set[Any]]) -> dict[str, list[Any]]:
    """Entities of ``component`` that are not in the ``before`` snapshot, by kind."""
    return {
        kind: [e for e in _entities(component, attr) if _key(e) not in before[kind]]
        for kind, attr in SNAPSHOT_KINDS
    }

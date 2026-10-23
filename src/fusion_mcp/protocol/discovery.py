"""Discovery file (design D2): ``~/.fusion-mcp/bridge.json`` with the add-in's port and token.

The add-in writes it atomically on start and removes it on stop; the MCP server reads it
on every (re)connect.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DISCOVERY_DIRNAME = ".fusion-mcp"
DISCOVERY_FILENAME = "bridge.json"


@dataclass(frozen=True)
class BridgeInfo:
    port: int
    token: str
    pid: int
    addin_version: str
    started_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def discovery_path(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return base / DISCOVERY_DIRNAME / DISCOVERY_FILENAME


def write_discovery(info: BridgeInfo, home: Path | None = None) -> Path:
    """Write the file atomically: temp file in the same directory, then ``os.replace``."""
    path = discovery_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".bridge-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(info.to_dict(), tmp)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return path


def read_discovery(home: Path | None = None) -> BridgeInfo | None:
    """Return the published bridge info, or ``None`` if the file is missing or invalid."""
    path = discovery_path(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        port, token, pid = data["port"], data["token"], data["pid"]
        addin_version, started_at = data["addin_version"], data["started_at"]
    except KeyError:
        return None
    if not (isinstance(port, int) and isinstance(token, str) and isinstance(pid, int)):
        return None
    return BridgeInfo(port, token, pid, str(addin_version), str(started_at))


def remove_discovery(home: Path | None = None) -> bool:
    """Delete the file; ``True`` if it existed."""
    try:
        discovery_path(home).unlink()
    except FileNotFoundError:
        return False
    return True

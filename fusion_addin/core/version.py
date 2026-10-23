"""Add-in version, read from ``FusionMCP.manifest`` (the one place Fusion also reads it)."""

from __future__ import annotations

import json
from pathlib import Path

ADDIN_DIR = Path(__file__).resolve().parent.parent
MANIFEST = ADDIN_DIR / "FusionMCP.manifest"


def addin_version() -> str:
    try:
        return str(json.loads(MANIFEST.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        return "unknown"


ADDIN_VERSION = addin_version()

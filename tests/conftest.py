"""Make the add-in and the ``adsk`` stubs importable in tests run outside Fusion."""

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = TESTS_DIR.parent  # mcp-plugin/: holds the fusion_addin package

for path in (TESTS_DIR / "stubs", PLUGIN_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

"""``fusion_mcp.protocol`` is shared with the add-in, so it may pull in stdlib modules only."""

import subprocess
import sys
import textwrap

import fusion_mcp.server

# Runs in a fresh interpreter so modules already loaded by pytest do not hide new imports.
_PROBE = textwrap.dedent(
    """
    import sys

    before = set(sys.modules)
    import fusion_mcp.protocol  # noqa: F401

    for name in sorted(set(sys.modules) - before):
        print(name)
    """
)


def _modules_loaded_by_protocol_import() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


def test_protocol_import_loads_only_stdlib() -> None:
    loaded = _modules_loaded_by_protocol_import()
    assert "fusion_mcp.protocol" in loaded
    foreign = [
        name
        for name in loaded
        if name.split(".")[0] not in sys.stdlib_module_names
        and name != "fusion_mcp"
        and not name.startswith("fusion_mcp.protocol")
    ]
    assert foreign == []


def test_server_package_importable() -> None:
    assert fusion_mcp.server.__doc__

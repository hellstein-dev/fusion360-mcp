"""Scaffold checks: package version and the ``fusion-mcp`` CLI entry point."""

import subprocess
import sys

import pytest

import fusion_mcp
from fusion_mcp.cli import main


def test_version_constant() -> None:
    assert fusion_mcp.__version__ == "0.1.0"


def test_cli_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_module_entry_point_prints_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "fusion_mcp", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "0.1.0" in result.stdout

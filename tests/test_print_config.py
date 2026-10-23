"""``fusion-mcp print-config`` (6.2)."""

import json
from pathlib import Path

import pytest

from fusion_mcp.cli import client_config, main

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def test_claude_desktop_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["print-config"]) == 0
    config = json.loads(capsys.readouterr().out)
    assert config == {
        "mcpServers": {
            "fusion360": {
                "command": "uv",
                "args": ["run", "--directory", str(PLUGIN_DIR), "fusion-mcp"],
            }
        }
    }
    assert Path(config["mcpServers"]["fusion360"]["args"][2]).is_absolute()


def test_claude_code_config(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["print-config", "--client", "claude-code"]) == 0
    server = json.loads(capsys.readouterr().out)["mcpServers"]["fusion360"]
    assert server["type"] == "stdio"
    assert server["args"] == ["run", "--directory", str(PLUGIN_DIR), "fusion-mcp"]


def test_custom_plugin_dir(tmp_path: Path) -> None:
    args = client_config(plugin=tmp_path)["mcpServers"]["fusion360"]["args"]
    assert args[2] == str(tmp_path.resolve())

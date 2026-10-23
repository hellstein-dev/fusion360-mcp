"""``fusion_mcp.protocol.discovery``: ``~/.fusion-mcp/bridge.json`` lifecycle on a temp home."""

import json
import os
from pathlib import Path

import pytest

from fusion_mcp.protocol import discovery as d

INFO = d.BridgeInfo(
    port=7432, token="ab" * 16, pid=1234, addin_version="0.1.0", started_at="2026-10-23T10:00:00Z"
)


def test_path_under_home(tmp_path: Path) -> None:
    assert d.discovery_path(tmp_path) == tmp_path / ".fusion-mcp" / "bridge.json"


def test_default_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert d.discovery_path() == tmp_path / ".fusion-mcp" / "bridge.json"


def test_write_read_remove(tmp_path: Path) -> None:
    path = d.write_discovery(INFO, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "port": 7432,
        "token": "ab" * 16,
        "pid": 1234,
        "addin_version": "0.1.0",
        "started_at": "2026-10-23T10:00:00Z",
    }
    assert d.read_discovery(tmp_path) == INFO
    assert d.remove_discovery(tmp_path) is True
    assert not path.exists()
    assert d.remove_discovery(tmp_path) is False
    assert d.read_discovery(tmp_path) is None


def test_write_is_atomic_replace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d.write_discovery(INFO, tmp_path)
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src: str, dst: str) -> None:
        calls.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    newer = d.BridgeInfo(7433, "cd" * 16, 99, "0.1.0", "later")
    d.write_discovery(newer, tmp_path)
    assert len(calls) == 1
    src, dst = calls[0]
    assert Path(src).parent == d.discovery_path(tmp_path).parent
    assert Path(dst) == d.discovery_path(tmp_path)
    assert d.read_discovery(tmp_path) == newer
    # No temp files left behind.
    assert [p.name for p in d.discovery_path(tmp_path).parent.iterdir()] == ["bridge.json"]


def test_failed_write_keeps_old_file_and_cleans_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d.write_discovery(INFO, tmp_path)

    def broken(src: str, dst: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError, match="disk full"):
        d.write_discovery(d.BridgeInfo(1, "x", 2, "v", "t"), tmp_path)
    assert d.read_discovery(tmp_path) == INFO
    assert [p.name for p in d.discovery_path(tmp_path).parent.iterdir()] == ["bridge.json"]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"port": 1}',
        '{"port": "x", "token": "t", "pid": 1, "addin_version": "v", "started_at": "s"}',
    ],
)
def test_invalid_file_reads_as_none(tmp_path: Path, content: str) -> None:
    path = d.discovery_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    assert d.read_discovery(tmp_path) is None

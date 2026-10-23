"""``server.install`` and the ``install-addin`` / ``uninstall-addin`` CLI (6.1) on temp dirs."""

import sys
from pathlib import Path

import pytest

from fusion_mcp.cli import main
from fusion_mcp.server import install as inst

SOURCE = inst.addin_source()


def test_paths() -> None:
    assert (SOURCE / "FusionMCP.py").is_file()
    assert inst.plugin_dir() == SOURCE.parent
    assert (inst.protocol_source() / "wire.py").is_file()


def test_addins_dir_windows() -> None:
    path = inst.addins_dir("win32", {"APPDATA": r"C:\Users\u\AppData\Roaming"})
    assert (
        path
        == Path(r"C:\Users\u\AppData\Roaming")
        / "Autodesk"
        / "Autodesk Fusion 360"
        / ("API")
        / "AddIns"
    )


def test_addins_dir_macos(tmp_path: Path) -> None:
    path = inst.addins_dir("darwin", {}, tmp_path)
    assert path == tmp_path / "Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns"


@pytest.mark.parametrize(("platform", "env"), [("win32", {}), ("linux", {})])
def test_addins_dir_errors(platform: str, env: dict[str, str]) -> None:
    with pytest.raises(inst.InstallError):
        inst.addins_dir(platform, env)


def test_link_install_and_uninstall(tmp_path: Path) -> None:
    addins = tmp_path / "AddIns"
    target = inst.install(addins=addins)
    assert target == addins / "FusionMCP"
    assert inst.is_link(target)
    assert (target / "FusionMCP.py").is_file()
    assert inst.is_ours(target, SOURCE)
    # Reinstall over our own link is fine.
    inst.install(addins=addins)
    assert inst.uninstall(addins=addins) is True
    assert not target.exists()
    assert not inst.is_link(target)
    assert (SOURCE / "FusionMCP.py").is_file()  # the source is untouched
    assert inst.uninstall(addins=addins) is False


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are Windows-only")
def test_windows_link_is_junction(tmp_path: Path) -> None:
    import os

    target = inst.install(addins=tmp_path)
    try:
        assert getattr(os.path, "isjunction")(target)  # noqa: B009 (3.12+, mypy targets 3.11)
    finally:
        inst.uninstall(addins=tmp_path)


def test_copy_install_vendors_protocol(tmp_path: Path) -> None:
    target = inst.install(copy=True, addins=tmp_path)
    assert not inst.is_link(target)
    assert (target / "FusionMCP.py").is_file()
    assert (target / "FusionMCP.manifest").is_file()
    assert (target / "_vendor" / "fusion_mcp" / "__init__.py").is_file()
    assert (target / "_vendor" / "fusion_mcp" / "protocol" / "wire.py").is_file()
    assert not list(target.rglob("__pycache__"))
    assert inst.is_ours(target, SOURCE)
    inst.install(copy=True, addins=tmp_path)  # reinstall over our copy
    assert inst.uninstall(addins=tmp_path) is True
    assert not target.exists()


def test_copied_addin_finds_vendored_protocol(tmp_path: Path) -> None:
    from fusion_addin import FusionMCP as entry

    target = inst.install(copy=True, addins=tmp_path)
    assert entry.protocol_root(target) == target / "_vendor"


def test_foreign_target_is_not_touched(tmp_path: Path) -> None:
    foreign = tmp_path / "FusionMCP"
    foreign.mkdir()
    (foreign / "mine.txt").write_text("keep")
    with pytest.raises(inst.InstallError, match="not created by fusion-mcp"):
        inst.install(addins=tmp_path)
    with pytest.raises(inst.InstallError):
        inst.install(copy=True, addins=tmp_path)
    with pytest.raises(inst.InstallError):
        inst.uninstall(addins=tmp_path)
    assert (foreign / "mine.txt").read_text() == "keep"


def test_foreign_link_is_not_touched(tmp_path: Path) -> None:
    other = tmp_path / "other"
    (other / "x").mkdir(parents=True)
    addins = tmp_path / "AddIns"
    inst.install(addins=addins, source=SOURCE)
    inst.uninstall(addins=addins)
    # A link to somewhere else is foreign as well.
    inst._link(other, addins / "FusionMCP", sys.platform)
    try:
        with pytest.raises(inst.InstallError):
            inst.install(addins=addins)
        assert (addins / "FusionMCP" / "x").is_dir()
    finally:
        inst._remove(addins / "FusionMCP")


def test_missing_source(tmp_path: Path) -> None:
    with pytest.raises(inst.InstallError, match="not found"):
        inst.install(addins=tmp_path, source=tmp_path / "nope")


def test_cli_install_uninstall(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["install-addin", "--addins-dir", str(tmp_path)]) == 0
    assert "linked at" in capsys.readouterr().out
    assert main(["uninstall-addin", "--addins-dir", str(tmp_path)]) == 0
    assert "removed" in capsys.readouterr().out
    assert main(["uninstall-addin", "--addins-dir", str(tmp_path)]) == 0
    assert "not installed" in capsys.readouterr().out
    assert main(["install-addin", "--copy", "--addins-dir", str(tmp_path)]) == 0
    assert "copied to" in capsys.readouterr().out
    assert main(["uninstall-addin", "--addins-dir", str(tmp_path)]) == 0


def test_cli_foreign_target_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "FusionMCP").mkdir()
    assert main(["install-addin", "--addins-dir", str(tmp_path)]) == 1
    assert "not created by fusion-mcp" in capsys.readouterr().err
    assert main(["uninstall-addin", "--addins-dir", str(tmp_path)]) == 1

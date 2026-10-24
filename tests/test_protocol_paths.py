"""``fusion_mcp.protocol.paths`` (c12 task 1.1, D2)."""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.paths import check_input_path, check_output_path

STEP = (".step", ".stp")


def reason(fn: Callable[..., object], *args: object) -> str:
    with pytest.raises(BridgeError) as info:
        fn(*args)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["param"] == "path"
    return str(info.value.details["reason"])


# --- output -------------------------------------------------------------------------------


def test_output_path_is_normalized(tmp_path: Path) -> None:
    raw = str(tmp_path / "sub" / ".." / "part.step")
    assert check_output_path(raw, STEP) == os.path.normpath(str(tmp_path / "part.step"))


def test_output_relative_path() -> None:
    assert reason(check_output_path, "out/part.step", STEP) == "not_absolute"


@pytest.mark.parametrize("value", ["", "   ", None, 5])
def test_output_not_a_path(value: object) -> None:
    assert reason(check_output_path, value, STEP) == "not_absolute"


def test_output_bad_extension(tmp_path: Path) -> None:
    with pytest.raises(BridgeError) as info:
        check_output_path(str(tmp_path / "part.stl"), STEP)
    assert info.value.details["reason"] == "bad_extension"
    assert info.value.details["expected"] == [".step", ".stp"]


def test_output_extension_any_case(tmp_path: Path) -> None:
    assert check_output_path(str(tmp_path / "PART.STP"), STEP).endswith("PART.STP")


def test_output_parent_missing(tmp_path: Path) -> None:
    assert reason(check_output_path, str(tmp_path / "nope" / "a.step"), STEP) == "parent_missing"


def test_output_is_directory(tmp_path: Path) -> None:
    folder = tmp_path / "dir.step"
    folder.mkdir()
    assert reason(check_output_path, str(folder), STEP) == "is_directory"


def test_output_exists_without_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "a.step"
    target.write_bytes(b"old")
    assert reason(check_output_path, str(target), STEP) == "exists"
    assert target.read_bytes() == b"old"


def test_output_exists_with_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "a.step"
    target.write_bytes(b"old")
    assert check_output_path(str(target), STEP, overwrite=True) == str(target)
    assert target.read_bytes() == b"old"


# --- input --------------------------------------------------------------------------------


def test_input_ok(tmp_path: Path) -> None:
    source = tmp_path / "bolt.STEP"
    source.write_bytes(b"x")
    assert check_input_path(str(source), STEP) == str(source)


def test_input_relative() -> None:
    assert reason(check_input_path, "in/bolt.step", STEP) == "not_absolute"


def test_input_not_found(tmp_path: Path) -> None:
    assert reason(check_input_path, str(tmp_path / "none.step"), STEP) == "not_found"


def test_input_is_directory(tmp_path: Path) -> None:
    folder = tmp_path / "d.step"
    folder.mkdir()
    assert reason(check_input_path, str(folder), STEP) == "is_directory"


def test_input_bad_extension(tmp_path: Path) -> None:
    source = tmp_path / "bolt.txt"
    source.write_bytes(b"x")
    assert reason(check_input_path, str(source), STEP) == "bad_extension"

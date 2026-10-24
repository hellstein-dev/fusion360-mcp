"""``fusion_mcp.protocol.exchange`` (c12 tasks 1.2, 1.3, D3)."""

import pytest

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.exchange import (
    CAD_EXPORT_FORMATS,
    EXTENSIONS,
    FILE_UNITS,
    IMPORT_FORMATS,
    MESH_EXPORT_FORMATS,
    REFINEMENTS,
    detect_format,
    extensions_of,
    format_color,
    parse_color,
)

# --- 1.2 formats --------------------------------------------------------------------------


def test_vocabularies() -> None:
    assert CAD_EXPORT_FORMATS == ("step", "iges", "sat", "smt", "f3d", "usdz")
    assert MESH_EXPORT_FORMATS == ("stl", "3mf", "obj")
    assert IMPORT_FORMATS == ("step", "iges", "sat", "smt", "f3d", "stl", "obj", "3mf")
    assert REFINEMENTS == ("low", "medium", "high", "custom")
    assert FILE_UNITS == ("mm", "cm", "m", "in", "ft")
    for name in (*CAD_EXPORT_FORMATS, *MESH_EXPORT_FORMATS, *IMPORT_FORMATS, "dxf"):
        assert all(ext.startswith(".") for ext in EXTENSIONS[name])


def test_extensions_of() -> None:
    assert extensions_of(("step", "stl")) == (".step", ".stp", ".stl")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("C:/out/a.stp", "step"),
        ("C:/out/a.STEP", "step"),
        ("C:/out/a.igs", "iges"),
        ("C:/out/a.iges", "iges"),
        ("C:/out/a.3mf", "3mf"),
        ("C:/out/a.f3d", "f3d"),
    ],
)
def test_detect_format(path: str, expected: str) -> None:
    assert detect_format(path, IMPORT_FORMATS) == expected


@pytest.mark.parametrize("path", ["C:/out/a.xyz", "C:/out/noext", "C:/out/a.usdz"])
def test_detect_format_unknown(path: str) -> None:
    with pytest.raises(BridgeError) as info:
        detect_format(path, IMPORT_FORMATS)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["param"] == "format"
    assert info.value.details["expected"] == list(IMPORT_FORMATS)


# --- 1.3 colours --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "rgba"),
    [
        ("#FF0000", (255, 0, 0, 255)),
        ("#ff000080", (255, 0, 0, 128)),
        ("#FF000080", (255, 0, 0, 128)),
        ([0, 128, 255], (0, 128, 255, 255)),
        ([0, 0, 0, 10], (0, 0, 0, 10)),
    ],
)
def test_parse_color(value: object, rgba: tuple[int, int, int, int]) -> None:
    assert parse_color(value) == rgba


@pytest.mark.parametrize(
    "value",
    ["red", "#FFF", "FF0000", "#GG0000", "#-10000", "#+F0000", "# F0000", "#FF 000",
     [0, 256, 0], [-1, 0, 0], [0, 0], [0, 0, 0, 0, 0], [0.5, 0, 0], [True, 0, 0], None, 255],
)  # fmt: skip
def test_parse_color_rejects(value: object) -> None:
    with pytest.raises(BridgeError) as info:
        parse_color(value)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["param"] == "color"


def test_format_color() -> None:
    assert format_color((255, 0, 16, 255)) == "#FF0010FF"
    assert format_color(parse_color("#12abCD")) == "#12ABCDFF"

"""The add-in package loads without Fusion, pulling ``adsk.core``/``adsk.fusion`` from the stubs.

Tests never import ``adsk`` themselves: only ``fusion_addin/`` may (INV-1), so the stubs are
reached through the add-in package.
"""

import sys

import pytest

import fusion_addin

STUBS_DIR = "stubs"


def test_fusion_addin_imports_adsk_from_stubs() -> None:
    for name in ("adsk", "adsk.core", "adsk.fusion"):
        module_file = sys.modules[name].__file__
        assert module_file is not None
        assert STUBS_DIR in module_file


def test_adsk_stub_entry_points_return_objects() -> None:
    adsk = fusion_addin.adsk
    assert adsk.core.Application.get() is not None
    assert adsk.fusion.Design.cast(None) is not None


def test_stubbed_api_access_raises_clear_error() -> None:
    adsk = fusion_addin.adsk
    app = adsk.core.Application.get()
    with pytest.raises(adsk.FusionUnavailableError, match="running Fusion 360"):
        _ = app.activeProduct

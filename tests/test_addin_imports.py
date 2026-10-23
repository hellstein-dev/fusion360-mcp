"""The add-in package and the ``adsk`` stubs load without Fusion.

``adsk`` is loaded via importlib: only ``fusion_addin/`` may import it statically (INV-1).
"""

import importlib

import pytest


def test_adsk_stubs_importable() -> None:
    core = importlib.import_module("adsk.core")
    fusion = importlib.import_module("adsk.fusion")
    assert core.Application.get() is not None
    assert fusion.Design.cast(None) is not None


def test_stubbed_api_access_raises_clear_error() -> None:
    adsk = importlib.import_module("adsk")
    app = importlib.import_module("adsk.core").Application.get()
    with pytest.raises(adsk.FusionUnavailableError, match="running Fusion 360"):
        _ = app.activeProduct


def test_fusion_addin_importable() -> None:
    addin = importlib.import_module("fusion_addin")
    assert addin.__doc__

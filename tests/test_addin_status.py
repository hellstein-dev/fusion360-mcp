"""``status.get`` handler (task 3.3) and handler group registration."""

import sys

import fusion_addin.handlers
from fusion_addin.core.context import Context
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers.status import status_get
from fusion_mcp import __version__
from tests.fakes.design import FakeApp, FakeDesign

STATUS_KEYS = {
    "fusion_version",
    "addin_version",
    "python_version",
    "document",
    "design_type",
    "units",
}


def test_handlers_package_registers_status() -> None:
    assert "status" in fusion_addin.handlers.__all__
    spec = REGISTRY.get("status.get")
    assert spec is not None
    assert spec.fn is status_get
    assert spec.mutating is False


def test_status_with_parametric_design() -> None:
    result = status_get(Context(FakeApp(FakeDesign(name="Housing"), version="2.0.21000")), {})
    assert set(result) == STATUS_KEYS
    assert result == {
        "fusion_version": "2.0.21000",
        "addin_version": __version__,
        "python_version": sys.version,
        "document": "Housing",
        "design_type": "parametric",
        "units": "mm",
    }


def test_status_with_direct_design() -> None:
    result = status_get(Context(FakeApp(FakeDesign(parametric=False))), {})
    assert result["design_type"] == "direct"


def test_status_without_document() -> None:
    result = status_get(Context(FakeApp(None)), {})
    assert result["document"] is None
    assert result["design_type"] is None
    assert result["units"] is None
    assert result["addin_version"] == __version__

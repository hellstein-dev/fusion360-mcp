"""``fusion_addin.core.context``: ``Context`` on fake Fusion objects and the adsk stubs."""

import math

import pytest

from fusion_addin.core.context import Context
from fusion_addin.core.refs import Refs
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.design import FakeApp, FakeDesign, FakeEntity


def test_app_ui_design_root() -> None:
    design = FakeDesign()
    app = FakeApp(design)
    ctx = Context(app)
    assert ctx.app is app
    assert ctx.ui is app.userInterface
    assert ctx.design is design
    assert ctx.root is design.rootComponent
    assert isinstance(ctx.refs, Refs)


def test_default_app_comes_from_adsk() -> None:
    import fusion_addin

    ctx = Context()
    with pytest.raises(fusion_addin.adsk.FusionUnavailableError):
        _ = ctx.app.activeProduct


@pytest.mark.parametrize("product", [None, FakeEntity("adsk::cam::CAM")])
def test_no_active_design(product: object) -> None:
    ctx = Context(FakeApp(product))
    assert ctx.design_or_none is None
    with pytest.raises(BridgeError) as exc:
        _ = ctx.design
    assert exc.value.code is ErrorCode.NO_ACTIVE_DESIGN
    with pytest.raises(BridgeError):
        _ = ctx.root


def test_units_default_mm_and_override() -> None:
    assert Context(FakeApp()).units.length(25) == pytest.approx(2.5)
    ctx = Context(FakeApp(), {"units": "in"})
    assert ctx.units.length(1) == pytest.approx(2.54)
    assert ctx.units.angle(180) == pytest.approx(math.pi)


@pytest.mark.parametrize("bad", ["yd", 5])
def test_bad_units_param(bad: object) -> None:
    with pytest.raises(BridgeError) as exc:
        Context(FakeApp(), {"units": bad})
    assert exc.value.code is ErrorCode.INVALID_PARAMS


def test_require() -> None:
    ctx = Context(FakeApp())
    params = {"name": "Body1", "depth": 5, "flag": True, "none": None}
    assert ctx.require(params, "name", str) == "Body1"
    assert ctx.require(params, "depth", (int, float)) == 5
    assert ctx.require(params, "flag", bool) is True
    assert ctx.require(params, "missing", str, default="x") == "x"
    assert ctx.require(params, "none", str, default=None) is None
    assert ctx.require(params, "name") == "Body1"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("missing", str), ("none", str), ("name", int), ("flag", (int, float))],
)
def test_require_invalid(name: str, expected: type | tuple[type, ...]) -> None:
    ctx = Context(FakeApp())
    params = {"name": "Body1", "flag": True, "none": None}
    with pytest.raises(BridgeError) as exc:
        ctx.require(params, name, expected)
    assert exc.value.code is ErrorCode.INVALID_PARAMS
    assert exc.value.details["param"] == name

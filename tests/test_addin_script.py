"""``script.execute`` handler (5.1) on fakes, directly and through the dispatcher."""

from typing import Any

import pytest

import fusion_addin
import fusion_addin.handlers.script as script
from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher
from fusion_addin.core.registry import REGISTRY
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request
from tests.fakes.design import FakeApp, FakeDesign
from tests.fakes.timeline import FakeTimeline


def run(code: str, design: Any = None, **params: Any) -> dict[str, Any]:
    ctx = Context(FakeApp(design))
    return script.script_execute(ctx, {"code": code, **params})


def test_registered_as_mutating() -> None:
    spec = REGISTRY.get("script.execute")
    assert spec is not None
    assert spec.mutating is True
    assert spec.fn is script.script_execute


def test_returns_result_and_empty_output() -> None:
    design = FakeDesign()
    design.rootComponent.bRepBodies = [1, 2]
    assert run("result = len(root.bRepBodies)", design) == {
        "result": 2,
        "stdout": "",
        "stderr": "",
    }


def test_namespace() -> None:
    design = FakeDesign()
    out = run(
        "result = [app.activeProduct is design, ui is app.userInterface,"
        " root is design.rootComponent, params, hasattr(adsk, 'core'), hasattr(adsk, 'fusion')]",
        design,
        params={"w": 5},
    )
    assert out["result"] == [True, True, True, {"w": 5}, True, True]
    ns_adsk = run("result = adsk.__name__", design)["result"]
    assert ns_adsk == fusion_addin.adsk.__name__


def test_no_design_means_none_in_namespace() -> None:
    assert run("result = [design, root, params]") == {
        "result": [None, None, {}],
        "stdout": "",
        "stderr": "",
    }


def test_captures_stdout_and_stderr() -> None:
    out = run("import sys\nprint('hello')\nprint('oops', file=sys.stderr)")
    assert out["stdout"] == "hello\n"
    assert out["stderr"] == "oops\n"
    assert out["result"] is None


def test_non_serialisable_result_is_repr() -> None:
    out = run("class X:\n    def __repr__(self):\n        return '<X>'\nresult = X()")
    assert out["result"] == "<X>"
    assert run("result = float('nan')")["result"] == "nan"


def test_large_output_truncated() -> None:
    out = run("print('x' * 100_000)")
    assert out["stdout"].endswith(script.TRUNCATION_MARKER)
    body = out["stdout"][: -len(script.TRUNCATION_MARKER)]
    assert len(body.encode("utf-8")) == script.OUTPUT_LIMIT_BYTES


def test_truncation_respects_multibyte_chars() -> None:
    buf = script.LimitedBuffer(limit=5)
    buf.write("ééé")  # 6 bytes
    assert buf.truncated
    assert buf.getvalue() == "éé" + script.TRUNCATION_MARKER


@pytest.mark.parametrize(
    ("code", "kind"),
    [
        ("1/0", "ZeroDivisionError"),
        ("def f(:\n", "SyntaxError"),
        ("raise SystemExit(3)", "SystemExit"),
    ],
)
def test_exception_is_fusion_api_error_with_traceback(code: str, kind: str) -> None:
    with pytest.raises(BridgeError) as exc:
        run("print('before')\n" + code if kind != "SyntaxError" else code)
    error = exc.value
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.message.startswith(kind)
    assert kind in error.details["traceback"]
    assert "<execute_python>" in error.details["traceback"]
    if kind != "SyntaxError":
        assert error.details["stdout"] == "before\n"


@pytest.mark.parametrize("params", [{}, {"code": 5}, {"code": "x", "params": [1]}])
def test_invalid_params(params: dict[str, Any]) -> None:
    with pytest.raises(BridgeError) as exc:
        script.script_execute(Context(FakeApp()), params)
    assert exc.value.code is ErrorCode.INVALID_PARAMS


def test_exception_rolls_back_timeline_through_dispatcher() -> None:
    timeline = FakeTimeline(["Sketch1"])
    app = FakeApp(FakeDesign(timeline=timeline))
    dispatcher = Dispatcher("t", REGISTRY, lambda params: Context(app, params))
    code = (
        "design.timeline.add('Extrude1')\ndesign.timeline.add('Fillet1')\nraise RuntimeError('bad')"
    )
    response = dispatcher.dispatch(Request("1", "t", "script.execute", {"code": code}))
    assert response.ok is False
    assert response.error is not None
    assert response.error.code is ErrorCode.FUSION_API_ERROR
    assert "RuntimeError: bad" in response.error.message
    assert "Traceback" in response.error.details["traceback"]
    assert timeline.names() == ["Sketch1"]
    assert timeline.markerPosition == 1

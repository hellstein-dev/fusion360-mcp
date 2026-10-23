"""``fusion_addin.core.dispatcher``: token, lookup, error mapping (2.3) and rollback (2.4)."""

from typing import Any

import pytest

from fusion_addin.core.context import Context
from fusion_addin.core.dispatcher import Dispatcher, rollback_timeline
from fusion_addin.core.registry import Registry
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.wire import Request, Response
from tests.fakes.design import FakeApp, FakeDesign
from tests.fakes.timeline import FakeTimeline

TOKEN = "s3cret"


def make(design: Any = None) -> tuple[Registry, Dispatcher]:
    reg = Registry()
    app = FakeApp(design)
    return reg, Dispatcher(TOKEN, reg, lambda params: Context(app, params))


def call(dispatcher: Dispatcher, method: str, params: dict | None = None, token: str = TOKEN):
    return dispatcher.dispatch(Request("r1", token, method, params or {}))


def error_of(response: Response) -> BridgeError:
    assert response.ok is False
    assert response.error is not None
    return response.error


# --- 2.3 -----------------------------------------------------------------------------


def test_success_passes_params_and_returns_result() -> None:
    reg, disp = make()
    seen: list[dict] = []

    @reg.handler("echo.run")
    def echo(ctx, params):
        seen.append(params)
        return {"got": params["x"]}

    response = call(disp, "echo.run", {"x": 1})
    assert response == Response("r1", True, result={"got": 1})
    assert seen == [{"x": 1}]


def test_wrong_token() -> None:
    reg, disp = make()
    reg.handler("echo.run")(lambda ctx, params: {})
    assert error_of(call(disp, "echo.run", token="nope")).code is ErrorCode.UNAUTHORIZED


def test_unknown_method() -> None:
    reg, disp = make()
    reg.handler("echo.run")(lambda ctx, params: {})
    error = error_of(call(disp, "nope.run"))
    assert error.code is ErrorCode.UNKNOWN_METHOD
    assert error.details["known"] == ["echo.run"]


def test_bridge_error_passes_as_is() -> None:
    reg, disp = make()

    @reg.handler("echo.run")
    def fail(ctx, params):
        raise BridgeError(ErrorCode.ENTITY_NOT_FOUND, "gone", {"ref": "x"})

    assert error_of(call(disp, "echo.run")) == BridgeError(
        ErrorCode.ENTITY_NOT_FOUND, "gone", {"ref": "x"}
    )


def test_other_exception_becomes_fusion_api_error() -> None:
    reg, disp = make()

    @reg.handler("echo.run")
    def fail(ctx, params):
        raise RuntimeError("3 : invalid argument profile")

    error = error_of(call(disp, "echo.run"))
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.message == "3 : invalid argument profile"
    assert error.details["exception_type"] == "RuntimeError"
    assert "Traceback" in error.details["traceback"]
    assert "invalid argument profile" in error.details["traceback"]


def test_context_errors_are_mapped() -> None:
    reg, disp = make()
    reg.handler("echo.run")(lambda ctx, params: {})
    assert error_of(call(disp, "echo.run", {"units": "yd"})).code is ErrorCode.INVALID_PARAMS


def test_needs_design_without_design() -> None:
    reg, disp = make(None)
    reg.handler("echo.run", mutating=True)(lambda ctx, params: ctx.root)
    assert error_of(call(disp, "echo.run")).code is ErrorCode.NO_ACTIVE_DESIGN


# --- 2.4 rollback --------------------------------------------------------------------


def adds_then(fail: bool, n: int = 1, group: bool = False):
    def fn(ctx, params):
        for i in range(n):
            ctx.design.timeline.add(f"new{i}")
        if group:
            ctx.design.timeline.add("grp", is_group=True)
        if fail:
            raise RuntimeError("feature failed")
        return {"done": True}

    return fn


def test_mutating_success_keeps_items() -> None:
    timeline = FakeTimeline(["a", "b"])
    reg, disp = make(FakeDesign(timeline=timeline))
    reg.handler("feat.add", mutating=True)(adds_then(fail=False))
    assert call(disp, "feat.add").ok
    assert timeline.names() == ["a", "b", "new0"]
    assert timeline.markerPosition == 3


def test_rollback_marker_at_end() -> None:
    timeline = FakeTimeline(["a", "b"])
    reg, disp = make(FakeDesign(timeline=timeline))
    reg.handler("feat.add", mutating=True)(adds_then(fail=True, n=2, group=True))
    error = error_of(call(disp, "feat.add"))
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert "rollback_error" not in error.details
    assert timeline.names() == ["a", "b"]
    assert timeline.markerPosition == 2
    assert timeline.group_deletions == [("grp", False)]


def test_rollback_marker_in_middle() -> None:
    timeline = FakeTimeline(["a", "b", "c"], marker=1)
    reg, disp = make(FakeDesign(timeline=timeline))
    reg.handler("feat.add", mutating=True)(adds_then(fail=True))
    assert timeline.names() == ["a", "b", "c"]  # untouched before the call
    error_of(call(disp, "feat.add"))
    assert timeline.names() == ["a", "b", "c"]
    assert timeline.markerPosition == 1


def test_rollback_when_handler_deleted_items() -> None:
    timeline = FakeTimeline(["a", "b", "c"])
    reg, disp = make(FakeDesign(timeline=timeline))

    @reg.handler("feat.delete", mutating=True)
    def delete(ctx, params):
        ctx.design.timeline.item(2).entity.deleteMe()
        raise RuntimeError("then failed")

    error = error_of(call(disp, "feat.delete"))
    assert "rollback_error" not in error.details
    # n1 < n0: nothing was added, nothing else is deleted; marker restored.
    assert timeline.names() == ["a", "b"]
    assert timeline.markerPosition == 2


def test_rollback_error_is_reported() -> None:
    timeline = FakeTimeline(["a"])
    reg, disp = make(FakeDesign(timeline=timeline))

    @reg.handler("feat.add", mutating=True)
    def fn(ctx, params):
        ctx.design.timeline.add("new")
        timeline.fail_delete = "cannot delete"
        raise RuntimeError("feature failed")

    error = error_of(call(disp, "feat.add"))
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.message == "feature failed"
    assert error.details["rollback_error"] == "cannot delete"


def test_direct_design_reports_unavailable_rollback() -> None:
    reg, disp = make(FakeDesign(parametric=False))
    reg.handler("feat.add", mutating=True)(
        lambda ctx, params: (_ for _ in ()).throw(BridgeError(ErrorCode.INVALID_PARAMS, "bad"))
    )
    error = error_of(call(disp, "feat.add"))
    assert error.code is ErrorCode.INVALID_PARAMS
    assert error.details["rollback"] == "unavailable_direct_design"


def test_non_mutating_does_not_touch_timeline() -> None:
    timeline = FakeTimeline(["a"])
    reg, disp = make(FakeDesign(timeline=timeline))
    reg.handler("feat.peek")(adds_then(fail=True))
    error = error_of(call(disp, "feat.peek"))
    assert "rollback" not in error.details
    assert timeline.names() == ["a", "new0"]


@pytest.mark.parametrize(
    ("names", "marker", "added_at", "expected_marker"),
    [(["a", "b"], 2, 2, 2), (["a", "b", "c"], 1, 1, 1), ([], 0, 0, 0)],
)
def test_rollback_timeline_range(
    names: list[str], marker: int, added_at: int, expected_marker: int
) -> None:
    timeline = FakeTimeline(names, marker=marker)
    m0, n0 = timeline.markerPosition, timeline.count
    timeline.add("x")
    timeline.add("y")
    assert timeline.names()[added_at : added_at + 2] == ["x", "y"]
    rollback_timeline(timeline, m0, n0)
    assert timeline.names() == names
    assert timeline.markerPosition == expected_marker

"""``core/compensate.py``: undo journal for property changes (c09 D4, task 1.1)."""

from typing import Any

import pytest

from fusion_addin.core.compensate import Compensation
from fusion_mcp.protocol.errors import BridgeError, ErrorCode


class Box:
    def __init__(self) -> None:
        self.a = 1
        self.b = "x"


def test_success_runs_no_undo() -> None:
    box = Box()
    calls: list[str] = []
    with Compensation() as comp:
        comp.add(lambda: calls.append("undo"), "a")
        box.a = 2
    assert calls == []
    assert box.a == 2
    assert len(comp) == 0


def test_two_steps_rolled_back_in_reverse_order() -> None:
    box = Box()
    order: list[str] = []

    def undo(attr: str, value: Any) -> Any:
        def run() -> None:
            order.append(attr)
            setattr(box, attr, value)

        return run

    with pytest.raises(BridgeError) as info, Compensation() as comp:
        comp.add(undo("a", box.a), "a")
        box.a = 2
        comp.add(undo("b", box.b), "b")
        box.b = "y"
        raise BridgeError(ErrorCode.INVALID_PARAMS, "second step failed")
    assert (box.a, box.b) == (1, "x")
    assert order == ["b", "a"]
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert "compensation_errors" not in info.value.details


def test_other_exception_becomes_fusion_api_error() -> None:
    box = Box()
    with pytest.raises(BridgeError) as info, Compensation() as comp:
        comp.add(lambda: setattr(box, "a", 1), "a")
        box.a = 5
        raise RuntimeError("Fusion said no")
    assert box.a == 1
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert info.value.message == "Fusion said no"
    assert isinstance(info.value.__cause__, RuntimeError)


def test_failing_undo_is_reported_and_the_rest_still_run() -> None:
    box = Box()

    def broken() -> None:
        raise RuntimeError("cannot restore")

    with pytest.raises(BridgeError) as info, Compensation() as comp:
        comp.add(lambda: setattr(box, "a", 1), "first")
        comp.add(broken, "second")
        box.a = 3
        raise BridgeError(ErrorCode.FUSION_API_ERROR, "boom")
    assert box.a == 1
    assert info.value.details["compensation_errors"] == ["second: cannot restore"]


def test_run_returns_errors_and_empties_the_journal() -> None:
    comp = Compensation()
    comp.add(lambda: None, "ok")
    comp.add(lambda: 1 / 0, "div")
    assert comp.run() == ["div: division by zero"]
    assert len(comp) == 0

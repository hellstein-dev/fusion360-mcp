"""``fusion_addin/sketch/journal.py``: ``SketchJournal`` atomicity (c05 D6, task 3.2)."""

from typing import Any

import pytest

from fusion_addin.sketch.journal import SketchJournal
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import P
from tests.fakes.fusion_sketch import make_sketch


def line(sketch: Any, x0: float, y0: float, x1: float, y1: float) -> Any:
    return sketch.sketchCurves.sketchLines.addByTwoPoints(P(x0, y0, 0), P(x1, y1, 0))


def deleted(sketch: Any) -> list[Any]:
    return [args[0] for name, args in sketch.calls if name == "deleteMe"]


def test_success_keeps_everything_and_restores_flag() -> None:
    sketch = make_sketch()
    with SketchJournal(sketch) as j:
        j.defer()
        assert sketch.isComputeDeferred is True
        a = j.created(line(sketch, 0, 0, 1, 0))
        b = j.created(line(sketch, 1, 0, 1, 1))
    assert a.isValid and b.isValid
    assert sketch.isComputeDeferred is False
    assert sketch.defer_log == [True, False]
    assert j.entries == []
    assert deleted(sketch) == []


def test_exception_deletes_created_in_reverse_order() -> None:
    sketch = make_sketch()
    with pytest.raises(BridgeError) as info, SketchJournal(sketch) as j:
        a = j.created(line(sketch, 0, 0, 1, 0))
        b = j.created(line(sketch, 1, 0, 1, 1))
        c = j.created(sketch.geometricConstraints.addVertical(b))
        sketch.sketchCurves.sketchLines.addByTwoPoints(P(5, 5, 0), P(5, 5, 0))
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert "two distinct points" in info.value.message
    assert "rollback_error" not in info.value.details
    assert deleted(sketch) == [c, b, a]
    assert not (a.isValid or b.isValid or c.isValid)
    assert sketch.sketchCurves.sketchLines.count == 0


def test_bridge_error_propagates_as_is_and_undo_closures_run_newest_first() -> None:
    sketch = make_sketch()
    target = line(sketch, 0, 0, 1, 0)
    order: list[str] = []
    error = BridgeError(ErrorCode.INVALID_PARAMS, "bad", {"index": 3})
    with pytest.raises(BridgeError) as info, SketchJournal(sketch) as j:
        target.isConstruction = True
        j.on_undo(lambda: order.append("construction"), "isConstruction")
        created = j.created(line(sketch, 2, 0, 3, 0))
        j.on_undo(lambda: order.append(f"after create, valid={created.isValid}"))
        raise error
    assert info.value is error
    assert info.value.details == {"index": 3}
    assert order == ["after create, valid=True", "construction"]
    assert not created.isValid
    assert target.isValid


def test_already_invalid_entity_is_skipped() -> None:
    sketch = make_sketch()
    with pytest.raises(BridgeError) as info, SketchJournal(sketch) as j:
        gone = j.created(line(sketch, 0, 0, 1, 0))
        gone.deleteMe()
        sketch.reset_calls()
        raise RuntimeError("solver failed")
    assert "rollback_error" not in info.value.details
    assert deleted(sketch) == []


def test_delete_and_undo_errors_go_to_rollback_error() -> None:
    sketch = make_sketch()

    def broken_undo() -> None:
        raise RuntimeError("cannot restore")

    with pytest.raises(BridgeError) as info, SketchJournal(sketch) as j:
        stuck = j.created(line(sketch, 0, 0, 1, 0))
        stuck.fail_delete = "locked"
        j.on_undo(broken_undo, "isFixed")
        other = j.created(line(sketch, 2, 0, 3, 0))
        raise RuntimeError("over-constrained")
    error = info.value
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.message == "over-constrained"
    rollback = error.details["rollback_error"]
    assert "undo isFixed: cannot restore" in rollback
    assert "delete SketchLine" in rollback and "locked" in rollback
    assert not other.isValid
    assert stuck.isValid


def test_flag_restored_before_rollback() -> None:
    sketch = make_sketch()
    seen: list[bool] = []
    with pytest.raises(BridgeError), SketchJournal(sketch) as j:
        j.defer()
        j.on_undo(lambda: seen.append(sketch.isComputeDeferred))
        raise RuntimeError("boom")
    assert seen == [False]
    assert sketch.isComputeDeferred is False


def test_previous_true_value_is_kept() -> None:
    sketch = make_sketch()
    sketch.isComputeDeferred = True
    with pytest.raises(BridgeError), SketchJournal(sketch) as j:
        j.defer()
        j.defer()
        raise RuntimeError("boom")
    assert sketch.isComputeDeferred is True


def test_undefer_inside_block_then_exit_is_noop() -> None:
    sketch = make_sketch()
    with SketchJournal(sketch) as j:
        j.defer()
        j.undefer()
        assert sketch.isComputeDeferred is False
        j.undefer()
    assert sketch.defer_log == [True, False]


def test_without_defer_flag_is_untouched() -> None:
    sketch = make_sketch()
    with pytest.raises(BridgeError), SketchJournal(sketch):
        raise RuntimeError("boom")
    assert sketch.defer_log == []


def test_created_none_is_fusion_api_error() -> None:
    sketch = make_sketch()
    with pytest.raises(BridgeError) as info, SketchJournal(sketch) as j:
        j.created(line(sketch, 0, 0, 1, 0))
        j.created(None)
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert sketch.sketchCurves.sketchLines.count == 0


def test_created_all_journals_a_collection() -> None:
    sketch = make_sketch()
    with pytest.raises(BridgeError), SketchJournal(sketch) as j:
        lines = j.created_all(
            sketch.sketchCurves.sketchLines.addTwoPointRectangle(P(0, 0, 0), P(2, 1, 0))
        )
        assert len(lines) == 4
        raise RuntimeError("boom")
    assert sketch.sketchCurves.sketchLines.count == 0


def test_keyboard_interrupt_restores_flag_without_rollback() -> None:
    sketch = make_sketch()
    with pytest.raises(KeyboardInterrupt), SketchJournal(sketch) as j:
        j.defer()
        kept = j.created(line(sketch, 0, 0, 1, 0))
        raise KeyboardInterrupt
    assert sketch.isComputeDeferred is False
    assert kept.isValid

"""``core/param_tx.py``: ``ParameterTransaction`` journal and rollback (c04 D7, task 1.4)."""

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.param_tx import ParameterTransaction
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.design import FakeDesign
from tests.fakes.parameters import FakeParameterSet, attach_parameters

adsk = fusion_addin.adsk


def setup() -> tuple[Any, FakeParameterSet]:
    design = FakeDesign()
    params = attach_parameters(design)
    return design, params


def snapshot(params: FakeParameterSet) -> list[tuple[str, str, str, str, bool]]:
    return [(p.name, p.expression, p.unit, p.comment, p.isFavorite) for p in params.params]


def test_create_uses_string_value_input() -> None:
    design, _params = setup()
    tx = ParameterTransaction(design)
    param = tx.create("width", "40 mm", "mm", "outer")
    assert design.userParameters.added == [("width", "40 mm", "mm", "outer")]
    assert param.value == pytest.approx(4.0)
    assert [e.action for e in tx.journal] == ["create"]


def test_rollback_create_in_reverse_order() -> None:
    design, params = setup()
    tx = ParameterTransaction(design)
    tx.create("width", "40 mm", "mm")
    tx.create("half", "width / 2", "mm")
    assert tx.rollback() == []
    assert params.params == []
    assert tx.journal == []


def test_rollback_update_restores_every_field() -> None:
    design, params = setup()
    vi = adsk.core.ValueInput
    width = design.userParameters.add("width", vi.createByString("40 mm"), "mm", "old")
    design.userParameters.add("half", vi.createByString("width / 2"), "mm", "")
    before = snapshot(params)
    tx = ParameterTransaction(design)
    tx.update(width, expression="50 mm", name="w", comment="new", is_favorite=True)
    assert (width.name, width.expression, width.comment, width.isFavorite) == (
        "w",
        "50 mm",
        "new",
        True,
    )
    assert params["half"].expression == "w / 2"
    assert tx.rollback() == []
    assert snapshot(params) == before


def test_update_only_given_fields() -> None:
    design, _params = setup()
    width = design.userParameters.add(
        "width", adsk.core.ValueInput.createByString("40 mm"), "mm", "c"
    )
    ParameterTransaction(design).update(width, comment="x")
    assert (width.name, width.expression, width.comment) == ("width", "40 mm", "x")


def test_rollback_delete_restores_parameter() -> None:
    design, params = setup()
    vi = adsk.core.ValueInput
    design.userParameters.add("width", vi.createByString("40 mm"), "mm", "w")
    half = design.userParameters.add("half", vi.createByString("width / 2"), "mm", "h")
    half.isFavorite = True
    before = snapshot(params)
    tx = ParameterTransaction(design)
    tx.delete(params.get("half"))
    tx.delete(params.get("width"))
    assert params.params == []
    assert tx.rollback() == []
    assert snapshot(params) == before


def test_delete_refused_by_fusion() -> None:
    design, params = setup()
    vi = adsk.core.ValueInput
    design.userParameters.add("width", vi.createByString("40 mm"), "mm", "")
    design.userParameters.add("half", vi.createByString("width / 2"), "mm", "")
    tx = ParameterTransaction(design)
    with pytest.raises(BridgeError) as info:
        tx.delete(params.get("width"))
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert tx.journal == []


def test_context_manager_rolls_back_on_fusion_error() -> None:
    design, params = setup()
    params.fail_on.add("second")
    with pytest.raises(BridgeError) as info, ParameterTransaction(design) as tx:
        tx.create("first", "1 mm", "mm")
        tx.create("second", "2 mm", "mm")
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert "second" in info.value.message
    assert "rollback_error" not in info.value.details
    assert params.params == []


def test_context_manager_keeps_bridge_error() -> None:
    design, params = setup()
    error = BridgeError(ErrorCode.INVALID_PARAMS, "stop")
    with pytest.raises(BridgeError) as info, ParameterTransaction(design) as tx:
        tx.create("first", "1 mm", "mm")
        raise error
    assert info.value is error
    assert params.params == []


def test_rollback_errors_are_collected() -> None:
    design, params = setup()
    tx = ParameterTransaction(design)
    first = tx.create("first", "1 mm", "mm")
    tx.create("second", "first * 2", "mm")
    # "second" depends on "first": undoing "second" works, then make "first" undeletable.
    tx.create("third", "first * 3", "mm")
    params.params.remove(params["third"])  # vanished behind the transaction's back
    params.add_user("keeper", "first + 1 mm", "mm", "")
    error = tx.fail(BridgeError(ErrorCode.FUSION_API_ERROR, "boom"))
    rollback = error.details["rollback_error"]
    assert "create first" in rollback
    assert params.get("second") is None
    assert first in params.params


def test_success_leaves_changes() -> None:
    design, params = setup()
    with ParameterTransaction(design) as tx:
        tx.create("a", "1 mm", "mm")
    assert [p.name for p in params.params] == ["a"]

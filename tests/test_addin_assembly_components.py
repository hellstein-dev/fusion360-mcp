"""``assembly.*`` component handlers on fakes (c10 tasks 2.1, 2.3, 2.5, 2.7, 2.9)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.handlers import assembly
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.assembly import add_component, fake_assembly_design, make_movable
from tests.fakes.brep import fake_box_body
from tests.fakes.design import FakeApp, FakeCollection, FakeEntity


def make_ctx(design: Any) -> Context:
    return Context(FakeApp(design))


def error_of(fn: Any, ctx: Any, params: dict[str, Any]) -> BridgeError:
    with pytest.raises(BridgeError) as exc:
        fn(ctx, params)
    return exc.value


@pytest.fixture
def asm() -> Any:
    design = fake_assembly_design(fake_box_body(1, 1, 1, token="rt"))
    _, design.box = add_component(design, "Box", fake_box_body(10, 6, 4, token="bx"))
    return design


# --- 2.1 assembly.list ----------------------------------------------------------------------


def test_list_components_and_occurrences(asm: Any) -> None:
    asm.rootComponent.joints = FakeCollection(
        [FakeEntity("adsk::fusion::Joint", "J", occurrenceOne=asm.box, occurrenceTwo=None)]
    )
    asm.box.component.partNumber = "P-1"
    out = assembly.assembly_list(make_ctx(asm), {})
    assert out["active_component"] == "component:root"
    by_name = {c["name"]: c for c in out["components"]}
    assert by_name["root"]["is_root"] is True
    assert by_name["root"]["body_count"] == 1
    assert by_name["Box"] == {
        "ref": "component:Box",
        "name": "Box",
        "is_root": False,
        "occurrence_count": 1,
        "body_count": 1,
        "part_number": "P-1",
    }
    (occ,) = out["occurrences"]
    assert occ["ref"] == "occurrence:Box:1"
    assert occ["joint_count"] == 1
    assert out["joint_count"] == 1
    assert out["truncated"] is False


def test_list_limit_truncates(asm: Any) -> None:
    add_component(asm, "Lid")
    out = assembly.assembly_list(make_ctx(asm), {"limit": 1})
    assert len(out["occurrences"]) == 1 and len(out["components"]) == 1
    assert out["truncated"] is True
    err = error_of(assembly.assembly_list, make_ctx(asm), {"limit": 0})
    assert err.code is ErrorCode.INVALID_PARAMS


# --- 2.3 assembly.new_component -----------------------------------------------------------------


def test_new_component_under_root(asm: Any) -> None:
    out = assembly.assembly_new_component(make_ctx(asm), {"name": "Lid"})
    assert out["component"] == "component:Lid"
    occ = out["occurrence"]
    assert occ["ref"] == "occurrence:Lid:1"
    assert occ["component"] == "component:Lid"
    assert occ["transform"]["translation_mm"] == [0.0, 0.0, 0.0]
    assert occ["transform"]["rotation"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    assert make_ctx(asm).refs.resolve("occurrence:Lid:1").component.name == "Lid"


def test_new_component_translate_and_parent(asm: Any) -> None:
    out = assembly.assembly_new_component(
        make_ctx(asm), {"name": "Hinge", "parent": "occurrence:Box:1", "translate_mm": [5, 0, 40]}
    )
    occ = out["occurrence"]
    assert occ["ref"] == "occurrence:Box:1+Hinge:1"
    assert occ["transform"]["translation_mm"] == [5.0, 0.0, 40.0]
    assert asm.box.component.occurrences.calls[0][0] == "addNewComponent"


def test_new_component_validation(asm: Any) -> None:
    ctx = make_ctx(asm)
    for params in ({"name": " "}, {"name": "Box"}, {"name": "X", "translate_mm": [1, 2]}):
        assert error_of(assembly.assembly_new_component, ctx, params).code is (
            ErrorCode.INVALID_PARAMS
        )
    err = error_of(assembly.assembly_new_component, ctx, {"name": "X", "parent": "component:Box"})
    assert err.code is ErrorCode.INVALID_PARAMS
    assert asm.rootComponent.occurrences.count == 1


def test_new_component_failure_deletes_it(asm: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(comp: Any, component: Any, name: str) -> None:
        raise RuntimeError("name refused")

    monkeypatch.setattr(assembly, "_rename", broken)
    err = error_of(assembly.assembly_new_component, make_ctx(asm), {"name": "Lid"})
    assert err.code is ErrorCode.FUSION_API_ERROR
    created = asm.rootComponent.occurrences.item(1)
    assert created.deleted is True


# --- 2.5 assembly.component_from_bodies ----------------------------------------------------------


@pytest.fixture
def two_bodies() -> Any:
    a, b = fake_box_body(1, 1, 1, token="a"), fake_box_body(2, 2, 2, token="b")
    design = fake_assembly_design(a, b)
    design.a, design.b = a, b
    return design


@pytest.mark.parametrize("returns", ["body", "occurrence", None])
def test_component_from_bodies(two_bodies: Any, returns: str | None) -> None:
    d = two_bodies
    for body in (d.a, d.b):
        make_movable(d, body, create_returns=returns)
    out = assembly.assembly_component_from_bodies(make_ctx(d), {"bodies": ["a", "b"], "name": "P"})
    assert out["component"] == "component:P"
    assert out["occurrence"]["ref"] == "occurrence:P:1"
    assert out["bodies"] == ["a@P:1", "b@P:1"]
    component = make_ctx(d).refs.resolve("component:P")
    assert list(component.bRepBodies) == [d.a, d.b]
    assert d.rootComponent.bRepBodies.count == 0


def test_component_from_bodies_of_different_owners(asm: Any) -> None:
    root_body, box_body = asm.by_token["rt"], asm.by_token["bx"]
    for body in (root_body, box_body):
        make_movable(asm, body)
    err = error_of(
        assembly.assembly_component_from_bodies,
        make_ctx(asm),
        {"bodies": ["rt", "bx"], "name": "P"},
    )
    assert err.code is ErrorCode.INVALID_PARAMS
    assert sorted(err.details["owners"]) == ["component:Box", "component:root"]
    assert root_body.parentComponent is asm.rootComponent
    assert getattr(root_body, "move_calls", 0) == 0
    assert asm.rootComponent.occurrences.count == 1


def test_component_from_bodies_validation(two_bodies: Any) -> None:
    ctx = make_ctx(two_bodies)
    fn = assembly.assembly_component_from_bodies
    assert error_of(fn, ctx, {"bodies": [], "name": "P"}).code is ErrorCode.INVALID_PARAMS
    assert error_of(fn, ctx, {"bodies": ["a", "a"], "name": "P"}).code is ErrorCode.INVALID_PARAMS
    assert error_of(fn, ctx, {"bodies": ["a-face+z"], "name": "P"}).code is (
        ErrorCode.INVALID_PARAMS
    )


def test_component_from_bodies_move_failure_leaves_cleanup_to_timeline(two_bodies: Any) -> None:
    d = two_bodies
    make_movable(d, d.a, create_returns=None)
    make_movable(d, d.b, create_returns=None)
    d.b.moveToComponent = lambda occurrence: None
    err = error_of(
        assembly.assembly_component_from_bodies, make_ctx(d), {"bodies": ["a", "b"], "name": "P"}
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    # Deleting the occurrence here would take body "a" with it; the D5 timeline rollback
    # undoes the move and then the new component.
    created = d.rootComponent.occurrences.item(0)
    assert created.deleted is False


# --- 2.7 assembly.activate -----------------------------------------------------------------------


def test_activate_occurrence_and_root(asm: Any) -> None:
    ctx = make_ctx(asm)
    out = assembly.assembly_activate(ctx, {"occurrence": "occurrence:Box:1"})
    assert out == {"active_component": "component:Box", "active_occurrence": "occurrence:Box:1"}
    assert asm.activeComponent is asm.box.component
    out = assembly.assembly_activate(ctx, {})
    assert out == {"active_component": "component:root", "active_occurrence": None}


def test_activate_failure_restores_previous(asm: Any) -> None:
    _, lid = add_component(asm, "Lid")
    ctx = make_ctx(asm)
    assembly.assembly_activate(ctx, {"occurrence": "occurrence:Box:1"})
    lid.activate = lambda: False  # type: ignore[method-assign]
    err = error_of(assembly.assembly_activate, ctx, {"occurrence": "occurrence:Lid:1"})
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert asm.activeOccurrence is asm.box
    assert asm.box.activations == 2


# --- 2.9 assembly.rename_component ---------------------------------------------------------------


def test_rename_component_updates_paths(asm: Any) -> None:
    ctx = make_ctx(asm)
    out = assembly.assembly_rename_component(ctx, {"component": "component:Box", "name": "Case"})
    assert out == {"component": "component:Case", "occurrences": ["occurrence:Case:1"]}
    with pytest.raises(BridgeError) as exc:
        ctx.refs.resolve("occurrence:Box:1")
    assert exc.value.code is ErrorCode.ENTITY_NOT_FOUND


def test_rename_by_occurrence_ref(asm: Any) -> None:
    out = assembly.assembly_rename_component(
        make_ctx(asm), {"component": "occurrence:Box:1", "name": "Case"}
    )
    assert out["component"] == "component:Case"


def test_rename_root_and_duplicates_are_invalid(asm: Any) -> None:
    add_component(asm, "Lid")
    ctx = make_ctx(asm)
    fn = assembly.assembly_rename_component
    assert error_of(fn, ctx, {"component": "component:root", "name": "X"}).code is (
        ErrorCode.INVALID_PARAMS
    )
    assert error_of(fn, ctx, {"component": "component:Box", "name": "Lid"}).code is (
        ErrorCode.INVALID_PARAMS
    )
    assert error_of(fn, ctx, {"component": "rt", "name": "X"}).code is ErrorCode.INVALID_PARAMS
    assert asm.box.component.name == "Box"


def test_rename_failure_restores_name(asm: Any) -> None:
    class Stubborn:
        objectType = "adsk::fusion::Component"
        entityToken = "stubborn"

        def __init__(self) -> None:
            self._name = "Old"
            self.sets = 0

        @property
        def name(self) -> str:
            return self._name

        @name.setter
        def name(self, value: str) -> None:
            self.sets += 1
            self._name = value
            if self.sets == 1:
                raise RuntimeError("refused")

    component = Stubborn()
    asm.allComponents.add(component)
    asm.add_entity(component)
    err = error_of(
        assembly.assembly_rename_component, make_ctx(asm), {"component": "stubborn", "name": "New"}
    )
    assert err.code is ErrorCode.FUSION_API_ERROR
    assert component.name == "Old"


def test_handlers_are_registered() -> None:
    from fusion_addin.core.registry import REGISTRY

    for method in (
        "assembly.list",
        "assembly.new_component",
        "assembly.component_from_bodies",
        "assembly.activate",
        "assembly.rename_component",
    ):
        assert method in REGISTRY
    assert fusion_addin.handlers.assembly is assembly

"""``import.file`` on fakes (c12 tasks 4.1, 4.2)."""

from pathlib import Path
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import importing as ih
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import FakeBody, FakeComponent, fake_part_design
from tests.fakes.design import FakeApp
from tests.fakes.fusion_exchange import FakeMeshBodies, install_exchange

adsk = fusion_addin.adsk


class Model:
    def __init__(self, tmp: Path, parametric: bool = True) -> None:
        self.tmp = tmp
        self.design: Any = fake_part_design(
            FakeBody("Body1", token="body-1"), parametric=parametric
        )
        self.root = self.design.rootComponent
        self.part: Any = FakeComponent("Part", token="comp-part")
        self.part.meshBodies = FakeMeshBodies()
        self.design.allComponents.add(self.part)
        self.design.add_entity(self.part)
        self.app = FakeApp(self.design)
        _, self.im = install_exchange(self.app, self.design)

    def file(self, name: str) -> str:
        path = self.tmp / name
        path.write_bytes(b"data")
        return str(path)

    def run(self, **params: Any) -> Any:
        return ih.import_file(Context(self.app, params), params)

    def fails(self, code: ErrorCode, **params: Any) -> BridgeError:
        with pytest.raises(BridgeError) as info:
            self.run(**params)
        assert info.value.code is code, info.value
        return info.value


@pytest.fixture
def m(tmp_path: Path) -> Model:
    return Model(tmp_path)


def test_import_is_mutating() -> None:
    spec = REGISTRY.get("import.file")
    assert spec is not None and spec.mutating is True


# --- 4.1 CAD ------------------------------------------------------------------------------


def test_import_step_into_root(m: Model) -> None:
    result = m.run(path=m.file("bolt.step"))
    assert [o.kind for o in m.im.created] == ["step"]
    method, _, target = m.im.calls[-1]
    assert method == "importToTarget2" and target is m.root
    occurrence = m.root.occurrences.item(0)
    assert result == {
        "format": "step",
        "target": m.root.entityToken,
        "created": {"occurrences": [occurrence.entityToken], "bodies": [], "mesh_bodies": []},
    }


def test_import_iges_by_extension(m: Model) -> None:
    assert m.run(path=m.file("plate.igs"))["format"] == "iges"
    assert m.im.created[-1].kind == "iges"


@pytest.mark.parametrize(("name", "kind"), [("a.sat", "sat"), ("a.smt", "smt"), ("a.f3d", "f3d")])
def test_import_other_cad(m: Model, name: str, kind: str) -> None:
    assert m.run(path=m.file(name))["format"] == kind


def test_import_into_component_reports_bodies(m: Model) -> None:
    def add_body(options: Any, component: Any) -> list[Any]:
        return [component.add_body(FakeBody("Imported", token="body-new"))]

    m.im.produce = add_body
    result = m.run(path=m.file("x.stp"), target="comp-part")
    assert m.im.calls[-1][2] is m.part
    assert result["created"]["bodies"] == ["body-new"] and result["target"] == "comp-part"


def test_import_falls_back_to_import_to_target(m: Model) -> None:
    m.im.missing.add("importToTarget2")
    result = m.run(path=m.file("bolt.step"))
    assert m.im.calls[-1][0] == "importToTarget" and len(result["created"]["occurrences"]) == 1


def test_import_failure_from_fusion(m: Model) -> None:
    m.im.result = False
    m.im.missing.add("importToTarget2")
    m.fails(ErrorCode.FUSION_API_ERROR, path=m.file("bolt.step"))


def test_import_produced_nothing(m: Model) -> None:
    m.im.produce = lambda options, component: []
    error = m.fails(ErrorCode.FUSION_API_ERROR, path=m.file("bolt.step"))
    assert "no geometry" in error.message


def test_import_format_must_match_extension(m: Model) -> None:
    error = m.fails(ErrorCode.INVALID_PARAMS, path=m.file("bolt.step"), format="iges")
    assert error.details["reason"] == "bad_extension"


def test_import_path_errors(m: Model, tmp_path: Path) -> None:
    error = m.fails(ErrorCode.INVALID_PARAMS, path=str(tmp_path / "none.step"))
    assert error.details["reason"] == "not_found"
    assert m.fails(ErrorCode.INVALID_PARAMS, path="rel/a.step").details["reason"] == "not_absolute"
    assert (
        m.fails(ErrorCode.INVALID_PARAMS, path=m.file("a.txt")).details["reason"] == "bad_extension"
    )
    assert m.im.calls == []


def test_import_target_must_be_component(m: Model) -> None:
    m.fails(ErrorCode.INVALID_PARAMS, path=m.file("bolt.step"), target="body-1")


# --- 4.2 meshes ---------------------------------------------------------------------------


def test_import_stl_in_inches(m: Model) -> None:
    path = m.file("part.stl")
    result = m.run(path=path, mesh_units="in")
    assert m.root.meshBodies.calls == [(path, adsk.fusion.MeshUnits.InchMeshUnit)]
    mesh = m.root.meshBodies.item(0)
    assert result["format"] == "stl" and result["created"]["mesh_bodies"] == [mesh.entityToken]


@pytest.mark.parametrize(("name", "fmt"), [("a.obj", "obj"), ("a.3mf", "3mf")])
def test_import_other_meshes_default_mm(m: Model, name: str, fmt: str) -> None:
    result = m.run(path=m.file(name), target="comp-part")
    assert result["format"] == fmt
    assert m.part.meshBodies.calls[-1][1] == adsk.fusion.MeshUnits.MillimeterMeshUnit


def test_import_mesh_add_returns_none(m: Model) -> None:
    m.root.meshBodies.return_none = True
    m.fails(ErrorCode.FUSION_API_ERROR, path=m.file("part.stl"))


def test_import_bad_mesh_units(m: Model) -> None:
    m.fails(ErrorCode.INVALID_PARAMS, path=m.file("part.stl"), mesh_units="yd")


def test_direct_design_failure_deletes_created(tmp_path: Path) -> None:
    m = Model(tmp_path, parametric=False)
    created: list[Any] = []

    def add_and_fail(options: Any, component: Any) -> list[Any]:
        created.extend(m.im.add_occurrence(options, component))
        return created

    m.im.produce = add_and_fail
    m.im.result = False  # the importer adds an occurrence, then reports failure
    m.im.missing.add("importToTarget2")
    m.fails(ErrorCode.FUSION_API_ERROR, path=m.file("bolt.step"))
    assert created and created[0].deleted


def test_direct_design_mesh_failure_after_import_deletes_mesh(tmp_path: Path) -> None:
    m = Model(tmp_path, parametric=False)
    meshes = m.root.meshBodies
    original_add = meshes.add

    def add_then_none(*args: Any) -> Any:
        original_add(*args)
        return None

    meshes.add = add_then_none
    m.fails(ErrorCode.FUSION_API_ERROR, path=m.file("part.stl"))
    assert meshes.count == 0


def test_parametric_failure_leaves_cleanup_to_rollback(m: Model) -> None:
    created: list[Any] = []

    def add(options: Any, component: Any) -> list[Any]:
        created.extend(m.im.add_occurrence(options, component))
        return created

    m.im.produce = add
    m.im.result = False
    m.im.missing.add("importToTarget2")
    m.fails(ErrorCode.FUSION_API_ERROR, path=m.file("bolt.step"))
    assert not created[0].deleted  # the dispatcher's timeline rollback removes it

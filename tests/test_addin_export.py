"""Exchange fakes, ``core.exchange.run_export`` and the ``export.*`` handlers (c12 tasks
3.1-3.6)."""

import math
import os
from pathlib import Path
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.exchange import run_export
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import export as eh
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import (
    FakeBody,
    FakeComponent,
    FakeOccurrence,
    add_occurrence,
    fake_part_design,
)
from tests.fakes.design import FakeApp, FakeEntity
from tests.fakes.fusion_exchange import FakeExportManager, install_exchange

adsk = fusion_addin.adsk
MR = adsk.fusion.MeshRefinementSettings
DU = adsk.fusion.DistanceUnits


class Model:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.body = FakeBody("Body1", token="body-1")
        self.design: Any = fake_part_design(self.body)
        self.root = self.design.rootComponent
        self.part = FakeComponent("Part", token="comp-part")
        self.lonely = FakeComponent("Lonely", token="comp-lonely")
        for component in (self.part, self.lonely):
            self.design.allComponents.add(component)
            self.design.add_entity(component)
        self.occ = add_occurrence(
            self.root, FakeOccurrence(self.part, "Part:1", token="occ-1"), self.root
        )
        self.design.add_entity(self.occ)
        self.sketch: Any = FakeEntity("adsk::fusion::Sketch", "Sketch1", "sketch-1")
        self.design.add_entity(self.sketch)
        self.app: Any = FakeApp(self.design)
        self.em, _ = install_exchange(self.app, self.design)

    def path(self, name: str) -> str:
        return str(self.tmp / name)

    def run(self, fn: Any, **params: Any) -> Any:
        return fn(Context(self.app, params), params)

    def fails(self, fn: Any, code: ErrorCode, **params: Any) -> BridgeError:
        with pytest.raises(BridgeError) as info:
            self.run(fn, **params)
        assert info.value.code is code, info.value
        return info.value


@pytest.fixture
def m(tmp_path: Path) -> Model:
    return Model(tmp_path)


# --- 3.1 fakes ----------------------------------------------------------------------------


def test_fake_export_manager_writes_files(tmp_path: Path) -> None:
    em = FakeExportManager()
    stl = em.createSTLExportOptions("geometry", str(tmp_path / "a.stl"))
    assert stl.geometry == "geometry" and stl.isBinaryFormat is True and hasattr(stl, "unitType")
    assert not hasattr(em.createC3MFExportOptions("g", "x.3mf"), "unitType")
    obj = em.createOBJExportOptions("g", str(tmp_path / "a.obj"))
    assert em.execute(obj) and (tmp_path / "a.mtl").exists()
    assert (tmp_path / "a.obj").stat().st_size == 128
    em.result = False
    assert em.execute(stl) is False and not (tmp_path / "a.stl").exists()
    em.missing.add("createUSDExportOptions")
    assert not hasattr(em, "createUSDExportOptions")


def test_fake_import_and_mesh_bodies(m: Model, tmp_path: Path) -> None:
    im = m.app.importManager
    options = im.createSTEPImportOptions(str(tmp_path / "bolt.step"))
    result = im.importToTarget2(options, m.root)
    assert result.count == 1 and m.root.occurrences.item(1).name == "bolt:1"
    mesh = m.root.meshBodies.add(str(tmp_path / "part.stl"), adsk.fusion.MeshUnits.InchMeshUnit)
    assert mesh.name == "part" and m.root.meshBodies.count == 1
    mesh.deleteMe()
    assert m.root.meshBodies.count == 0
    im.missing.add("importToTarget2")
    assert not hasattr(im, "importToTarget2")


# --- 3.2 run_export -----------------------------------------------------------------------


def _step(m: Model, **kwargs: Any) -> dict[str, Any]:
    ctx = Context(m.app, {})
    return run_export(
        ctx,
        lambda path: m.em.createSTEPExportOptions(path, m.root),
        kwargs.pop("path", m.path("a.step")),
        kwargs.pop("overwrite", False),
        extensions=(".step", ".stp"),
        fmt="step",
        scope="root",
        **kwargs,
    )


def test_run_export_success(m: Model) -> None:
    result = _step(m)
    assert result == {
        "files": [{"path": m.path("a.step"), "size_bytes": 128}],
        "format": "step",
        "scope": "root",
    }


def test_run_export_execute_false(m: Model) -> None:
    m.em.result = False
    with pytest.raises(BridgeError) as info:
        _step(m)
    assert info.value.code is ErrorCode.FUSION_API_ERROR and "export failed" in info.value.message


def test_run_export_license_restricted(m: Model) -> None:
    """Fusion refuses a format the license does not allow (c15 D3): a distinct reason."""
    m.em.restricted.add("step")
    with pytest.raises(BridgeError) as info:
        _step(m)
    error = info.value
    assert error.code is ErrorCode.FUSION_API_ERROR
    assert error.message == "export of step is not allowed by this Fusion license"
    assert error.details["reason"] == "license_restricted"
    assert error.details["format"] == "step"
    assert error.details["path"] == m.path("a.step")
    assert "user is restricted" in error.details["fusion_message"]


def test_run_export_other_exception_is_export_failed(m: Model) -> None:
    def boom(path: str) -> Any:
        raise RuntimeError("3 : disk full")

    with pytest.raises(BridgeError) as info:
        _step(m, execute=boom)
    assert info.value.message == "export failed: 3 : disk full"
    assert "reason" not in info.value.details


def test_run_export_empty_or_missing_file(m: Model) -> None:
    m.em.size = 0
    with pytest.raises(BridgeError) as info:
        _step(m)
    assert "empty" in info.value.message
    m.em.size, m.em.write_file = 10, False
    with pytest.raises(BridgeError) as info:
        _step(m, path=m.path("b.step"))
    assert "no file" in info.value.message


def test_run_export_refuses_existing_without_overwrite(m: Model) -> None:
    Path(m.path("a.step")).write_bytes(b"old")
    with pytest.raises(BridgeError) as info:
        _step(m)
    assert info.value.details["reason"] == "exists"
    assert m.em.executed == [] and Path(m.path("a.step")).read_bytes() == b"old"


def test_run_export_overwrite_removes_old_file(m: Model) -> None:
    Path(m.path("a.step")).write_bytes(b"old")
    m.em.write_file = False
    with pytest.raises(BridgeError):
        _step(m, overwrite=True)
    assert not os.path.exists(m.path("a.step"))
    m.em.write_file = True
    assert _step(m, overwrite=True)["files"][0]["size_bytes"] == 128


def test_run_export_reports_fresh_extra_files_only(m: Model) -> None:
    stale = Path(m.path("a.mtl"))
    stale.write_bytes(b"old")
    os.utime(stale, (1_000_000, 1_000_000))
    result = _step(m, extra_files=lambda path: [m.path("a.mtl")])
    assert [f["path"] for f in result["files"]] == [m.path("a.step")]


def test_export_handlers_are_read_only() -> None:
    for name in ("export.cad", "export.mesh", "export.sketch_dxf"):
        spec = REGISTRY.get(name)
        assert spec is not None and spec.mutating is False


# --- 3.3 export.cad -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "name"),
    [("step", "a.step"), ("iges", "a.igs"), ("sat", "a.sat"), ("smt", "a.smt"), ("f3d", "a.f3d")],
)
def test_export_cad_formats(m: Model, fmt: str, name: str) -> None:
    result = m.run(eh.export_cad, path=m.path(name), format=fmt)
    options = m.em.executed[-1]
    assert options.kind == fmt and options.geometry is m.root
    assert result["format"] == fmt and result["scope"] == "root"
    assert result["files"][0]["size_bytes"] > 0


def test_export_cad_detects_format(m: Model) -> None:
    assert m.run(eh.export_cad, path=m.path("a.stp"))["format"] == "step"
    m.fails(eh.export_cad, ErrorCode.INVALID_PARAMS, path=m.path("a.xyz"))


def test_export_cad_extension_must_match(m: Model) -> None:
    error = m.fails(eh.export_cad, ErrorCode.INVALID_PARAMS, path=m.path("a.stl"), format="step")
    assert error.details["reason"] == "bad_extension"


def test_export_cad_occurrence_and_component_scope(m: Model) -> None:
    result = m.run(eh.export_cad, path=m.path("o.step"), scope="occ-1")
    assert m.em.executed[-1].geometry is m.occ and result["scope"] == "occ-1"
    result = m.run(eh.export_cad, path=m.path("c.step"), scope="comp-part")
    assert m.em.executed[-1].geometry is m.occ and result["scope"] == "comp-part"
    result = m.run(eh.export_cad, path=m.path("r.step"), scope="component:root")
    assert m.em.executed[-1].geometry is m.root and result["scope"] == "root"


def test_export_cad_rejects_body_and_lonely_component(m: Model) -> None:
    error = m.fails(eh.export_cad, ErrorCode.INVALID_PARAMS, path=m.path("b.step"), scope="body-1")
    assert "export_mesh" in error.message
    error = m.fails(
        eh.export_cad, ErrorCode.INVALID_PARAMS, path=m.path("l.step"), scope="comp-lonely"
    )
    assert error.details["reason"] == "no_occurrence"
    assert m.em.executed == []


# --- 3.4 usdz -----------------------------------------------------------------------------


def test_export_cad_usdz(m: Model) -> None:
    result = m.run(eh.export_cad, path=m.path("a.usdz"))
    assert result["format"] == "usdz" and m.em.executed[-1].kind == "usdz"


def test_export_cad_usdz_unavailable(m: Model) -> None:
    m.em.missing.add("createUSDExportOptions")
    error = m.fails(eh.export_cad, ErrorCode.FUSION_API_ERROR, path=m.path("a.usdz"))
    assert "USD export not available" in error.message


# --- 3.5 export.mesh ----------------------------------------------------------------------


def test_export_mesh_stl_high_binary_mm_of_body(m: Model) -> None:
    result = m.run(eh.export_mesh, path=m.path("b.stl"), scope="body-1", refinement="high")
    options = m.em.executed[-1]
    assert options.geometry is m.body
    assert options.meshRefinement == MR.MeshRefinementHigh
    assert options.isBinaryFormat is True and options.unitType == DU.MillimeterDistanceUnits
    assert options.isOneFilePerBody is False and options.sendToPrintUtility is False
    assert len(result["files"]) == 1 and "warnings" not in result


def test_export_mesh_ascii_inches(m: Model) -> None:
    m.run(eh.export_mesh, path=m.path("a.stl"), binary=False, file_units="in", refinement="low")
    options = m.em.executed[-1]
    assert options.isBinaryFormat is False and options.unitType == DU.InchDistanceUnits
    assert options.meshRefinement == MR.MeshRefinementLow


def test_export_mesh_custom_values_converted(m: Model) -> None:
    m.run(
        eh.export_mesh,
        path=m.path("c.stl"),
        refinement="custom",
        surface_deviation=0.5,
        normal_deviation=10,
        max_edge_length=20,
        aspect_ratio=5,
    )
    options = m.em.executed[-1]
    assert options.meshRefinement == MR.MeshRefinementCustom
    assert options.surfaceDeviation == pytest.approx(0.05)
    assert options.normalDeviation == pytest.approx(math.radians(10))
    assert options.maximumEdgeLength == pytest.approx(2.0)
    assert options.aspectRatio == 5.0


def test_export_mesh_custom_in_request_units(m: Model) -> None:
    m.run(
        eh.export_mesh, path=m.path("c.stl"), refinement="custom", surface_deviation=1, units="cm"
    )
    assert m.em.executed[-1].surfaceDeviation == pytest.approx(1.0)


def test_export_mesh_custom_values_need_custom_refinement(m: Model) -> None:
    error = m.fails(
        eh.export_mesh, ErrorCode.INVALID_PARAMS, path=m.path("a.stl"), surface_deviation=0.1
    )
    assert error.details["param"] == "surface_deviation"
    error = m.fails(
        eh.export_mesh, ErrorCode.INVALID_PARAMS, path=m.path("a.stl"), refinement="custom"
    )
    assert error.details["param"] == "refinement"
    assert m.em.executed == []


@pytest.mark.parametrize("value", ["0.1 mm", 0, -1, True])
def test_export_mesh_custom_value_must_be_positive_number(m: Model, value: Any) -> None:
    error = m.fails(
        eh.export_mesh,
        ErrorCode.INVALID_PARAMS,
        path=m.path("a.stl"),
        refinement="custom",
        surface_deviation=value,
    )
    assert error.details["param"] == "surface_deviation"


def test_export_mesh_3mf_warns_about_file_units(m: Model) -> None:
    result = m.run(eh.export_mesh, path=m.path("a.3mf"), file_units="in")
    assert result["format"] == "3mf" and len(result["warnings"]) == 1
    assert "warnings" not in m.run(eh.export_mesh, path=m.path("b.3mf"))


def test_export_mesh_obj_with_mtl(m: Model) -> None:
    result = m.run(eh.export_mesh, path=m.path("a.obj"), scope="occ-1")
    assert [f["path"] for f in result["files"]] == [m.path("a.obj"), m.path("a.mtl")]
    assert m.em.executed[-1].unitType == DU.MillimeterDistanceUnits


def test_export_mesh_obj_overwrite_removes_old_mtl(m: Model) -> None:
    Path(m.path("a.obj")).write_bytes(b"old")
    Path(m.path("a.mtl")).write_bytes(b"old")
    m.em.write_mtl = False
    result = m.run(eh.export_mesh, path=m.path("a.obj"), overwrite=True)
    assert len(result["files"]) == 1 and not os.path.exists(m.path("a.mtl"))


def test_export_mesh_bad_choices(m: Model) -> None:
    m.fails(eh.export_mesh, ErrorCode.INVALID_PARAMS, path=m.path("a.stl"), refinement="ultra")
    m.fails(eh.export_mesh, ErrorCode.INVALID_PARAMS, path=m.path("a.stl"), file_units="yd")
    m.fails(eh.export_mesh, ErrorCode.INVALID_PARAMS, path=m.path("a.step"))


# --- 3.6 export.sketch_dxf ----------------------------------------------------------------


def test_export_sketch_dxf_preview_options(m: Model) -> None:
    result = m.run(eh.export_sketch_dxf, sketch="sketch-1", path=m.path("s.dxf"))
    assert m.em.executed[-1].kind == "dxf" and m.em.executed[-1].geometry is m.sketch
    assert result["format"] == "dxf" and result["scope"] == "sketch-1"
    assert result["files"][0]["size_bytes"] == 128


def test_export_sketch_dxf_save_as_fallback(m: Model) -> None:
    m.em.missing.add("createDXFSketchExportOptions")
    saved: list[str] = []

    def save(path: str) -> bool:
        saved.append(path)
        Path(path).write_bytes(b"dxf")
        return True

    m.sketch.saveAsDXF = save
    result = m.run(eh.export_sketch_dxf, sketch="sketch-1", path=m.path("s.dxf"))
    assert saved == [m.path("s.dxf")] and result["files"][0]["size_bytes"] == 3


def test_export_sketch_dxf_unavailable(m: Model) -> None:
    m.em.missing.add("createDXFSketchExportOptions")
    error = m.fails(
        eh.export_sketch_dxf, ErrorCode.FUSION_API_ERROR, sketch="sketch-1", path=m.path("s.dxf")
    )
    assert "sketch DXF export not available" in error.message


def test_export_sketch_dxf_rejects_non_sketch(m: Model) -> None:
    m.fails(eh.export_sketch_dxf, ErrorCode.INVALID_PARAMS, sketch="body-1", path=m.path("s.dxf"))

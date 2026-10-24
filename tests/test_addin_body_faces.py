"""``body.split_face`` / ``delete_faces`` / ``replace_face`` handlers on feature fakes
(c08 D10, D12; tasks 4.3, 6.3, 6.4)."""

from __future__ import annotations

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import body_faces as h
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import FakeBody, FakeConstructionPlane, FakeFace, add_face, fake_box_body
from tests.fakes.fusion_features import fake_feature_design, install_features, make_ctx

adsk = fusion_addin.adsk


class Setup:
    def __init__(self, omit: tuple[str, ...] = ()) -> None:
        self.box = fake_box_body(4, 2, 1, name="Box")
        self.other = fake_box_body(1, 1, 1, name="Other")
        self.surface = FakeBody("Patch", is_solid=False)
        self.design = fake_feature_design(self.box, self.other, self.surface)
        self.root = self.design.rootComponent
        if omit:
            install_features(self.root, self.design, omit)
        self.features = self.root.features
        faces = {f.entityToken.rsplit("face", 1)[1]: f for f in self.box.faces}
        self.top, self.px, self.nx = faces["+z"], faces["+x"], faces["-x"]
        edges = {e.entityToken.rsplit("-e", 1)[1]: e for e in self.box.edges}
        self.z_edge = edges["000001"]
        self.plane = FakeConstructionPlane(
            "Mid", adsk.core.Point3D.create(2, 0, 0), adsk.core.Vector3D.create(1, 0, 0)
        )
        self.aliases: dict[str, Any] = {
            "box": self.box,
            "other": self.other,
            "patch": self.surface,
            "top": self.top,
            "px": self.px,
            "nx": self.nx,
            "ze": self.z_edge,
            "mid": self.plane,
            "vertex": self.box.vertices.item(0),
        }

    def ctx(self, params: dict[str, Any]) -> Context:
        return make_ctx(self.design, params, self.aliases)

    def run(self, fn: Any, params: dict[str, Any]) -> Any:
        return fn(self.ctx(params), params)

    def error(self, fn: Any, params: dict[str, Any]) -> BridgeError:
        with pytest.raises(BridgeError) as info:
            fn(self.ctx(params), params)
        return info.value

    def invalid(self, fn: Any, params: dict[str, Any], param: str) -> BridgeError:
        err = self.error(fn, params)
        assert err.code is ErrorCode.INVALID_PARAMS, err
        assert err.details.get("param") == param, err.details
        return err


@pytest.mark.parametrize("method", ["body.split_face", "body.delete_faces", "body.replace_face"])
def test_handlers_are_registered_as_mutating(method: str) -> None:
    spec = REGISTRY.get(method)
    assert spec is not None and spec.mutating is True


# --- 4.3 split face ------------------------------------------------------------------------


def test_split_face_by_plane_reports_new_faces() -> None:
    s = Setup()
    halves = [
        FakeFace(None, 1.0, adsk.core.Point3D.create(1, 1, 1), token="half-a"),
        FakeFace(None, 1.0, adsk.core.Point3D.create(3, 1, 1), token="half-b"),
    ]
    splits = s.features.splitFaceFeatures

    def split(feature: Any) -> None:
        for face in halves:
            add_face(s.box, face)

    splits.on_add = split
    splits.will_modify(s.box)
    result = s.run(h.body_split_face, {"faces": ["top"], "tool": "mid"})
    inp = splits.last_input
    faces, tool, extend = inp.create_args
    assert list(faces) == [s.top] and tool is s.plane and extend is True
    assert inp.calls == []  # intersection: nothing else to call
    assert [b["name"] for b in result["modified_bodies"]] == ["Box"]
    assert result["faces"] == [
        {"ref": "half-a", "type": "BRepFace"},
        {"ref": "half-b", "type": "BRepFace"},
    ]


def test_split_face_along_vector_and_closest_point() -> None:
    s = Setup()
    s.run(
        h.body_split_face,
        {
            "faces": ["top", "px"],
            "tool": "other",
            "split_type": "along_vector",
            "direction": "ze",
            "extend_tool": False,
        },
    )
    inp = s.features.splitFaceFeatures.last_input
    assert inp.create_args[2] is False
    assert inp.call("setAlongVectorSplitType") == (s.z_edge,)
    s.run(
        h.body_split_face,
        {"faces": ["top"], "tool": "origin:YZ", "split_type": "closest_point"},
    )
    inp = s.features.splitFaceFeatures.last_input
    assert inp.create_args[1] is s.root.yZConstructionPlane
    assert inp.call("setClosestPointSplitType") == ()


def test_split_type_not_available() -> None:
    s = Setup()
    s.features.splitFaceFeatures.on_input = lambda inp: inp.missing.add("setClosestPointSplitType")
    err = s.error(
        h.body_split_face, {"faces": ["top"], "tool": "mid", "split_type": "closest_point"}
    )
    assert err.code is ErrorCode.FUSION_API_ERROR and "not available" in err.message
    assert s.features.splitFaceFeatures.added == []


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"split_type": "along_vector"}, "direction"),
        ({"split_type": "along_vector", "direction": "vertex"}, "direction"),
        ({"direction": "ze"}, "direction"),
        ({"split_type": "slice"}, "split_type"),
        ({"tool": "top"}, "tool"),
        ({"tool": "vertex"}, "tool"),
        ({"faces": ["box"]}, "faces"),
    ],
)
def test_split_face_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.body_split_face, {"faces": ["top"], "tool": "mid", **params}, param)
    assert s.features.splitFaceFeatures.inputs == []


# --- 6.3 delete faces ----------------------------------------------------------------------


def test_delete_fillet_faces() -> None:
    s = Setup()
    s.features.deleteFaceFeatures.will_modify(s.box)
    result = s.run(h.body_delete_faces, {"faces": ["px", "nx"]})
    added = s.features.deleteFaceFeatures.added[-1]
    (faces,) = added.create_args
    assert isinstance(faces, adsk.core.ObjectCollection)
    assert list(faces) == [s.px, s.nx]
    assert [b["name"] for b in result["modified_bodies"]] == ["Box"]
    assert result["feature"]["type"] == "DeleteFaceFeature"


def test_delete_faces_validation_and_old_api() -> None:
    s = Setup()
    s.invalid(h.body_delete_faces, {"faces": []}, "faces")
    s.invalid(h.body_delete_faces, {"faces": ["box"]}, "faces")
    old = Setup(omit=("deleteFaceFeatures",))
    err = old.error(h.body_delete_faces, {"faces": ["px"]})
    assert err.code is ErrorCode.FUSION_API_ERROR and "not available" in err.message


# --- 6.4 replace face ----------------------------------------------------------------------


def test_replace_face_with_plane() -> None:
    s = Setup()
    result = s.run(h.body_replace_face, {"faces": ["px"], "target": "mid"})
    inp = s.features.replaceFaceFeatures.last_input
    faces, tangent_chain, target = inp.create_args
    assert list(faces) == [s.px] and tangent_chain is True and target is s.plane
    assert [b["name"] for b in result["modified_bodies"]] == ["Box"]


def test_replace_face_with_surface_body_origin_plane_or_face() -> None:
    s = Setup()
    s.run(h.body_replace_face, {"faces": ["top"], "target": "patch", "tangent_chain": False})
    _, chain, target = s.features.replaceFaceFeatures.last_input.create_args
    assert target is s.surface and chain is False
    s.run(h.body_replace_face, {"faces": ["top"], "target": "origin:XY"})
    assert s.features.replaceFaceFeatures.last_input.create_args[2] is s.root.xYConstructionPlane
    other_top = next(f for f in s.other.faces if f.entityToken.endswith("face+z"))
    s.aliases["ot"] = other_top
    s.run(h.body_replace_face, {"faces": ["top"], "target": "ot"})
    assert s.features.replaceFaceFeatures.last_input.create_args[2] is other_top


@pytest.mark.parametrize(
    ("params", "param"),
    [
        ({"target": "other"}, "target"),
        ({"target": "px"}, "target"),
        ({"target": "ze"}, "target"),
        ({"target": None}, "target"),
        ({"tangent_chain": "no"}, "tangent_chain"),
    ],
)
def test_replace_face_validation(params: dict[str, Any], param: str) -> None:
    s = Setup()
    s.invalid(h.body_replace_face, {"faces": ["px"], "target": "mid", **params}, param)
    assert s.features.replaceFaceFeatures.inputs == []

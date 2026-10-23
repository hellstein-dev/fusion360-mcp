"""``fusion_addin.core.refs``: ``to_ref`` / ``resolve`` on a fake design."""

from typing import Any

import pytest

from fusion_addin.core import refs
from fusion_addin.core.context import Context
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.design import FakeApp, FakeCollection, FakeDesign, FakeEntity


@pytest.fixture
def design() -> FakeDesign:
    d = FakeDesign()
    d.add_entity(FakeEntity("adsk::fusion::BRepFace", token="tok-face"))
    d.allParameters.add(FakeEntity("adsk::fusion::UserParameter", "width"))
    d.allComponents.add(FakeEntity("adsk::fusion::Component", "Lid"))
    root: Any = d.rootComponent
    root.allOccurrences.add(FakeEntity("adsk::fusion::Occurrence", "Lid:1", fullPathName="Lid:1"))
    root.allOccurrences.add(
        FakeEntity("adsk::fusion::Occurrence", "Pin:1", fullPathName="Lid:1+Pin:1")
    )
    return d


def test_to_ref_prefers_entity_token() -> None:
    face = FakeEntity("adsk::fusion::BRepFace", token="tok-face")
    assert refs.to_ref(face) == "tok-face"
    # Types without a describer keep the minimal form (faces are covered in test_addin_describe).
    profile = FakeEntity("adsk::fusion::Profile", token="tok-profile")
    assert refs.describe(profile) == {"ref": "tok-profile", "type": "Profile"}


@pytest.mark.parametrize(
    ("entity", "ref"),
    [
        (FakeEntity("adsk::fusion::UserParameter", "width"), "param:width"),
        (FakeEntity("adsk::fusion::ModelParameter", "d1"), "param:d1"),
        (
            FakeEntity(
                "adsk::fusion::Component",
                "Lid",
                bRepBodies=FakeCollection(),
                sketches=FakeCollection(),
                occurrences=FakeCollection(),
            ),
            "component:Lid",
        ),
        (
            FakeEntity(
                "adsk::fusion::Occurrence",
                "Pin:1",
                fullPathName="Lid:1+Pin:1",
                component=FakeEntity("adsk::fusion::Component", "Pin"),
                isGrounded=False,
                isVisible=True,
            ),
            "occurrence:Lid:1+Pin:1",
        ),
    ],
)
def test_to_ref_name_paths(entity: FakeEntity, ref: str) -> None:
    assert refs.to_ref(entity) == ref
    assert refs.describe(entity)["name"] == entity.name


def test_to_ref_without_token_or_path() -> None:
    with pytest.raises(BridgeError) as exc:
        refs.to_ref(FakeEntity("adsk::fusion::SketchPoint"))
    assert exc.value.code is ErrorCode.INTERNAL


@pytest.mark.parametrize(
    ("ref", "name"),
    [
        ("param:width", "width"),
        ("component:Lid", "Lid"),
        ("occurrence:Lid:1", "Lid:1"),
        ("occurrence:Lid:1+Pin:1", "Pin:1"),
    ],
)
def test_resolve_name_paths(design: FakeDesign, ref: str, name: str) -> None:
    assert refs.resolve(design, ref).name == name


def test_resolve_token_and_dict(design: FakeDesign) -> None:
    face = design.by_token["tok-face"]
    assert refs.resolve(design, "tok-face") is face
    assert refs.resolve(design, {"ref": "tok-face", "type": "BRepFace"}) is face


@pytest.mark.parametrize(
    "ref", ["tok-deleted", "bad-token", "param:nope", "component:nope", "occurrence:X:1"]
)
def test_stale_ref_not_found(design: FakeDesign, ref: str) -> None:
    with pytest.raises(BridgeError) as exc:
        refs.resolve(design, ref)
    assert exc.value.code is ErrorCode.ENTITY_NOT_FOUND
    assert exc.value.details["ref"] == ref


@pytest.mark.parametrize("ref", ["", None, 5, {"type": "x"}])
def test_bad_ref_value(design: FakeDesign, ref: object) -> None:
    with pytest.raises(BridgeError) as exc:
        refs.resolve(design, ref)
    assert exc.value.code is ErrorCode.INVALID_PARAMS


def test_context_refs_uses_active_design(design: FakeDesign) -> None:
    ctx = Context(FakeApp(design))
    assert ctx.refs.resolve("param:width").name == "width"
    assert ctx.refs.to_ref(design.by_token["tok-face"]) == "tok-face"
    with pytest.raises(BridgeError) as exc:
        Context(FakeApp(None)).refs.resolve("param:width")
    assert exc.value.code is ErrorCode.NO_ACTIVE_DESIGN

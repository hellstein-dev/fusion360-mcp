"""Fake material libraries and appearances (c12 task 2.1)."""

from typing import Any, cast

import pytest

from tests.fakes.design import FakeApp, FakeDesign, FakeEntity
from tests.fakes.fusion_materials import FakeAppearance, install_materials


def test_libraries_and_lookup() -> None:
    design: Any = FakeDesign()
    app = FakeApp(design)
    libraries = install_materials(app, design)
    assert libraries.count == 3
    materials = cast(Any, libraries.itemByName("Fusion Material Library"))
    assert materials.materials.itemByName("Steel").id == "mat-steel"
    assert materials.materials.itemById("mat-abs").name == "ABS Plastic"
    assert libraries.itemById("lib-materials") is materials
    appearances = libraries.item(1).appearances
    assert appearances.count == 3
    assert appearances.itemById("app-paint-red").color.red == 200
    assert not hasattr(appearances.itemById("app-legacy-blue"), "color")
    assert design.materials.count == 0 and design.appearances.count == 0


def test_add_by_copy_keeps_id_for_same_name_and_library_is_read_only() -> None:
    design: Any = FakeDesign()
    libraries = install_materials(FakeApp(design), design)
    red = libraries.item(1).appearances.itemById("app-paint-red")
    same = design.appearances.addByCopy(red, red.name)
    renamed = design.appearances.addByCopy(red, "My red")
    assert same.id == red.id and renamed.id != red.id
    assert renamed.color.red == 200 and renamed.color is not red.color
    assert design.appearances.copies == [(red, red.name), (red, "My red")]
    with pytest.raises(RuntimeError):
        libraries.item(1).appearances.addByCopy(red, "x")
    design.appearances.fail_add_by_copy = True
    with pytest.raises(RuntimeError):
        design.appearances.addByCopy(red, "y")


def test_is_used_and_delete() -> None:
    design: Any = FakeDesign()
    install_materials(FakeApp(design), design)
    copy = design.appearances.addByCopy(FakeAppearance("a", "A"), "A")
    body: Any = FakeEntity("adsk::fusion::BRepBody", "Body1", "b1", appearance=None)
    design.appearances.users.append(body)
    assert not copy.isUsed
    body.appearance = copy
    assert copy.isUsed
    body.appearance = None
    assert copy.deleteMe() and copy.deleted
    assert design.appearances.count == 0


def test_legacy_color_property() -> None:
    design: Any = FakeDesign()
    libraries = install_materials(FakeApp(design), design)
    legacy = libraries.item(1).appearances.itemById("app-legacy-blue")
    prop = legacy.appearanceProperties.itemById("generic_diffuse")
    assert prop.value.blue == 200
    assert legacy.appearanceProperties.itemById("opaque_albedo") is None

"""``core.materials`` and the ``material.*`` / ``appearance.*`` handlers on fakes (c12 tasks
2.2-2.8)."""

from typing import Any

import pytest

import fusion_addin
from fusion_addin.core import materials as core
from fusion_addin.core.context import Context
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import appearance as ah
from fusion_addin.handlers import material as mh
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import (
    FakeBody,
    FakeComponent,
    FakeFace,
    FakeOccurrence,
    add_face,
    add_occurrence,
    fake_part_design,
)
from tests.fakes.design import FakeApp
from tests.fakes.fusion_materials import FakeAppearance, install_materials

adsk = fusion_addin.adsk


class Flaky:
    """A body whose ``material`` / ``appearance`` setter applies the value, then raises once
    when ``fail`` is set (Fusion failing half-way)."""

    objectType = "adsk::fusion::BRepBody"

    def __init__(self, token: str) -> None:
        self.name = "Flaky"
        self.entityToken = token
        self.parentComponent: Any = None
        self.fail = False
        self._material: Any = None
        self._appearance: Any = None

    def _apply(self, attr: str, value: Any) -> None:
        setattr(self, attr, value)
        if self.fail:
            self.fail = False
            raise RuntimeError("Fusion refused")

    @property
    def material(self) -> Any:
        return self._material

    @material.setter
    def material(self, value: Any) -> None:
        self._apply("_material", value)

    @property
    def appearance(self) -> Any:
        return self._appearance

    @appearance.setter
    def appearance(self, value: Any) -> None:
        self._apply("_appearance", value)


class Model:
    def __init__(self) -> None:
        self.body = FakeBody("Body1", token="body-1")
        self.face = add_face(
            self.body, FakeFace(None, 1.0, adsk.core.Point3D.create(0, 0, 0), token="face-1")
        )
        self.design: Any = fake_part_design(self.body)
        root = self.design.rootComponent
        self.part = FakeComponent("Part", token="comp-part")
        self.design.allComponents.add(self.part)
        self.design.add_entity(self.part)
        self.occ = add_occurrence(root, FakeOccurrence(self.part, "Part:1", token="occ-1"), root)
        self.design.add_entity(self.occ)
        self.flaky = Flaky("flaky-1")
        self.flaky.parentComponent = root
        self.design.add_entity(self.flaky)
        self.app = FakeApp(self.design)
        self.libraries = install_materials(self.app, self.design)
        appearances: Any = getattr(self.design, "appearances")  # noqa: B009 (set by the fake)
        appearances.users.extend([self.body, self.face, self.occ, self.flaky])

    def library(self, index: int) -> Any:
        return self.libraries.item(index)

    def run(self, fn: Any, **params: Any) -> Any:
        return fn(Context(self.app, params), params)

    def fails(self, fn: Any, code: ErrorCode, **params: Any) -> BridgeError:
        with pytest.raises(BridgeError) as info:
            self.run(fn, **params)
        assert info.value.code is code, info.value
        return info.value


@pytest.fixture
def m() -> Model:
    return Model()


# --- 2.2 core lookup ----------------------------------------------------------------------


def test_iter_libraries(m: Model) -> None:
    assert [lib.name for lib in core.iter_libraries(m.app)] == [
        "Fusion Material Library",
        "Fusion Appearance Library",
        "Custom Library",
    ]


def test_find_material_by_id_then_name(m: Model) -> None:
    ctx = Context(m.app, {})
    item, label = core.find_material(ctx, "mat-aluminum")
    assert (item.name, label) == ("Aluminum", "Fusion Material Library")
    item, label = core.find_material(ctx, "ABS Plastic")
    assert item.id == "mat-abs"


def test_find_material_ambiguous_name_lists_candidates(m: Model) -> None:
    with pytest.raises(BridgeError) as info:
        core.find_material(Context(m.app, {}), "Steel")
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["candidates"] == [
        {"id": "mat-steel", "name": "Steel", "library": "Fusion Material Library"},
        {"id": "mat-custom-steel", "name": "Steel", "library": "Custom Library"},
    ]


def test_find_material_in_named_library(m: Model) -> None:
    ctx = Context(m.app, {})
    item, label = core.find_material(ctx, "Steel", "lib-custom")
    assert (item.id, label) == ("mat-custom-steel", "Custom Library")


def test_find_unknown_material_and_library(m: Model) -> None:
    ctx = Context(m.app, {})
    with pytest.raises(BridgeError) as info:
        core.find_material(ctx, "Unobtainium")
    assert info.value.code is ErrorCode.ENTITY_NOT_FOUND
    with pytest.raises(BridgeError) as info:
        core.find_material(ctx, "Steel", "No Such Library")
    assert info.value.code is ErrorCode.ENTITY_NOT_FOUND
    assert info.value.details["param"] == "library"


def test_find_appearance_prefers_design_copy(m: Model) -> None:
    ctx = Context(m.app, {})
    red = m.library(1).appearances.itemById("app-paint-red")
    copy = m.design.appearances.addByCopy(red, red.name)
    item, label = core.find_appearance(ctx, "Paint - Enamel Glossy (Red)")
    assert (item, label) == (copy, "design")


def test_describe_material_locates_library(m: Model) -> None:
    ctx = Context(m.app, {})
    steel = m.library(0).materials.itemById("mat-steel")
    assert core.describe_material(ctx, steel) == {
        "id": "mat-steel",
        "name": "Steel",
        "library": "Fusion Material Library",
    }
    assert core.describe_material(ctx, None) is None


def test_list_items_filter_and_pagination(m: Model) -> None:
    ctx = Context(m.app, {})
    page = core.list_items(ctx, "materials", {"query": "STEEL", "limit": 1})
    assert page["total"] == 2 and page["truncated"] is True
    assert page["items"] == [
        {"id": "mat-steel", "name": "Steel", "library": "Fusion Material Library"}
    ]
    page = core.list_items(ctx, "materials", {"query": "steel", "offset": 1})
    assert page["items"][0]["id"] == "mat-custom-steel" and page["truncated"] is False


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 1001},
        {"offset": -1},
        {"limit": True},
        {"in_design": True, "library": "x"},
    ],
)
def test_list_items_rejects(m: Model, params: dict[str, Any]) -> None:
    with pytest.raises(BridgeError) as info:
        core.list_items(Context(m.app, {}), "materials", params)
    assert info.value.code is ErrorCode.INVALID_PARAMS


# --- 2.3 material.list_libraries / material.list -----------------------------------------


def test_handlers_registered_with_mutating_flags() -> None:
    expected = {
        "material.list_libraries": False,
        "material.list": False,
        "material.get": False,
        "material.assign": True,
        "appearance.list": False,
        "appearance.assign": True,
        "appearance.set_color": True,
    }
    for name, mutating in expected.items():
        spec = REGISTRY.get(name)
        assert spec is not None and spec.mutating is mutating, name


def test_list_libraries_counts(m: Model) -> None:
    result = m.run(mh.material_list_libraries)
    assert result["libraries"][0] == {
        "id": "lib-materials",
        "name": "Fusion Material Library",
        "is_native": True,
        "material_count": 3,
        "appearance_count": 0,
    }
    assert result["libraries"][2]["is_native"] is False


def test_material_list_filter_library_and_paging(m: Model) -> None:
    result = m.run(mh.material_list, query="a", library="Fusion Material Library", limit=2)
    assert [i["name"] for i in result["items"]] == ["Aluminum", "ABS Plastic"]
    assert result["total"] == 2 and not result["truncated"]
    result = m.run(mh.material_list, offset=4)
    assert [i["id"] for i in result["items"]] == []
    assert result["total"] == 4


def test_material_list_in_design(m: Model) -> None:
    assert m.run(mh.material_list, in_design=True)["total"] == 0
    m.design.materials.addByCopy(m.library(0).materials.itemById("mat-abs"), "ABS Plastic")
    items = m.run(mh.material_list, in_design=True)["items"]
    assert items == [{"id": "mat-abs", "name": "ABS Plastic", "library": "design"}]


def test_material_list_unknown_library(m: Model) -> None:
    m.fails(mh.material_list, ErrorCode.ENTITY_NOT_FOUND, library="No Such Library")


# --- 2.4 material.get ---------------------------------------------------------------------


def test_material_get_body(m: Model) -> None:
    m.body.material = m.library(0).materials.itemById("mat-steel")
    m.body.appearance = m.library(1).appearances.itemById("app-steel-satin")
    result = m.run(mh.material_get, target="body-1")
    assert result["target"] == "body-1"
    assert result["material"]["id"] == "mat-steel"
    assert result["appearance"] == {
        "id": "app-steel-satin",
        "name": "Steel - Satin",
        "library": "Fusion Appearance Library",
    }


def test_material_get_component_and_occurrence(m: Model) -> None:
    m.part.material = m.library(0).materials.itemById("mat-abs")
    m.occ.appearance = m.library(1).appearances.itemById("app-paint-red")
    component = m.run(mh.material_get, target="comp-part")
    assert component["material"]["id"] == "mat-abs" and component["appearance"] is None
    occurrence = m.run(mh.material_get, target="occ-1")
    assert occurrence["material"]["id"] == "mat-abs"
    assert occurrence["appearance"]["id"] == "app-paint-red"


def test_material_get_rejects_face(m: Model) -> None:
    error = m.fails(mh.material_get, ErrorCode.INVALID_PARAMS, target="face-1")
    assert error.details["param"] == "target"


# --- 2.5 material.assign ------------------------------------------------------------------


def test_assign_to_body_by_id(m: Model) -> None:
    result = m.run(mh.material_assign, target="body-1", material="mat-aluminum")
    assert m.body.material.id == "mat-aluminum"
    assert result == {
        "target": "body-1",
        "applied_to": "body-1",
        "material": {
            "id": "mat-aluminum",
            "name": "Aluminum",
            "library": "Fusion Material Library",
        },
        "previous": None,
    }


def test_assign_reports_previous(m: Model) -> None:
    m.run(mh.material_assign, target="body-1", material="mat-aluminum")
    result = m.run(mh.material_assign, target="body-1", material="Steel", library="lib-materials")
    assert result["previous"]["id"] == "mat-aluminum"
    assert result["material"]["id"] == "mat-steel"


def test_assign_to_component(m: Model) -> None:
    result = m.run(mh.material_assign, target="comp-part", material="mat-abs")
    assert m.part.material.id == "mat-abs" and "note" not in result


def test_assign_to_occurrence_applies_to_component(m: Model) -> None:
    result = m.run(mh.material_assign, target="occ-1", material="mat-abs")
    assert m.part.material.id == "mat-abs"
    assert result["target"] == "occ-1" and result["applied_to"] == "comp-part"
    assert result["note"] == "applies to all occurrences of the component"


def test_assign_ambiguous_name(m: Model) -> None:
    error = m.fails(mh.material_assign, ErrorCode.INVALID_PARAMS, target="body-1", material="Steel")
    assert len(error.details["candidates"]) == 2
    assert m.body.material is None


def test_assign_failure_restores_previous_material(m: Model) -> None:
    steel = m.library(0).materials.itemById("mat-steel")
    m.flaky.material = steel
    m.flaky.fail = True
    error = m.fails(
        mh.material_assign, ErrorCode.FUSION_API_ERROR, target="flaky-1", material="mat-abs"
    )
    assert "Fusion refused" in error.message
    assert m.flaky.material is steel


# --- 2.6 appearance.list ------------------------------------------------------------------


def test_appearance_list(m: Model) -> None:
    result = m.run(ah.appearance_list, query="steel")
    assert [(i["id"], i["library"]) for i in result["items"]] == [
        ("app-steel-satin", "Fusion Appearance Library"),
        ("app-custom-satin", "Custom Library"),
    ]
    assert m.run(ah.appearance_list, in_design=True)["items"] == []
    m.fails(ah.appearance_list, ErrorCode.ENTITY_NOT_FOUND, library="nope")


# --- 2.7 appearance.assign ----------------------------------------------------------------


def test_assign_library_appearance_to_face_copies_it(m: Model) -> None:
    result = m.run(ah.appearance_assign, target="face-1", appearance="app-paint-red")
    copy = m.design.appearances.itemById("app-paint-red")
    assert copy is not None and m.face.appearance is copy
    assert m.design.appearances.copies[0][1] == "Paint - Enamel Glossy (Red)"
    assert result["copied"] is True and result["previous"] is None
    assert result["appearance"]["library"] == "design"


def test_assign_reuses_design_copy(m: Model) -> None:
    m.run(ah.appearance_assign, target="face-1", appearance="app-paint-red")
    result = m.run(ah.appearance_assign, target="body-1", appearance="app-paint-red")
    assert result["copied"] is False
    assert len(m.design.appearances.copies) == 1
    assert m.body.appearance is m.face.appearance


def test_assign_appearance_to_occurrence_reports_previous(m: Model) -> None:
    m.run(ah.appearance_assign, target="occ-1", appearance="app-paint-red")
    result = m.run(
        ah.appearance_assign,
        target="occ-1",
        appearance="app-steel-satin",
        library="lib-appearances",
    )
    assert result["previous"]["id"] == "app-paint-red"
    assert m.occ.appearance.id == "app-steel-satin"


def test_assign_appearance_rejects_component(m: Model) -> None:
    error = m.fails(
        ah.appearance_assign,
        ErrorCode.INVALID_PARAMS,
        target="comp-part",
        appearance="app-paint-red",
    )
    assert "bodies or to an occurrence" in error.message
    assert m.design.appearances.count == 0


def test_assign_appearance_failure_restores_and_deletes_copy(m: Model) -> None:
    satin = m.library(1).appearances.itemById("app-steel-satin")
    m.flaky.appearance = satin
    m.flaky.fail = True
    m.fails(
        ah.appearance_assign,
        ErrorCode.FUSION_API_ERROR,
        target="flaky-1",
        appearance="app-paint-red",
    )
    assert m.flaky.appearance is satin
    assert m.design.appearances.count == 0


# --- 2.8 appearance.set_color -------------------------------------------------------------


def test_set_color_creates_copy(m: Model) -> None:
    m.body.appearance = m.library(1).appearances.itemById("app-steel-satin")
    result = m.run(ah.appearance_set_color, target="body-1", color="#FF0000")
    copy = m.body.appearance
    assert copy.name == "MCP Steel - Satin #FF0000"
    assert (copy.color.red, copy.color.green, copy.color.blue, copy.color.opacity) == (
        255,
        0,
        0,
        255,
    )
    assert result["created"] is True and result["color"] == "#FF0000FF"
    satin = m.library(1).appearances.itemById("app-steel-satin")
    assert satin.color.red == 180  # the library appearance is untouched


def test_set_color_repeat_reuses(m: Model) -> None:
    m.body.appearance = m.library(1).appearances.itemById("app-steel-satin")
    m.run(ah.appearance_set_color, target="body-1", color="#FF0000")
    first = m.body.appearance
    m.body.appearance = m.library(1).appearances.itemById("app-steel-satin")
    result = m.run(ah.appearance_set_color, target="body-1", color=[255, 0, 0])
    assert result["created"] is False and m.body.appearance is first
    assert m.design.appearances.count == 1


def test_set_color_straight_repeat_keeps_current_appearance(m: Model) -> None:
    """The live smoke sequence (c15 5.2): the second call starts from the target's current
    appearance - the copy the first call made - and must reuse it, not copy it again as
    "MCP MCP Steel - Satin #FF0000 #FF0000"."""
    m.body.appearance = m.library(1).appearances.itemById("app-steel-satin")
    m.run(ah.appearance_set_color, target="body-1", color="#FF0000")
    first = m.body.appearance
    again = m.run(ah.appearance_set_color, target="body-1", color="#FF0000")
    assert again["created"] is False
    assert again["appearance"]["name"] == "MCP Steel - Satin #FF0000"
    assert m.body.appearance is first
    assert m.design.appearances.count == 1
    # Another colour still starts from the current appearance and makes a new copy.
    blue = m.run(ah.appearance_set_color, target="body-1", color="#0000FF")
    assert blue["created"] is True and m.design.appearances.count == 2
    # An explicit other name is honoured even when the colour matches.
    named = m.run(ah.appearance_set_color, target="body-1", color="#0000FF", name="Blue")
    assert named["created"] is True and m.body.appearance.name == "Blue"


def test_set_color_with_base_and_name_and_alpha(m: Model) -> None:
    result = m.run(
        ah.appearance_set_color,
        target="face-1",
        color="#00FF0080",
        base="app-paint-red",
        name="Glass green",
    )
    assert m.face.appearance.name == "Glass green"
    assert m.face.appearance.color.opacity == 128
    assert result["color"] == "#00FF0080"
    default = m.run(
        ah.appearance_set_color, target="occ-1", color="#00FF0080", base="app-paint-red"
    )
    assert default["appearance"]["name"] == "MCP Paint - Enamel Glossy (Red) #00FF0080"


def test_set_color_legacy_color_property(m: Model) -> None:
    result = m.run(
        ah.appearance_set_color, target="body-1", color="#102030", base="app-legacy-blue"
    )
    copy = m.body.appearance
    assert not hasattr(copy, "color")
    value = copy.appearanceProperties.itemById("generic_diffuse").value
    assert (value.red, value.green, value.blue) == (16, 32, 48)
    assert result["created"] is True


def test_set_color_no_editable_color_deletes_copy(m: Model) -> None:
    m.library(1).appearances._append(FakeAppearance("app-bare", "Bare", has_color=False))
    error = m.fails(
        ah.appearance_set_color,
        ErrorCode.FUSION_API_ERROR,
        target="body-1",
        color="#FF0000",
        base="app-bare",
    )
    assert "no editable color" in error.message
    assert m.design.appearances.count == 0 and m.body.appearance is None


def test_set_color_needs_base_without_current_appearance(m: Model) -> None:
    error = m.fails(
        ah.appearance_set_color, ErrorCode.INVALID_PARAMS, target="body-1", color="#FF0000"
    )
    assert error.details["param"] == "base"


def test_set_color_invalid_color_changes_nothing(m: Model) -> None:
    error = m.fails(
        ah.appearance_set_color,
        ErrorCode.INVALID_PARAMS,
        target="body-1",
        color="red",
        base="app-paint-red",
    )
    assert error.details["param"] == "color"
    assert m.design.appearances.count == 0


def test_set_color_failure_on_assign_restores(m: Model) -> None:
    satin = m.library(1).appearances.itemById("app-steel-satin")
    m.flaky.appearance = satin
    m.flaky.fail = True
    m.fails(ah.appearance_set_color, ErrorCode.FUSION_API_ERROR, target="flaky-1", color="#FF0000")
    assert m.flaky.appearance is satin
    assert m.design.appearances.count == 0

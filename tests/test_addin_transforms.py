"""``fusion_addin/core/transforms.py`` (c08 D8; tasks 2.3-2.5): move plans, ``apply_move``
on a ``createInput2`` input and the ``matrix_for`` fallback."""

from __future__ import annotations

import math
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core import transforms as t
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import FakeConstructionPoint, fake_box_body
from tests.fakes.fusion_features import FakeFeatureInput, fake_feature_design, make_ctx

adsk = fusion_addin.adsk
IDENTITY = [1.0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


class Setup:
    def __init__(self, units: str = "mm") -> None:
        self.box = fake_box_body(1, 1, 1, name="Box")
        self.design = fake_feature_design(self.box)
        self.root = self.design.rootComponent
        edges = {e.entityToken.rsplit("-e", 1)[1]: e for e in self.box.edges}
        self.x_edge = edges["000100"]
        self.vertex = self.box.vertices.item(7)  # (1, 1, 1) cm
        self.point = FakeConstructionPoint("P", adsk.core.Point3D.create(2, 0, 0))
        self.top = next(f for f in self.box.faces if f.entityToken.endswith("face+z"))
        self.units = units

    def ctx(self) -> Any:
        aliases = {"xe": self.x_edge, "v": self.vertex, "p": self.point, "top": self.top}
        return make_ctx(self.design, {"units": self.units}, aliases)


def apply(spec: dict[str, Any], units: str = "mm") -> FakeFeatureInput:
    s = Setup(units)
    inp = FakeFeatureInput()
    assert t.apply_move(s.ctx(), inp, spec) is True
    return inp


def real(value: Any) -> float:
    assert value.kind == "real", value
    return float(value.value)


def invalid(spec: Any, param: str, units: str = "mm") -> BridgeError:
    with pytest.raises(BridgeError) as info:
        t.move_plan(Setup(units).ctx(), spec)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    assert info.value.details["param"] == param, info.value.details
    return info.value


# --- 2.3 translate, translate_along, rotate ------------------------------------------------


def test_translate_in_cm() -> None:
    inp = apply({"type": "translate", "x": 10, "z": -5})
    x, y, z, design_space = inp.call("defineAsTranslateXYZ")
    assert (real(x), real(y), real(z), design_space) == (1.0, 0.0, -0.5, True)


def test_translate_units_and_expressions() -> None:
    inp = apply({"type": "translate", "x": 1, "y": "d / 2", "design_space": False}, units="in")
    x, y, _, design_space = inp.call("defineAsTranslateXYZ")
    assert real(x) == pytest.approx(2.54)
    assert (y.kind, y.value) == ("string", "d / 2")
    assert design_space is False


def test_translate_along_and_rotate() -> None:
    inp = apply({"type": "translate_along", "entity": "xe", "distance": 25})
    entity, distance = inp.call("defineAsTranslateAlongEntity")
    assert entity.entityToken.endswith("-e000100")
    assert real(distance) == pytest.approx(2.5)
    rotated = apply({"type": "rotate", "axis": "origin:Z", "angle": 90})
    axis, angle = rotated.call("defineAsRotate")
    assert axis.name == "Z"
    assert real(angle) == pytest.approx(math.pi / 2)
    expression = apply({"type": "rotate", "axis": "xe", "angle": "a + 5 deg"})
    assert expression.call("defineAsRotate")[1].value == "a + 5 deg"


def test_translate_and_rotate_validation() -> None:
    invalid({"type": "translate", "x": True}, "transform.x")
    invalid({"type": "translate", "x": 1, "design_space": "yes"}, "transform.design_space")
    invalid({"type": "translate", "w": 1}, "transform")
    invalid({"type": "translate_along", "entity": "top", "distance": 1}, "transform.entity")
    invalid({"type": "translate_along", "entity": "xe"}, "transform.distance")
    invalid({"type": "rotate", "axis": "top", "angle": 5}, "transform.axis")
    invalid({"type": "spin"}, "transform.type")
    invalid("translate", "transform")


def test_apply_move_without_method_returns_false_and_rejection_raises() -> None:
    s = Setup()
    old = FakeFeatureInput()
    old.missing.add("defineAsRotate")
    assert t.apply_move(s.ctx(), old, {"type": "rotate", "axis": "origin:X", "angle": 1}) is False
    assert old.calls == []
    refused = FakeFeatureInput()
    refused.returns["defineAsTranslateXYZ"] = False
    with pytest.raises(BridgeError) as info:
        t.apply_move(s.ctx(), refused, {"type": "translate", "x": 1})
    assert info.value.code is ErrorCode.FUSION_API_ERROR


# --- 2.4 rotate_about, align, matrix -------------------------------------------------------


def test_rotate_about_builds_a_free_move_matrix_in_cm() -> None:
    inp = apply({"type": "rotate_about", "point": [10, 0, 0], "direction": [0, 0, 2], "angle": 90})
    (matrix,) = inp.call("defineAsFreeMove")
    probe = adsk.core.Point3D.create(2, 0, 0)  # 1 cm from the axis through (1, 0, 0) cm
    probe.transformBy(matrix)
    assert probe.asArray() == pytest.approx([1.0, 1.0, 0.0])


def test_rotate_about_validation() -> None:
    base = {"type": "rotate_about", "point": [0, 0, 0], "direction": [0, 0, 1], "angle": 5}
    invalid({**base, "angle": "5 deg"}, "transform.angle")
    invalid({**base, "direction": [0, 0, 0]}, "transform.direction")
    invalid({**base, "point": [0, 0]}, "transform.point")


def test_align_points() -> None:
    inp = apply({"type": "align", "from": "v", "to": "origin:O"})
    source, target = inp.call("defineAsPointToPoint")
    assert source.entityToken.endswith("-v111")
    assert target.name == "O"
    invalid({"type": "align", "from": "top", "to": "p"}, "transform.from")
    invalid({"type": "align", "from": "v"}, "transform.to")


def test_matrix_translation_is_converted() -> None:
    values = list(IDENTITY)
    values[3], values[7], values[11] = 10, 20, -30
    inp = apply({"type": "matrix", "matrix": values})
    (matrix,) = inp.call("defineAsFreeMove")
    assert matrix.translation.asArray() == pytest.approx([1.0, 2.0, -3.0])
    assert matrix.getCell(0, 0) == 1.0


def test_matrix_rotation_is_accepted() -> None:
    c, s_ = math.cos(0.3), math.sin(0.3)
    values = [c, -s_, 0, 0, s_, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    (matrix,) = apply({"type": "matrix", "matrix": values}).call("defineAsFreeMove")
    assert matrix.getCell(1, 0) == pytest.approx(s_)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({0: 2.0}, "not_orthonormal"),
        ({0: -1.0}, "mirror"),
        ({12: 1.0}, "projective"),
    ],
)
def test_matrix_must_be_rigid(change: dict[int, float], reason: str) -> None:
    values = list(IDENTITY)
    for index, value in change.items():
        values[index] = value
    err = invalid({"type": "matrix", "matrix": values}, "transform.matrix")
    assert err.details["reason"] == reason


def test_matrix_needs_sixteen_numbers() -> None:
    invalid({"type": "matrix", "matrix": IDENTITY[:15]}, "transform.matrix")
    invalid({"type": "matrix", "matrix": ["1", *IDENTITY[1:]]}, "transform.matrix")


# --- 2.5 matrix_for fallback ---------------------------------------------------------------


def test_fallback_translate_with_numbers() -> None:
    s = Setup()
    matrix = t.matrix_for(s.ctx(), {"type": "translate", "x": 10, "z": 50})
    assert matrix.translation.asArray() == pytest.approx([1.0, 0.0, 5.0])


def test_fallback_align_uses_point_geometry() -> None:
    s = Setup()
    matrix = t.matrix_for(s.ctx(), {"type": "align", "from": "v", "to": "p"})
    assert matrix.translation.asArray() == pytest.approx([1.0, -1.0, -1.0])


def test_fallback_rotate_about_and_matrix_reuse_the_plan() -> None:
    s = Setup()
    spec = {"type": "rotate_about", "point": [0, 0, 0], "direction": [0, 0, 1], "angle": 180}
    matrix = t.matrix_for(s.ctx(), spec)
    assert matrix.getCell(0, 0) == pytest.approx(-1.0)
    plain = t.matrix_for(s.ctx(), {"type": "matrix", "matrix": IDENTITY})
    assert plain.asArray() == IDENTITY


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "translate", "x": "d"},
        {"type": "translate", "x": 1, "design_space": False},
        {"type": "translate_along", "entity": "xe", "distance": 1},
        {"type": "rotate", "axis": "origin:Z", "angle": 10},
    ],
)
def test_fallback_not_available(spec: dict[str, Any]) -> None:
    with pytest.raises(BridgeError) as info:
        t.matrix_for(Setup().ctx(), spec)
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert "not available" in info.value.message


def test_plan_is_reused_by_apply_and_fallback() -> None:
    s = Setup()
    plan = t.move_plan(s.ctx(), {"type": "translate", "y": 10})
    inp = FakeFeatureInput()
    assert t.apply_move(s.ctx(), inp, plan) is True
    assert real(inp.call("defineAsTranslateXYZ")[1]) == 1.0
    assert t.matrix_for(s.ctx(), plan).translation.asArray() == pytest.approx([0, 1.0, 0])

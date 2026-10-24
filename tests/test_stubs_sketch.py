"""``adsk`` stubs used by sketch code (c05 2.1): ``Matrix3D``, ``ObjectCollection``,
``ValueInput`` and the sketch enums."""

import math

import pytest

import fusion_addin

adsk = fusion_addin.adsk
P = adsk.core.Point3D.create
V = adsk.core.Vector3D.create
M = adsk.core.Matrix3D


def xyz(p: object) -> tuple[float, float, float]:
    return (round(p.x, 9), round(p.y, 9), round(p.z, 9))  # type: ignore[attr-defined]


def test_matrix_create_is_identity() -> None:
    m = M.create()
    assert m.asArray() == [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    assert m.determinant == 1.0
    p = P(1, 2, 3)
    assert p.transformBy(m)
    assert xyz(p) == (1, 2, 3)


def test_rotation_about_z_through_a_point() -> None:
    m = M.create()
    assert m.setToRotation(math.pi / 2, V(0, 0, 1), P(1, 0, 0))
    p = P(2, 0, 5)
    p.transformBy(m)
    # (2,0) around (1,0) by +90 deg -> (1,1); z untouched.
    assert xyz(p) == (1, 1, 5)
    assert m.determinant == pytest.approx(1.0)
    # The origin of the rotation is fixed.
    o = P(1, 0, 0)
    o.transformBy(m)
    assert xyz(o) == (1, 0, 0)


def test_rotation_axis_need_not_be_unit_and_zero_axis_fails() -> None:
    m = M.create()
    assert m.setToRotation(math.pi, V(0, 0, 7), P(0, 0, 0))
    p = P(1, 2, 0)
    p.transformBy(m)
    assert xyz(p) == (-1, -2, 0)
    before = m.asArray()
    assert m.setToRotation(1.0, V(0, 0, 0), P(0, 0, 0)) is False
    assert m.asArray() == before


def test_translation_property_and_vectors_ignore_it() -> None:
    m = M.create()
    m.translation = V(3, -1, 2)
    assert m.translation.asArray() == [3, -1, 2]
    assert m.asArray()[3] == 3 and m.asArray()[7] == -1 and m.asArray()[11] == 2
    p = P(1, 1, 1)
    p.transformBy(m)
    assert xyz(p) == (4, 0, 3)
    v = V(1, 1, 1)
    v.transformBy(m)
    assert v.asArray() == [1, 1, 1]


def test_rotation_then_translation_and_invert() -> None:
    rot = M.create()
    rot.setToRotation(math.pi / 2, V(0, 0, 1), P(0, 0, 0))
    move = M.create()
    move.translation = V(10, 0, 0)
    m = rot.copy()
    assert m.transformBy(move)  # first rotate, then translate
    p = P(1, 0, 0)
    p.transformBy(m)
    assert xyz(p) == (10, 1, 0)

    inverse = m.copy()
    assert inverse.invert()
    q = P(10, 1, 0)
    q.transformBy(inverse)
    assert xyz(q) == (1, 0, 0)
    both = m.copy()
    both.transformBy(inverse)
    assert both.isEqualTo(M.create())
    # copy is independent of the original.
    assert not m.isEqualTo(inverse)


def test_invert_singular_returns_false_unchanged() -> None:
    m = M.create()
    m.setCell(0, 0, 0.0)
    before = m.asArray()
    assert m.determinant == 0.0
    assert m.invert() is False
    assert m.asArray() == before


def test_coordinate_system_round_trip() -> None:
    m = M.create()
    # Fusion's XZ sketch: x = X, y = -Z, normal = +Y.
    m.setWithCoordinateSystem(P(1, 2, 3), V(1, 0, 0), V(0, 0, -1), V(0, 1, 0))
    origin, x_axis, y_axis, z_axis = m.getAsCoordinateSystem()
    assert xyz(origin) == (1, 2, 3)
    assert x_axis.asArray() == [1, 0, 0]
    assert y_axis.asArray() == [0, 0, -1]
    assert z_axis.asArray() == [0, 1, 0]
    p = P(0, 5, 0)  # sketch y = 5 -> model z = -5 (+ origin)
    p.transformBy(m)
    assert xyz(p) == (1, 2, -2)


def test_align_coordinate_systems() -> None:
    m = M.create()
    ok = m.setToAlignCoordinateSystems(
        P(0, 0, 0), V(1, 0, 0), V(0, 1, 0), V(0, 0, 1),
        P(5, 0, 0), V(0, 1, 0), V(-1, 0, 0), V(0, 0, 1),
    )  # fmt: skip
    assert ok
    p = P(1, 0, 0)
    p.transformBy(m)
    assert xyz(p) == (5, 1, 0)


def test_set_with_array_and_cells() -> None:
    m = M.create()
    assert m.setWithArray(range(16))
    assert m.getCell(1, 2) == 6.0
    assert m.setWithArray([1, 2, 3]) is False
    assert m.setToIdentity()
    assert m.isEqualTo(M.create())


def test_vector_and_point_helpers() -> None:
    a, b = V(1, 0, 0), V(0, 2, 0)
    assert a.dotProduct(b) == 0.0
    assert a.crossProduct(b).asArray() == [0, 0, 2]
    assert a.angleTo(b) == pytest.approx(math.pi / 2)
    p, q = P(0, 0, 0), P(3, 4, 0)
    assert p.distanceTo(q) == 5.0
    assert p.vectorTo(q).asArray() == [3, 4, 0]
    assert p.translateBy(V(3, 4, 0)) and p.isEqualTo(q)


def test_object_collection() -> None:
    one, two = object(), object()
    coll = adsk.core.ObjectCollection.create()
    assert coll.count == 0
    assert coll.add(one) and coll.add(two)
    assert coll.count == 2 and len(coll) == 2
    assert coll.item(0) is one and coll.item(1) is two and coll.item(5) is None
    assert list(coll) == [one, two]
    assert coll.find(two) == 1 and coll.contains(one)
    assert coll.find(object()) == -1
    assert coll.removeByItem(one)
    assert coll.asArray() == [two]
    assert coll.removeByIndex(3) is False
    assert coll.clear() and coll.count == 0
    built = adsk.core.ObjectCollection.createWithArray([one, two])
    assert built.asArray() == [one, two]


def test_value_input() -> None:
    real = adsk.core.ValueInput.createByReal(2.5)
    text = adsk.core.ValueInput.createByString("25 mm")
    assert (real.kind, real.value, real.realValue, real.stringValue) == ("real", 2.5, 2.5, "")
    assert (text.kind, text.value, text.stringValue, text.realValue) == (
        "string",
        "25 mm",
        "25 mm",
        0.0,
    )


def test_sketch_enums_with_fusion_values() -> None:
    fusion, core = adsk.fusion, adsk.core
    assert fusion.DimensionOrientations.AlignedDimensionOrientation == 0
    assert fusion.DimensionOrientations.HorizontalDimensionOrientation == 1
    assert fusion.DimensionOrientations.VerticalDimensionOrientation == 2
    assert fusion.PatternDistanceType.ExtentPatternDistanceType == 0
    assert fusion.PatternDistanceType.SpacingPatternDistanceType == 1
    assert core.HorizontalAlignments.LeftHorizontalAlignment == 0
    assert core.HorizontalAlignments.CenterHorizontalAlignment == 1
    assert core.HorizontalAlignments.RightHorizontalAlignment == 2
    assert core.VerticalAlignments.TopVerticalAlignment == 0
    assert core.VerticalAlignments.MiddleVerticalAlignment == 1
    assert core.VerticalAlignments.BottomVerticalAlignment == 2


def test_c03_factories_still_in_place() -> None:
    assert isinstance(P(1, 2, 3), adsk.core.Point3D)
    assert isinstance(V(1, 2, 3), adsk.core.Vector3D)
    assert adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy == 1

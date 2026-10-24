"""``adsk`` stubs added for patterns and body operations (c08 D15, task 1.4)."""

from __future__ import annotations

import math

import fusion_addin

adsk = fusion_addin.adsk
F = adsk.fusion
C = adsk.core


def test_new_enums_import_with_distinct_values() -> None:
    expected = {
        F.PatternComputeOptions: (
            "OptimizedPatternCompute",
            "IdenticalPatternCompute",
            "AdjustPatternCompute",
        ),
        F.PointContainment: (
            "PointInsidePointContainment",
            "PointOnPointContainment",
            "PointOutsidePointContainment",
            "UnknownPointContainment",
        ),
    }
    for enum, names in expected.items():
        values = [getattr(enum, name) for name in names]
        assert all(isinstance(v, int) for v in values)
        assert len(set(values)) == len(values), enum


def test_c05_pattern_distance_type_is_kept() -> None:
    assert F.PatternDistanceType.ExtentPatternDistanceType == 0
    assert F.PatternDistanceType.SpacingPatternDistanceType == 1


def test_matrix_set_with_array_round_trips() -> None:
    values = [1.0, 0, 0, 2, 0, 1, 0, 3, 0, 0, 1, 4, 0, 0, 0, 1]
    matrix = C.Matrix3D.create()
    assert matrix.setWithArray(values) is True
    assert matrix.asArray() == values
    assert matrix.translation.asArray() == [2.0, 3.0, 4.0]
    assert matrix.setWithArray(values[:15]) is False
    assert matrix.asArray() == values


def test_matrix_rotation_is_kept() -> None:
    matrix = C.Matrix3D.create()
    assert matrix.setToRotation(math.pi / 2, C.Vector3D.create(0, 0, 1), C.Point3D.create())
    p = C.Point3D.create(1, 0, 0)
    p.transformBy(matrix)
    assert abs(p.x) < 1e-12 and abs(p.y - 1) < 1e-12


def test_vector_additions() -> None:
    v = C.Vector3D.create()
    assert v.setWithArray([1, 2, 3]) is True
    assert v.asArray() == [1.0, 2.0, 3.0]
    assert v.setWithArray([1, 2]) is False
    assert C.Vector3D.create(0, 0, 2).isParallelTo(C.Vector3D.create(0, 0, -1))
    assert not C.Vector3D.create(1, 0, 0).isParallelTo(C.Vector3D.create(0, 1, 0))
    assert C.Vector3D.create(1, 0, 0).isPerpendicularTo(C.Vector3D.create(0, 3, 0))
    assert not C.Vector3D.create(1, 1, 0).isPerpendicularTo(C.Vector3D.create(1, 0, 0))

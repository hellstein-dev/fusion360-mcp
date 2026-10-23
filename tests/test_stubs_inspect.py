"""``adsk`` stubs used by inspection code (c03 1.4, D13): value types and enums."""

import fusion_addin

adsk = fusion_addin.adsk


def test_enums_import_with_fusion_values() -> None:
    core, fusion = adsk.core, adsk.fusion
    assert core.SurfaceTypes.PlaneSurfaceType == 0
    assert core.SurfaceTypes.NurbsSurfaceType == 7
    assert core.Curve3DTypes.Line3DCurveType == 0
    assert core.Curve3DTypes.NurbsCurve3DCurveType == 6
    assert core.ViewOrientations.IsoTopRightViewOrientation == 7
    assert core.ViewOrientations.TopViewOrientation == 10
    assert core.VisualStyles.ShadedWithVisibleEdgesOnlyVisualStyle == 2
    assert core.VisualStyles.WireframeVisualStyle == 3
    assert fusion.CalculationAccuracy.HighCalculationAccuracy == 2
    assert fusion.CalculationAccuracy.VeryHighCalculationAccuracy == 3
    assert fusion.FeatureHealthStates.HealthyFeatureHealthState == 0
    assert fusion.FeatureHealthStates.ErrorFeatureHealthState == 2


def test_enum_values_are_distinct() -> None:
    for enum in (
        adsk.core.SurfaceTypes,
        adsk.core.Curve3DTypes,
        adsk.core.ViewOrientations,
        adsk.core.VisualStyles,
        adsk.fusion.CalculationAccuracy,
        adsk.fusion.FeatureHealthStates,
    ):
        values = [v for k, v in vars(enum).items() if not k.startswith("_")]
        assert len(values) == len(set(values))


def test_point3d_create_and_transform() -> None:
    p = adsk.core.Point3D.create(1, 2, 3)
    assert (p.x, p.y, p.z) == (1.0, 2.0, 3.0)

    class Shift:
        def asArray(self) -> list[float]:
            return [0, -1, 0, 10, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

    q = p.copy()
    assert q.transformBy(Shift())
    assert (q.x, q.y, q.z) == (8.0, 1.0, 3.0)
    assert (p.x, p.y, p.z) == (1.0, 2.0, 3.0)


def test_vector3d_create_and_normalize() -> None:
    v = adsk.core.Vector3D.create(0, 3, 4)
    assert v.length == 5.0
    n = v.copy()
    assert n.normalize()
    assert n.asArray() == [0.0, 0.6, 0.8]
    assert not adsk.core.Vector3D.create().normalize()

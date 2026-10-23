"""Stub of ``adsk.core``: ``Application.get()``, event handler base classes, ``ValueInput``,
value types ``Point3D``/``Vector3D`` and the enums the add-in reads."""

from . import Unavailable


class Application:
    @staticmethod
    def get() -> Unavailable:
        return Unavailable("adsk.core.Application")


class _EventHandler:
    """Base of Fusion's event handler classes; the add-in subclasses them and overrides
    ``notify``."""

    def __init__(self) -> None:
        pass

    def notify(self, args: object) -> None:
        raise NotImplementedError


class CustomEventHandler(_EventHandler):
    pass


class CommandCreatedEventHandler(_EventHandler):
    pass


class CommandEventHandler(_EventHandler):
    pass


class ValueInput:
    """Records how it was built: ``kind`` is ``"real"`` or ``"string"``."""

    def __init__(self, kind: str, value: object) -> None:
        self.kind = kind
        self.value = value

    @staticmethod
    def createByReal(value: float) -> "ValueInput":
        return ValueInput("real", value)

    @staticmethod
    def createByString(value: str) -> "ValueInput":
        return ValueInput("string", value)


class Point3D:
    """Plain value point (cm), enough for add-in code that builds or transforms points."""

    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    @staticmethod
    def create(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> "Point3D":
        return Point3D(x, y, z)

    def copy(self) -> "Point3D":
        return Point3D(self.x, self.y, self.z)

    def asArray(self) -> list[float]:
        return [self.x, self.y, self.z]

    def transformBy(self, matrix: object) -> bool:
        """``matrix.asArray()``: 16 numbers, row-major, translation in column 3."""
        m = matrix.asArray()  # type: ignore[attr-defined]
        x, y, z = self.x, self.y, self.z
        self.x = m[0] * x + m[1] * y + m[2] * z + m[3]
        self.y = m[4] * x + m[5] * y + m[6] * z + m[7]
        self.z = m[8] * x + m[9] * y + m[10] * z + m[11]
        return True

    def __repr__(self) -> str:
        return f"Point3D({self.x}, {self.y}, {self.z})"


class Vector3D:
    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0) -> None:
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    @staticmethod
    def create(x: float = 0.0, y: float = 0.0, z: float = 0.0) -> "Vector3D":
        return Vector3D(x, y, z)

    @property
    def length(self) -> float:
        return float((self.x**2 + self.y**2 + self.z**2) ** 0.5)

    def copy(self) -> "Vector3D":
        return Vector3D(self.x, self.y, self.z)

    def normalize(self) -> bool:
        length = self.length
        if length == 0.0:
            return False
        self.x, self.y, self.z = self.x / length, self.y / length, self.z / length
        return True

    def asArray(self) -> list[float]:
        return [self.x, self.y, self.z]

    def __repr__(self) -> str:
        return f"Vector3D({self.x}, {self.y}, {self.z})"


# Enum values match Fusion's adsk.core.
class DocumentTypes:
    FusionDesignDocumentType = 0
    FusionDrawingDocumentType = 1
    FusionElectronicsDocumentType = 2
    FusionCAMDocumentType = 3
    FusionBoardDocumentType = 4
    FusionSchematicDocumentType = 5


class SurfaceTypes:
    PlaneSurfaceType = 0
    CylinderSurfaceType = 1
    ConeSurfaceType = 2
    SphereSurfaceType = 3
    TorusSurfaceType = 4
    EllipticalCylinderSurfaceType = 5
    EllipticalConeSurfaceType = 6
    NurbsSurfaceType = 7


class Curve3DTypes:
    Line3DCurveType = 0
    Arc3DCurveType = 1
    Circle3DCurveType = 2
    Ellipse3DCurveType = 3
    EllipticalArc3DCurveType = 4
    InfiniteLine3DCurveType = 5
    NurbsCurve3DCurveType = 6


class ViewOrientations:
    ArbitraryViewOrientation = 0
    BackViewOrientation = 1
    BottomViewOrientation = 2
    FrontViewOrientation = 3
    IsoBottomLeftViewOrientation = 4
    IsoBottomRightViewOrientation = 5
    IsoTopLeftViewOrientation = 6
    IsoTopRightViewOrientation = 7
    LeftViewOrientation = 8
    RightViewOrientation = 9
    TopViewOrientation = 10


class VisualStyles:
    ShadedVisualStyle = 0
    ShadedWithHiddenEdgesVisualStyle = 1
    ShadedWithVisibleEdgesOnlyVisualStyle = 2
    WireframeVisualStyle = 3
    WireframeWithHiddenEdgesVisualStyle = 4
    WireframeWithVisibleEdgesOnlyVisualStyle = 5

"""Stub of ``adsk.core``: ``Application.get()``, event handler base classes, ``ValueInput``,
value types ``Point3D``/``Vector3D``/``Matrix3D``, ``ObjectCollection`` and the enums the
add-in reads."""

import math
from collections.abc import Iterable, Iterator
from typing import Any

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

    @property
    def realValue(self) -> float:
        return float(self.value) if self.kind == "real" else 0.0  # type: ignore[arg-type]

    @property
    def stringValue(self) -> str:
        return str(self.value) if self.kind == "string" else ""


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

    def distanceTo(self, point: "Point3D") -> float:
        return math.dist(self.asArray(), point.asArray())

    def vectorTo(self, point: "Point3D") -> "Vector3D":
        return Vector3D(point.x - self.x, point.y - self.y, point.z - self.z)

    def translateBy(self, vector: "Vector3D") -> bool:
        self.x += vector.x
        self.y += vector.y
        self.z += vector.z
        return True

    def isEqualTo(self, point: "Point3D") -> bool:
        return self.distanceTo(point) < 1e-9

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

    def dotProduct(self, vector: "Vector3D") -> float:
        return self.x * vector.x + self.y * vector.y + self.z * vector.z

    def crossProduct(self, vector: "Vector3D") -> "Vector3D":
        return Vector3D(
            self.y * vector.z - self.z * vector.y,
            self.z * vector.x - self.x * vector.z,
            self.x * vector.y - self.y * vector.x,
        )

    def angleTo(self, vector: "Vector3D") -> float:
        lengths = self.length * vector.length
        if lengths == 0.0:
            return 0.0
        return math.acos(max(-1.0, min(1.0, self.dotProduct(vector) / lengths)))

    def scaleBy(self, scale: float) -> bool:
        self.x, self.y, self.z = self.x * scale, self.y * scale, self.z * scale
        return True

    def add(self, vector: "Vector3D") -> bool:
        self.x, self.y, self.z = self.x + vector.x, self.y + vector.y, self.z + vector.z
        return True

    def subtract(self, vector: "Vector3D") -> bool:
        self.x, self.y, self.z = self.x - vector.x, self.y - vector.y, self.z - vector.z
        return True

    def transformBy(self, matrix: object) -> bool:
        """Rotation/scale part of ``matrix`` only: vectors are not translated."""
        m = matrix.asArray()  # type: ignore[attr-defined]
        x, y, z = self.x, self.y, self.z
        self.x = m[0] * x + m[1] * y + m[2] * z
        self.y = m[4] * x + m[5] * y + m[6] * z
        self.z = m[8] * x + m[9] * y + m[10] * z
        return True

    def isEqualTo(self, vector: "Vector3D") -> bool:
        return math.dist(self.asArray(), vector.asArray()) < 1e-9

    def setWithArray(self, coordinates: Iterable[float]) -> bool:
        values = [float(v) for v in coordinates]
        if len(values) != 3:
            return False
        self.x, self.y, self.z = values
        return True

    def isParallelTo(self, vector: "Vector3D") -> bool:
        return self.crossProduct(vector).length < 1e-9 * max(1.0, self.length * vector.length)

    def isPerpendicularTo(self, vector: "Vector3D") -> bool:
        return abs(self.dotProduct(vector)) < 1e-9 * max(1.0, self.length * vector.length)

    def __repr__(self) -> str:
        return f"Vector3D({self.x}, {self.y}, {self.z})"


def _identity() -> list[list[float]]:
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def _rows(matrix: object) -> list[list[float]]:
    """4x4 cells of any object with Fusion's ``asArray()`` (16 numbers, row-major)."""
    values = [float(v) for v in matrix.asArray()]  # type: ignore[attr-defined]
    return [values[i * 4 : (i + 1) * 4] for i in range(4)]


def _multiply(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _pivot(rows: list[list[float]], col: int) -> int:
    """Row at or below ``col`` with the largest magnitude in column ``col``."""
    best = col
    for row in range(col + 1, 4):
        if abs(rows[row][col]) > abs(rows[best][col]):
            best = row
    return best


class Matrix3D:
    """4x4 transform like Fusion's ``Matrix3D`` (cm, radians).

    Cells are row-major and act on column vectors, so the translation is column 3
    (``asArray()[3], [7], [11]``) and the columns of the 3x3 part are the axes of the
    coordinate system the matrix maps into. ``a.transformBy(b)`` makes ``a`` "first ``a``,
    then ``b``" (``a = b * a``).
    """

    def __init__(self, cells: list[list[float]] | None = None) -> None:
        self._m = [[float(v) for v in row] for row in cells] if cells is not None else _identity()

    @staticmethod
    def create() -> "Matrix3D":
        return Matrix3D()

    def copy(self) -> "Matrix3D":
        return Matrix3D(self._m)

    def asArray(self) -> list[float]:
        return [v for row in self._m for v in row]

    def setWithArray(self, cellValues: Iterable[float]) -> bool:
        values = [float(v) for v in cellValues]
        if len(values) != 16:
            return False
        self._m = [values[i * 4 : (i + 1) * 4] for i in range(4)]
        return True

    def getCell(self, row: int, column: int) -> float:
        return self._m[row][column]

    def setCell(self, row: int, column: int, value: float) -> bool:
        self._m[row][column] = float(value)
        return True

    def setToIdentity(self) -> bool:
        self._m = _identity()
        return True

    @property
    def translation(self) -> Vector3D:
        return Vector3D(self._m[0][3], self._m[1][3], self._m[2][3])

    @translation.setter
    def translation(self, value: Vector3D) -> None:
        self._m[0][3], self._m[1][3], self._m[2][3] = value.x, value.y, value.z

    def setToRotation(self, angle: float, axis: Vector3D, origin: Point3D) -> bool:
        """Rotation by ``angle`` (radians, right-hand rule) about ``axis`` through ``origin``."""
        length = axis.length
        if length == 0.0:
            return False
        x, y, z = axis.x / length, axis.y / length, axis.z / length
        c, s = math.cos(angle), math.sin(angle)
        t = 1.0 - c
        r = [
            [t * x * x + c, t * x * y - s * z, t * x * z + s * y],
            [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
            [t * x * z - s * y, t * y * z + s * x, t * z * z + c],
        ]
        o = (origin.x, origin.y, origin.z)
        shift = [o[i] - sum(r[i][j] * o[j] for j in range(3)) for i in range(3)]
        self._m = [[*r[i], shift[i]] for i in range(3)] + [[0.0, 0.0, 0.0, 1.0]]
        return True

    def transformBy(self, matrix: object) -> bool:
        self._m = _multiply(_rows(matrix), self._m)
        return True

    def invert(self) -> bool:
        """Gauss-Jordan with partial pivoting; ``False`` (unchanged) if singular."""
        a = [row[:] + [1.0 if i == j else 0.0 for j in range(4)] for i, row in enumerate(self._m)]
        for col in range(4):
            pivot = _pivot(a, col)
            if abs(a[pivot][col]) < 1e-12:
                return False
            a[col], a[pivot] = a[pivot], a[col]
            scale = a[col][col]
            a[col] = [v / scale for v in a[col]]
            for r in range(4):
                if r != col and a[r][col] != 0.0:
                    factor = a[r][col]
                    a[r] = [v - factor * p for v, p in zip(a[r], a[col], strict=True)]
        self._m = [row[4:] for row in a]
        return True

    @property
    def determinant(self) -> float:
        a = [row[:] for row in self._m]
        det = 1.0
        for col in range(4):
            pivot = _pivot(a, col)
            if abs(a[pivot][col]) < 1e-15:
                return 0.0
            if pivot != col:
                a[col], a[pivot] = a[pivot], a[col]
                det = -det
            det *= a[col][col]
            for r in range(col + 1, 4):
                factor = a[r][col] / a[col][col]
                a[r] = [v - factor * p for v, p in zip(a[r], a[col], strict=True)]
        return det

    def getAsCoordinateSystem(self) -> tuple[Point3D, Vector3D, Vector3D, Vector3D]:
        """``(origin, xAxis, yAxis, zAxis)`` of the coordinate system the matrix maps into."""
        m = self._m
        return (
            Point3D(m[0][3], m[1][3], m[2][3]),
            Vector3D(m[0][0], m[1][0], m[2][0]),
            Vector3D(m[0][1], m[1][1], m[2][1]),
            Vector3D(m[0][2], m[1][2], m[2][2]),
        )

    def setWithCoordinateSystem(
        self, origin: Point3D, xAxis: Vector3D, yAxis: Vector3D, zAxis: Vector3D
    ) -> bool:
        self._m = [
            [xAxis.x, yAxis.x, zAxis.x, origin.x],
            [xAxis.y, yAxis.y, zAxis.y, origin.y],
            [xAxis.z, yAxis.z, zAxis.z, origin.z],
            [0.0, 0.0, 0.0, 1.0],
        ]
        return True

    def setToAlignCoordinateSystems(
        self,
        fromOrigin: Point3D,
        fromXAxis: Vector3D,
        fromYAxis: Vector3D,
        fromZAxis: Vector3D,
        toOrigin: Point3D,
        toXAxis: Vector3D,
        toYAxis: Vector3D,
        toZAxis: Vector3D,
    ) -> bool:
        source = Matrix3D()
        source.setWithCoordinateSystem(fromOrigin, fromXAxis, fromYAxis, fromZAxis)
        if not source.invert():
            return False
        target = Matrix3D()
        target.setWithCoordinateSystem(toOrigin, toXAxis, toYAxis, toZAxis)
        self._m = _multiply(target._m, source._m)
        return True

    def isEqualTo(self, matrix: object) -> bool:
        other = _rows(matrix)
        return all(abs(self._m[i][j] - other[i][j]) < 1e-9 for i in range(4) for j in range(4))

    def __repr__(self) -> str:
        return f"Matrix3D({self._m})"


class ObjectCollection:
    """Ordered list of API objects (``ObjectCollection.create()``), iterable like in Fusion."""

    def __init__(self, items: Iterable[Any] = ()) -> None:
        self._items: list[Any] = list(items)

    @staticmethod
    def create() -> "ObjectCollection":
        return ObjectCollection()

    @staticmethod
    def createWithArray(array: Iterable[Any]) -> "ObjectCollection":
        return ObjectCollection(array)

    @property
    def count(self) -> int:
        return len(self._items)

    def item(self, index: int) -> Any:
        return self._items[index] if 0 <= index < len(self._items) else None

    def add(self, item: Any) -> bool:
        self._items.append(item)
        return True

    def find(self, item: Any, startIndex: int = 0) -> int:
        for index in range(startIndex, len(self._items)):
            if self._items[index] is item:
                return index
        return -1

    def contains(self, item: Any) -> bool:
        return self.find(item) >= 0

    def removeByIndex(self, index: int) -> bool:
        if not 0 <= index < len(self._items):
            return False
        del self._items[index]
        return True

    def removeByItem(self, item: Any) -> bool:
        return self.removeByIndex(self.find(item))

    def clear(self) -> bool:
        self._items.clear()
        return True

    def asArray(self) -> list[Any]:
        return list(self._items)

    def __iter__(self) -> Iterator[Any]:
        return iter(list(self._items))

    def __len__(self) -> int:
        return len(self._items)


class Color:
    """``adsk.core.Color``: RGBA channels 0..255 (``opacity`` is the alpha)."""

    def __init__(self, red: int, green: int, blue: int, opacity: int = 255) -> None:
        self.red = red
        self.green = green
        self.blue = blue
        self.opacity = opacity

    @staticmethod
    def create(red: int, green: int, blue: int, opacity: int) -> "Color":
        return Color(red, green, blue, opacity)

    def getColor(self) -> tuple[bool, int, int, int, int]:
        return True, self.red, self.green, self.blue, self.opacity


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


class HorizontalAlignments:
    LeftHorizontalAlignment = 0
    CenterHorizontalAlignment = 1
    RightHorizontalAlignment = 2


class VerticalAlignments:
    TopVerticalAlignment = 0
    MiddleVerticalAlignment = 1
    BottomVerticalAlignment = 2


class VisualStyles:
    ShadedVisualStyle = 0
    ShadedWithHiddenEdgesVisualStyle = 1
    ShadedWithVisibleEdgesOnlyVisualStyle = 2
    WireframeVisualStyle = 3
    WireframeWithHiddenEdgesVisualStyle = 4
    WireframeWithVisibleEdgesOnlyVisualStyle = 5

"""Fake Fusion sketch API for the c05 handler tests (design D13).

There is one sketch class: ``tests.fakes.brep.FakeSketch`` (c03) mixes in ``SketchApiMixin``
from this module. Sketch-entity ``geometry`` is sketch space, cm, z = 0; ``worldGeometry`` is
model space through ``Sketch.transform`` (a stub ``Matrix3D``, sketch -> model).

- ``sketchPoints``, ``sketchCurves.sketchLines|sketchCircles|sketchArcs|sketchEllipses|
  sketchFittedSplines``, ``sketchTexts``, ``geometricConstraints``, ``sketchDimensions``
  create objects with ``entityToken`` (``"<sketch token>/<Type>-<n>"``: never a valid local
  id), ``objectType`` (``"adsk::fusion::SketchLine"``...), ``isValid`` and ``deleteMe``.
  Deleting cascades like Fusion: constraints/dimensions referencing the object go too, a
  curve takes its end points that nothing else uses, a point takes the curves built on it.
- An existing ``FakeSketchPoint`` passed to ``add*`` is reused (shared point).
- Every API call is appended to ``sketch.calls`` as ``(name, args)``, e.g.
  ``("sketchLines.addByTwoPoints", (p1, p2))``, ``("geometricConstraints.addTangent", ...)``,
  ``("Sketch.offset", ...)``, ``("SketchLine.trim", ...)``, ``("deleteMe", (entity,))``.
  ``sketch.fail_on(name, n=1, exc=None)`` raises ``exc`` (default ``RuntimeError``) on the
  n-th matching call counted from now; ``name`` is a full name, a bare method name (matches
  any collection) or ``"*"`` (any call). Nothing is created by a failing call.
- The fake solver does not move geometry: constraints and dimensions are recorded only.
  ``sketch.solver = simple_solver`` applies driving radial/diameter/ellipse/linear
  dimensions; any callable ``(sketch, created_or_changed_object)`` works.
- Dimension parameters are ``FakeDimensionParameter`` (a ``FakeModelParameter``) in the
  design's parameter set when ``attach_parameters`` was used, so expressions can name user
  parameters and other dimensions.
- Profiles are explicit (``add_profile``, ``sketch.set_profiles``) or recomputed on change
  with ``sketch.profile_builder = auto_profiles`` (closed curves and simple cycles of
  lines/arcs/open splines, nesting by containment; crossing curves are not split).
- Optional API: ``sketch.hide(name)`` / ``geometricConstraints.hide(...)`` /
  ``remove_optional_api(sketch)`` make attributes disappear (``hasattr`` is False) to test
  fallbacks for an older Fusion.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

import fusion_addin

from .design import FakeCollection
from .parameters import FakeModelParameter, FakeParameterSet, attach_parameters, unit_scale

adsk = fusion_addin.adsk
P = adsk.core.Point3D.create
V = adsk.core.Vector3D.create
CT = adsk.core.Curve3DTypes
TAU = 2 * math.pi
EPS = 1e-9
CUT_EPS = 1e-7
Vec = tuple[float, float, float]
XY = tuple[float, float]

_ids = itertools.count(1)

POINT = ("SketchPoint",)
LINE = ("SketchLine",)
CIRCULAR = ("SketchCircle", "SketchArc")
CURVES = (
    "SketchLine",
    "SketchCircle",
    "SketchArc",
    "SketchEllipse",
    "SketchEllipticalArc",
    "SketchFittedSpline",
    "SketchFixedSpline",
    "SketchControlPointSpline",
    "SketchConicCurve",
)
OPTIONAL_CONSTRAINT_METHODS = (
    "createOffsetInput",
    "addOffset2",
    "createRectangularPatternInput",
    "addRectangularPattern",
    "createCircularPatternInput",
    "addCircularPattern",
)


# --- small vector helpers (sketch space, tuples) ------------------------------------------


def _v(p: Any) -> Vec:
    """``(x, y, z)`` of a ``Point3D``/``Vector3D``, a ``FakeSketchPoint`` or a tuple."""
    if isinstance(p, FakeSketchPoint):
        return p._xyz()
    if isinstance(p, tuple | list):
        x, y, *rest = p
        return (float(x), float(y), float(rest[0]) if rest else 0.0)
    if p is None or not hasattr(p, "x"):
        raise RuntimeError(f"expected a point, got {type(p).__name__}")
    return (float(p.x), float(p.y), float(getattr(p, "z", 0.0)))


def _same(p: Vec) -> Vec:
    return p


def _add(a: Vec, b: Vec) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec, b: Vec) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a: Vec, s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def _lerp(a: Vec, b: Vec, t: float) -> Vec:
    return _add(a, _mul(_sub(b, a), t))


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross2(a: Vec, b: Vec) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _cross3(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _len(a: Vec) -> float:
    return math.sqrt(_dot(a, a))


def _dist(a: Vec, b: Vec) -> float:
    return _len(_sub(a, b))


def _unit(a: Vec) -> Vec:
    length = _len(a)
    if length < EPS:
        raise RuntimeError("zero-length direction")
    return _mul(a, 1.0 / length)


def _perp(a: Vec) -> Vec:
    return (-a[1], a[0], 0.0)


def _angle(center: Vec, p: Vec) -> float:
    return math.atan2(p[1] - center[1], p[0] - center[0]) % TAU


def _polar(center: Vec, radius: float, theta: float) -> Vec:
    return (center[0] + radius * math.cos(theta), center[1] + radius * math.sin(theta), center[2])


def _apply(matrix: Any, p: Vec) -> Vec:
    q = P(*p)
    q.transformBy(matrix)
    return (q.x, q.y, q.z)


def _apply_vec(matrix: Any, v: Vec) -> Vec:
    w = V(*v)
    w.transformBy(matrix)
    return (w.x, w.y, w.z)


def _first(item: tuple[float, Any]) -> float:
    return item[0]


def _items(value: Any) -> list[Any]:
    """Entities of an ``ObjectCollection``/list/collection, or ``[value]`` for one entity."""
    if value is None:
        return []
    if isinstance(value, _SketchObject) or not hasattr(value, "__iter__"):
        return [value]
    return list(value)


def _kind(obj: Any) -> str:
    object_type = getattr(obj, "objectType", None)
    if isinstance(object_type, str) and object_type:
        return object_type.rsplit("::", 1)[-1]
    return type(obj).__name__


def _circumcenter(a: Vec, b: Vec, c: Vec) -> Vec:
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        raise RuntimeError("the three points are collinear")
    sa, sb, sc = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    ux = (sa * (b[1] - c[1]) + sb * (c[1] - a[1]) + sc * (a[1] - b[1])) / d
    uy = (sa * (c[0] - b[0]) + sb * (a[0] - c[0]) + sc * (b[0] - a[0])) / d
    return (ux, uy, 0.0)


def _intersect_lines(one: tuple[Vec, Vec], two: tuple[Vec, Vec]) -> Vec | None:
    """Intersection of two infinite lines ``(point, direction)``; ``None`` if parallel."""
    (a, d1), (b, d2) = one, two
    den = _cross2(d1, d2)
    if abs(den) < 1e-12:
        return None
    return _add(a, _mul(d1, _cross2(_sub(b, a), d2) / den))


def _expression(value: float, unit: str) -> str:
    """Fusion-like expression for an internal value: ``2.5`` cm in ``"mm"`` -> ``"25 mm"``."""
    shown = round(value / unit_scale(unit), 6) if unit else round(value, 6)
    return f"{shown:g} {unit}" if unit else f"{shown:g}"


# --- optional attributes and fault injection ----------------------------------------------


class Hideable:
    """``hide(*names)`` removes public attributes (``hasattr`` is False), like an older
    Fusion without those methods; ``unhide`` restores them."""

    def hide(self, *names: str) -> None:
        self.__dict__.setdefault("_hidden", set()).update(names)

    def unhide(self, *names: str) -> None:
        self.__dict__.setdefault("_hidden", set()).difference_update(names)

    def __getattribute__(self, name: str) -> Any:
        if name[:1] != "_":
            hidden = object.__getattribute__(self, "__dict__").get("_hidden")
            if hidden and name in hidden:
                raise AttributeError(f"{type(self).__name__} has no attribute {name!r} (hidden)")
        return object.__getattribute__(self, name)


@dataclass
class _Fault:
    method: str
    remaining: int
    exc: BaseException | None


class FaultInjector:
    """Call log plus "raise on the n-th matching call" faults."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._faults: list[_Fault] = []

    def fail_on(self, method: str, n: int = 1, exc: BaseException | None = None) -> None:
        if n < 1:
            raise ValueError("n counts calls from 1")
        self._faults.append(_Fault(method, n, exc))

    def clear_faults(self) -> None:
        self._faults.clear()

    def record(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        for fault in list(self._faults):
            if fault.method in ("*", name) or name.endswith("." + fault.method):
                fault.remaining -= 1
                if fault.remaining == 0:
                    self._faults.remove(fault)
                    if fault.exc is not None:
                        raise fault.exc
                    raise RuntimeError(f"injected failure: {name}")


# --- transient geometry (Curve3D) ---------------------------------------------------------


class FakeCurveEvaluator:
    """``CurveEvaluator3D`` over the parameter range [0, 1]; ``getStrokes`` samples the
    curve uniformly (lines exactly, splines through their fit points, circles in 96 chords),
    the tolerance is recorded but not used."""

    def __init__(self, sampler: Callable[[float], Vec], segments: int) -> None:
        self._sampler = sampler
        self._segments = max(1, segments)
        self.stroke_calls: list[tuple[float, float, float]] = []

    def getParameterExtents(self) -> tuple[bool, float, float]:
        return True, 0.0, 1.0

    def getPointAtParameter(self, parameter: float) -> tuple[bool, Any]:
        return True, P(*self._sampler(parameter))

    def getEndPoints(self) -> tuple[bool, Any, Any]:
        return True, P(*self._sampler(0.0)), P(*self._sampler(1.0))

    def getStrokes(
        self, fromParameter: float, toParameter: float, tolerance: float
    ) -> tuple[bool, list[Any]]:
        self.stroke_calls.append((fromParameter, toParameter, tolerance))
        span = toParameter - fromParameter
        n = max(1, math.ceil(abs(span) * self._segments - 1e-9))
        return True, [P(*self._sampler(fromParameter + span * k / n)) for k in range(n + 1)]


class FakeCurve3D:
    """``Line3D``/``Circle3D``/``Arc3D``/``Ellipse3D``/``NurbsCurve3D`` snapshot."""

    def __init__(
        self,
        kind: str,
        curve_type: int,
        sampler: Callable[[float], Vec],
        segments: int,
        **attrs: Any,
    ) -> None:
        self.objectType = f"adsk::core::{kind}"
        self.curveType = curve_type
        self.evaluator = FakeCurveEvaluator(sampler, segments)
        for key, value in attrs.items():
            setattr(self, key, value)


class FakeBoundingBox3D:
    objectType = "adsk::core::BoundingBox3D"

    def __init__(self, points: Sequence[Vec]) -> None:
        pts = list(points) or [(0.0, 0.0, 0.0)]
        self.minPoint = P(*(min(p[i] for p in pts) for i in range(3)))
        self.maxPoint = P(*(max(p[i] for p in pts) for i in range(3)))


# --- sketch objects -------------------------------------------------------------------------


class _SketchObject(Hideable):
    """Anything a sketch owns: token, ``objectType``, ``isValid``, ``deleteMe``."""

    type_name = "SketchObject"

    def __init__(self, sketch: SketchApiMixin) -> None:
        self._sketch = sketch
        self.objectType = f"adsk::fusion::{self.type_name}"
        self.entityToken = f"{sketch.entityToken}/{self.type_name}-{next(_ids)}"
        self._valid = True
        self._params: list[Any] = []
        # Makes ``deleteMe`` raise: a message or an exception.
        self.fail_delete: BaseException | str | None = None

    @property
    def isValid(self) -> bool:
        return self._valid and self._sketch._valid

    @property
    def parentSketch(self) -> Any:
        return self._sketch

    @property
    def isDeletable(self) -> bool:
        return True

    def deleteMe(self) -> bool:
        self._sketch._record("deleteMe", self)
        if not self._valid:
            raise RuntimeError(f"{self.type_name} is no longer valid")
        if self.fail_delete is not None:
            fail = self.fail_delete
            raise fail if isinstance(fail, BaseException) else RuntimeError(fail)
        self._sketch._delete(self)
        return True

    def _references(self) -> list[Any]:
        return []

    def __repr__(self) -> str:
        return f"<{self.type_name} {self.entityToken}>"


class FakeSketchEntity(_SketchObject):
    def __init__(self, sketch: SketchApiMixin) -> None:
        super().__init__(sketch)
        self.isFixed = False
        self.isFullyConstrained = False
        self.isReference = False
        self.isLinked = False
        self.referencedEntity: Any = None


class FakeSketchPoint(FakeSketchEntity):
    type_name = "SketchPoint"

    def __init__(self, sketch: SketchApiMixin, xyz: Vec, standalone: bool = False) -> None:
        super().__init__(sketch)
        self._pos = [float(xyz[0]), float(xyz[1]), float(xyz[2])]
        # Added by ``sketchPoints.add`` (or a copied point): survives deleting its curves.
        self._standalone = standalone
        self._is_origin = False

    def _xyz(self) -> Vec:
        return (self._pos[0], self._pos[1], self._pos[2])

    def _set(self, xyz: Vec) -> None:
        self._pos = [float(xyz[0]), float(xyz[1]), float(xyz[2])]
        self._sketch._touch()

    @property
    def geometry(self) -> Any:
        return P(*self._xyz())

    @property
    def worldGeometry(self) -> Any:
        return self._sketch.sketchToModelSpace(self.geometry)

    @property
    def connectedEntities(self) -> FakeCollection:
        return FakeCollection(self._sketch._users(self))

    def move(self, translation: Any) -> bool:
        self._sketch._record("SketchPoint.move", self, translation)
        self._set(_add(self._xyz(), _v(translation)))
        return True


class FakeSketchCurve(FakeSketchEntity):
    def __init__(self, sketch: SketchApiMixin) -> None:
        super().__init__(sketch)
        self._construction = False

    @property
    def isConstruction(self) -> bool:
        return self._construction

    @isConstruction.setter
    def isConstruction(self, value: bool) -> None:
        # Construction curves bound no profiles: a change invalidates the profile cache.
        self._construction = bool(value)
        self._sketch._touch()

    def _points(self) -> list[FakeSketchPoint]:
        raise NotImplementedError

    def _ends(self) -> tuple[FakeSketchPoint, FakeSketchPoint] | None:
        """Start and end points of an open curve; ``None`` for closed ones."""
        return None

    def _geometry(self, mp: Callable[[Vec], Vec], mv: Callable[[Vec], Vec]) -> FakeCurve3D:
        raise NotImplementedError

    @property
    def geometry(self) -> Any:
        return self._geometry(_same, _same)

    @property
    def worldGeometry(self) -> Any:
        matrix = self._sketch._transform

        def mp(p: Vec) -> Vec:
            return _apply(matrix, p)

        def mv(v: Vec) -> Vec:
            return _apply_vec(matrix, v)

        return self._geometry(mp, mv)

    @property
    def length(self) -> float:
        raise NotImplementedError

    def trim(self, segmentPoint: Any, createConstraints: bool = True) -> Any:
        self._sketch._record(f"{self.type_name}.trim", self, segmentPoint, createConstraints)
        return self._sketch._trim(self, _v(segmentPoint), createConstraints)

    def extend(self, endPoint: Any, createConstraints: bool = True) -> Any:
        self._sketch._record(f"{self.type_name}.extend", self, endPoint, createConstraints)
        return self._sketch._extend(self, _v(endPoint), createConstraints)


class FakeSketchLine(FakeSketchCurve):
    type_name = "SketchLine"

    def __init__(
        self, sketch: SketchApiMixin, start: FakeSketchPoint, end: FakeSketchPoint
    ) -> None:
        super().__init__(sketch)
        self._start = start
        self._end = end

    @property
    def startSketchPoint(self) -> FakeSketchPoint:
        return self._start

    @property
    def endSketchPoint(self) -> FakeSketchPoint:
        return self._end

    def _points(self) -> list[FakeSketchPoint]:
        return [self._start, self._end]

    def _ends(self) -> tuple[FakeSketchPoint, FakeSketchPoint]:
        return self._start, self._end

    def _geometry(self, mp: Callable[[Vec], Vec], mv: Callable[[Vec], Vec]) -> FakeCurve3D:
        a, b = self._start._xyz(), self._end._xyz()

        def sample(u: float) -> Vec:
            return mp(_lerp(a, b, u))

        return FakeCurve3D(
            "Line3D",
            CT.Line3DCurveType,
            sample,
            1,
            startPoint=P(*mp(a)),
            endPoint=P(*mp(b)),
        )

    @property
    def length(self) -> float:
        return _dist(self._start._xyz(), self._end._xyz())


class FakeSketchCircle(FakeSketchCurve):
    type_name = "SketchCircle"

    def __init__(self, sketch: SketchApiMixin, center: FakeSketchPoint, radius: float) -> None:
        super().__init__(sketch)
        self._center = center
        self._radius = float(radius)

    @property
    def centerSketchPoint(self) -> FakeSketchPoint:
        return self._center

    @property
    def radius(self) -> float:
        return self._radius

    @radius.setter
    def radius(self, value: float) -> None:
        if value <= EPS:
            raise RuntimeError("radius must be positive")
        self._radius = float(value)
        self._sketch._touch()

    def _points(self) -> list[FakeSketchPoint]:
        return [self._center]

    def _geometry(self, mp: Callable[[Vec], Vec], mv: Callable[[Vec], Vec]) -> FakeCurve3D:
        c, r = self._center._xyz(), self._radius

        def sample(u: float) -> Vec:
            return mp(_polar(c, r, TAU * u))

        return FakeCurve3D(
            "Circle3D",
            CT.Circle3DCurveType,
            sample,
            96,
            center=P(*mp(c)),
            normal=V(*mv((0.0, 0.0, 1.0))),
            radius=r,
        )

    @property
    def length(self) -> float:
        return TAU * self._radius


class FakeSketchArc(FakeSketchCurve):
    """Counter-clockwise (about the sketch normal) from ``startSketchPoint`` to
    ``endSketchPoint``, like Fusion's sketch arcs; the radius is the centre-start distance."""

    type_name = "SketchArc"

    def __init__(
        self,
        sketch: SketchApiMixin,
        center: FakeSketchPoint,
        start: FakeSketchPoint,
        end: FakeSketchPoint,
    ) -> None:
        super().__init__(sketch)
        self._center = center
        self._start = start
        self._end = end

    @property
    def centerSketchPoint(self) -> FakeSketchPoint:
        return self._center

    @property
    def startSketchPoint(self) -> FakeSketchPoint:
        return self._start

    @property
    def endSketchPoint(self) -> FakeSketchPoint:
        return self._end

    @property
    def radius(self) -> float:
        return _dist(self._center._xyz(), self._start._xyz())

    def _angles(self) -> tuple[float, float]:
        """``(start angle, sweep)`` in radians, sweep in (0, 2*pi]."""
        c = self._center._xyz()
        a0 = _angle(c, self._start._xyz())
        sweep = (_angle(c, self._end._xyz()) - a0) % TAU
        return a0, sweep if sweep > EPS else TAU

    def _points(self) -> list[FakeSketchPoint]:
        return [self._center, self._start, self._end]

    def _ends(self) -> tuple[FakeSketchPoint, FakeSketchPoint]:
        return self._start, self._end

    def _geometry(self, mp: Callable[[Vec], Vec], mv: Callable[[Vec], Vec]) -> FakeCurve3D:
        c, r = self._center._xyz(), self.radius
        a0, sweep = self._angles()

        def sample(u: float) -> Vec:
            return mp(_polar(c, r, a0 + sweep * u))

        return FakeCurve3D(
            "Arc3D",
            CT.Arc3DCurveType,
            sample,
            max(4, math.ceil(96 * sweep / TAU)),
            center=P(*mp(c)),
            normal=V(*mv((0.0, 0.0, 1.0))),
            referenceVector=V(*mv((1.0, 0.0, 0.0))),
            radius=r,
            startAngle=a0,
            endAngle=a0 + sweep,
        )

    @property
    def length(self) -> float:
        return self.radius * self._angles()[1]


class FakeSketchEllipse(FakeSketchCurve):
    type_name = "SketchEllipse"

    def __init__(
        self,
        sketch: SketchApiMixin,
        center: FakeSketchPoint,
        axis: Vec,
        major: float,
        minor: float,
    ) -> None:
        super().__init__(sketch)
        self._center = center
        self._axis = _unit(axis)
        self._major = float(major)
        self._minor = float(minor)

    @property
    def centerSketchPoint(self) -> FakeSketchPoint:
        return self._center

    @property
    def majorAxis(self) -> Any:
        return V(*self._axis)

    @property
    def majorAxisRadius(self) -> float:
        return self._major

    @majorAxisRadius.setter
    def majorAxisRadius(self, value: float) -> None:
        self._major = float(value)
        self._sketch._touch()

    @property
    def minorAxisRadius(self) -> float:
        return self._minor

    @minorAxisRadius.setter
    def minorAxisRadius(self, value: float) -> None:
        self._minor = float(value)
        self._sketch._touch()

    def _points(self) -> list[FakeSketchPoint]:
        return [self._center]

    def _geometry(self, mp: Callable[[Vec], Vec], mv: Callable[[Vec], Vec]) -> FakeCurve3D:
        c, ax, big, small = self._center._xyz(), self._axis, self._major, self._minor
        mn = _perp(ax)

        def sample(u: float) -> Vec:
            t = TAU * u
            return mp(_add(c, _add(_mul(ax, big * math.cos(t)), _mul(mn, small * math.sin(t)))))

        return FakeCurve3D(
            "Ellipse3D",
            CT.Ellipse3DCurveType,
            sample,
            96,
            center=P(*mp(c)),
            normal=V(*mv((0.0, 0.0, 1.0))),
            majorAxis=V(*mv(ax)),
            majorRadius=big,
            minorRadius=small,
        )

    @property
    def length(self) -> float:
        a, b = self._major, self._minor
        return math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))


class FakeSketchFittedSpline(FakeSketchCurve):
    """Fitted spline whose fake geometry is the polyline through its fit points."""

    type_name = "SketchFittedSpline"

    def __init__(self, sketch: SketchApiMixin, points: Sequence[FakeSketchPoint]) -> None:
        super().__init__(sketch)
        self._fit = list(points)
        self._closed = False

    @property
    def fitPoints(self) -> FakeCollection:
        return FakeCollection(list(self._fit))

    @property
    def isClosed(self) -> bool:
        return self._closed

    @isClosed.setter
    def isClosed(self, value: bool) -> None:
        self._closed = bool(value)
        self._sketch._touch()

    @property
    def startSketchPoint(self) -> FakeSketchPoint:
        return self._fit[0]

    @property
    def endSketchPoint(self) -> FakeSketchPoint:
        return self._fit[-1]

    def _points(self) -> list[FakeSketchPoint]:
        return list(self._fit)

    def _ends(self) -> tuple[FakeSketchPoint, FakeSketchPoint] | None:
        return None if self._closed else (self._fit[0], self._fit[-1])

    def _polyline(self) -> list[Vec]:
        pts = [p._xyz() for p in self._fit]
        return [*pts, pts[0]] if self._closed else pts

    def _geometry(self, mp: Callable[[Vec], Vec], mv: Callable[[Vec], Vec]) -> FakeCurve3D:
        pts = self._polyline()
        n = len(pts) - 1

        def sample(u: float) -> Vec:
            if n < 1:
                return mp(pts[0])
            s = min(max(u, 0.0), 1.0) * n
            i = min(int(s), n - 1)
            return mp(_lerp(pts[i], pts[i + 1], s - i))

        return FakeCurve3D(
            "NurbsCurve3D",
            CT.NurbsCurve3DCurveType,
            sample,
            n,
            isClosed=self._closed,
            controlPointCount=len(self._fit),
        )

    @property
    def length(self) -> float:
        pts = self._polyline()
        return sum(_dist(a, b) for a, b in itertools.pairwise(pts))


class FakeGeometricConstraint(_SketchObject):
    """``<kind>`` (``CoincidentConstraint``...) with that class's entity properties:
    ``point``/``entity``, ``line``, ``pointOne``/``pointTwo``, ``lineOne``/``lineTwo``,
    ``curveOne``/``curveTwo``, ``entityOne``/``entityTwo``, ``midPointCurve``,
    ``symmetryLine``, ``parentCurves``, ``entities``/``centerPoint``. ``roles`` maps them."""

    def __init__(self, sketch: SketchApiMixin, kind: str, **roles: Any) -> None:
        self.type_name = kind
        super().__init__(sketch)
        self.roles = dict(roles)
        for key, value in roles.items():
            setattr(self, key, value)

    def _references(self) -> list[Any]:
        refs: list[Any] = []
        for value in self.roles.values():
            refs.extend(v for v in _items(value) if isinstance(v, _SketchObject))
        return refs


class FakeDimensionParameter(FakeModelParameter):
    """``ModelParameter`` of a sketch dimension or a pattern/offset constraint. Setting
    ``expression`` of a driven dimension raises; a change re-runs ``sketch.solver``."""

    def __init__(
        self,
        owner: FakeParameterSet,
        name: str,
        expression: str,
        unit: str,
        created_by: Any,
        component: Any,
        role: str,
    ) -> None:
        super().__init__(owner, name, expression, unit, created_by, role, component)
        self.dimension = created_by if isinstance(created_by, FakeSketchDimension) else None

    @property
    def expression(self) -> str:
        return self._expression

    @expression.setter
    def expression(self, text: str) -> None:
        dimension = self.dimension
        if dimension is not None and not dimension._driving:
            raise RuntimeError("the parameter of a driven dimension cannot be changed")
        self._owner.set_expression(self, text)
        if dimension is not None:
            dimension._changed()

    @property
    def value(self) -> float:
        return self._owner.evaluate(self._expression, self.unit, exclude=self)

    @value.setter
    def value(self, internal: float) -> None:
        self.expression = _expression(internal, self.unit)


class FakeSketchDimension(_SketchObject):
    """``Sketch<kind>Dimension`` with its entity properties (``entityOne``/``entityTwo``/
    ``orientation``, ``line``/``entity``, ``entity``, ``lineOne``/``lineTwo``,
    ``ellipse``), ``parameter``, ``isDriving``, ``textPosition`` and ``value`` (cm/rad)."""

    def __init__(
        self,
        sketch: SketchApiMixin,
        kind: str,
        text_point: Any,
        is_driving: bool,
        measure: Callable[[], float],
        unit: str,
        roles: dict[str, Any],
    ) -> None:
        self.type_name = kind
        super().__init__(sketch)
        self.roles = dict(roles)
        for key, value in roles.items():
            setattr(self, key, value)
        self._driving = bool(is_driving)
        self._measure = measure
        self._unit = unit
        self.textPosition = P(*_v(text_point))
        self.parameter: Any = None

    @property
    def isDriving(self) -> bool:
        return self._driving

    @isDriving.setter
    def isDriving(self, value: bool) -> None:
        self._sketch._record("SketchDimension.isDriving", self, value)
        self._driving = bool(value)
        if not self._driving and self.parameter is not None:
            # A driven dimension reports the geometry.
            self.parameter._expression = _expression(self._measure(), self._unit)
        self._changed()

    @property
    def value(self) -> float:
        return float(self.parameter.value)

    def _references(self) -> list[Any]:
        refs: list[Any] = []
        for value in self.roles.values():
            refs.extend(v for v in _items(value) if isinstance(v, _SketchObject))
        return refs

    def _changed(self) -> None:
        self._sketch._touch()
        self._sketch._solve(self)


class FakeMultiLineTextDefinition:
    objectType = "adsk::fusion::MultiLineTextDefinition"

    def __init__(
        self, corner: Any, diagonal: Any, horizontal: int, vertical: int, spacing: float
    ) -> None:
        self.cornerPoint = corner
        self.diagonalPoint = diagonal
        self.horizontalAlignment = horizontal
        self.verticalAlignment = vertical
        self.characterSpacing = spacing


class FakeSketchTextInput:
    objectType = "adsk::fusion::SketchTextInput"

    def __init__(self, text: str, height: float, position: Any = None) -> None:
        self.text = text
        self.height = height
        self.position = position
        self.fontName = "Arial"
        self.angle = 0.0
        self.isHorizontalFlip = False
        self.isVerticalFlip = False
        self.textStyle = 0
        self.definition: FakeMultiLineTextDefinition | None = None
        self.multi_line_calls: list[tuple[Any, ...]] = []

    def setAsMultiLine(
        self,
        cornerPoint: Any,
        diagonalPoint: Any,
        horizontalAlignment: int,
        verticalAlignment: int,
        characterSpacing: float,
    ) -> bool:
        args = (cornerPoint, diagonalPoint, horizontalAlignment, verticalAlignment)
        self.multi_line_calls.append((*args, characterSpacing))
        self.definition = FakeMultiLineTextDefinition(
            P(*_v(cornerPoint)),
            P(*_v(diagonalPoint)),
            horizontalAlignment,
            verticalAlignment,
            characterSpacing,
        )
        return True


class FakeSketchText(_SketchObject):
    type_name = "SketchText"

    def __init__(self, sketch: SketchApiMixin, data: FakeSketchTextInput) -> None:
        super().__init__(sketch)
        self.text = data.text
        self.height = float(data.height)
        self.fontName = data.fontName
        self.angle = data.angle
        self.isHorizontalFlip = data.isHorizontalFlip
        self.isVerticalFlip = data.isVerticalFlip
        self.textStyle = data.textStyle
        definition = data.definition
        self._multi_line = definition is not None
        if definition is not None:
            self._corner = _v(definition.cornerPoint)
            self._diagonal = _v(definition.diagonalPoint)
            self.horizontalAlignment = definition.horizontalAlignment
            self.verticalAlignment = definition.verticalAlignment
            self.characterSpacing = definition.characterSpacing
        else:
            corner = _v(data.position)
            width = 0.6 * self.height * max(1, len(self.text))
            self._corner = corner
            self._diagonal = (corner[0] + width, corner[1] + self.height, corner[2])
            self.horizontalAlignment = adsk.core.HorizontalAlignments.LeftHorizontalAlignment
            self.verticalAlignment = adsk.core.VerticalAlignments.BottomVerticalAlignment
            self.characterSpacing = 0.0

    @property
    def position(self) -> Any:
        return P(*self._corner)

    @property
    def definition(self) -> FakeMultiLineTextDefinition | None:
        if not self._multi_line:
            return None
        return FakeMultiLineTextDefinition(
            P(*self._corner),
            P(*self._diagonal),
            self.horizontalAlignment,
            self.verticalAlignment,
            self.characterSpacing,
        )

    @property
    def boundingBox(self) -> FakeBoundingBox3D:
        return FakeBoundingBox3D([self._corner, self._diagonal])

    def _transform(self, matrix: Any) -> None:
        self._corner = _apply(matrix, self._corner)
        self._diagonal = _apply(matrix, self._diagonal)


# --- pattern / offset inputs -----------------------------------------------------------------


class FakeOffsetConstraintInput:
    """``OffsetConstraintInput``. Side: ``dirPoint`` (a Point3D on the offset side) if set,
    else the left of a line / outside of a circle; ``isFlipped`` or a negative value flips."""

    objectType = "adsk::fusion::OffsetConstraintInput"

    def __init__(self, curves: list[Any], offset: Any) -> None:
        self.curves = curves
        self.offset = offset
        self.isTopologyMatched = True
        self.dirPoint: Any = None
        self.isFlipped = False


class FakeRectangularPatternConstraintInput:
    objectType = "adsk::fusion::RectangularPatternConstraintInput"

    def __init__(self, entities: list[Any], distance_type: int) -> None:
        self.entities = entities
        self.distanceType = distance_type
        self.directionOneEntity: Any = None
        self.directionTwoEntity: Any = None
        self.quantityOne = adsk.core.ValueInput.createByReal(1)
        self.quantityTwo = adsk.core.ValueInput.createByReal(1)
        self.distanceOne = adsk.core.ValueInput.createByReal(0.0)
        self.distanceTwo = adsk.core.ValueInput.createByReal(0.0)
        self.isSymmetricInDirectionOne = False
        self.isSymmetricInDirectionTwo = False

    def setDirectionOne(self, directionOneEntity: Any, quantityOne: Any, distanceOne: Any) -> bool:
        self.directionOneEntity = directionOneEntity
        self.quantityOne = quantityOne
        self.distanceOne = distanceOne
        return True

    def setDirectionTwo(self, directionTwoEntity: Any, quantityTwo: Any, distanceTwo: Any) -> bool:
        self.directionTwoEntity = directionTwoEntity
        self.quantityTwo = quantityTwo
        self.distanceTwo = distanceTwo
        return True


class FakeCircularPatternConstraintInput:
    objectType = "adsk::fusion::CircularPatternConstraintInput"

    def __init__(self, entities: list[Any], center_point: Any) -> None:
        self.entities = entities
        self.centerPoint = center_point
        self.quantity = adsk.core.ValueInput.createByReal(3)
        self.totalAngle = adsk.core.ValueInput.createByString("360 deg")
        self.isSymmetric = False
        self.isClockwise = False


# --- collections -------------------------------------------------------------------------------


class _SketchView(Hideable):
    """Live view of one kind of sketch object: ``count``, ``item``, iteration."""

    name = "view"

    def __init__(self, sketch: SketchApiMixin) -> None:
        self._sketch = sketch

    def _list(self) -> list[Any]:
        raise NotImplementedError

    @property
    def count(self) -> int:
        return len(self._list())

    def item(self, index: int) -> Any:
        items = self._list()
        return items[index] if 0 <= index < len(items) else None

    def __iter__(self) -> Iterator[Any]:
        return iter(self._list())

    def __len__(self) -> int:
        return len(self._list())

    def _record(self, method: str, *args: Any) -> None:
        self._sketch._record(f"{self.name}.{method}", *args)


class FakeSketchPoints(_SketchView):
    """``sketch.sketchPoints`` (the origin point is item 0, like in Fusion)."""

    name = "sketchPoints"

    def _list(self) -> list[Any]:
        return self._sketch._of(FakeSketchPoint)

    def add(self, point: Any) -> FakeSketchPoint:
        self._record("add", point)
        return self._sketch._new_point(_v(point), standalone=True)


class FakeCurveView(_SketchView):
    def __init__(self, sketch: SketchApiMixin, name: str, types: tuple[str, ...]) -> None:
        super().__init__(sketch)
        self.name = name
        self._types = types

    def _list(self) -> list[Any]:
        return [c for c in self._sketch._of(FakeSketchCurve) if c.type_name in self._types]


class FakeSketchLines(FakeCurveView):
    """``sketchCurves.sketchLines``. Rectangles return a ``FakeCollection`` of 4 lines on
    shared corners (two-point/center: bottom-or-top edge from the first corner, then
    counter-wise around); ``rectangle_constraints`` (default True) adds the
    horizontal/vertical (two-point, center) or perpendicular/parallel (three-point)
    constraints Fusion adds; ``center_rectangle_diagonals`` (default False) also adds two
    construction diagonals and a midpoint constraint of the centre point, like some Fusion
    versions."""

    def __init__(self, sketch: SketchApiMixin) -> None:
        super().__init__(sketch, "sketchLines", LINE)
        self.rectangle_constraints = True
        self.center_rectangle_diagonals = False

    def addByTwoPoints(self, startPoint: Any, endPoint: Any) -> FakeSketchLine:
        self._record("addByTwoPoints", startPoint, endPoint)
        s = self._sketch
        if _dist(s._pos(startPoint), s._pos(endPoint)) < EPS:
            raise RuntimeError("a line needs two distinct points")
        return s._line(s._as_point(startPoint), s._as_point(endPoint))

    def _box(self, corners: list[FakeSketchPoint]) -> list[FakeSketchLine]:
        s = self._sketch
        return [s._line(corners[i], corners[(i + 1) % 4]) for i in range(4)]

    def _axis_constraints(self, lines: list[FakeSketchLine]) -> None:
        if self.rectangle_constraints:
            s = self._sketch
            for line, kind in zip(lines, ("Horizontal", "Vertical") * 2, strict=True):
                s._constraint(f"{kind}Constraint", line=line)

    def addTwoPointRectangle(self, pointOne: Any, pointTwo: Any) -> FakeCollection:
        self._record("addTwoPointRectangle", pointOne, pointTwo)
        s = self._sketch
        a, c = s._pos(pointOne), s._pos(pointTwo)
        if abs(a[0] - c[0]) < EPS or abs(a[1] - c[1]) < EPS:
            raise RuntimeError("degenerate rectangle")
        p0, p2 = s._as_point(pointOne), s._as_point(pointTwo)
        p1, p3 = s._new_point((c[0], a[1], 0.0)), s._new_point((a[0], c[1], 0.0))
        lines = self._box([p0, p1, p2, p3])
        self._axis_constraints(lines)
        return FakeCollection(lines)

    def addCenterPointRectangle(self, centerPoint: Any, cornerPoint: Any) -> FakeCollection:
        self._record("addCenterPointRectangle", centerPoint, cornerPoint)
        s = self._sketch
        c, k = s._pos(centerPoint), s._pos(cornerPoint)
        o = (2 * c[0] - k[0], 2 * c[1] - k[1], 0.0)
        if abs(o[0] - k[0]) < EPS or abs(o[1] - k[1]) < EPS:
            raise RuntimeError("degenerate rectangle")
        p0 = s._as_point(cornerPoint)
        p1, p2, p3 = (s._new_point(xyz) for xyz in ((o[0], k[1], 0.0), o, (k[0], o[1], 0.0)))
        lines = self._box([p0, p1, p2, p3])
        self._axis_constraints(lines)
        if self.center_rectangle_diagonals:
            one, two = s._line(p0, p2), s._line(p1, p3)
            one.isConstruction = two.isConstruction = True
            s._constraint("MidPointConstraint", point=s._as_point(centerPoint), midPointCurve=one)
        return FakeCollection(lines)

    def addThreePointRectangle(self, pointOne: Any, pointTwo: Any, pointThree: Any) -> Any:
        self._record("addThreePointRectangle", pointOne, pointTwo, pointThree)
        s = self._sketch
        a, b, t = s._pos(pointOne), s._pos(pointTwo), s._pos(pointThree)
        if _dist(a, b) < EPS:
            raise RuntimeError("degenerate rectangle")
        n = _perp(_unit(_sub(b, a)))
        h = _dot(_sub(t, a), n)
        if abs(h) < EPS:
            raise RuntimeError("degenerate rectangle")
        p0, p1 = s._as_point(pointOne), s._as_point(pointTwo)
        p2, p3 = s._new_point(_add(b, _mul(n, h))), s._new_point(_add(a, _mul(n, h)))
        lines = self._box([p0, p1, p2, p3])
        if self.rectangle_constraints:
            s._constraint("PerpendicularConstraint", lineOne=lines[0], lineTwo=lines[1])
            s._constraint("ParallelConstraint", lineOne=lines[0], lineTwo=lines[2])
            s._constraint("ParallelConstraint", lineOne=lines[1], lineTwo=lines[3])
        return FakeCollection(lines)


class FakeSketchCircles(FakeCurveView):
    """Two/three-point circles get a new centre point; input ``SketchPoint``s are tied to
    the circle with coincident constraints."""

    def __init__(self, sketch: SketchApiMixin) -> None:
        super().__init__(sketch, "sketchCircles", ("SketchCircle",))

    def addByCenterRadius(self, centerPoint: Any, radius: float) -> FakeSketchCircle:
        self._record("addByCenterRadius", centerPoint, radius)
        s = self._sketch
        s._pos(centerPoint)
        if radius <= EPS:
            raise RuntimeError("radius must be positive")
        return s._circle(s._as_point(centerPoint), radius)

    def addByTwoPoints(self, pointOne: Any, pointTwo: Any) -> FakeSketchCircle:
        self._record("addByTwoPoints", pointOne, pointTwo)
        s = self._sketch
        a, b = s._pos(pointOne), s._pos(pointTwo)
        if _dist(a, b) < EPS:
            raise RuntimeError("a circle needs two distinct points")
        circle = s._circle(s._new_point(_lerp(a, b, 0.5)), _dist(a, b) / 2)
        s._tie(circle, pointOne, pointTwo)
        return circle

    def addByThreePoints(self, pointOne: Any, pointTwo: Any, pointThree: Any) -> FakeSketchCircle:
        self._record("addByThreePoints", pointOne, pointTwo, pointThree)
        s = self._sketch
        a, b, c = s._pos(pointOne), s._pos(pointTwo), s._pos(pointThree)
        center = _circumcenter(a, b, c)
        circle = s._circle(s._new_point(center), _dist(center, a))
        s._tie(circle, pointOne, pointTwo, pointThree)
        return circle


class FakeSketchArcs(FakeCurveView):
    def __init__(self, sketch: SketchApiMixin) -> None:
        super().__init__(sketch, "sketchArcs", ("SketchArc",))

    def addByThreePoints(self, startPoint: Any, point: Any, endPoint: Any) -> FakeSketchArc:
        """A clockwise ``start -> point -> end`` arc is stored counter-clockwise, i.e. its
        ``startSketchPoint`` is the given end point."""
        self._record("addByThreePoints", startPoint, point, endPoint)
        return self._sketch._arc_three(startPoint, point, endPoint)

    def addByCenterStartSweep(
        self, centerPoint: Any, startPoint: Any, sweepAngle: float
    ) -> FakeSketchArc:
        """Negative ``sweepAngle`` (clockwise) swaps the stored start/end points."""
        self._record("addByCenterStartSweep", centerPoint, startPoint, sweepAngle)
        s = self._sketch
        c, st = s._pos(centerPoint), s._pos(startPoint)
        radius = _dist(c, st)
        if radius < EPS or abs(sweepAngle) < EPS or abs(sweepAngle) > TAU + EPS:
            raise RuntimeError("invalid arc")
        end = _polar(c, radius, _angle(c, st) + sweepAngle)
        cp, sp = s._as_point(centerPoint), s._as_point(startPoint)
        ep = s._new_point(end)
        return s._arc(cp, sp, ep) if sweepAngle > 0 else s._arc(cp, ep, sp)

    def addFillet(
        self,
        firstEntity: Any,
        firstEntityPoint: Any,
        secondEntity: Any,
        secondEntityPoint: Any,
        radius: float,
    ) -> FakeSketchArc:
        """Lines only: their corner ends move to the tangent points (the lines keep their
        tokens), the old corner point is deleted if unused, tangent constraints are added."""
        args = (firstEntity, firstEntityPoint, secondEntity, secondEntityPoint, radius)
        self._record("addFillet", *args)
        return self._sketch._fillet(*args)


class FakeSketchEllipses(FakeCurveView):
    def __init__(self, sketch: SketchApiMixin) -> None:
        super().__init__(sketch, "sketchEllipses", ("SketchEllipse",))

    def add(self, centerPoint: Any, majorAxisPoint: Any, point: Any) -> FakeSketchEllipse:
        self._record("add", centerPoint, majorAxisPoint, point)
        s = self._sketch
        c, m, q = s._pos(centerPoint), s._pos(majorAxisPoint), s._pos(point)
        major = _dist(c, m)
        if major < EPS:
            raise RuntimeError("degenerate ellipse")
        axis = _unit(_sub(m, c))
        minor = abs(_cross2(axis, _sub(q, c)))
        if minor < EPS or minor > major + EPS:
            raise RuntimeError("the point must give a minor radius in (0, major radius]")
        return s._ellipse(s._as_point(centerPoint), axis, major, minor)


class FakeSketchFittedSplines(FakeCurveView):
    def __init__(self, sketch: SketchApiMixin) -> None:
        super().__init__(sketch, "sketchFittedSplines", ("SketchFittedSpline",))

    def add(self, fitPoints: Any) -> FakeSketchFittedSpline:
        self._record("add", fitPoints)
        s = self._sketch
        specs = _items(fitPoints)
        if len(specs) < 2:
            raise RuntimeError("a spline needs at least two fit points")
        for spec in specs:
            s._pos(spec)
        return s._spline([s._as_point(p) for p in specs])


class FakeSketchCurves(FakeCurveView):
    def __init__(self, sketch: SketchApiMixin) -> None:
        super().__init__(sketch, "sketchCurves", CURVES)
        self.sketchLines = FakeSketchLines(sketch)
        self.sketchCircles = FakeSketchCircles(sketch)
        self.sketchArcs = FakeSketchArcs(sketch)
        self.sketchEllipses = FakeSketchEllipses(sketch)
        self.sketchFittedSplines = FakeSketchFittedSplines(sketch)
        self.sketchEllipticalArcs = FakeCurveView(
            sketch, "sketchEllipticalArcs", ("SketchEllipticalArc",)
        )
        self.sketchFixedSplines = FakeCurveView(
            sketch, "sketchFixedSplines", ("SketchFixedSpline",)
        )
        self.sketchControlPointSplines = FakeCurveView(
            sketch, "sketchControlPointSplines", ("SketchControlPointSpline",)
        )
        self.sketchConicCurves = FakeCurveView(sketch, "sketchConicCurves", ("SketchConicCurve",))


class FakeSketchTexts(_SketchView):
    name = "sketchTexts"

    def _list(self) -> list[Any]:
        return self._sketch._of(FakeSketchText)

    def createInput2(self, formattedText: str, height: float) -> FakeSketchTextInput:
        self._record("createInput2", formattedText, height)
        return FakeSketchTextInput(formattedText, height)

    def createInput(self, formattedText: str, height: float, position: Any) -> FakeSketchTextInput:
        self._record("createInput", formattedText, height, position)
        return FakeSketchTextInput(formattedText, height, position)

    def add(self, input: FakeSketchTextInput) -> FakeSketchText:
        self._record("add", input)
        if not input.text:
            raise RuntimeError("empty text")
        if input.height <= EPS:
            raise RuntimeError("text height must be positive")
        if input.definition is None and input.position is None:
            raise RuntimeError("text input has no position: call setAsMultiLine")
        text = FakeSketchText(self._sketch, input)
        self._sketch._register(text)
        return text


class FakeGeometricConstraints(_SketchView):
    """``sketch.geometricConstraints``. ``remove_optional()`` hides the offset/pattern
    methods (``OPTIONAL_CONSTRAINT_METHODS``) like an older Fusion."""

    name = "geometricConstraints"

    def _list(self) -> list[Any]:
        return self._sketch._of(FakeGeometricConstraint)

    def remove_optional(self) -> None:
        self.hide(*OPTIONAL_CONSTRAINT_METHODS)

    def _two(
        self, method: str, kind: str, names: tuple[str, str], one: Any, two: Any, kinds: Any
    ) -> FakeGeometricConstraint:
        self._record(method, one, two)
        s = self._sketch
        s._check(one, kinds, names[0])
        s._check(two, kinds, names[1])
        if one is two:
            raise RuntimeError(f"{method}: the two entities must differ")
        return s._constraint(kind, **{names[0]: one, names[1]: two})

    def addCoincident(self, point: Any, entity: Any) -> FakeGeometricConstraint:
        self._record("addCoincident", point, entity)
        s = self._sketch
        s._check(point, POINT, "point")
        s._check(entity, POINT + CURVES, "entity")
        if point is entity:
            raise RuntimeError("addCoincident: the two entities must differ")
        return s._constraint("CoincidentConstraint", point=point, entity=entity)

    def addHorizontal(self, line: Any) -> FakeGeometricConstraint:
        self._record("addHorizontal", line)
        self._sketch._check(line, LINE, "line")
        return self._sketch._constraint("HorizontalConstraint", line=line)

    def addVertical(self, line: Any) -> FakeGeometricConstraint:
        self._record("addVertical", line)
        self._sketch._check(line, LINE, "line")
        return self._sketch._constraint("VerticalConstraint", line=line)

    def addHorizontalPoints(self, pointOne: Any, pointTwo: Any) -> FakeGeometricConstraint:
        names = ("pointOne", "pointTwo")
        kind = "HorizontalPointsConstraint"
        return self._two("addHorizontalPoints", kind, names, pointOne, pointTwo, POINT)

    def addVerticalPoints(self, pointOne: Any, pointTwo: Any) -> FakeGeometricConstraint:
        names = ("pointOne", "pointTwo")
        kind = "VerticalPointsConstraint"
        return self._two("addVerticalPoints", kind, names, pointOne, pointTwo, POINT)

    def addParallel(self, lineOne: Any, lineTwo: Any) -> FakeGeometricConstraint:
        names = ("lineOne", "lineTwo")
        return self._two("addParallel", "ParallelConstraint", names, lineOne, lineTwo, LINE)

    def addPerpendicular(self, lineOne: Any, lineTwo: Any) -> FakeGeometricConstraint:
        names = ("lineOne", "lineTwo")
        kind = "PerpendicularConstraint"
        return self._two("addPerpendicular", kind, names, lineOne, lineTwo, LINE)

    def addCollinear(self, lineOne: Any, lineTwo: Any) -> FakeGeometricConstraint:
        names = ("lineOne", "lineTwo")
        return self._two("addCollinear", "CollinearConstraint", names, lineOne, lineTwo, LINE)

    def addTangent(self, curveOne: Any, curveTwo: Any) -> FakeGeometricConstraint:
        self._record("addTangent", curveOne, curveTwo)
        s = self._sketch
        s._check(curveOne, CURVES, "curveOne")
        s._check(curveTwo, CURVES, "curveTwo")
        if curveOne is curveTwo or curveOne.type_name == curveTwo.type_name == "SketchLine":
            raise RuntimeError("addTangent: two different curves, not both lines")
        return s._constraint("TangentConstraint", curveOne=curveOne, curveTwo=curveTwo)

    def addEqual(self, curveOne: Any, curveTwo: Any) -> FakeGeometricConstraint:
        self._record("addEqual", curveOne, curveTwo)
        s = self._sketch
        s._check(curveOne, LINE + CIRCULAR, "curveOne")
        s._check(curveTwo, LINE + CIRCULAR, "curveTwo")
        if curveOne is curveTwo or (curveOne.type_name in LINE) != (curveTwo.type_name in LINE):
            raise RuntimeError("addEqual: two lines or two arcs/circles are required")
        return s._constraint("EqualConstraint", curveOne=curveOne, curveTwo=curveTwo)

    def addConcentric(self, entityOne: Any, entityTwo: Any) -> FakeGeometricConstraint:
        names = ("entityOne", "entityTwo")
        kinds = (*CIRCULAR, "SketchEllipse", "SketchEllipticalArc")
        kind = "ConcentricConstraint"
        return self._two("addConcentric", kind, names, entityOne, entityTwo, kinds)

    def addMidPoint(self, point: Any, midPointCurve: Any) -> FakeGeometricConstraint:
        self._record("addMidPoint", point, midPointCurve)
        s = self._sketch
        s._check(point, POINT, "point")
        s._check(midPointCurve, ("SketchLine", "SketchArc"), "midPointCurve")
        return s._constraint("MidPointConstraint", point=point, midPointCurve=midPointCurve)

    def addSymmetry(
        self, entityOne: Any, entityTwo: Any, symmetryLine: Any
    ) -> FakeGeometricConstraint:
        self._record("addSymmetry", entityOne, entityTwo, symmetryLine)
        s = self._sketch
        s._check(entityOne, POINT + CURVES, "entityOne")
        s._check(entityTwo, POINT + CURVES, "entityTwo")
        s._check(symmetryLine, LINE, "symmetryLine")
        if entityOne is entityTwo or symmetryLine in (entityOne, entityTwo):
            raise RuntimeError("addSymmetry: the entities must differ")
        return s._constraint(
            "SymmetryConstraint",
            entityOne=entityOne,
            entityTwo=entityTwo,
            symmetryLine=symmetryLine,
        )

    def addSmooth(self, curveOne: Any, curveTwo: Any) -> FakeGeometricConstraint:
        self._record("addSmooth", curveOne, curveTwo)
        s = self._sketch
        s._check(curveOne, CURVES, "curveOne")
        s._check(curveTwo, CURVES, "curveTwo")
        splines = ("SketchFittedSpline", "SketchFixedSpline", "SketchControlPointSpline")
        if curveOne is curveTwo or not (
            curveOne.type_name in splines or curveTwo.type_name in splines
        ):
            raise RuntimeError("addSmooth: one of the curves must be a spline")
        return s._constraint("SmoothConstraint", curveOne=curveOne, curveTwo=curveTwo)

    def createOffsetInput(self, curves: Any, offset: Any) -> FakeOffsetConstraintInput:
        self._record("createOffsetInput", curves, offset)
        items = _items(curves)
        for curve in items:
            self._sketch._check(curve, CURVES, "curves")
        return FakeOffsetConstraintInput(items, offset)

    def addOffset2(self, offsetInput: FakeOffsetConstraintInput) -> FakeGeometricConstraint:
        """Offset curves + an ``OffsetConstraint`` (``parentCurves``, ``childCurves``,
        ``dimension`` = ``SketchOffsetCurvesDimension`` whose parameter holds the
        expression of a string ``ValueInput``)."""
        self._record("addOffset2", offsetInput)
        return self._sketch._offset_constraint(offsetInput)

    def createRectangularPatternInput(
        self, entities: Any, distanceType: int
    ) -> FakeRectangularPatternConstraintInput:
        self._record("createRectangularPatternInput", entities, distanceType)
        items = _items(entities)
        for entity in items:
            self._sketch._check(entity, POINT + CURVES, "entities")
        return FakeRectangularPatternConstraintInput(items, distanceType)

    def addRectangularPattern(
        self, input: FakeRectangularPatternConstraintInput
    ) -> FakeGeometricConstraint:
        self._record("addRectangularPattern", input)
        return self._sketch._rectangular_pattern(input)

    def createCircularPatternInput(
        self, entities: Any, centerPoint: Any
    ) -> FakeCircularPatternConstraintInput:
        self._record("createCircularPatternInput", entities, centerPoint)
        items = _items(entities)
        for entity in items:
            self._sketch._check(entity, POINT + CURVES, "entities")
        self._sketch._check(centerPoint, POINT, "centerPoint")
        return FakeCircularPatternConstraintInput(items, centerPoint)

    def addCircularPattern(
        self, input: FakeCircularPatternConstraintInput
    ) -> FakeGeometricConstraint:
        self._record("addCircularPattern", input)
        return self._sketch._circular_pattern(input)


class FakeSketchDimensions(_SketchView):
    """``sketch.sketchDimensions``. Each dimension gets a parameter ``d<n>`` with the
    measured value as expression (``"25 mm"``, ``"90 deg"``). ``offset_accepts_point``
    (default True): set False to make ``addOffsetDimension(line, point)`` raise."""

    name = "sketchDimensions"

    def __init__(self, sketch: SketchApiMixin) -> None:
        super().__init__(sketch)
        self.offset_accepts_point = True

    def _list(self) -> list[Any]:
        return self._sketch._of(FakeSketchDimension)

    def addDistanceDimension(
        self,
        pointOne: Any,
        pointTwo: Any,
        orientation: int,
        textPoint: Any,
        isDriving: bool = True,
    ) -> FakeSketchDimension:
        self._record("addDistanceDimension", pointOne, pointTwo, orientation, textPoint, isDriving)
        s = self._sketch
        s._check(pointOne, POINT, "pointOne")
        s._check(pointTwo, POINT, "pointTwo")
        if pointOne is pointTwo:
            raise RuntimeError("addDistanceDimension: the points must differ")
        if orientation not in (0, 1, 2):
            raise RuntimeError(f"invalid orientation {orientation!r}")

        def measure() -> float:
            a, b = pointOne._xyz(), pointTwo._xyz()
            if orientation == adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation:
                return abs(b[0] - a[0])
            if orientation == adsk.fusion.DimensionOrientations.VerticalDimensionOrientation:
                return abs(b[1] - a[1])
            return _dist(a, b)

        roles = {"entityOne": pointOne, "entityTwo": pointTwo, "orientation": orientation}
        return s._dimension("SketchLinearDimension", textPoint, isDriving, measure, "mm", roles)

    def addOffsetDimension(
        self, line: Any, entity: Any, textPoint: Any, isDriving: bool = True
    ) -> FakeSketchDimension:
        self._record("addOffsetDimension", line, entity, textPoint, isDriving)
        s = self._sketch
        s._check(line, LINE, "line")
        s._check(entity, LINE + POINT if self.offset_accepts_point else LINE, "entity")
        if line is entity:
            raise RuntimeError("addOffsetDimension: the entities must differ")
        if entity.type_name == "SketchLine":
            d1 = _unit(_sub(line._end._xyz(), line._start._xyz()))
            d2 = _unit(_sub(entity._end._xyz(), entity._start._xyz()))
            if abs(_cross2(d1, d2)) > 1e-6:
                raise RuntimeError("addOffsetDimension: the lines are not parallel")

        def measure() -> float:
            a, b = line._start._xyz(), line._end._xyz()
            other = entity._start if entity.type_name == "SketchLine" else entity
            return abs(_cross2(_unit(_sub(b, a)), _sub(other._xyz(), a)))

        roles = {"line": line, "entity": entity}
        return s._dimension("SketchOffsetDimension", textPoint, isDriving, measure, "mm", roles)

    def _radius_dim(
        self, method: str, kind: str, entity: Any, textPoint: Any, isDriving: bool, scale: float
    ) -> FakeSketchDimension:
        self._record(method, entity, textPoint, isDriving)
        s = self._sketch
        s._check(entity, CIRCULAR, "entity")

        def measure() -> float:
            return float(entity.radius) * scale

        return s._dimension(kind, textPoint, isDriving, measure, "mm", {"entity": entity})

    def addRadialDimension(
        self, entity: Any, textPoint: Any, isDriving: bool = True
    ) -> FakeSketchDimension:
        kind = "SketchRadialDimension"
        return self._radius_dim("addRadialDimension", kind, entity, textPoint, isDriving, 1.0)

    def addDiameterDimension(
        self, entity: Any, textPoint: Any, isDriving: bool = True
    ) -> FakeSketchDimension:
        kind = "SketchDiameterDimension"
        return self._radius_dim("addDiameterDimension", kind, entity, textPoint, isDriving, 2.0)

    def addAngularDimension(
        self, lineOne: Any, lineTwo: Any, textPoint: Any, isDriving: bool = True
    ) -> FakeSketchDimension:
        """Measures the angle of the sector between the lines that contains ``textPoint``."""
        self._record("addAngularDimension", lineOne, lineTwo, textPoint, isDriving)
        s = self._sketch
        s._check(lineOne, LINE, "lineOne")
        s._check(lineTwo, LINE, "lineTwo")
        text = _v(textPoint)

        def measure() -> float:
            a1, b1 = lineOne._start._xyz(), lineOne._end._xyz()
            a2, b2 = lineTwo._start._xyz(), lineTwo._end._xyz()
            d1, d2 = _unit(_sub(b1, a1)), _unit(_sub(b2, a2))
            den = _cross2(d1, d2)
            if abs(den) < 1e-12:
                raise RuntimeError("addAngularDimension: the lines are parallel")
            corner = _add(a1, _mul(d1, _cross2(_sub(a2, a1), d2) / den))
            w = _sub(text, corner)
            s1 = -1.0 if _cross2(w, d2) / den < 0 else 1.0
            s2 = -1.0 if _cross2(d1, w) / den < 0 else 1.0
            cosine = _dot(_mul(d1, s1), _mul(d2, s2))
            return math.acos(max(-1.0, min(1.0, cosine)))

        roles = {"lineOne": lineOne, "lineTwo": lineTwo}
        return s._dimension("SketchAngularDimension", textPoint, isDriving, measure, "deg", roles)

    def _ellipse_dim(
        self, method: str, kind: str, ellipse: Any, textPoint: Any, isDriving: bool, attr: str
    ) -> FakeSketchDimension:
        self._record(method, ellipse, textPoint, isDriving)
        s = self._sketch
        s._check(ellipse, ("SketchEllipse", "SketchEllipticalArc"), "ellipse")

        def measure() -> float:
            return float(getattr(ellipse, attr))

        return s._dimension(kind, textPoint, isDriving, measure, "mm", {"ellipse": ellipse})

    def addEllipseMajorRadiusDimension(
        self, ellipse: Any, textPoint: Any, isDriving: bool = True
    ) -> FakeSketchDimension:
        return self._ellipse_dim(
            "addEllipseMajorRadiusDimension",
            "SketchEllipseMajorRadiusDimension",
            ellipse,
            textPoint,
            isDriving,
            "majorAxisRadius",
        )

    def addEllipseMinorRadiusDimension(
        self, ellipse: Any, textPoint: Any, isDriving: bool = True
    ) -> FakeSketchDimension:
        return self._ellipse_dim(
            "addEllipseMinorRadiusDimension",
            "SketchEllipseMinorRadiusDimension",
            ellipse,
            textPoint,
            isDriving,
            "minorAxisRadius",
        )


# --- profiles ------------------------------------------------------------------------------------


def _loop_polygon(curves: Sequence[Any]) -> list[XY]:
    """Closed polyline (sketch space) through the strokes of ``curves`` chained end to end."""
    pieces: list[list[XY]] = []
    for curve in curves:
        _, strokes = curve.geometry.evaluator.getStrokes(0.0, 1.0, 0.001)
        pieces.append([(p.x, p.y) for p in strokes])
    if not pieces:
        return []
    poly = list(pieces[0])
    if len(pieces) > 1:
        nxt = pieces[1]
        head = min(math.dist(poly[0], nxt[0]), math.dist(poly[0], nxt[-1]))
        tail = min(math.dist(poly[-1], nxt[0]), math.dist(poly[-1], nxt[-1]))
        if head < tail:
            poly.reverse()
        for piece in pieces[1:]:
            if math.dist(poly[-1], piece[-1]) < math.dist(poly[-1], piece[0]):
                piece = piece[::-1]
            poly.extend(piece[1:])
    if len(poly) > 1 and math.dist(poly[0], poly[-1]) < 1e-9:
        poly.pop()
    return poly


def polygon_area_centroid(poly: Sequence[XY]) -> tuple[float, float, float]:
    """``(area >= 0, cx, cy)`` of a simple polygon (shoelace)."""
    n = len(poly)
    if n < 3:
        return 0.0, 0.0, 0.0
    area = cx = cy = 0.0
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    area /= 2
    if abs(area) < 1e-15:
        return 0.0, sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n
    return abs(area), cx / (6 * area), cy / (6 * area)


def point_in_polygon(point: XY, poly: Sequence[XY]) -> bool:
    x, y = point
    inside = False
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i], poly[i - 1]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


class FakeAreaProperties:
    objectType = "adsk::fusion::AreaProperties"

    def __init__(self, area: float, centroid: Any, perimeter: float) -> None:
        self.area = area
        self.centroid = centroid
        self.perimeter = perimeter


class FakeProfileCurve:
    objectType = "adsk::fusion::ProfileCurve"

    def __init__(self, entity: Any) -> None:
        self.sketchEntity = entity

    @property
    def geometry(self) -> Any:
        """Sketch-space ``Curve3D`` of the sketch entity."""
        return self.sketchEntity.geometry

    @property
    def geometryType(self) -> int:
        return int(self.sketchEntity.geometry.curveType)


class FakeProfileLoop:
    objectType = "adsk::fusion::ProfileLoop"

    def __init__(self, profile: FakeProfile, curves: Sequence[Any], is_outer: bool) -> None:
        self.parentProfile = profile
        self.isOuter = is_outer
        self.profileCurves = FakeCollection([FakeProfileCurve(c) for c in curves])
        self._curves = list(curves)


class FakeProfile:
    """``Profile``: one outer loop and inner loops of sketch curves. ``areaProperties``
    computes area/centroid from the loops' strokes; the centroid is in model space
    (``centroid_space="model"``, through ``sketchToModelSpace``) or sketch space
    (``"sketch"``), see design Risks. ``boundingBox`` is sketch space."""

    objectType = "adsk::fusion::Profile"

    def __init__(
        self,
        sketch: Any,
        outer: Sequence[Any],
        inners: Iterable[Sequence[Any]] = (),
        centroid_space: str = "model",
    ) -> None:
        self._sketch = sketch
        self._valid = True
        self.centroid_space = centroid_space
        loops = [FakeProfileLoop(self, outer, True)] if outer else []
        loops += [FakeProfileLoop(self, inner, False) for inner in inners]
        self.profileLoops = FakeCollection(loops)
        self.accuracy_calls: list[int] = []

    @property
    def isValid(self) -> bool:
        return self._valid and bool(self._sketch.isValid)

    @property
    def parentSketch(self) -> Any:
        return self._sketch

    def _polygons(self) -> tuple[list[XY], list[list[XY]]]:
        outer: list[XY] = []
        inners: list[list[XY]] = []
        for loop in self.profileLoops:
            poly = _loop_polygon(loop._curves)
            if loop.isOuter:
                outer = poly
            else:
                inners.append(poly)
        return outer, inners

    def areaProperties(
        self, accuracy: int = adsk.fusion.CalculationAccuracy.MediumCalculationAccuracy
    ) -> FakeAreaProperties:
        self.accuracy_calls.append(accuracy)
        outer, inners = self._polygons()
        area, cx, cy = polygon_area_centroid(outer)
        mx, my = area * cx, area * cy
        for inner in inners:
            a, x, y = polygon_area_centroid(inner)
            area, mx, my = area - a, mx - a * x, my - a * y
        if area > 1e-15:
            cx, cy = mx / area, my / area
        centroid = P(cx, cy, 0.0)
        if self.centroid_space == "model":
            centroid = self._sketch.sketchToModelSpace(centroid)
        perimeter = sum(c.length for loop in self.profileLoops for c in loop._curves)
        return FakeAreaProperties(max(area, 0.0), centroid, perimeter)

    @property
    def boundingBox(self) -> FakeBoundingBox3D:
        outer, _ = self._polygons()
        return FakeBoundingBox3D([(x, y, 0.0) for x, y in outer])


class FakeProfileCollection(FakeCollection):
    pass


def _closed(curve: Any) -> bool:
    if isinstance(curve, FakeSketchFittedSpline):
        return curve.isClosed
    return curve.type_name in ("SketchCircle", "SketchEllipse")


def auto_profiles(sketch: Any) -> list[FakeProfile]:
    """Profile builder: loops are closed curves (circles, ellipses, closed splines) and
    simple cycles of lines/arcs/open splines joined at shared ``SketchPoint``s (every point
    of the component used by exactly two curves; construction curves ignored). Each loop is
    the outer loop of one profile whose inner loops are the loops directly inside it."""
    curves = [c for c in sketch._of(FakeSketchCurve) if not c.isConstruction]
    loops: list[list[Any]] = [[c] for c in curves if _closed(c)]
    open_curves = [c for c in curves if not _closed(c) and c._ends() is not None]
    adjacency: dict[int, list[Any]] = {}
    for curve in open_curves:
        for point in curve._ends():
            adjacency.setdefault(id(point), []).append(curve)
    seen: set[int] = set()
    for first in open_curves:
        if id(first) in seen:
            continue
        component, stack = [first], [first]
        seen.add(id(first))
        while stack:
            for point in stack.pop()._ends():
                for other in adjacency[id(point)]:
                    if id(other) not in seen:
                        seen.add(id(other))
                        component.append(other)
                        stack.append(other)
        nodes = {id(p) for c in component for p in c._ends()}
        if any(len(adjacency[n]) != 2 for n in nodes):
            continue
        ordered = [first]
        node = first._ends()[1]
        while True:
            nxt = next(c for c in adjacency[id(node)] if c is not ordered[-1])
            if nxt is first:
                break
            ordered.append(nxt)
            start, end = nxt._ends()
            node = end if start is node else start
        loops.append(ordered)
    polys = [_loop_polygon(loop) for loop in loops]
    areas = [polygon_area_centroid(poly)[0] for poly in polys]
    keep = [i for i, poly in enumerate(polys) if len(poly) >= 3 and areas[i] > 1e-12]
    parent: dict[int, int | None] = {}
    for i in keep:
        containers = [
            j
            for j in keep
            if j != i and areas[j] > areas[i] and point_in_polygon(polys[i][0], polys[j])
        ]
        parent[i] = min(containers, key=areas.__getitem__) if containers else None
    return [FakeProfile(sketch, loops[i], [loops[k] for k in keep if parent[k] == i]) for i in keep]


# --- the sketch ---------------------------------------------------------------------------------


def default_transform(plane: Any) -> Any:
    """Sketch -> model ``Matrix3D`` for a plane: origin = plane origin, z = normal, x = model
    X projected on the plane (or model -Z when the normal is along X). Gives Fusion's
    origin-plane sketches: XY (x=X, y=Y), XZ (x=X, y=-Z), YZ (x=-Z, y=Y)."""
    matrix = adsk.core.Matrix3D.create()
    geometry = getattr(plane, "geometry", None)
    origin = getattr(geometry, "origin", None)
    normal = getattr(geometry, "normal", None)
    if origin is None or normal is None:
        return matrix
    n = _unit(_v(normal))
    x = _sub((1.0, 0.0, 0.0), _mul(n, n[0]))
    if _len(x) < 1e-6:
        x = _sub((0.0, 0.0, -1.0), _mul(n, -n[2]))
    x = _unit(x)
    y = _cross3(n, x)
    matrix.setWithCoordinateSystem(P(*_v(origin)), V(*x), V(*y), V(*n))
    return matrix


class SketchApiMixin(Hideable):
    """The c05 ``Sketch`` API; ``FakeSketch`` (brep.py) calls ``_init_sketch_api`` after it
    has set ``entityToken``.

    Test hooks: ``calls``/``call_names()``/``reset_calls()``, ``fail_on``, ``solver``,
    ``profile_builder``, ``set_profiles``, ``allow_project_body`` (default False:
    ``project(body)`` raises like some Fusion versions), ``created_with``
    (``"add"``/``"addWithoutEdges"``), ``defer_log`` (every ``isComputeDeferred`` set).
    """

    objectType = "adsk::fusion::Sketch"
    entityToken: str

    def _init_sketch_api(
        self,
        *,
        reference_plane: Any = None,
        transform: Any = None,
        component: Any = None,
        design: Any = None,
        profile_count: int = 0,
    ) -> None:
        self._faults = FaultInjector()
        self._objects: list[_SketchObject] = []
        self._version = 0
        self._valid = True
        self._deferred = False
        self.defer_log: list[bool] = []
        self.parentComponent = component
        self.design = design
        self._own_params: FakeParameterSet | None = None
        self.solver: Callable[[Any, Any], None] | None = None
        self.profile_builder: Callable[[Any], list[FakeProfile]] | None = None
        self._explicit_profiles: list[FakeProfile] = [
            FakeProfile(self, []) for _ in range(profile_count)
        ]
        self._profile_cache: tuple[int, list[FakeProfile]] | None = None
        self.allow_project_body = False
        self.created_with: str | None = None
        self._timeline_item: Any = None
        self._collection: Any = None
        self._transform = (
            transform.copy() if transform is not None else default_transform(reference_plane)
        )
        origin = FakeSketchPoint(self, (0.0, 0.0, 0.0))
        origin._is_origin = True
        origin.isReference = True
        origin.isFixed = True
        origin.isFullyConstrained = True
        self.originPoint = self._register(origin)
        self.sketchPoints = FakeSketchPoints(self)
        self.sketchCurves = FakeSketchCurves(self)
        self.sketchTexts = FakeSketchTexts(self)
        self.geometricConstraints = FakeGeometricConstraints(self)
        self.sketchDimensions = FakeSketchDimensions(self)

    # --- test hooks ------------------------------------------------------------------

    @property
    def calls(self) -> list[tuple[str, tuple[Any, ...]]]:
        return self._faults.calls

    def call_names(self) -> list[str]:
        return [name for name, _ in self._faults.calls]

    def reset_calls(self) -> None:
        self._faults.calls.clear()

    def fail_on(self, method: str, n: int = 1, exc: BaseException | None = None) -> None:
        """Raise ``exc`` (default ``RuntimeError``) on the n-th call matching ``method``
        (full name, bare method name or ``"*"``), counted from now."""
        self._faults.fail_on(method, n, exc)

    def set_profiles(self, profiles: Iterable[FakeProfile]) -> None:
        self.profile_builder = None
        self._explicit_profiles = list(profiles)

    @property
    def parameter_set(self) -> FakeParameterSet:
        shared = getattr(self.design, "parameter_set", None)
        if isinstance(shared, FakeParameterSet):
            return shared
        if self._own_params is None:
            self._own_params = FakeParameterSet()
        return self._own_params

    # --- bookkeeping ---------------------------------------------------------------

    def _record(self, name: str, *args: Any) -> None:
        self._faults.record(name, *args)

    def _touch(self) -> None:
        self._version += 1

    def _register(self, obj: Any) -> Any:
        self._objects.append(obj)
        self._touch()
        if self.design is not None and hasattr(self.design, "add_entity"):
            self.design.add_entity(obj)
        return obj

    def _unregister(self, obj: Any) -> None:
        by_token = getattr(self.design, "by_token", None)
        token = obj.entityToken
        if isinstance(by_token, dict) and by_token.get(token) is obj:
            del by_token[token]

    def _of(self, cls: type) -> list[Any]:
        return [o for o in self._objects if o._valid and isinstance(o, cls)]

    def _users(self, point: FakeSketchPoint) -> list[Any]:
        return [c for c in self._of(FakeSketchCurve) if any(p is point for p in c._points())]

    def _delete(self, obj: _SketchObject) -> None:
        if not obj._valid:
            return
        obj._valid = False
        self._touch()
        self._unregister(obj)
        for other in self._objects:
            if (
                other._valid
                and isinstance(other, FakeGeometricConstraint | FakeSketchDimension)
                and any(ref is obj for ref in other._references())
            ):
                self._delete(other)
        if isinstance(obj, FakeSketchPoint):
            for curve in self._users(obj):
                self._delete(curve)
        elif isinstance(obj, FakeSketchCurve):
            for point in obj._points():
                if (
                    point._valid
                    and not point._standalone
                    and not point._is_origin
                    and not self._users(point)
                ):
                    self._delete(point)
        params = self.parameter_set
        for param in obj._params:
            if param in params.params:
                params.params.remove(param)
            param.deleted = True

    def _check(self, value: Any, kinds: tuple[str, ...], role: str) -> None:
        if (
            not isinstance(value, _SketchObject)
            or value._sketch is not self
            or not value._valid
            or value.type_name not in kinds
        ):
            got = getattr(value, "objectType", type(value).__name__)
            raise RuntimeError(f"{role}: expected {'/'.join(kinds)} of this sketch, got {got}")

    def _pos(self, spec: Any) -> Vec:
        """Position of a ``Point3D`` or a valid ``SketchPoint`` of this sketch."""
        if isinstance(spec, _SketchObject):
            self._check(spec, POINT, "point")
            position: Vec = spec._xyz()
            return position
        return _v(spec)

    def _as_point(self, spec: Any) -> FakeSketchPoint:
        """The ``SketchPoint`` itself (shared) or a new point at a ``Point3D``."""
        if isinstance(spec, FakeSketchPoint):
            self._check(spec, POINT, "point")
            return spec
        return self._new_point(_v(spec))

    def _solve(self, obj: Any) -> None:
        if self.solver is not None:
            self.solver(self, obj)

    # --- creation (not recorded) ---------------------------------------------------

    def _new_point(self, xyz: Vec, standalone: bool = False) -> FakeSketchPoint:
        point: FakeSketchPoint = self._register(FakeSketchPoint(self, xyz, standalone))
        return point

    def _line(self, start: FakeSketchPoint, end: FakeSketchPoint) -> FakeSketchLine:
        line: FakeSketchLine = self._register(FakeSketchLine(self, start, end))
        return line

    def _circle(self, center: FakeSketchPoint, radius: float) -> FakeSketchCircle:
        circle: FakeSketchCircle = self._register(FakeSketchCircle(self, center, radius))
        return circle

    def _arc(
        self, center: FakeSketchPoint, start: FakeSketchPoint, end: FakeSketchPoint
    ) -> FakeSketchArc:
        arc: FakeSketchArc = self._register(FakeSketchArc(self, center, start, end))
        return arc

    def _ellipse(
        self, center: FakeSketchPoint, axis: Vec, major: float, minor: float
    ) -> FakeSketchEllipse:
        ellipse: FakeSketchEllipse = self._register(
            FakeSketchEllipse(self, center, axis, major, minor)
        )
        return ellipse

    def _spline(self, points: Sequence[FakeSketchPoint]) -> FakeSketchFittedSpline:
        spline: FakeSketchFittedSpline = self._register(FakeSketchFittedSpline(self, points))
        return spline

    def _arc_three(self, start: Any, mid: Any, end: Any) -> FakeSketchArc:
        a, m, b = self._pos(start), self._pos(mid), self._pos(end)
        center = _circumcenter(a, m, b)
        cp = self._new_point(center)
        sp, ep = self._as_point(start), self._as_point(end)
        ccw = _cross2(_sub(m, a), _sub(b, a)) > 0
        return self._arc(cp, sp, ep) if ccw else self._arc(cp, ep, sp)

    def _tie(self, curve: Any, *specs: Any) -> None:
        for spec in specs:
            if isinstance(spec, FakeSketchPoint):
                self._constraint("CoincidentConstraint", point=spec, entity=curve)

    def _constraint(self, kind: str, **roles: Any) -> FakeGeometricConstraint:
        constraint: FakeGeometricConstraint = self._register(
            FakeGeometricConstraint(self, kind, **roles)
        )
        self._solve(constraint)
        return constraint

    def _new_parameter(self, owner: Any, expression: str, unit: str, role: str) -> Any:
        params = self.parameter_set
        k = 1
        while params.get(f"d{k}") is not None:
            k += 1
        param = FakeDimensionParameter(
            params, f"d{k}", expression, unit, owner, self.parentComponent, role
        )
        params.params.append(param)
        owner._params.append(param)
        return param

    def _dimension(
        self,
        kind: str,
        text_point: Any,
        is_driving: bool,
        measure: Callable[[], float],
        unit: str,
        roles: dict[str, Any],
    ) -> FakeSketchDimension:
        if text_point is None:
            raise RuntimeError("textPoint is required")
        value = measure()
        dimension = FakeSketchDimension(self, kind, text_point, is_driving, measure, unit, roles)
        dimension.parameter = self._new_parameter(dimension, _expression(value, unit), unit, kind)
        self._register(dimension)
        self._solve(dimension)
        return dimension

    def _value(self, value_input: Any, unit: str) -> float:
        """Internal value (cm, rad, count) of a ``ValueInput`` or a number."""
        kind = getattr(value_input, "kind", None)
        if kind == "real":
            return float(value_input.value)
        if kind == "string":
            return self.parameter_set.evaluate(value_input.value, unit)
        if isinstance(value_input, int | float):
            return float(value_input)
        raise RuntimeError(f"expected a ValueInput, got {type(value_input).__name__}")

    def _value_expression(self, value_input: Any, unit: str) -> str:
        if getattr(value_input, "kind", None) == "string":
            return str(value_input.value)
        return _expression(self._value(value_input, unit), unit)

    # --- coordinates -----------------------------------------------------------------

    @property
    def transform(self) -> Any:
        return self._transform.copy()

    @transform.setter
    def transform(self, value: Any) -> None:
        self._record("Sketch.transform", value)
        self._transform = value.copy()
        self._touch()

    def modelToSketchSpace(self, modelCoordinate: Any) -> Any:
        inverse = self._transform.copy()
        if not inverse.invert():
            raise RuntimeError("sketch transform is singular")
        point = P(*_v(modelCoordinate))
        point.transformBy(inverse)
        return point

    def sketchToModelSpace(self, sketchCoordinate: Any) -> Any:
        point = P(*_v(sketchCoordinate))
        point.transformBy(self._transform)
        return point

    @property
    def origin(self) -> Any:
        return self._transform.getAsCoordinateSystem()[0]

    @property
    def xDirection(self) -> Any:
        return self._transform.getAsCoordinateSystem()[1]

    @property
    def yDirection(self) -> Any:
        return self._transform.getAsCoordinateSystem()[2]

    # --- state -----------------------------------------------------------------------

    @property
    def isValid(self) -> bool:
        return self._valid

    @property
    def isComputeDeferred(self) -> bool:
        return self._deferred

    @isComputeDeferred.setter
    def isComputeDeferred(self, value: bool) -> None:
        self._deferred = bool(value)
        self.defer_log.append(self._deferred)

    @property
    def isFullyConstrained(self) -> bool:
        """All non-reference points and curves are fully constrained (hide it with
        ``sketch.hide("isFullyConstrained")`` to test the fallback)."""
        entities = [e for e in self._of(FakeSketchEntity) if not e.isReference]
        return all(e.isFullyConstrained for e in entities)

    @property
    def profiles(self) -> FakeProfileCollection:
        if self.profile_builder is None:
            return FakeProfileCollection(list(self._explicit_profiles))
        cache = self._profile_cache
        if cache is None or (cache[0] != self._version and not self._deferred):
            if cache is not None:
                for profile in cache[1]:
                    profile._valid = False
            cache = (self._version, list(self.profile_builder(self)))
            self._profile_cache = cache
        return FakeProfileCollection(list(cache[1]))

    def deleteMe(self) -> bool:
        self._record("Sketch.deleteMe")
        if not self._valid:
            raise RuntimeError("sketch is no longer valid")
        for obj in self._objects:
            if obj._valid:
                obj._valid = False
                self._unregister(obj)
        self._valid = False
        self._unregister(self)
        if self._collection is not None:
            self._collection._remove(self)
        item = self._timeline_item
        if item is not None:
            self._timeline_item = None
            item._timeline._remove(item)
        return True

    # --- Sketch methods ----------------------------------------------------------------

    def findConnectedCurves(self, curve: Any) -> Any:
        """``curve`` plus every curve chained to it through shared end points."""
        self._record("Sketch.findConnectedCurves", curve)
        self._check(curve, CURVES, "curve")
        found = [curve]
        stack = [curve]
        while stack:
            for point in stack.pop()._ends() or ():
                for other in self._users(point):
                    ends = other._ends() or ()
                    if any(p is point for p in ends) and not any(o is other for o in found):
                        found.append(other)
                        stack.append(other)
        return adsk.core.ObjectCollection.createWithArray(found)

    def project(self, entity: Any) -> Any:
        """Reference curves/points for an edge, face (its edges), vertex, construction point
        or axis, or another sketch's entity; circular edges become circles. A body raises
        unless ``allow_project_body``."""
        self._record("Sketch.project", entity)
        return adsk.core.ObjectCollection.createWithArray(self._project(entity, False))

    def include(self, entity: Any) -> Any:
        self._record("Sketch.include", entity)
        return adsk.core.ObjectCollection.createWithArray(self._project(entity, True))

    def intersectWithSketchPlane(self, entities: Any) -> Any:
        """Points where line edges cross the sketch plane; per face a line through them."""
        self._record("Sketch.intersectWithSketchPlane", entities)
        return adsk.core.ObjectCollection.createWithArray(self._intersect(_items(entities)))

    def projectCutEdges(self, body: Any) -> Any:
        self._record("Sketch.projectCutEdges", body)
        return adsk.core.ObjectCollection.createWithArray(self._intersect([body]))

    def offset(self, curves: Any, directionPoint: Any, offset: float) -> Any:
        """Non-parametric offset of lines (mitred at shared points), circles and arcs; a
        chain of connected curves goes to the side of ``directionPoint`` (decided by the
        curve nearest to it); a negative ``offset`` flips the side."""
        self._record("Sketch.offset", curves, directionPoint, offset)
        items = _items(curves)
        for curve in items:
            self._check(curve, CURVES, "curves")
        if abs(offset) < EPS:
            raise RuntimeError("offset must not be zero")
        created = self._offset_curves(items, _v(directionPoint), abs(offset), offset < 0)
        return adsk.core.ObjectCollection.createWithArray(created)

    def move(self, sketchEntities: Any, transform: Any) -> bool:
        self._record("Sketch.move", sketchEntities, transform)
        self._move(_items(sketchEntities), transform)
        return True

    def copy(self, sketchEntities: Any, transform: Any, targetSketch: Any = None) -> Any:
        self._record("Sketch.copy", sketchEntities, transform, targetSketch)
        if targetSketch is not None and targetSketch is not self:
            raise RuntimeError("fake copy only supports the same sketch")
        created = self._copy(_items(sketchEntities), transform)
        return adsk.core.ObjectCollection.createWithArray(created)

    # --- editing internals -------------------------------------------------------------

    def _move(self, entities: list[Any], matrix: Any) -> None:
        points: dict[int, FakeSketchPoint] = {}
        for entity in entities:
            if isinstance(entity, FakeSketchText):
                self._check(entity, ("SketchText",), "entity")
                entity._transform(matrix)
                continue
            self._check(entity, POINT + CURVES, "entity")
            if isinstance(entity, FakeSketchPoint):
                points[id(entity)] = entity
                continue
            for point in entity._points():
                points[id(point)] = point
            if isinstance(entity, FakeSketchEllipse):
                entity._axis = _unit(_apply_vec(matrix, entity._axis))
        for point in points.values():
            if not point._is_origin:
                point._pos = list(_apply(matrix, point._xyz()))
        self._touch()

    def _copy(self, entities: list[Any], matrix: Any) -> list[Any]:
        m = matrix.asArray()
        mirrored = m[0] * m[5] - m[1] * m[4] < 0
        points: dict[int, FakeSketchPoint] = {}

        def mp(point: FakeSketchPoint) -> FakeSketchPoint:
            if id(point) not in points:
                points[id(point)] = self._new_point(_apply(matrix, point._xyz()))
            return points[id(point)]

        for entity in entities:
            self._check(entity, POINT + CURVES, "entity")
        created: list[Any] = []
        for entity in entities:
            new: Any
            if isinstance(entity, FakeSketchPoint):
                new = mp(entity)
                new._standalone = True
            elif isinstance(entity, FakeSketchLine):
                new = self._line(mp(entity._start), mp(entity._end))
            elif isinstance(entity, FakeSketchCircle):
                new = self._circle(mp(entity._center), entity._radius)
            elif isinstance(entity, FakeSketchArc):
                c, s, e = mp(entity._center), mp(entity._start), mp(entity._end)
                new = self._arc(c, e, s) if mirrored else self._arc(c, s, e)
            elif isinstance(entity, FakeSketchEllipse):
                axis = _unit(_apply_vec(matrix, entity._axis))
                new = self._ellipse(mp(entity._center), axis, entity._major, entity._minor)
            elif isinstance(entity, FakeSketchFittedSpline):
                new = self._spline([mp(p) for p in entity._fit])
                new._closed = entity._closed
            else:
                raise RuntimeError(f"fake copy does not support {entity.type_name}")
            if isinstance(new, FakeSketchCurve):
                new.isConstruction = entity.isConstruction
            created.append(new)
        return created

    def _offset_sides(self, curves: list[Any], side: Vec | None) -> dict[int, bool]:
        """Per curve: offset to its left (lines: of start -> end; arcs/circles, which run
        counter-clockwise: inwards)? Without ``side``: left of lines, outside of circles.
        With ``side`` the whole chain (curves joined at shared end points) goes to one side,
        decided by the curve nearest to ``side``, like Fusion's direction point."""
        if side is None:
            return {id(c): isinstance(c, FakeSketchLine) for c in curves}
        result: dict[int, bool] = {}
        todo = list(curves)
        while todo:
            # Orient one connected chain: forward[id] = traversed start -> end.
            first = todo.pop(0)
            forward: dict[int, bool] = {id(first): True}
            chain, stack = [first], [first]
            while stack:
                cur = stack.pop()
                ends = cur._ends()
                if ends is None:
                    continue
                head, tail = ends if forward[id(cur)] else ends[::-1]
                for other in list(todo):
                    o_ends = other._ends()
                    if o_ends is None:
                        continue
                    if o_ends[0] is tail or o_ends[1] is head:
                        forward[id(other)] = True
                    elif o_ends[1] is tail or o_ends[0] is head:
                        forward[id(other)] = False
                    else:
                        continue
                    todo.remove(other)
                    chain.append(other)
                    stack.append(other)
            ref = min(chain, key=lambda c: _distance_to_curve(c, side))
            left = _is_left(ref, side) == forward[id(ref)]
            for curve in chain:
                result[id(curve)] = left == forward[id(curve)]
        return result

    def _offset_curves(
        self, curves: list[Any], side: Vec | None, distance: float, flip: bool
    ) -> list[Any]:
        candidates: dict[int, list[tuple[Vec, tuple[Vec, Vec] | None]]] = {}
        plans: list[tuple[str, Any, float]] = []
        for curve in curves:
            if not isinstance(curve, FakeSketchLine | FakeSketchCircle | FakeSketchArc):
                raise RuntimeError(f"fake offset does not support {curve.type_name}")
        sides = self._offset_sides(curves, side)
        for curve in curves:
            left = sides[id(curve)] != flip
            if isinstance(curve, FakeSketchLine):
                a, b = curve._start._xyz(), curve._end._xyz()
                n = _perp(_unit(_sub(b, a)))
                shift = _mul(n, distance if left else -distance)
                na, nb = _add(a, shift), _add(b, shift)
                for point, pos in ((curve._start, na), (curve._end, nb)):
                    candidates.setdefault(id(point), []).append((pos, (na, _sub(nb, na))))
                plans.append(("line", curve, 0.0))
            else:
                # Counter-clockwise arcs/circles: left is inwards.
                c, r = curve._center._xyz(), curve.radius
                radius = r - distance if left else r + distance
                if radius <= EPS:
                    raise RuntimeError("the offset collapses a circle or an arc")
                if isinstance(curve, FakeSketchArc):
                    for point in (curve._start, curve._end):
                        pos = _add(c, _mul(_unit(_sub(point._xyz(), c)), radius))
                        candidates.setdefault(id(point), []).append((pos, None))
                kind = "circle" if isinstance(curve, FakeSketchCircle) else "arc"
                plans.append((kind, curve, radius))
        resolved: dict[int, FakeSketchPoint] = {}

        def point_for(point: FakeSketchPoint) -> FakeSketchPoint:
            if id(point) not in resolved:
                options = candidates[id(point)]
                lines = [line for _, line in options if line is not None]
                pos = _intersect_lines(lines[0], lines[1]) if len(lines) >= 2 else None
                if pos is None:
                    count = len(options)
                    pos = (
                        sum(o[0][0] for o in options) / count,
                        sum(o[0][1] for o in options) / count,
                        0.0,
                    )
                resolved[id(point)] = self._new_point(pos)
            return resolved[id(point)]

        created: list[Any] = []
        for kind, curve, radius in plans:
            new: Any
            if kind == "line":
                new = self._line(point_for(curve._start), point_for(curve._end))
            elif kind == "circle":
                new = self._circle(curve._center, radius)
            else:
                new = self._arc(curve._center, point_for(curve._start), point_for(curve._end))
            new.isConstruction = curve.isConstruction
            created.append(new)
        return created

    def _fillet(
        self, first: Any, first_point: Any, second: Any, second_point: Any, radius: float
    ) -> FakeSketchArc:
        self._check(first, LINE, "firstEntity")
        self._check(second, LINE, "secondEntity")
        if first is second or radius <= EPS:
            raise RuntimeError("addFillet: two different lines and a positive radius")
        common = [p for p in first._ends() if p is second._start or p is second._end]
        a1, b1 = first._start._xyz(), first._end._xyz()
        a2, b2 = second._start._xyz(), second._end._xyz()
        meet = _intersect_lines((a1, _sub(b1, a1)), (a2, _sub(b2, a2)))
        if common:
            meet = common[0]._xyz()
        if meet is None:
            raise RuntimeError("addFillet: the lines are parallel")
        corner: Vec = meet

        def direction(a: Vec, b: Vec, pick: Vec) -> Vec:
            d = _unit(_sub(b, a))
            return d if _dot(_sub(pick, corner), d) >= 0 else _mul(d, -1.0)

        u1 = direction(a1, b1, _v(first_point))
        u2 = direction(a2, b2, _v(second_point))
        theta = math.acos(max(-1.0, min(1.0, _dot(u1, u2))))
        if theta < 1e-6 or theta > math.pi - 1e-6:
            raise RuntimeError("addFillet: the lines are parallel")
        reach = radius / math.tan(theta / 2)
        for line, u in ((first, u1), (second, u2)):
            far = max(_dot(_sub(p._xyz(), corner), u) for p in line._ends())
            if reach > far + EPS:
                raise RuntimeError("addFillet: the radius is too large")
        t1, t2 = _add(corner, _mul(u1, reach)), _add(corner, _mul(u2, reach))
        center = _add(corner, _mul(_unit(_add(u1, u2)), radius / math.sin(theta / 2)))
        old: list[FakeSketchPoint] = []
        new_points: list[FakeSketchPoint] = []
        for line, tangent in ((first, t1), (second, t2)):
            point = self._new_point(tangent)
            new_points.append(point)
            start_d = _dist(line._start._xyz(), corner)
            if start_d <= _dist(line._end._xyz(), corner):
                old.append(line._start)
                line._start = point
            else:
                old.append(line._end)
                line._end = point
        p1, p2 = new_points
        cp = self._new_point(center)
        ccw = _cross2(_sub(t1, center), _sub(t2, center)) > 0
        arc = self._arc(cp, p1, p2) if ccw else self._arc(cp, p2, p1)
        self._constraint("TangentConstraint", curveOne=first, curveTwo=arc)
        self._constraint("TangentConstraint", curveOne=second, curveTwo=arc)
        for point in old:
            if (
                point._valid
                and not point._standalone
                and not point._is_origin
                and not self._users(point)
            ):
                self._delete(point)
        self._touch()
        return arc

    def _others(self, curve: Any) -> list[Any]:
        return [c for c in self._of(FakeSketchCurve) if c is not curve]

    def _trim(self, curve: Any, p: Vec, create_constraints: bool) -> Any:
        """Lines, circles and arcs cut by lines, circles and arcs. The trimmed curve is
        deleted; the kept pieces are new curves (new tokens) sharing the untouched end
        points; with ``createConstraints`` each new end is coincident with the cutter."""
        self._check(curve, ("SketchLine", "SketchCircle", "SketchArc"), "curve")
        cuts: list[tuple[float, Any]] = []
        created: list[Any] = []
        ties: list[tuple[FakeSketchPoint, Any]] = []
        if isinstance(curve, FakeSketchLine):
            a, b = curve._start._xyz(), curve._end._xyz()
            for other in self._others(curve):
                for prim in _prims(other):
                    hits = _line_hits(a, b, prim)
                    cuts += [(t, other) for t in hits if CUT_EPS < t < 1 - CUT_EPS]
            tp = _dot(_sub(p, a), _sub(b, a)) / max(_dot(_sub(b, a), _sub(b, a)), EPS)
            lo = max([c for c in cuts if c[0] < tp], default=None, key=_first)
            hi = min([c for c in cuts if c[0] > tp], default=None, key=_first)
            if lo is not None:
                point = self._new_point(_lerp(a, b, lo[0]))
                created.append(self._line(curve._start, point))
                ties.append((point, lo[1]))
            if hi is not None:
                point = self._new_point(_lerp(a, b, hi[0]))
                created.append(self._line(point, curve._end))
                ties.append((point, hi[1]))
        elif isinstance(curve, FakeSketchCircle):
            c, r = curve._center._xyz(), curve.radius
            for other in self._others(curve):
                for prim in _prims(other):
                    cuts += [(th % TAU, other) for th in _circle_hits(c, r, prim)]
            if len({round(t, 9) for t, _ in cuts}) >= 2:
                tp = _angle(c, p)
                below = [x for x in cuts if x[0] < tp]
                above = [x for x in cuts if x[0] > tp]
                lo = max(below or cuts, key=_first)
                hi = min(above or cuts, key=_first)
                start = self._new_point(_polar(c, r, hi[0]))
                end = self._new_point(_polar(c, r, lo[0]))
                created.append(self._arc(curve._center, start, end))
                ties += [(start, hi[1]), (end, lo[1])]
        else:
            c, r = curve._center._xyz(), curve.radius
            a0, sweep = curve._angles()
            for other in self._others(curve):
                for prim in _prims(other):
                    for th in _circle_hits(c, r, prim):
                        u = ((th - a0) % TAU) / sweep
                        if CUT_EPS < u < 1 - CUT_EPS:
                            cuts.append((u, other))
            up = ((_angle(c, p) - a0) % TAU) / sweep
            if up > 1:
                up = 1.0 if (up - 1) * sweep < TAU - up * sweep else 0.0
            lo = max([x for x in cuts if x[0] < up], default=None, key=_first)
            hi = min([x for x in cuts if x[0] > up], default=None, key=_first)
            if lo is not None:
                point = self._new_point(_polar(c, r, a0 + sweep * lo[0]))
                created.append(self._arc(curve._center, curve._start, point))
                ties.append((point, lo[1]))
            if hi is not None:
                point = self._new_point(_polar(c, r, a0 + sweep * hi[0]))
                created.append(self._arc(curve._center, point, curve._end))
                ties.append((point, hi[1]))
        for new in created:
            new.isConstruction = curve.isConstruction
        if create_constraints:
            for point, other in ties:
                self._constraint("CoincidentConstraint", point=point, entity=other)
        self._delete(curve)
        return adsk.core.ObjectCollection.createWithArray(created)

    def _extend(self, curve: Any, p: Vec, create_constraints: bool) -> Any:
        """Lines and arcs: the end nearer ``p`` runs to the nearest cutter. The curve is
        replaced by a new one; nothing to reach -> empty collection, no change."""
        self._check(curve, ("SketchLine", "SketchArc"), "curve")
        best: tuple[float, Any] | None = None
        new: Any
        if isinstance(curve, FakeSketchLine):
            a, b = curve._start._xyz(), curve._end._xyz()
            tp = _dot(_sub(p, a), _sub(b, a)) / max(_dot(_sub(b, a), _sub(b, a)), EPS)
            at_end = tp >= 0.5
            hits = [
                (t, o)
                for o in self._others(curve)
                for pr in _prims(o)
                for t in _line_hits(a, b, pr)
            ]
            if at_end:
                best = min([h for h in hits if h[0] > 1 + CUT_EPS], default=None, key=_first)
            else:
                best = max([h for h in hits if h[0] < -CUT_EPS], default=None, key=_first)
            if best is None:
                return adsk.core.ObjectCollection.create()
            point = self._new_point(_lerp(a, b, best[0]))
            new = self._line(curve._start, point) if at_end else self._line(point, curve._end)
        else:
            c, r = curve._center._xyz(), curve.radius
            a0, sweep = curve._angles()
            at_end = _dist(p, curve._end._xyz()) <= _dist(p, curve._start._xyz())
            hits = [
                (th, o)
                for o in self._others(curve)
                for pr in _prims(o)
                for th in _circle_hits(c, r, pr)
            ]
            base = a0 + sweep
            deltas = [((th - base) % TAU if at_end else (a0 - th) % TAU, o) for th, o in hits]
            room = TAU - sweep - CUT_EPS
            best = min([d for d in deltas if CUT_EPS < d[0] < room], default=None, key=_first)
            if best is None:
                return adsk.core.ObjectCollection.create()
            if at_end:
                point = self._new_point(_polar(c, r, base + best[0]))
                new = self._arc(curve._center, curve._start, point)
            else:
                point = self._new_point(_polar(c, r, a0 - best[0]))
                new = self._arc(curve._center, point, curve._end)
        new.isConstruction = curve.isConstruction
        if create_constraints:
            self._constraint("CoincidentConstraint", point=point, entity=best[1])
        self._delete(curve)
        return adsk.core.ObjectCollection.createWithArray([new])

    # --- constraints with created geometry ----------------------------------------------

    def _offset_constraint(self, data: FakeOffsetConstraintInput) -> FakeGeometricConstraint:
        curves = list(data.curves)
        for curve in curves:
            self._check(curve, CURVES, "curves")
        distance = self._value(data.offset, "mm")
        if abs(distance) < EPS:
            raise RuntimeError("offset must not be zero")
        side = _v(data.dirPoint) if data.dirPoint is not None else None
        flip = bool(data.isFlipped) != (distance < 0)
        created = self._offset_curves(curves, side, abs(distance), flip)
        constraint: Any = self._constraint("OffsetConstraint", parentCurves=curves)
        constraint.childCurves = FakeCollection(created)
        constraint.isTopologyMatched = data.isTopologyMatched
        first = created[0]
        text = first._points()[0]._xyz()
        expression = self._value_expression(data.offset, "mm")
        dimension = self._dimension(
            "SketchOffsetCurvesDimension",
            P(*text),
            True,
            lambda: abs(distance),
            "mm",
            {"offsetConstraint": constraint},
        )
        dimension.parameter._expression = expression
        constraint.dimension = dimension
        return constraint

    def _direction(self, entity: Any, default: Vec) -> Vec:
        if entity is None:
            return default
        self._check(entity, LINE, "direction")
        return _unit(_sub(entity._end._xyz(), entity._start._xyz()))

    def _pattern_parameter(self, owner: Any, value_input: Any, unit: str, role: str) -> Any:
        return self._new_parameter(owner, self._value_expression(value_input, unit), unit, role)

    def _rectangular_pattern(
        self, data: FakeRectangularPatternConstraintInput
    ) -> FakeGeometricConstraint:
        entities = list(data.entities)
        for entity in entities:
            self._check(entity, POINT + CURVES, "entities")
        q1 = round(self._value(data.quantityOne, ""))
        q2 = round(self._value(data.quantityTwo, ""))
        if q1 < 1 or q2 < 1 or q1 * q2 < 2:
            raise RuntimeError("a pattern needs at least two instances")
        d1, d2 = self._value(data.distanceOne, "mm"), self._value(data.distanceTwo, "mm")
        dir1 = self._direction(data.directionOneEntity, (1.0, 0.0, 0.0))
        dir2 = self._direction(data.directionTwoEntity, (0.0, 1.0, 0.0))
        extent = data.distanceType == adsk.fusion.PatternDistanceType.ExtentPatternDistanceType
        step1 = d1 / (q1 - 1) if extent and q1 > 1 else d1
        step2 = d2 / (q2 - 1) if extent and q2 > 1 else d2
        created: list[Any] = []
        for i in range(q1):
            for j in range(q2):
                if i == j == 0:
                    continue
                matrix = adsk.core.Matrix3D.create()
                matrix.translation = V(*_add(_mul(dir1, step1 * i), _mul(dir2, step2 * j)))
                created += self._copy(entities, matrix)
        constraint: Any = self._constraint("RectangularPatternConstraint", entities=entities)
        constraint.createdEntities = adsk.core.ObjectCollection.createWithArray(created)
        constraint.distanceType = data.distanceType
        constraint.directionOneEntity = data.directionOneEntity
        constraint.directionTwoEntity = data.directionTwoEntity
        constraint.quantityOne = self._pattern_parameter(constraint, data.quantityOne, "", "q1")
        constraint.quantityTwo = self._pattern_parameter(constraint, data.quantityTwo, "", "q2")
        constraint.distanceOne = self._pattern_parameter(constraint, data.distanceOne, "mm", "d1")
        constraint.distanceTwo = self._pattern_parameter(constraint, data.distanceTwo, "mm", "d2")
        return constraint

    def _circular_pattern(
        self, data: FakeCircularPatternConstraintInput
    ) -> FakeGeometricConstraint:
        entities = list(data.entities)
        for entity in entities:
            self._check(entity, POINT + CURVES, "entities")
        center = data.centerPoint
        self._check(center, POINT, "centerPoint")
        count = round(self._value(data.quantity, ""))
        if count < 2:
            raise RuntimeError("a pattern needs at least two instances")
        total = self._value(data.totalAngle, "deg")
        full = abs(abs(total) - TAU) < 1e-9
        step = total / count if full else total / (count - 1)
        if data.isClockwise:
            step = -step
        created: list[Any] = []
        for k in range(1, count):
            matrix = adsk.core.Matrix3D.create()
            matrix.setToRotation(step * k, V(0, 0, 1), P(*center._xyz()))
            created += self._copy(entities, matrix)
        constraint: Any = self._constraint(
            "CircularPatternConstraint", entities=entities, centerPoint=center
        )
        constraint.createdEntities = adsk.core.ObjectCollection.createWithArray(created)
        constraint.isClockwise = data.isClockwise
        constraint.isSymmetric = data.isSymmetric
        constraint.quantity = self._pattern_parameter(constraint, data.quantity, "", "quantity")
        constraint.totalAngle = self._pattern_parameter(constraint, data.totalAngle, "deg", "angle")
        return constraint

    # --- projection / intersection ------------------------------------------------------

    def _to_sketch(self, model: Vec) -> Vec:
        q = self.modelToSketchSpace(P(*model))
        return (q.x, q.y, q.z)

    def _project(self, entity: Any, linked: bool) -> list[Any]:
        if isinstance(entity, _SketchObject) and entity._sketch is self:
            raise RuntimeError("cannot project an entity of the same sketch")
        cache: dict[tuple[float, float], FakeSketchPoint] = {}

        def at(xyz: Vec) -> FakeSketchPoint:
            key = (round(xyz[0], 9), round(xyz[1], 9))
            if key not in cache:
                cache[key] = self._new_point((xyz[0], xyz[1], 0.0))
            return cache[key]

        created: list[Any] = []
        self._project_into(entity, at, created)
        for obj in [*created, *cache.values()]:
            for sub in [obj, *(obj._points() if isinstance(obj, FakeSketchCurve) else [])]:
                sub.isReference = True
                sub.isLinked = linked
                sub.isFullyConstrained = True
                sub.referencedEntity = entity
        return created

    def _project_into(
        self, entity: Any, at: Callable[[Vec], FakeSketchPoint], out: list[Any]
    ) -> None:
        kind = _kind(entity)
        if kind == "BRepBody":
            if not self.allow_project_body:
                raise RuntimeError("project: a body cannot be projected")
            for edge in entity.edges:
                self._project_into(edge, at, out)
        elif kind == "BRepFace":
            for edge in entity.edges:
                self._project_into(edge, at, out)
        elif kind == "BRepEdge":
            self._project_curve(entity.geometry, at, out)
        elif kind in ("BRepVertex", "ConstructionPoint"):
            out.append(at(self._to_sketch(_v(entity.geometry))))
        elif kind == "ConstructionAxis":
            o, d = _v(entity.geometry.origin), _unit(_v(entity.geometry.direction))
            a = at(self._to_sketch(_sub(o, _mul(d, 10.0))))
            b = at(self._to_sketch(_add(o, _mul(d, 10.0))))
            out.append(a if a is b else self._line(a, b))
        elif kind == "SketchPoint":
            out.append(at(self._to_sketch(_v(entity.worldGeometry))))
        elif kind == "SketchArc":
            s = at(self._to_sketch(_v(entity.startSketchPoint.worldGeometry)))
            e = at(self._to_sketch(_v(entity.endSketchPoint.worldGeometry)))
            _, mid = entity.worldGeometry.evaluator.getPointAtParameter(0.5)
            m = self._to_sketch(_v(mid))
            center = self._new_point(_circumcenter(s._xyz(), m, e._xyz()))
            ccw = _cross2(_sub(m, s._xyz()), _sub(e._xyz(), s._xyz())) > 0
            out.append(self._arc(center, s, e) if ccw else self._arc(center, e, s))
            center.isReference = True
            center.isFullyConstrained = True
        elif kind in CURVES and hasattr(entity, "worldGeometry"):
            self._project_curve(entity.worldGeometry, at, out)
        else:
            raise RuntimeError(f"project: cannot project {kind}")

    def _project_curve(
        self, geometry: Any, at: Callable[[Vec], FakeSketchPoint], out: list[Any]
    ) -> None:
        kind = _kind(geometry)
        if kind == "Line3D":
            a = at(self._to_sketch(_v(geometry.startPoint)))
            b = at(self._to_sketch(_v(geometry.endPoint)))
            out.append(a if a is b else self._line(a, b))
        elif kind in ("Circle3D", "Arc3D"):
            out.append(self._circle(at(self._to_sketch(_v(geometry.center))), geometry.radius))
        elif kind == "Ellipse3D":
            inverse = self._transform.copy()
            inverse.invert()
            axis = _apply_vec(inverse, _v(geometry.majorAxis))
            center = at(self._to_sketch(_v(geometry.center)))
            ellipse = self._ellipse(
                center, _unit((axis[0], axis[1], 0.0)), geometry.majorRadius, geometry.minorRadius
            )
            out.append(ellipse)
        else:
            raise RuntimeError(f"project: unsupported curve {kind}")

    def _edge_cuts(self, edge: Any) -> list[Vec]:
        geometry = edge.geometry
        if _kind(geometry) != "Line3D":
            return []
        a = self._to_sketch(_v(geometry.startPoint))
        b = self._to_sketch(_v(geometry.endPoint))
        if (a[2] > EPS and b[2] > EPS) or (a[2] < -EPS and b[2] < -EPS):
            return []
        if abs(a[2] - b[2]) < EPS:
            return []
        t = a[2] / (a[2] - b[2])
        x, y, _ = _lerp(a, b, t)
        return [(x, y, 0.0)]

    def _intersect(self, entities: list[Any]) -> list[Any]:
        cache: dict[tuple[float, float], FakeSketchPoint] = {}

        def at(xyz: Vec) -> FakeSketchPoint:
            key = (round(xyz[0], 9), round(xyz[1], 9))
            if key not in cache:
                cache[key] = self._new_point(xyz)
            return cache[key]

        created: list[Any] = []
        for entity in entities:
            kind = _kind(entity)
            if kind == "BRepEdge":
                created += [at(xyz) for xyz in self._edge_cuts(entity)]
                continue
            if kind == "BRepBody":
                faces = list(entity.faces)
            elif kind == "BRepFace":
                faces = [entity]
            else:
                raise RuntimeError(f"intersect: cannot intersect {kind}")
            for face in faces:
                points: list[FakeSketchPoint] = []
                for edge in face.edges:
                    for xyz in self._edge_cuts(edge):
                        point = at(xyz)
                        if not any(p is point for p in points):
                            points.append(point)
                if len(points) >= 2:
                    created.append(self._line(points[0], points[1]))
        for obj in [*created, *cache.values()]:
            obj.isReference = True
            obj.isFullyConstrained = True
        return created


# --- intersections for trim/extend ------------------------------------------------------------

Prim = tuple[Any, ...]


def _prims(curve: Any) -> list[Prim]:
    if isinstance(curve, FakeSketchLine):
        return [("seg", curve._start._xyz(), curve._end._xyz())]
    if isinstance(curve, FakeSketchCircle):
        return [("circ", curve._center._xyz(), curve.radius, None)]
    if isinstance(curve, FakeSketchArc):
        return [("circ", curve._center._xyz(), curve.radius, curve._angles())]
    return []


def _in_range(prim: Prim, point: Vec) -> bool:
    if prim[0] == "seg":
        a, b = prim[1], prim[2]
        d = _sub(b, a)
        t = _dot(_sub(point, a), d) / max(_dot(d, d), EPS)
        return -CUT_EPS <= t <= 1 + CUT_EPS
    arc = prim[3]
    if arc is None:
        return True
    a0, sweep = arc
    return (_angle(prim[1], point) - a0) % TAU <= sweep + CUT_EPS


def _line_hits(a: Vec, b: Vec, prim: Prim) -> list[float]:
    """Parameters t (point ``a + t (b - a)``, unbounded) where the line meets ``prim``."""
    d = _sub(b, a)
    if prim[0] == "seg":
        p, q = prim[1], prim[2]
        e = _sub(q, p)
        den = _cross2(d, e)
        if abs(den) < 1e-12:
            return []
        t = _cross2(_sub(p, a), e) / den
        s = _cross2(_sub(p, a), d) / den
        return [t] if -CUT_EPS <= s <= 1 + CUT_EPS else []
    c, r = prim[1], prim[2]
    f = _sub(a, c)
    qa, qb, qc = _dot(d, d), 2 * _dot(f, d), _dot(f, f) - r * r
    disc = qb * qb - 4 * qa * qc
    if qa < EPS or disc < 0:
        return []
    root = math.sqrt(disc)
    ts = sorted({(-qb - root) / (2 * qa), (-qb + root) / (2 * qa)})
    return [t for t in ts if _in_range(prim, _lerp(a, b, t))]


def _circle_hits(c: Vec, r: float, prim: Prim) -> list[float]:
    """Angles on the circle ``(c, r)`` where it meets ``prim``."""
    if prim[0] == "seg":
        p, q = prim[1], prim[2]
        circle = ("circ", c, r, None)
        hits = [t for t in _line_hits(p, q, circle) if -CUT_EPS <= t <= 1 + CUT_EPS]
        return [_angle(c, _lerp(p, q, t)) for t in hits]
    c2, r2 = prim[1], prim[2]
    d = _dist(c, c2)
    if d < EPS or d > r + r2 + CUT_EPS or d < abs(r - r2) - CUT_EPS:
        return []
    along = (r * r - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(0.0, r * r - along * along))
    u = _unit(_sub(c2, c))
    base = _add(c, _mul(u, along))
    points = [_add(base, _mul(_perp(u), h))]
    if h > EPS:
        points.append(_add(base, _mul(_perp(u), -h)))
    return [_angle(c, pt) for pt in points if _in_range(prim, pt)]


def _distance_to_curve(curve: Any, p: Vec) -> float:
    """Distance from ``p`` to a line segment, or to the circle of an arc/circle."""
    if isinstance(curve, FakeSketchLine):
        a, b = curve._start._xyz(), curve._end._xyz()
        d = _sub(b, a)
        t = min(1.0, max(0.0, _dot(_sub(p, a), d) / max(_dot(d, d), EPS)))
        return _dist(p, _lerp(a, b, t))
    return abs(_dist(p, curve._center._xyz()) - curve.radius)


def _is_left(curve: Any, p: Vec) -> bool:
    """``p`` is left of a line (start -> end) or inside an arc/circle (counter-clockwise)."""
    if isinstance(curve, FakeSketchLine):
        a, b = curve._start._xyz(), curve._end._xyz()
        return _cross2(_sub(b, a), _sub(p, a)) >= 0
    return bool(_dist(p, curve._center._xyz()) < curve.radius)


# --- sketches collection and helpers -----------------------------------------------------------


def _is_planar(entity: Any) -> bool:
    kind = _kind(entity)
    if kind == "ConstructionPlane":
        return True
    if kind == "BRepFace":
        return _kind(getattr(entity, "geometry", None)) == "Plane"
    return False


class FakeSketches(FakeCollection):
    """``component.sketches``. ``add(plane)``/``addWithoutEdges(plane)`` create a
    ``FakeSketch`` (recorded in ``calls`` as ``sketches.add``/``sketches.addWithoutEdges``,
    ``sketch.created_with``); ``add`` on a planar face projects the face's edges. In a
    parametric ``design`` a timeline item is added whose ``entity`` is the sketch (rolling it
    back deletes the sketch). ``add(<FakeSketch>)`` just appends an existing sketch (c03)."""

    def __init__(self, component: Any = None, design: Any = None) -> None:
        super().__init__()
        self.component = component
        self.design = design
        self._faults = FaultInjector()

    @property
    def calls(self) -> list[tuple[str, tuple[Any, ...]]]:
        return self._faults.calls

    def fail_on(self, method: str, n: int = 1, exc: BaseException | None = None) -> None:
        self._faults.fail_on(method, n, exc)

    def add(self, item: Any) -> Any:
        if isinstance(item, SketchApiMixin):
            item._collection = self
            return super().add(item)
        return self._create("add", item, True)

    def addWithoutEdges(self, planarEntity: Any) -> Any:
        return self._create("addWithoutEdges", planarEntity, False)

    def _create(self, method: str, plane: Any, project_edges: bool) -> Any:
        self._faults.record(f"sketches.{method}", plane)
        if not _is_planar(plane):
            raise RuntimeError(f"{_kind(plane)} is not a plane or a planar face")
        from .brep import FakeSketch

        k = self.count + 1
        while self.itemByName(f"Sketch{k}") is not None:
            k += 1
        sketch = FakeSketch(
            f"Sketch{k}", reference_plane=plane, component=self.component, design=self.design
        )
        sketch.created_with = method
        sketch._collection = self
        super().add(sketch)
        design = self.design
        if design is not None:
            if hasattr(design, "add_entity"):
                design.add_entity(sketch)
            if getattr(design, "parametric", False):
                item = design.timeline.add(sketch.name)
                item.entity = sketch
                sketch._timeline_item = item
        if project_edges and _kind(plane) == "BRepFace":
            sketch._project(plane, False)
        return sketch

    def _remove(self, sketch: Any) -> None:
        self._items = [s for s in self._items if s is not sketch]


def fake_sketch_design(*bodies: Any, parametric: bool = True) -> Any:
    """``fake_part_design`` (c03) with parameters/units manager (c04) whose root component
    creates sketches through ``FakeSketches``."""
    from .brep import fake_part_design

    design = fake_part_design(*bodies, parametric=parametric)
    attach_parameters(design)
    return design


def make_sketch(design: Any = None, plane: Any = "XY", project_edges: bool = False) -> Any:
    """New sketch on ``origin:<plane>`` (or a plane/face entity) of ``design``'s root
    (default: a new ``fake_sketch_design()``), with the creation call cleared from logs."""
    from .brep import ORIGIN_ATTRS

    design = design if design is not None else fake_sketch_design()
    root = design.rootComponent
    entity = getattr(root, ORIGIN_ATTRS[plane]) if isinstance(plane, str) else plane
    sketches = root.sketches
    sketch = sketches.add(entity) if project_edges else sketches.addWithoutEdges(entity)
    sketches.calls.clear()
    sketch.reset_calls()
    return sketch


def polygon_curves(sketch: Any, points: Sequence[Any], closed: bool = True) -> list[Any]:
    """Lines through ``points`` (sketch cm, ``(x, y)``) on shared points, not recorded."""
    pts = [sketch._new_point(_v(p)) for p in points]
    pairs = list(itertools.pairwise(pts)) + ([(pts[-1], pts[0])] if closed else [])
    return [sketch._line(a, b) for a, b in pairs]


def circle_curve(sketch: Any, center: Any, radius: float) -> Any:
    """A circle (sketch cm), not recorded."""
    return sketch._circle(sketch._new_point(_v(center)), radius)


def add_profile(
    sketch: Any, outer: Sequence[Any], *inners: Sequence[Any], centroid_space: str = "model"
) -> FakeProfile:
    """Append an explicit profile (outer loop + inner loops, each a list of sketch curves)."""
    profile = FakeProfile(sketch, outer, inners, centroid_space)
    sketch._explicit_profiles.append(profile)
    return profile


def remove_optional_api(sketch: Any) -> None:
    """Hide ``createOffsetInput``/``addOffset2``/``create*PatternInput``/``add*Pattern``."""
    sketch.geometricConstraints.remove_optional()


def mark_fully_constrained(sketch: Any, value: bool = True) -> None:
    for entity in sketch._of(FakeSketchEntity):
        entity.isFullyConstrained = value


def simple_solver(sketch: Any, obj: Any) -> None:
    """Applies driving radial, diameter, ellipse-radius and linear (distance) dimensions to
    the geometry; other constraints and dimensions are not solved."""
    if not isinstance(obj, FakeSketchDimension) or not obj.isDriving:
        return
    value = obj.parameter.value
    kind = obj.type_name
    if kind in ("SketchRadialDimension", "SketchDiameterDimension"):
        radius = value if kind == "SketchRadialDimension" else value / 2
        curve = obj.entity
        if isinstance(curve, FakeSketchCircle):
            curve._radius = radius
        else:
            c = curve._center._xyz()
            for point in (curve._start, curve._end):
                point._pos = list(_polar(c, radius, _angle(c, point._xyz())))
    elif kind == "SketchEllipseMajorRadiusDimension":
        obj.ellipse._major = value
    elif kind == "SketchEllipseMinorRadiusDimension":
        obj.ellipse._minor = value
    elif kind == "SketchLinearDimension":
        fixed, moving = obj.entityOne, obj.entityTwo
        if moving._is_origin or moving.isFixed:
            fixed, moving = moving, fixed
        a, b = fixed._xyz(), moving._xyz()
        orientation = obj.orientation
        if orientation == adsk.fusion.DimensionOrientations.HorizontalDimensionOrientation:
            moving._pos[0] = a[0] + (value if b[0] >= a[0] else -value)
        elif orientation == adsk.fusion.DimensionOrientations.VerticalDimensionOrientation:
            moving._pos[1] = a[1] + (value if b[1] >= a[1] else -value)
        else:
            moving._pos = list(_add(a, _mul(_unit(_sub(b, a)), value)))
    sketch._touch()

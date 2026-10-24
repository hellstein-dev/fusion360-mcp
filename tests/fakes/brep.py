"""Fake B-Rep, component and feature objects for inspection tests (c03 D13).

Geometry is in Fusion's internal units (cm, radians). Points and vectors are the stub
``adsk.core.Point3D``/``Vector3D``. ``fake_box_body(w, h, d)`` builds an axis-aligned box
from the origin with 6 faces, 12 edges and 8 vertices; later changes reuse these fakes.
"""

from __future__ import annotations

import itertools
from typing import Any

import fusion_addin

from .design import FakeCollection, FakeDesign
from .fusion_sketch import FakeProfileCollection, FakeSketches, SketchApiMixin

adsk = fusion_addin.adsk
P = adsk.core.Point3D.create
V = adsk.core.Vector3D.create
ST = adsk.core.SurfaceTypes
CT = adsk.core.Curve3DTypes

_ids = itertools.count(1)


def _token(prefix: str) -> str:
    return f"{prefix}{next(_ids)}"


class FakeBoundingBox:
    def __init__(self, lo: tuple[float, float, float], hi: tuple[float, float, float]) -> None:
        self.minPoint = P(*lo)
        self.maxPoint = P(*hi)


def bbox_of(points: list[Any]) -> FakeBoundingBox:
    xs, ys, zs = ([getattr(p, a) for p in points] for a in "xyz")
    return FakeBoundingBox((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


class FakeMatrix:
    """``Matrix3D``: 3x3 rotation (rows) plus translation in cm."""

    def __init__(
        self,
        rotation: list[list[float]] | None = None,
        translation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> None:
        self.rotation = rotation or [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        self.translation = V(*translation)

    def asArray(self) -> list[float]:
        r, t = self.rotation, self.translation
        return [*r[0], t.x, *r[1], t.y, *r[2], t.z, 0.0, 0.0, 0.0, 1.0]


class Proxyable:
    """``createForAssemblyContext`` for native fakes: a ``FakeProxy`` (tests/fakes/assembly.py)."""

    def createForAssemblyContext(self, occurrence: Any) -> Any:
        from .assembly import FakeProxy

        return FakeProxy(self, occurrence)


# --- surfaces and curves --------------------------------------------------------------------


class FakeSurface:
    def __init__(self, surface_type: int, object_type: str, **attrs: Any) -> None:
        self.surfaceType = surface_type
        self.objectType = f"adsk::core::{object_type}"
        for key, value in attrs.items():
            setattr(self, key, value)


def plane_surface(origin: Any, normal: Any) -> FakeSurface:
    return FakeSurface(ST.PlaneSurfaceType, "Plane", origin=origin, normal=normal)


def cylinder_surface(origin: Any, axis: Any, radius: float) -> FakeSurface:
    return FakeSurface(ST.CylinderSurfaceType, "Cylinder", origin=origin, axis=axis, radius=radius)


def cone_surface(origin: Any, axis: Any, radius: float, half_angle: float) -> FakeSurface:
    return FakeSurface(
        ST.ConeSurfaceType, "Cone", origin=origin, axis=axis, radius=radius, halfAngle=half_angle
    )


def sphere_surface(origin: Any, radius: float) -> FakeSurface:
    return FakeSurface(ST.SphereSurfaceType, "Sphere", origin=origin, radius=radius)


def torus_surface(origin: Any, axis: Any, major: float, minor: float) -> FakeSurface:
    return FakeSurface(
        ST.TorusSurfaceType,
        "Torus",
        origin=origin,
        axis=axis,
        majorRadius=major,
        minorRadius=minor,
    )


def nurbs_surface() -> FakeSurface:
    return FakeSurface(ST.NurbsSurfaceType, "NurbsSurface")


class FakeCurve:
    def __init__(self, curve_type: int, object_type: str, **attrs: Any) -> None:
        self.curveType = curve_type
        self.objectType = f"adsk::core::{object_type}"
        for key, value in attrs.items():
            setattr(self, key, value)


def line_curve(start: Any, end: Any) -> FakeCurve:
    return FakeCurve(CT.Line3DCurveType, "Line3D", startPoint=start, endPoint=end)


def arc_curve(center: Any, normal: Any, radius: float) -> FakeCurve:
    return FakeCurve(CT.Arc3DCurveType, "Arc3D", center=center, normal=normal, radius=radius)


def circle_curve(center: Any, normal: Any, radius: float) -> FakeCurve:
    return FakeCurve(CT.Circle3DCurveType, "Circle3D", center=center, normal=normal, radius=radius)


def ellipse_curve(center: Any, normal: Any, major: float, minor: float) -> FakeCurve:
    return FakeCurve(
        CT.Ellipse3DCurveType,
        "Ellipse3D",
        center=center,
        normal=normal,
        majorRadius=major,
        minorRadius=minor,
    )


def nurbs_curve() -> FakeCurve:
    return FakeCurve(CT.NurbsCurve3DCurveType, "NurbsCurve3D")


# --- B-Rep -----------------------------------------------------------------------------------


class FakeEvaluator:
    def __init__(self, normal: Any) -> None:
        self._normal = normal
        self.calls: list[Any] = []

    def getNormalAtPoint(self, point: Any) -> tuple[bool, Any]:
        self.calls.append(point)
        if self._normal is None:
            return False, None
        return True, self._normal.copy()


class FakeVertex(Proxyable):
    objectType = "adsk::fusion::BRepVertex"

    def __init__(self, geometry: Any, token: str | None = None, body: Any = None) -> None:
        self.geometry = geometry
        self.entityToken = token or _token("vertex-")
        self.body = body
        self.edges = FakeCollection()


class FakeEdge(Proxyable):
    objectType = "adsk::fusion::BRepEdge"

    def __init__(
        self,
        geometry: Any,
        length: float,
        point_on_edge: Any,
        start: FakeVertex | None = None,
        end: FakeVertex | None = None,
        token: str | None = None,
        body: Any = None,
        bounding_box: FakeBoundingBox | None = None,
    ) -> None:
        self.geometry = geometry
        self.length = length
        self.pointOnEdge = point_on_edge
        self.startVertex = start
        self.endVertex = end
        self.entityToken = token or _token("edge-")
        self.body = body
        self.faces = FakeCollection()
        ends = [v.geometry for v in (start, end) if v is not None]
        self.boundingBox = bounding_box or bbox_of(ends or [point_on_edge])


class FakeFace(Proxyable):
    objectType = "adsk::fusion::BRepFace"

    def __init__(
        self,
        geometry: Any,
        area: float,
        centroid: Any,
        normal: Any = None,
        point_on_face: Any = None,
        token: str | None = None,
        body: Any = None,
        bounding_box: FakeBoundingBox | None = None,
    ) -> None:
        self.geometry = geometry
        self.area = area
        self.centroid = centroid
        self.pointOnFace = point_on_face or centroid
        self.evaluator = FakeEvaluator(normal)
        self.entityToken = token or _token("face-")
        self.body = body
        self.appearance: Any = None
        self.edges = FakeCollection()
        self.vertices = FakeCollection()
        self.boundingBox = bounding_box or bbox_of([centroid])


class FakePhysicalProperties:
    def __init__(
        self,
        mass: float = 0.0,
        volume: float = 0.0,
        area: float = 0.0,
        density: float = 0.0,
        center: Any = None,
        moments: tuple[float, ...] | None = (1.0, 2.0, 3.0, 0.1, 0.2, 0.3),
        principal: tuple[float, ...] | None = (1.0, 2.0, 3.0),
    ) -> None:
        self.mass = mass
        self.volume = volume
        self.area = area
        self.density = density
        self.centerOfMass = center or P(0, 0, 0)
        self._moments = moments
        self._principal = principal

    def getXYZMomentsOfInertia(self) -> tuple[Any, ...]:
        if self._moments is None:
            return (False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        return (True, *self._moments)

    def getPrincipalMomentsOfInertia(self) -> tuple[Any, ...]:
        if self._principal is None:
            return (False, 0.0, 0.0, 0.0)
        return (True, *self._principal)


class FakeMaterial:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeBody(Proxyable):
    objectType = "adsk::fusion::BRepBody"

    def __init__(
        self,
        name: str = "Body1",
        token: str | None = None,
        volume: float = 0.0,
        area: float = 0.0,
        is_solid: bool = True,
        is_visible: bool = True,
        material: str | None = None,
        component: Any = None,
        bounding_box: FakeBoundingBox | None = None,
        physical: FakePhysicalProperties | None = None,
    ) -> None:
        self.name = name
        self.entityToken = token or _token("body-")
        self.volume = volume
        self.area = area
        self.isSolid = is_solid
        self.isVisible = is_visible
        self.material: Any = FakeMaterial(material) if material else None
        self.appearance: Any = None
        self.parentComponent = component
        self.faces = FakeCollection()
        self.edges = FakeCollection()
        self.vertices = FakeCollection()
        self.boundingBox = bounding_box or FakeBoundingBox((0, 0, 0), (0, 0, 0))
        self.physical = physical or FakePhysicalProperties(volume=volume, area=area)
        self.accuracy_calls: list[int] = []

    def getPhysicalProperties(self, accuracy: int = 2) -> FakePhysicalProperties:
        self.accuracy_calls.append(accuracy)
        return self.physical


def add_face(body: FakeBody, face: FakeFace) -> FakeFace:
    face.body = body
    body.faces.add(face)
    return face


def fake_cylinder_face(
    radius: float,
    height: float,
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    axis: tuple[float, float, float] = (0.0, 0.0, 1.0),
    token: str | None = None,
) -> FakeFace:
    """Cylindrical face of ``radius``/``height`` (cm) around ``axis`` through ``origin``."""
    ox, oy, oz = origin
    ax, ay, az = axis
    centre = P(ox + ax * height / 2, oy + ay * height / 2, oz + az * height / 2)
    return FakeFace(
        cylinder_surface(P(*origin), V(*axis), radius),
        2 * 3.141592653589793 * radius * height,
        centre,
        normal=None,
        token=token,
    )


def fake_box_body(
    w: float, h: float, d: float, name: str = "Box", token: str | None = None
) -> FakeBody:
    """Box ``w`` x ``h`` x ``d`` cm from the origin along +X/+Y/+Z.

    Face tokens are ``<body token>-face<+x|-x|+y|-y|+z|-z>``.
    """
    size = (w, h, d)
    body = FakeBody(
        name,
        token=token,
        volume=w * h * d,
        area=2 * (w * h + h * d + w * d),
        bounding_box=FakeBoundingBox((0, 0, 0), size),
    )
    base = body.entityToken
    corners = {
        ijk: FakeVertex(P(ijk[0] * w, ijk[1] * h, ijk[2] * d), f"{base}-v{''.join(map(str, ijk))}")
        for ijk in itertools.product((0, 1), repeat=3)
    }
    for vertex in corners.values():
        vertex.body = body
        body.vertices.add(vertex)

    edges: list[tuple[tuple[int, ...], tuple[int, ...], FakeEdge]] = []
    for a, b in itertools.combinations(sorted(corners), 2):
        diff = [i for i in range(3) if a[i] != b[i]]
        if len(diff) != 1:
            continue
        va, vb = corners[a], corners[b]
        pa, pb = va.geometry, vb.geometry
        mid = P((pa.x + pb.x) / 2, (pa.y + pb.y) / 2, (pa.z + pb.z) / 2)
        edge = FakeEdge(
            line_curve(pa.copy(), pb.copy()),
            size[diff[0]],
            mid,
            start=va,
            end=vb,
            token=f"{base}-e{''.join(map(str, a))}{''.join(map(str, b))}",
            body=body,
        )
        va.edges.add(edge)
        vb.edges.add(edge)
        body.edges.add(edge)
        edges.append((a, b, edge))

    for axis in range(3):
        for side in (0, 1):
            others = [i for i in range(3) if i != axis]
            n = [0.0, 0.0, 0.0]
            n[axis] = 1.0 if side else -1.0
            c = [s / 2 for s in size]
            c[axis] = size[axis] * side
            lo = [0.0, 0.0, 0.0]
            hi = list(size)
            lo[axis] = hi[axis] = size[axis] * side
            face = FakeFace(
                plane_surface(P(*c), V(*n)),
                size[others[0]] * size[others[1]],
                P(*c),
                normal=V(*n),
                token=f"{base}-face{'+' if side else '-'}{'xyz'[axis]}",
                bounding_box=FakeBoundingBox(tuple(lo), tuple(hi)),  # type: ignore[arg-type]
            )
            add_face(body, face)
            for a, b, edge in edges:
                if a[axis] == side and b[axis] == side:
                    face.edges.add(edge)
                    edge.faces.add(face)
            for ijk, vertex in corners.items():
                if ijk[axis] == side:
                    face.vertices.add(vertex)
    return body


# --- construction geometry, sketches, features ---------------------------------------------


class FakeInfiniteLine:
    objectType = "adsk::core::InfiniteLine3D"

    def __init__(self, origin: Any, direction: Any) -> None:
        self.origin = origin
        self.direction = direction


class FakeConstructionPlane(Proxyable):
    objectType = "adsk::fusion::ConstructionPlane"

    def __init__(
        self, name: str, origin: Any, normal: Any, token: str | None = None, **attrs: Any
    ) -> None:
        self.name = name
        self.geometry = plane_surface(origin, normal)
        self.entityToken = token or _token("plane-")
        for key, value in attrs.items():
            setattr(self, key, value)


class FakeConstructionAxis(Proxyable):
    objectType = "adsk::fusion::ConstructionAxis"

    def __init__(
        self, name: str, origin: Any, direction: Any, token: str | None = None, **attrs: Any
    ) -> None:
        self.name = name
        self.geometry = FakeInfiniteLine(origin, direction)
        self.entityToken = token or _token("axis-")
        for key, value in attrs.items():
            setattr(self, key, value)


class FakeConstructionPoint(Proxyable):
    objectType = "adsk::fusion::ConstructionPoint"

    def __init__(self, name: str, point: Any, token: str | None = None, **attrs: Any) -> None:
        self.name = name
        self.geometry = point
        self.entityToken = token or _token("point-")
        for key, value in attrs.items():
            setattr(self, key, value)


FakeProfiles = FakeProfileCollection


class FakeSketch(SketchApiMixin, Proxyable):
    """The one sketch fake: c03 identity/plane fields plus the c05 sketch API
    (``SketchApiMixin``, tests/fakes/fusion_sketch.py). ``profile_count`` creates that many
    empty placeholder profiles; the transform comes from ``reference_plane`` unless given."""

    objectType = "adsk::fusion::Sketch"

    def __init__(
        self,
        name: str = "Sketch1",
        profile_count: int = 0,
        reference_plane: Any = None,
        is_visible: bool = True,
        token: str | None = None,
        *,
        transform: Any = None,
        component: Any = None,
        design: Any = None,
    ) -> None:
        self.name = name
        self.referencePlane = reference_plane
        self.isVisible = is_visible
        self.entityToken = token or _token("sketch-")
        self._init_sketch_api(
            reference_plane=reference_plane,
            transform=transform,
            component=component,
            design=design,
            profile_count=profile_count,
        )


class FakeTimelineRef:
    def __init__(self, index: int) -> None:
        self.index = index


class FakeFeature(Proxyable):
    """Any ``*Feature``: ``objectType`` is ``adsk::fusion::<kind>``."""

    def __init__(
        self,
        kind: str = "ExtrudeFeature",
        name: str = "Extrude1",
        health: int | None = 0,
        message: str = "",
        timeline_index: int | None = None,
        is_suppressed: bool = False,
        component: Any = None,
        token: str | None = None,
    ) -> None:
        self.objectType = f"adsk::fusion::{kind}"
        self.name = name
        if health is not None:
            self.healthState = health
        self.errorOrWarningMessage = message
        self.timelineObject = (
            FakeTimelineRef(timeline_index) if timeline_index is not None else None
        )
        self.isSuppressed = is_suppressed
        self.parentComponent = component
        self.entityToken = token or _token("feature-")


# --- measuring ---------------------------------------------------------------------------------


class FakeOrientedBox:
    def __init__(self, box: FakeBoundingBox) -> None:
        lo, hi = box.minPoint, box.maxPoint
        self.centerPoint = P((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, (lo.z + hi.z) / 2)
        self.length = hi.x - lo.x
        self.width = hi.y - lo.y
        self.height = hi.z - lo.z


class FakeMeasureResults:
    def __init__(self, value: float, one: Any = None, two: Any = None) -> None:
        self.value = value
        self.positionOne = one
        self.positionTwo = two


class FakeMeasureManager:
    """``app.measureManager``. Oriented boxes come from ``entity.tight_box`` if set, else
    ``entity.boundingBox``; types in ``unsupported`` raise like Fusion does."""

    def __init__(self) -> None:
        self.unsupported: set[str] = set()
        self.calls: list[tuple[Any, ...]] = []
        self.distance = FakeMeasureResults(0.0, P(0, 0, 0), P(0, 0, 0))
        self.angle = FakeMeasureResults(0.0)

    def getOrientedBoundingBox(self, entity: Any, length_dir: Any, width_dir: Any) -> Any:
        self.calls.append(("obb", entity, length_dir.asArray(), width_dir.asArray()))
        if entity.objectType.rsplit("::", 1)[-1] in self.unsupported:
            raise RuntimeError("InternalValidationError: entity not supported")
        return FakeOrientedBox(getattr(entity, "tight_box", None) or entity.boundingBox)

    def measureMinimumDistance(self, a: Any, b: Any) -> FakeMeasureResults:
        self.calls.append(("distance", a, b))
        return self.distance

    def measureAngle(self, a: Any, b: Any, c: Any = None) -> FakeMeasureResults:
        self.calls.append(("angle", a, b, c))
        return self.angle


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class FakeCamera:
    def __init__(self) -> None:
        self.viewOrientation = 0
        self.isFitView = False
        self.isSmoothTransition = True

    def copy(self) -> FakeCamera:
        camera = FakeCamera()
        camera.__dict__.update(self.__dict__)
        return camera


class FakeViewport:
    """``app.activeViewport``: ``camera`` hands out a copy and applies on assignment, like
    Fusion. ``saveAsImageFile`` writes PNG bytes unless ``save_result`` is False or
    ``save_error`` is set."""

    def __init__(self) -> None:
        self._camera = FakeCamera()
        self.camera_sets = 0
        self.visualStyle = 0
        self.refreshes = 0
        self.saved: list[tuple[str, int, int]] = []
        self.save_result = True
        self.save_error: str | None = None
        self.image = PNG_SIGNATURE + b"fake-image-data"

    @property
    def camera(self) -> FakeCamera:
        return self._camera.copy()

    @camera.setter
    def camera(self, value: FakeCamera) -> None:
        self.camera_sets += 1
        self._camera = value.copy()

    def refresh(self) -> None:
        self.refreshes += 1

    def saveAsImageFile(self, path: str, width: int, height: int) -> bool:
        self.saved.append((path, width, height))
        with open(path, "wb") as file:
            file.write(self.image)
        if self.save_error is not None:
            raise RuntimeError(self.save_error)
        return self.save_result


# --- components and occurrences ---------------------------------------------------------------

ORIGIN_ATTRS = {
    "XY": "xYConstructionPlane",
    "XZ": "xZConstructionPlane",
    "YZ": "yZConstructionPlane",
    "X": "xConstructionAxis",
    "Y": "yConstructionAxis",
    "Z": "zConstructionAxis",
    "O": "originConstructionPoint",
}


class FakeComponent:
    objectType = "adsk::fusion::Component"

    def __init__(self, name: str = "root", token: str | None = None) -> None:
        self.name = name
        self.entityToken = token or _token(f"component-{name}-")
        self.material: Any = None
        self.meshBodies: Any = None  # c12 fakes install ``FakeMeshBodies``
        self.bRepBodies = FakeCollection()
        self.sketches = FakeSketches(self)
        self.constructionPlanes = FakeCollection()
        self.constructionAxes = FakeCollection()
        self.constructionPoints = FakeCollection()
        self.occurrences = FakeCollection()
        self.allOccurrences = FakeCollection()
        self.boundingBox = FakeBoundingBox((0, 0, 0), (0, 0, 0))
        self.physical = FakePhysicalProperties()
        self.accuracy_calls: list[int] = []
        o = P(0, 0, 0)
        self.xYConstructionPlane = FakeConstructionPlane("XY", o, V(0, 0, 1), f"{name}-oXY")
        self.xZConstructionPlane = FakeConstructionPlane("XZ", o, V(0, 1, 0), f"{name}-oXZ")
        self.yZConstructionPlane = FakeConstructionPlane("YZ", o, V(1, 0, 0), f"{name}-oYZ")
        self.xConstructionAxis = FakeConstructionAxis("X", o, V(1, 0, 0), f"{name}-oX")
        self.yConstructionAxis = FakeConstructionAxis("Y", o, V(0, 1, 0), f"{name}-oY")
        self.zConstructionAxis = FakeConstructionAxis("Z", o, V(0, 0, 1), f"{name}-oZ")
        self.originConstructionPoint = FakeConstructionPoint("O", o, f"{name}-oO")
        for attr in ORIGIN_ATTRS.values():
            getattr(self, attr).parentComponent = self

    def origin_elements(self) -> dict[str, Any]:
        return {name: getattr(self, attr) for name, attr in ORIGIN_ATTRS.items()}

    def add_body(self, body: FakeBody) -> FakeBody:
        body.parentComponent = self
        self.bRepBodies.add(body)
        return body

    def getPhysicalProperties(self, accuracy: int = 2) -> FakePhysicalProperties:
        self.accuracy_calls.append(accuracy)
        return self.physical


class FakeOccurrence:
    """``Occurrence``. With an explicit ``name`` the name and path are fixed (c03 tests);
    with ``name=None`` they follow Fusion: ``<component name>:<index>``, prefixed by the
    path of ``assemblyContext`` (``Box:1+Hinge:1``), so renaming the component renames them.
    ``nativeObject`` is set on the root-context copies of nested occurrences (c10 D9)."""

    objectType = "adsk::fusion::Occurrence"

    def __init__(
        self,
        component: FakeComponent,
        name: str | None = "Part:1",
        full_path_name: str | None = None,
        transform: Any = None,
        is_grounded: bool = False,
        is_visible: bool = True,
        token: str | None = None,
        *,
        index: int = 1,
        assembly_context: Any = None,
        is_light_bulb_on: bool = True,
    ) -> None:
        self.component = component
        self._name = name
        self._full_path_name = full_path_name or name
        self._index = index
        self.assemblyContext = assembly_context
        self.nativeObject: Any = None
        self.transform2 = transform or FakeMatrix()
        self.isGrounded = is_grounded
        self.isVisible = is_visible
        self.isLightBulbOn = is_light_bulb_on
        self.entityToken = token or _token("occurrence-")
        self.appearance: Any = None
        self.childOccurrences = FakeCollection()
        self.bRepBodies = FakeCollection()
        self.boundingBox = FakeBoundingBox((0, 0, 0), (0, 0, 0))
        self.deleted = False
        self.activations = 0

    @property
    def name(self) -> str:
        if self._name is not None:
            return self._name
        return f"{self.component.name}:{self._index}"

    @name.setter
    def name(self, value: str) -> None:
        self._name = value

    @property
    def fullPathName(self) -> str:
        if self._full_path_name is not None:
            return self._full_path_name
        if self.assemblyContext is None:
            return self.name
        return f"{self.assemblyContext.fullPathName}+{self.name}"

    def createForAssemblyContext(self, occurrence: Any) -> FakeOccurrence:
        """This occurrence seen through ``occurrence`` (a nested path)."""
        native = self.nativeObject or self
        proxy = FakeOccurrence(
            self.component,
            None,
            transform=self.transform2,
            is_grounded=self.isGrounded,
            is_visible=self.isVisible,
            token=f"{native.entityToken}+{occurrence.entityToken}",
            index=native._index,
            assembly_context=occurrence,
            is_light_bulb_on=self.isLightBulbOn,
        )
        proxy._name = native._name
        proxy.nativeObject = native
        return proxy

    def activate(self) -> bool:
        self.activations += 1
        return True

    def deleteMe(self) -> bool:
        self.deleted = True
        return True


def add_occurrence(parent: Any, occurrence: FakeOccurrence, root: FakeComponent) -> FakeOccurrence:
    """Attach ``occurrence`` under ``parent`` (a component or occurrence) and to
    ``root.allOccurrences``."""
    if isinstance(parent, FakeOccurrence):
        parent.childOccurrences.add(occurrence)
    else:
        parent.occurrences.add(occurrence)
    root.allOccurrences.add(occurrence)
    return occurrence


def fake_part_design(*bodies: FakeBody, parametric: bool = True) -> FakeDesign:
    """``FakeDesign`` whose root component is a ``FakeComponent`` holding ``bodies``;
    every body, face, edge, vertex and origin element is findable by token."""
    design = FakeDesign(parametric=parametric)
    root = FakeComponent("root")
    root.sketches.design = design
    design.rootComponent = root
    design.allComponents = FakeCollection([root])
    for element in root.origin_elements().values():
        design.add_entity(element)
    for body in bodies:
        root.add_body(body)
        register_body(design, body)
    return design


def register_body(design: FakeDesign, body: FakeBody) -> None:
    design.add_entity(body)
    for collection in (body.faces, body.edges, body.vertices):
        for entity in collection:
            design.add_entity(entity)

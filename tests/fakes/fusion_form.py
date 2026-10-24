"""Fake form / T-spline API for ``form.*`` tests (c14 D1).

Builds on the c07 feature fakes (``tests/fakes/fusion_features.py``).

- ``FakeTSplineVertex`` (``geometry`` / ``smoothGeometry`` in cm, ``move``, ``transform``),
  ``FakeTSplineEdge`` (``startVertex`` / ``endVertex``, ``isCreased`` get / set,
  ``isDegenerate``), ``FakeTSplineFace`` (``vertices``, ``edges``, ``move``, ``boundingBox``).
  None of them has an ``entityToken`` (like Fusion). ``move_error`` / ``crease_error`` make the
  next change raise; ``moves`` / ``crease_sets`` record what was applied.
- ``FakeTSplineBody``: ``entityToken``, ``name``, ``parentFormFeature``, ``vertices`` /
  ``edges`` / ``faces``, ``bRepBody`` (``None`` until the form regenerates it),
  ``getTSMDescription`` / ``saveAsTSMFile``, ``transform`` (``transforms`` records the
  matrices), ``isLightBulbOn``, ``boundingBox``. ``build_box_mesh`` gives it the control mesh
  of a box of ``n`` x ``n`` faces per side (default 2 x 2, 26 vertices, 48 edges, 24 faces).
- ``FakeTSplineBodies``: ``create{Box,Plane,Cylinder,Sphere,Torus,Quadball}Input`` (recording
  ``FakeShapeInput``), ``addByShapeInput`` / ``addByTSMDescription`` (text containing
  ``"invalid"`` -> ``None``) / ``addByTSMFile``; every new body is a 2 x 2 x 2 cm box mesh.
- ``FakeFormFeature``: ``startEdit`` / ``finishEdit`` / ``updateBRep`` (``log`` records the
  calls), ``brep_mode`` - ``"ok"`` (``updateBRep`` makes the B-Rep bodies), ``"after_edit"``
  (only ``startEdit`` + ``finishEdit`` does) or ``"never"``; ``insertEdge`` unless the form
  was made with ``insert_edge=False`` (a Fusion without the preview API); ``deleteMe``.
- ``FakeFormFeatures`` (``add``), ``FakeBaseFeatures`` (``add`` -> ``FakeBaseFeature`` with
  ``startEdit`` / ``finishEdit``), ``FakeBRepBodies`` (``add(body, baseFeature)``) and
  ``FakeTemporaryBRepManager`` (``copy``), installed by ``install_form``.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import fusion_addin

from .brep import FakeBody, FakeBoundingBox, bbox_of, register_body
from .design import FakeCollection, FakeDesign
from .fusion_features import fake_feature_design

adsk = fusion_addin.adsk
P = adsk.core.Point3D
HEALTH = adsk.fusion.FeatureHealthStates

_ids = itertools.count(1)

SHAPE_METHODS = {
    "createBoxInput": "Box",
    "createPlaneInput": "Plane",
    "createCylinderInput": "Cylinder",
    "createSphereInput": "Sphere",
    "createTorusInput": "Torus",
    "createQuadballInput": "Quadball",
}


# --- control mesh --------------------------------------------------------------------------


class FakeTSplineVertex:
    objectType = "adsk::fusion::TSplineVertex"

    def __init__(self, body: FakeTSplineBody, xyz: Any) -> None:
        self.body = body
        self.p = [float(c) for c in xyz]
        self.moves: list[tuple[float, float, float]] = []
        self.move_error: str | None = None

    @property
    def geometry(self) -> Any:
        return P(*self.p)

    @property
    def smoothGeometry(self) -> Any:
        """The limit surface point: 90 % of the way from the body centre to the vertex."""
        c = self.body.center()
        return P(*(c[i] + (self.p[i] - c[i]) * 0.9 for i in range(3)))

    def shift(self, vector: Any) -> None:
        self.p = [self.p[0] + vector.x, self.p[1] + vector.y, self.p[2] + vector.z]

    def move(self, vector: Any) -> bool:
        if self.move_error is not None:
            raise RuntimeError(self.move_error)
        self.moves.append((vector.x, vector.y, vector.z))
        self.shift(vector)
        return True

    def transform(self, matrix: Any) -> bool:
        point = P(*self.p)
        point.transformBy(matrix)
        self.p = [point.x, point.y, point.z]
        return True


class FakeTSplineEdge:
    objectType = "adsk::fusion::TSplineEdge"

    def __init__(
        self, body: FakeTSplineBody, start: FakeTSplineVertex, end: FakeTSplineVertex
    ) -> None:
        self.body = body
        self.startVertex = start
        self.endVertex = end
        self.isDegenerate = False
        self._creased = False
        self.crease_sets: list[bool] = []
        self.crease_error: str | None = None

    @property
    def isCreased(self) -> bool:
        return self._creased

    @isCreased.setter
    def isCreased(self, value: bool) -> None:
        if self.crease_error is not None:
            raise RuntimeError(self.crease_error)
        self.crease_sets.append(bool(value))
        self._creased = bool(value)


class FakeTSplineFace:
    objectType = "adsk::fusion::TSplineFace"

    def __init__(
        self,
        body: FakeTSplineBody,
        vertices: list[FakeTSplineVertex],
        edges: list[FakeTSplineEdge],
    ) -> None:
        self.body = body
        self.vertices = FakeCollection(vertices)
        self.edges = FakeCollection(edges)
        self.moves: list[tuple[float, float, float]] = []
        self.move_error: str | None = None

    @property
    def boundingBox(self) -> FakeBoundingBox:
        return bbox_of([v.geometry for v in self.vertices])

    def move(self, vector: Any) -> bool:
        if self.move_error is not None:
            raise RuntimeError(self.move_error)
        self.moves.append((vector.x, vector.y, vector.z))
        for vertex in self.vertices:
            vertex.shift(vector)
        return True


class FakeTSplineBody:
    objectType = "adsk::fusion::TSplineBody"

    def __init__(self, form: Any, name: str = "Body1", token: str | None = None) -> None:
        self.parentFormFeature = form
        self.name = name
        self.entityToken = token or f"tsbody-{next(_ids)}"
        self.vertices = FakeCollection()
        self.edges = FakeCollection()
        self.faces = FakeCollection()
        self.bRepBody: Any = None
        self.isLightBulbOn = True
        self.isValid = True
        self.transforms: list[list[float]] = []
        self.transform_error: str | None = None
        self.saved: list[str] = []
        self.tsm: str | None = None

    @property
    def boundingBox(self) -> FakeBoundingBox:
        return bbox_of([v.geometry for v in self.vertices])

    def center(self) -> list[float]:
        box = self.boundingBox
        lo, hi = box.minPoint, box.maxPoint
        return [(lo.x + hi.x) / 2, (lo.y + hi.y) / 2, (lo.z + hi.z) / 2]

    def transform(self, matrix: Any) -> bool:
        if self.transform_error is not None:
            raise RuntimeError(self.transform_error)
        self.transforms.append(list(matrix.asArray()))
        for vertex in self.vertices:
            vertex.transform(matrix)
        return True

    def deleteMe(self) -> bool:
        form = self.parentFormFeature
        if form is not None and self in form.tSplineBodies._items:
            form.tSplineBodies._items.remove(self)
        self.isValid = False
        return True

    def getTSMDescription(self) -> str:
        return self.tsm if self.tsm is not None else f"FAKE-TSM vertices={self.vertices.count}"

    def saveAsTSMFile(self, path: str) -> bool:
        Path(path).write_text(self.getTSMDescription(), encoding="utf-8")
        self.saved.append(path)
        return True


def build_box_mesh(
    body: FakeTSplineBody,
    size: tuple[float, float, float] = (2.0, 2.0, 2.0),
    origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
    n: int = 2,
) -> FakeTSplineBody:
    """Control mesh of a ``size`` (cm) box from ``origin`` with ``n`` x ``n`` faces per side."""
    vertices: dict[tuple[int, ...], FakeTSplineVertex] = {}
    edges: dict[frozenset[tuple[int, ...]], FakeTSplineEdge] = {}

    def vertex(ijk: tuple[int, ...]) -> FakeTSplineVertex:
        if ijk not in vertices:
            xyz = [origin[a] + size[a] * ijk[a] / n for a in range(3)]
            vertices[ijk] = FakeTSplineVertex(body, xyz)
            body.vertices.add(vertices[ijk])
        return vertices[ijk]

    def edge(a: tuple[int, ...], b: tuple[int, ...]) -> FakeTSplineEdge:
        key = frozenset((a, b))
        if key not in edges:
            edges[key] = FakeTSplineEdge(body, vertex(a), vertex(b))
            body.edges.add(edges[key])
        return edges[key]

    for axis in range(3):
        u, w = (i for i in range(3) if i != axis)
        for side in (0, n):
            for i, j in itertools.product(range(n), repeat=2):
                corners: list[tuple[int, ...]] = []
                for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    ijk = [0, 0, 0]
                    ijk[axis], ijk[u], ijk[w] = side, i + di, j + dj
                    corners.append(tuple(ijk))
                face = FakeTSplineFace(
                    body,
                    [vertex(c) for c in corners],
                    [edge(corners[k], corners[(k + 1) % 4]) for k in range(4)],
                )
                body.faces.add(face)
    return body


# --- T-spline bodies -----------------------------------------------------------------------


class FakeShapeInput:
    """``TSpline<Shape>Input``: ``method`` / ``args`` of the creation call; properties are
    plain attributes."""

    def __init__(self, method: str, args: tuple[Any, ...]) -> None:
        self.method = method
        self.args = args
        self.objectType = f"adsk::fusion::TSpline{SHAPE_METHODS[method]}Input"


class FakeTSplineBodies(FakeCollection):
    def __init__(self, form: FakeFormFeature) -> None:
        super().__init__()
        self.form = form
        self.inputs: list[FakeShapeInput] = []
        self.missing: set[str] = set()
        self.add_error: str | None = None
        self.add_none = False
        self.tsm_texts: list[str] = []
        self.tsm_files: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name not in SHAPE_METHODS or name in self.__dict__.get("missing", set()):
            raise AttributeError(f"TSplineBodies has no attribute {name!r}")

        def create(*args: Any) -> FakeShapeInput:
            inp = FakeShapeInput(name, args)
            self.inputs.append(inp)
            return inp

        return create

    def _new(self) -> FakeTSplineBody | None:
        if self.add_error is not None:
            raise RuntimeError(self.add_error)
        if self.add_none:
            return None
        body = build_box_mesh(FakeTSplineBody(self.form, f"Body{self.count + 1}"))
        self.add(body)
        if self.form.design is not None:
            self.form.design.add_entity(body)
        return body

    def addByShapeInput(self, inp: FakeShapeInput) -> FakeTSplineBody | None:
        if not isinstance(inp, FakeShapeInput):
            raise RuntimeError(f"addByShapeInput expects a shape input, got {inp!r}")
        return self._new()

    def addByTSMDescription(self, text: str) -> FakeTSplineBody | None:
        self.tsm_texts.append(text)
        return None if "invalid" in text else self._new()

    def addByTSMFile(self, path: str) -> FakeTSplineBody | None:
        self.tsm_files.append(path)
        return self._new()


# --- form features -------------------------------------------------------------------------


class FakeFormFeature:
    objectType = "adsk::fusion::FormFeature"

    def __init__(self, collection: FakeFormFeatures, name: str, insert_edge: bool = True) -> None:
        self.collection = collection
        self.design = collection.design
        self.parentComponent = collection.component
        self.name = name
        self.entityToken = f"form-{next(_ids)}"
        self.healthState = HEALTH.HealthyFeatureHealthState
        self.errorOrWarningMessage = ""
        self.isValid = True
        self.tSplineBodies = FakeTSplineBodies(self)
        self.log: list[str] = []
        self.is_editing = False
        self.brep_mode = "ok"
        self.finish_error: str | None = None
        self.update_error: str | None = None
        self.insert_error: str | None = None
        self.inserted: list[Any] = []
        self.timeline_item: Any = None
        if insert_edge:
            self.insertEdge = self._insert_edge

    def startEdit(self) -> bool:
        self.log.append("startEdit")
        self.is_editing = True
        return True

    def finishEdit(self) -> bool:
        self.log.append("finishEdit")
        if self.finish_error is not None:
            raise RuntimeError(self.finish_error)
        was_editing, self.is_editing = self.is_editing, False
        if was_editing and self.brep_mode in ("ok", "after_edit"):
            self._regenerate()
        return True

    def updateBRep(self) -> bool:
        self.log.append("updateBRep")
        if self.update_error is not None:
            raise RuntimeError(self.update_error)
        if not self.is_editing and self.brep_mode == "ok":
            self._regenerate()
        return True

    def _regenerate(self) -> None:
        for body in self.tSplineBodies:
            if body.bRepBody is None:
                brep = FakeBody(
                    body.name, bounding_box=body.boundingBox, component=self.parentComponent
                )
                body.bRepBody = brep
                if self.design is not None:
                    register_body(self.design, brep)

    def _insert_edge(self, inp: Any) -> bool:
        self.log.append("insertEdge")
        if self.insert_error is not None:
            raise RuntimeError(self.insert_error)
        self.inserted.append(inp)
        both = inp.insertionSide == adsk.fusion.FormEdgeInsertionSides.BothFormEdgeInsertionSide
        for edge in list(inp.edges):
            for _ in range(2 if both else 1):
                edge.body.edges.add(FakeTSplineEdge(edge.body, edge.startVertex, edge.endVertex))
        return True

    def deleteMe(self) -> bool:
        if self.timeline_item is not None and self.design is not None:
            self.design.timeline._remove(self.timeline_item)
        self.collection._items.remove(self)
        self.isValid = False
        for body in self.tSplineBodies:
            body.isValid = False
        return True


class FakeFormFeatures(FakeCollection):
    def __init__(self, component: Any, design: FakeDesign | None, insert_edge: bool = True) -> None:
        super().__init__()
        self.component = component
        self.design = design
        self.insert_edge = insert_edge
        self.add_error: str | None = None
        self.brep_mode = "ok"

    def add(self, item: Any = None) -> FakeFormFeature:
        if self.add_error is not None:
            raise RuntimeError(self.add_error)
        number = len(self._items) + 1
        form = FakeFormFeature(self, f"Form{number}", self.insert_edge)
        form.brep_mode = self.brep_mode
        self._items.append(form)
        _attach(self.design, form)
        return form


def _attach(design: FakeDesign | None, feature: Any) -> None:
    """Token lookup and (parametric) a timeline item whose entity is ``feature``."""
    if design is None:
        return
    design.add_entity(feature)
    if design.parametric:
        item = design.timeline.add(feature.name)
        item.entity = feature
        feature.timeline_item = item


# --- base features, B-Rep bodies, temporary B-Rep --------------------------------------------


class FakeBaseFeature:
    objectType = "adsk::fusion::BaseFeature"

    def __init__(self, collection: FakeBaseFeatures, name: str) -> None:
        self.collection = collection
        self.design = collection.design
        self.parentComponent = collection.component
        self.name = name
        self.entityToken = f"base-{next(_ids)}"
        self.healthState = HEALTH.HealthyFeatureHealthState
        self.errorOrWarningMessage = ""
        self.isValid = True
        self.log: list[str] = []
        self.bodies: list[Any] = []
        self.timeline_item: Any = None

    def startEdit(self) -> bool:
        self.log.append("startEdit")
        return True

    def finishEdit(self) -> bool:
        self.log.append("finishEdit")
        return True

    def deleteMe(self) -> bool:
        if self.timeline_item is not None and self.design is not None:
            self.design.timeline._remove(self.timeline_item)
        self.collection._items.remove(self)
        bodies = self.parentComponent.bRepBodies
        bodies._items = [b for b in bodies._items if b not in self.bodies]
        self.isValid = False
        return True


class FakeBaseFeatures(FakeCollection):
    def __init__(self, component: Any, design: FakeDesign | None) -> None:
        super().__init__()
        self.component = component
        self.design = design

    def add(self, item: Any = None) -> FakeBaseFeature:
        feature = FakeBaseFeature(self, f"Base Feature{len(self._items) + 1}")
        self._items.append(feature)
        _attach(self.design, feature)
        return feature


class FakeBRepBodies(FakeCollection):
    """``Component.bRepBodies``: ``add(body, baseFeature=None)`` adds a temporary body."""

    def __init__(self, component: Any, design: FakeDesign | None, items: list[Any]) -> None:
        super().__init__(items)
        self.component = component
        self.design = design
        self.add_error: str | None = None
        self.added: list[tuple[Any, Any]] = []

    def add(self, item: Any, base_feature: Any = None) -> Any:
        if self.add_error is not None:
            raise RuntimeError(self.add_error)
        self.added.append((item, base_feature))
        item.parentComponent = self.component
        self._items.append(item)
        if self.design is not None:
            register_body(self.design, item)
        if base_feature is not None:
            base_feature.bodies.append(item)
        return item


class FakeTemporaryBRepManager:
    def __init__(self) -> None:
        self.copies: list[Any] = []
        self.copy_none = False

    def copy(self, entity: Any) -> Any:
        self.copies.append(entity)
        if self.copy_none:
            return None
        return FakeBody(
            getattr(entity, "name", "Body"),
            is_solid=getattr(entity, "isSolid", True),
            bounding_box=getattr(entity, "boundingBox", None),
        )


def install_form(design: FakeDesign, insert_edge: bool = True) -> FakeDesign:
    """Form and base features, ``bRepBodies.add`` and the temporary B-Rep manager on the root
    of ``design`` (``insert_edge=False``: forms without the preview ``insertEdge``)."""
    root = design.rootComponent
    root.features.formFeatures = FakeFormFeatures(root, design, insert_edge)
    root.features.baseFeatures = FakeBaseFeatures(root, design)
    root.bRepBodies = FakeBRepBodies(root, design, list(root.bRepBodies))
    manager = FakeTemporaryBRepManager()
    adsk.fusion.TemporaryBRepManager.instance = manager
    design.brep_manager = manager  # type: ignore[attr-defined]
    return design


def fake_form_design(
    *bodies: FakeBody, parametric: bool = True, insert_edge: bool = True
) -> FakeDesign:
    """``fake_feature_design`` with the form fakes installed."""
    return install_form(fake_feature_design(*bodies, parametric=parametric), insert_edge)


def add_box_form(design: FakeDesign, brep: bool = True) -> tuple[FakeFormFeature, Any]:
    """A form with one 2 x 2 x 2 cm box body (with its B-Rep body unless ``brep=False``)."""
    form = design.rootComponent.features.formFeatures.add()
    body = form.tSplineBodies._new()
    if brep:
        form.updateBRep()
    form.log.clear()
    return form, body

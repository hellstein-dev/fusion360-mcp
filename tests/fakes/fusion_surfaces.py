"""Fake surface feature collections for ``surface.*`` handler tests (c11 D1).

Builds on the c07/c08 feature fakes (``tests/fakes/fusion_features.py``):

- ``install_surfaces(component, design)`` hangs the surface collections on
  ``component.features`` (``patchFeatures``, ``extendFeatures``, ``trimFeatures``,
  ``untrimFeatures``, ``stitchFeatures``, ``unstitchFeatures``, ``ruledSurfaceFeatures``,
  ``offsetFeatures``) and gives the component ``createOpenProfile`` /
  ``createBRepEdgeProfile`` (-> ``FakeOpenProfile``, recorded in ``component.open_profiles``;
  ``profile_error`` makes them raise ``RuntimeError``). ``fake_surface_design`` is
  ``fake_feature_design`` with them installed on the root.
- Every collection is a c07 ``FakeFeatureCollection`` (``createInput`` /
  ``createInputFromFaces`` record their arguments, ``add`` returns a ``FakeFeature``;
  ``will_create`` / ``will_modify`` / ``health`` as in c07). A trim input has ``bRepCells``
  (c08 ``FakeBRepCells`` of ``FakeBRepCell`` over the collection's ``cells``) and records
  ``cancel()`` like every other method call.
- ``unstitchFeatures.add(faces, isChainSelection)`` takes the entities directly; the arguments
  are kept as ``feature.input.create_args``.
- ``surface_cell(lo, hi, area)`` is a temporary cell body (cm box, ``isSolid = False``).
- ``FakeCellMeasure`` is ``app.measureManager`` whose ``measureMinimumDistance(body, point)``
  is the distance from the point to the body's bounding box; ``error`` makes it raise (a
  temporary body Fusion refuses to measure).
"""

from __future__ import annotations

import math
from typing import Any

import fusion_addin

from .brep import FakeBody, FakeMeasureResults
from .design import FakeDesign
from .fusion_features import (
    FakeBRepCell,
    FakeBRepCells,
    FakeCellBody,
    FakeFeatureCollection,
    FakeFeatureInput,
    fake_feature_design,
)

adsk = fusion_addin.adsk

# ``Features`` attribute -> (feature object type, name prefix, creation methods).
SURFACE_COLLECTIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "patchFeatures": ("PatchFeature", "Patch", ("createInput",)),
    "extendFeatures": ("ExtendFeature", "Extend", ("createInput",)),
    "trimFeatures": ("TrimFeature", "Trim", ("createInput",)),
    "untrimFeatures": ("UntrimFeature", "Untrim", ("createInputFromFaces",)),
    "stitchFeatures": ("StitchFeature", "Stitch", ("createInput",)),
    "unstitchFeatures": ("UnstitchFeature", "Unstitch", ()),
    "ruledSurfaceFeatures": ("RuledSurfaceFeature", "RuledSurface", ("createInput",)),
    "offsetFeatures": ("OffsetFeature", "Offset", ("createInput",)),
}


class FakeTrimFeatures(FakeFeatureCollection):
    """``trimFeatures``: every ``createInput`` input gets fresh ``bRepCells`` over ``cells``."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cells: list[FakeCellBody] = []

    def __getattr__(self, name: str) -> Any:
        create = super().__getattr__(name)

        def with_cells(*args: Any) -> FakeFeatureInput:
            inp = create(*args)
            inp.provide("bRepCells", FakeBRepCells([FakeBRepCell(b) for b in self.cells]))
            return inp

        return with_cells


class FakeUnstitchFeatures(FakeFeatureCollection):
    """``unstitchFeatures``: ``add(faces, isChainSelection)`` without a feature input."""

    def add(self, item: Any, *args: Any) -> Any:
        if isinstance(item, FakeFeatureInput):
            return super().add(item)
        if self.add_error is not None:
            raise RuntimeError(self.add_error)
        inp = FakeFeatureInput(self, "add", (item, *args))
        self.inputs.append(inp)
        return super().add(inp)


class FakeOpenProfile:
    """``Profile`` from ``createOpenProfile`` / ``createBRepEdgeProfile``."""

    objectType = "adsk::fusion::Profile"

    def __init__(self, method: str, entities: Any, chain: bool, component: Any) -> None:
        self.method = method
        self.entities = list(entities)
        self.chain = chain
        self.parentComponent = component
        self.isOpen = True


def _profile_maker(component: Any, method: str) -> Any:
    def make(entities: Any, chain: bool = True) -> FakeOpenProfile:
        error = getattr(component, "profile_error", None)
        if error is not None:
            raise RuntimeError(error)
        profile = FakeOpenProfile(method, entities, chain, component)
        component.open_profiles.append(profile)
        return profile

    make.__name__ = method
    return make


def install_surfaces(component: Any, design: FakeDesign | None = None) -> Any:
    """Add the surface collections to ``component.features`` (installed by c07
    ``install_features``) and the open profile methods to ``component``; returns it."""
    features = component.features
    for attr, (kind, prefix, methods) in SURFACE_COLLECTIONS.items():
        cls: type[FakeFeatureCollection] = FakeFeatureCollection
        if attr == "trimFeatures":
            cls = FakeTrimFeatures
        elif attr == "unstitchFeatures":
            cls = FakeUnstitchFeatures
        setattr(features, attr, cls(component, design, kind, prefix, methods))
    component.open_profiles = []
    component.profile_error = None
    component.createOpenProfile = _profile_maker(component, "createOpenProfile")
    component.createBRepEdgeProfile = _profile_maker(component, "createBRepEdgeProfile")
    return component


def fake_surface_design(*bodies: FakeBody, parametric: bool = True) -> FakeDesign:
    """``fake_feature_design`` whose root also has the surface collections."""
    design = fake_feature_design(*bodies, parametric=parametric)
    install_surfaces(design.rootComponent, design)
    return design


def surface_cell(
    lo: tuple[float, float, float],
    hi: tuple[float, float, float],
    area: float = 1.0,
    name: str = "Cell",
) -> FakeCellBody:
    """Trim cell body: a ``lo``..``hi`` box (cm) of surface area ``area`` cm²."""
    cell = FakeCellBody(lo, hi, name)
    cell.isSolid = False
    cell.area = area
    return cell


class FakeCellMeasure:
    """``app.measureManager`` measuring point-to-bounding-box distances (cm)."""

    def __init__(self) -> None:
        self.error: str | None = None
        self.calls: list[tuple[Any, Any]] = []

    def measureMinimumDistance(self, a: Any, b: Any) -> FakeMeasureResults:
        self.calls.append((a, b))
        if self.error is not None:
            raise RuntimeError(self.error)
        lo, hi = a.boundingBox.minPoint, a.boundingBox.maxPoint
        gaps = [
            max(low - c, 0.0, c - high)
            for low, c, high in zip(lo.asArray(), b.asArray(), hi.asArray(), strict=True)
        ]
        return FakeMeasureResults(math.hypot(*gaps))

"""Interference fakes (c10 task 6.5)."""

from __future__ import annotations

import fusion_addin
from tests.fakes.assembly import (
    FakeInterference,
    FakeInterferenceResult,
    add_component,
    fake_assembly_design,
)
from tests.fakes.brep import FakeBody, fake_box_body

adsk = fusion_addin.adsk


def test_analysis_filters_by_input_and_coincident_faces() -> None:
    design = fake_assembly_design()
    _, box = add_component(design, "Box", fake_box_body(10, 6, 4, token="bx"))
    _, lid = add_component(design, "Lid", fake_box_body(10, 6, 0.5, token="ld"))
    _, pin = add_component(design, "Pin", fake_box_body(1, 1, 1, token="pn"))
    overlap = FakeInterferenceResult(box, pin, FakeBody(volume=1.0))
    touch = FakeInterferenceResult(box, lid, FakeBody(volume=0.0), coincident=True)
    interference = FakeInterference(design, [overlap, touch])

    def analyze(*entities: object, coincident: bool = False) -> list[FakeInterferenceResult]:
        collection = adsk.core.ObjectCollection.create()
        for entity in entities:
            collection.add(entity)
        interference_input = design.createInterferenceInput(collection)
        interference_input.areCoincidentFacesIncluded = coincident
        results = design.analyzeInterference(interference_input)
        return [results.item(i) for i in range(results.count)]

    assert analyze(box, lid) == []
    assert analyze(box, lid, coincident=True) == [touch]
    assert analyze(box, lid, pin) == [overlap]
    # Entities are matched by identity or occurrence path: Lid:1+Box:1 is not Box:1.
    assert analyze(box.createForAssemblyContext(lid), pin) == []
    assert len(interference.inputs) == 4
    assert all(not r.bodies_created for r in interference.analyzed)

"""``sketch_*`` tools and their input models on ``FakeBridge`` (c05 tasks 9.1-9.20, D12)."""

import asyncio
import json
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import TypeAdapter, ValidationError

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text
from fusion_mcp.server.tools.sketch_schema import (
    STRING_COORDINATE_HINT,
    ConstraintItem,
    DimensionItem,
    DimensionUpdate,
    DrawItem,
    dump,
)

SKETCH = "SKTOKEN=="
STATUS = {"fully_constrained": True, "under_constrained_count": 0, "under_constrained": []}

DRAW: TypeAdapter[Any] = TypeAdapter(DrawItem)
CONSTRAINT: TypeAdapter[Any] = TypeAdapter(ConstraintItem)
DIMENSION: TypeAdapter[Any] = TypeAdapter(DimensionItem)


class Rejected:
    """Arguments refused by the tool's input schema (``ToolError``) before the bridge."""

    is_error = True

    def __init__(self, message: str) -> None:
        self.message = message


def call(name: str, arguments: dict[str, Any], response: Any = None) -> tuple[Any, FakeBridge]:
    bridge = FakeBridge()
    method = name.replace("sketch_", "sketch.", 1)
    bridge.respond(method, response if response is not None else {"status": STATUS})
    try:
        result = asyncio.run(call_tool(create_server(bridge), name, arguments))
    except ToolError as exc:
        return Rejected(str(exc)), bridge
    return result, bridge


def rejected(name: str, arguments: dict[str, Any]) -> str:
    result, bridge = call(name, arguments)
    assert result.is_error is True and bridge.calls == []
    assert isinstance(result, Rejected)
    return result.message


def tool(name: str) -> Any:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    return next(t for t in tools if t.name == name)


def draw_item(raw: dict[str, Any]) -> dict[str, Any]:
    return dump(DRAW.validate_python(raw))


# --- 9.1 schema: Point, DrawItem -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"kind": "point", "at": [1, 2]}, {"at": [1.0, 2.0]}),
        ({"kind": "point", "at": {"model": [1, 2, 3]}}, {"at": {"model": [1.0, 2.0, 3.0]}}),
        ({"kind": "point", "at": "l1.end"}, {"at": "l1.end"}),
        ({"kind": "line", "start": "origin", "end": [40, 0]}, {"start": "origin"}),
        (
            {"kind": "polyline", "points": [[0, 0], [1, 0], [1, 1]], "closed": True},
            {"closed": True},
        ),
        ({"kind": "rectangle", "corner1": [0, 0], "corner2": [10, 5]}, {"mode": "two_point"}),
        ({"kind": "rectangle", "mode": "center", "center": "origin", "corner": [5, 3]}, {}),
        (
            {"kind": "rectangle", "mode": "three_point", "p1": [0, 0], "p2": [1, 0], "p3": [1, 1]},
            {},
        ),
        ({"kind": "circle", "center": [0, 0], "radius": 5}, {"mode": "center_radius"}),
        ({"kind": "circle", "mode": "two_point", "p1": [0, 0], "p2": [10, 0]}, {}),
        ({"kind": "circle", "mode": "three_point", "p1": [0, 0], "p2": [1, 1], "p3": [2, 0]}, {}),
        ({"kind": "arc", "start": [0, 0], "mid": [1, 1], "end": [2, 0]}, {"mode": "three_point"}),
        (
            {
                "kind": "arc",
                "mode": "center_start_sweep",
                "center": [0, 0],
                "start": [5, 0],
                "sweep": 90,
            },
            {"sweep": 90.0},
        ),
        ({"kind": "arc", "mode": "tangent", "from": "l.end", "to": [60, 20]}, {"from": "l.end"}),
        (
            {"kind": "ellipse", "center": [0, 0], "major_radius": 10, "minor_radius": 5},
            {"angle": 0},
        ),
        (
            {"kind": "polygon", "center": [0, 0], "radius": 20, "sides": 6},
            {"mode": "inscribed", "constrain": True, "angle": 0},
        ),
        (
            {
                "kind": "polygon",
                "center": [0, 0],
                "radius": 20,
                "sides": 3,
                "mode": "circumscribed",
            },
            {"mode": "circumscribed"},
        ),
        ({"kind": "spline", "points": [[0, 0], [5, 5], [10, 0]]}, {"closed": False}),
        (
            {"kind": "slot", "p1": [0, 0], "p2": [40, 0], "width": 10},
            {"mode": "center_to_center"},
        ),
        ({"kind": "slot", "mode": "overall", "p1": [0, 0], "p2": [40, 0], "width": 10}, {}),
        (
            {"kind": "text", "text": "A", "height": 5, "corner": [0, 0]},
            {"h_align": "left", "v_align": "bottom"},
        ),
    ],
)
def test_draw_item_kinds_and_defaults(raw: dict[str, Any], expected: dict[str, Any]) -> None:
    out = draw_item({**raw, "id": "a"})
    assert out["kind"] == raw["kind"]
    assert out["id"] == "a"
    assert out["construction"] is False
    for key, value in expected.items():
        assert out[key] == value


def test_draw_item_optional_fields_are_dropped_and_construction_kept() -> None:
    out = draw_item({"kind": "line", "start": [0, 0], "end": [1, 0], "construction": True})
    assert "id" not in out
    assert out["construction"] is True
    text = draw_item({"kind": "text", "text": "A", "height": 5, "corner": [0, 0], "size": [20, 6]})
    assert text["size"] == [20.0, 6.0]
    assert "font" not in text


def test_unknown_kind_and_mode() -> None:
    with pytest.raises(ValidationError, match="unknown or missing kind; expected one of: point"):
        DRAW.validate_python({"kind": "blob"})
    with pytest.raises(ValidationError, match="unknown or missing kind"):
        DRAW.validate_python({"at": [0, 0]})
    with pytest.raises(ValidationError, match="unknown circle mode; expected one of"):
        DRAW.validate_python({"kind": "circle", "mode": "tangent", "center": [0, 0]})


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "line", "start": [0, 0]},
        {"kind": "circle", "center": [0, 0]},
        {"kind": "rectangle", "mode": "center", "center": [0, 0]},
        {"kind": "arc", "mode": "tangent", "to": [1, 1]},
        {"kind": "text", "text": "A", "corner": [0, 0]},
        {"kind": "polyline", "points": [[0, 0]]},
        {"kind": "circle", "center": [0, 0], "radius": 0},
        {"kind": "line", "start": [0, 0], "end": [1, 0], "id": "origin"},
        {"kind": "line", "start": [0, 0], "end": [1, 0], "id": "1bad"},
        {"kind": "line", "start": [0, 0], "end": [1, 0], "colour": "red"},
        {"kind": "line", "start": [0, 0, 0], "end": [1, 0]},
        {"kind": "point", "at": {"model": [0, 0]}},
        {
            "kind": "arc",
            "mode": "center_start_sweep",
            "center": [0, 0],
            "start": [1, 0],
            "sweep": 0,
        },
    ],
)
def test_missing_or_bad_fields_rejected(raw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DRAW.validate_python(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {"kind": "line", "start": ["w/2", 0], "end": [1, 0]},
        {"kind": "point", "at": ["10", "0"]},
        {"kind": "circle", "center": [0, 0], "radius": "r"},
        {"kind": "point", "at": {"model": [0, "z", 0]}},
    ],
)
def test_string_coordinates_rejected_with_dimension_hint(raw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="bind exact or parametric sizes with dimensions"):
        DRAW.validate_python(raw)


@pytest.mark.parametrize("sides", [2, 65])
def test_polygon_sides_out_of_range(sides: int) -> None:
    with pytest.raises(ValidationError):
        DRAW.validate_python({"kind": "polygon", "center": [0, 0], "radius": 5, "sides": sides})


def test_string_coordinate_rejected_by_tool_with_hint() -> None:
    items = [{"kind": "line", "start": ["w/2", 0], "end": [10, 0]}]
    message = rejected("sketch_draw", {"sketch": SKETCH, "items": items})
    assert STRING_COORDINATE_HINT in message


def test_draw_more_than_500_items_rejected() -> None:
    items = [{"kind": "point", "at": [i, 0]} for i in range(501)]
    rejected("sketch_draw", {"sketch": SKETCH, "items": items})
    rejected("sketch_draw", {"sketch": SKETCH, "items": []})


# --- 9.2 schema: constraints, dimensions, updates, patterns ------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "coincident", "point": "r.center", "entity": "origin"},
        {"type": "horizontal", "line": "l"},
        {"type": "vertical", "points": ["a", "b"]},
        {"type": "parallel", "line1": "a", "line2": "b"},
        {"type": "perpendicular", "line1": "a", "line2": "b"},
        {"type": "collinear", "line1": "a", "line2": "b"},
        {"type": "tangent", "curve1": "a", "curve2": "b"},
        {"type": "equal", "curve1": "a", "curve2": "b"},
        {"type": "concentric", "curve1": "a", "curve2": "b"},
        {"type": "midpoint", "point": "p", "curve": "l"},
        {"type": "symmetry", "entity1": "a", "entity2": "b", "line": "axis"},
        {"type": "smooth", "spline": "s", "curve": "a"},
        {"type": "fix", "entity": "p"},
    ],
)
def test_constraint_types(raw: dict[str, Any]) -> None:
    out = dump(CONSTRAINT.validate_python(raw))
    assert out == {**raw, **({"fixed": True} if raw["type"] == "fix" else {})}


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "weld", "point": "a", "entity": "b"},
        {"type": "horizontal"},
        {"type": "horizontal", "line": "l", "points": ["a", "b"]},
        {"type": "vertical", "points": ["a"]},
        {"type": "parallel", "line1": "a"},
        {"type": "coincident", "point": "", "entity": "b"},
    ],
)
def test_constraint_rejected(raw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CONSTRAINT.validate_python(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "distance", "line": "r.bottom", "value": 100, "parameter_name": "plate_w"},
        {"type": "distance", "point1": "origin", "point2": "h.center", "orientation": "vertical"},
        {"type": "offset", "line": "a", "entity": "p", "value": "plate_w / 4"},
        {"type": "radial", "curve": "c", "value": 5},
        {"type": "diameter", "curve": "c", "driven": True},
        {"type": "angular", "line1": "a", "line2": "b", "value": 30, "text_at": [5, 5]},
        {"type": "ellipse_major", "ellipse": "e", "value": 10},
        {"type": "ellipse_minor", "ellipse": "e", "value": "5 mm"},
    ],
)
def test_dimension_types(raw: dict[str, Any]) -> None:
    out = dump(DIMENSION.validate_python(raw))
    assert out["type"] == raw["type"]
    assert out["driven"] is raw.get("driven", False)
    for key in ("value", "parameter_name", "orientation"):
        if key in raw:
            assert out[key] == raw[key]


@pytest.mark.parametrize(
    "raw",
    [
        {"type": "diameter", "curve": "c", "driven": True, "value": 10},
        {"type": "chord", "curve": "c"},
        {"type": "distance", "point1": "a"},
        {"type": "distance", "line": "l", "point1": "a", "point2": "b"},
        {"type": "distance", "line": "l", "parameter_name": "plate w"},
        {"type": "radial"},
    ],
)
def test_dimension_rejected(raw: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DIMENSION.validate_python(raw)


def test_driven_with_value_message() -> None:
    with pytest.raises(ValidationError, match="driven dimension measures the geometry"):
        DIMENSION.validate_python({"type": "radial", "curve": "c", "driven": True, "value": 3})


def test_dimension_update() -> None:
    out = dump(DimensionUpdate.model_validate({"dimension": "d", "value": "plate_w + 10"}))
    assert out == {"dimension": "d", "value": "plate_w + 10"}
    for bad in (
        {"dimension": "d"},
        {"dimension": "d", "driven": True, "value": 1},
        {"dimension": "", "value": 1},
        {"dimension": "d", "parameter_name": "1x"},
    ):
        with pytest.raises(ValidationError):
            DimensionUpdate.model_validate(bad)


# --- 9.3-9.6 create, list, get, profiles -------------------------------------------------------


def test_sketch_create() -> None:
    answer = {"sketch": {"ref": SKETCH, "type": "Sketch", "name": "Base"}, "transform": {}}
    result, bridge = call("sketch_create", {"plane": "origin:XY", "name": "Base"}, answer)
    assert result.is_error is False
    assert bridge.last.method == "sketch.create"
    assert bridge.last.params == {"plane": "origin:XY", "name": "Base", "project_edges": False}
    assert json.loads(result_text(result)) == answer
    _, bridge = call(
        "sketch_create", {"plane": "FACE==", "component": "COMP==", "project_edges": True}
    )
    assert bridge.last.params == {"plane": "FACE==", "component": "COMP==", "project_edges": True}
    rejected("sketch_create", {})
    rejected("sketch_create", {"plane": ""})
    doc = tool("sketch_create").description
    assert "origin:XZ" in doc and "-Z" in doc and "transform" in doc


def test_sketch_create_bridge_error() -> None:
    error = BridgeError(
        ErrorCode.INVALID_PARAMS, "face is not planar", {"reason": "face_not_planar"}
    )
    result, _ = call("sketch_create", {"plane": "FACE=="}, error)
    assert result.is_error is True
    assert result_text(result).startswith("INVALID_PARAMS")
    assert "face_not_planar" in result_text(result)


def test_sketch_list() -> None:
    _, bridge = call("sketch_list", {}, {"sketches": []})
    assert (bridge.last.method, bridge.last.params) == ("sketch.list", {})
    _, bridge = call("sketch_list", {"component": "COMP=="})
    assert bridge.last.params == {"component": "COMP=="}


def test_sketch_get() -> None:
    _, bridge = call("sketch_get", {"sketch": SKETCH})
    assert bridge.last.method == "sketch.get"
    assert bridge.last.params == {"sketch": SKETCH, "limit": 500}
    _, bridge = call("sketch_get", {"sketch": SKETCH, "include": ["curves"], "limit": 10})
    assert bridge.last.params == {"sketch": SKETCH, "include": ["curves"], "limit": 10}
    rejected("sketch_get", {"sketch": SKETCH, "include": ["bodies"]})
    rejected("sketch_get", {"sketch": SKETCH, "limit": 0})
    rejected("sketch_get", {})


def test_sketch_profiles() -> None:
    _, bridge = call("sketch_profiles", {"sketch": SKETCH}, {"profiles": []})
    assert (bridge.last.method, bridge.last.params) == ("sketch.profiles", {"sketch": SKETCH})
    _, bridge = call("sketch_profiles", {"sketch": SKETCH, "contains": [30, 15]})
    assert bridge.last.params == {"sketch": SKETCH, "contains": [30.0, 15.0]}
    rejected("sketch_profiles", {"sketch": SKETCH, "contains": ["x", 0]})
    doc = tool("sketch_profiles").description
    assert "profile:<sketch token>@" in doc and "feature" in doc


# --- 9.7 draw -----------------------------------------------------------------------------------


def test_sketch_draw_forwards_items_constraints_dimensions() -> None:
    arguments = {
        "sketch": SKETCH,
        "items": [
            {
                "kind": "rectangle",
                "mode": "center",
                "id": "r",
                "center": "origin",
                "corner": [50, 30],
            },
            {"kind": "line", "id": "a", "start": [0, 0], "end": [40, 0], "construction": True},
            {"kind": "arc", "mode": "tangent", "from": "a.end", "to": [60, 20]},
        ],
        "constraints": [{"type": "coincident", "point": "r.center", "entity": "origin"}],
        "dimensions": [{"type": "distance", "line": "r.bottom", "value": "plate_w"}],
        "units": "in",
        "return_profiles": False,
    }
    result, bridge = call("sketch_draw", arguments, {"refs": {"r": {"ref": "T"}}})
    assert result.is_error is False
    assert bridge.last.method == "sketch.draw"
    assert bridge.last.params == {
        "sketch": SKETCH,
        "items": [
            {
                "kind": "rectangle",
                "mode": "center",
                "id": "r",
                "construction": False,
                "center": "origin",
                "corner": [50.0, 30.0],
            },
            {
                "kind": "line",
                "id": "a",
                "construction": True,
                "start": [0.0, 0.0],
                "end": [40.0, 0.0],
            },
            {
                "kind": "arc",
                "mode": "tangent",
                "construction": False,
                "from": "a.end",
                "to": [60.0, 20.0],
            },
        ],
        "constraints": [{"type": "coincident", "point": "r.center", "entity": "origin"}],
        "dimensions": [
            {
                "type": "distance",
                "line": "r.bottom",
                "value": "plate_w",
                "driven": False,
                "orientation": "aligned",
            }
        ],
        "units": "in",
        "return_profiles": False,
    }


def test_sketch_draw_defaults_and_docstring() -> None:
    _, bridge = call("sketch_draw", {"sketch": SKETCH, "items": [{"kind": "point", "at": [1, 2]}]})
    assert bridge.last.params == {
        "sketch": SKETCH,
        "items": [{"kind": "point", "construction": False, "at": [1.0, 2.0]}],
        "constraints": [],
        "dimensions": [],
        "units": "mm",
        "return_profiles": True,
    }
    rejected("sketch_draw", {"sketch": SKETCH, "items": [{"kind": "blob"}]})
    rejected(
        "sketch_draw", {"sketch": SKETCH, "items": [{"kind": "point", "at": [0, 0]}], "units": "yd"}
    )
    doc = tool("sketch_draw").description
    for needle in ("polygon", "slot", "r.bottom", "Local ids live one call", "fully constrained"):
        assert needle in doc


def test_sketch_draw_bridge_error_details() -> None:
    error = BridgeError(
        ErrorCode.INVALID_PARAMS,
        "unknown local id zz",
        {"phase": "geometry", "index": 1, "id": "b"},
    )
    result, _ = call(
        "sketch_draw", {"sketch": SKETCH, "items": [{"kind": "point", "at": [0, 0]}]}, error
    )
    assert result.is_error is True
    assert '"phase": "geometry"' in result_text(result)


# --- 9.8-9.12 constrain, dimension, dimension_set, set_construction, delete --------------------


def test_sketch_constrain() -> None:
    constraints = [
        {"type": "horizontal", "points": ["P1==", "P2=="]},
        {"type": "fix", "entity": "P3==", "fixed": False},
    ]
    _, bridge = call("sketch_constrain", {"sketch": SKETCH, "constraints": constraints})
    assert bridge.last.method == "sketch.constrain"
    assert bridge.last.params == {
        "sketch": SKETCH,
        "constraints": constraints,
        "return_profiles": True,
    }
    rejected("sketch_constrain", {"sketch": SKETCH, "constraints": []})
    message = rejected("sketch_constrain", {"sketch": SKETCH, "constraints": [{"type": "weld"}]})
    assert "weld" in message


def test_sketch_dimension() -> None:
    dims = [{"type": "angular", "line1": "A==", "line2": "B==", "value": 30}]
    _, bridge = call("sketch_dimension", {"sketch": SKETCH, "dimensions": dims})
    assert bridge.last.method == "sketch.dimension"
    assert bridge.last.params == {
        "sketch": SKETCH,
        "dimensions": [{**dims[0], "value": 30.0, "driven": False}],
        "units": "mm",
        "return_profiles": True,
    }
    message = rejected(
        "sketch_dimension",
        {
            "sketch": SKETCH,
            "dimensions": [{"type": "diameter", "curve": "C", "driven": True, "value": 5}],
        },
    )
    assert "driven" in message


def test_sketch_dimension_set() -> None:
    updates = [{"dimension": "D==", "value": "plate_w + 10"}, {"dimension": "E==", "driven": True}]
    _, bridge = call("sketch_dimension_set", {"sketch": SKETCH, "updates": updates})
    assert bridge.last.method == "sketch.dimension_set"
    assert bridge.last.params == {"sketch": SKETCH, "updates": updates, "return_profiles": True}
    rejected("sketch_dimension_set", {"sketch": SKETCH, "updates": [{"dimension": "D=="}]})


def test_sketch_set_construction() -> None:
    _, bridge = call("sketch_set_construction", {"sketch": SKETCH, "curves": ["L=="]})
    assert bridge.last.method == "sketch.set_construction"
    assert bridge.last.params == {
        "sketch": SKETCH,
        "curves": ["L=="],
        "construction": True,
        "return_profiles": True,
    }
    rejected("sketch_set_construction", {"sketch": SKETCH, "curves": []})


def test_sketch_delete_and_docstring() -> None:
    answer = {"deleted": ["L=="], "cascaded": ["C=="], "status": STATUS}
    result, bridge = call("sketch_delete", {"sketch": SKETCH, "entities": ["L==", "C=="]}, answer)
    assert bridge.last.method == "sketch.delete"
    assert bridge.last.params == {
        "sketch": SKETCH,
        "entities": ["L==", "C=="],
        "return_profiles": True,
    }
    assert json.loads(result_text(result))["cascaded"] == ["C=="]
    rejected("sketch_delete", {"sketch": SKETCH, "entities": []})
    doc = tool("sketch_delete").description
    assert "NOT ATOMIC" in doc and "details.deleted" in doc


# --- 9.13-9.20 edit tools -----------------------------------------------------------------------


def test_sketch_transform() -> None:
    _, bridge = call("sketch_transform", {"sketch": SKETCH, "entities": ["C=="]})
    assert bridge.last.method == "sketch.transform"
    assert bridge.last.params == {
        "sketch": SKETCH,
        "entities": ["C=="],
        "translate": [0, 0],
        "rotate": 0,
        "about": [0, 0],
        "copy": False,
        "return_profiles": True,
    }
    _, bridge = call(
        "sketch_transform",
        {
            "sketch": SKETCH,
            "entities": ["C=="],
            "translate": [30, 0],
            "rotate": "tilt",
            "about": {"model": [1, 2, 3]},
            "copy": True,
        },
    )
    assert bridge.last.params["translate"] == [30.0, 0.0]
    assert bridge.last.params["rotate"] == "tilt"
    assert bridge.last.params["about"] == {"model": [1.0, 2.0, 3.0]}
    assert bridge.last.params["copy"] is True


def test_sketch_offset() -> None:
    arguments = {"sketch": SKETCH, "curves": ["L=="], "distance": "wall", "side_point": [0, 0]}
    _, bridge = call("sketch_offset", arguments)
    assert bridge.last.method == "sketch.offset"
    assert bridge.last.params == {
        "sketch": SKETCH,
        "curves": ["L=="],
        "distance": "wall",
        "side_point": [0.0, 0.0],
        "chain": True,
        "return_profiles": True,
    }
    rejected("sketch_offset", {"sketch": SKETCH, "curves": ["L=="], "distance": 2})


@pytest.mark.parametrize("name", ["sketch_trim", "sketch_extend"])
def test_sketch_trim_extend(name: str) -> None:
    _, bridge = call(name, {"sketch": SKETCH, "curve": "L==", "point": [10, 0]})
    assert bridge.last.method == name.replace("sketch_", "sketch.")
    assert bridge.last.params == {
        "sketch": SKETCH,
        "curve": "L==",
        "point": [10.0, 0.0],
        "return_profiles": True,
    }
    rejected(name, {"sketch": SKETCH, "curves": ["L=="], "point": [10, 0]})
    assert "ONE curve per call" in tool(name).description


def test_sketch_fillet() -> None:
    arguments = {"sketch": SKETCH, "curve1": "A==", "curve2": "B==", "radius": "r_corner"}
    _, bridge = call("sketch_fillet", arguments)
    assert bridge.last.method == "sketch.fillet"
    assert bridge.last.params == {**arguments, "dimension": True, "return_profiles": True}
    _, bridge = call(
        "sketch_fillet",
        {**arguments, "radius": 3, "point1": [1, 0], "point2": "B.end", "dimension": False},
    )
    assert bridge.last.params["radius"] == 3.0
    assert bridge.last.params["point1"] == [1.0, 0.0]
    assert bridge.last.params["point2"] == "B.end"
    assert bridge.last.params["dimension"] is False
    assert "ONE corner" in tool("sketch_fillet").description


def test_sketch_mirror() -> None:
    _, bridge = call("sketch_mirror", {"sketch": SKETCH, "entities": ["A==", "B=="], "axis": "X=="})
    assert bridge.last.method == "sketch.mirror"
    assert bridge.last.params == {
        "sketch": SKETCH,
        "entities": ["A==", "B=="],
        "axis": "X==",
        "constrain": True,
        "return_profiles": True,
    }
    rejected("sketch_mirror", {"sketch": SKETCH, "entities": ["A=="]})


def test_sketch_pattern_rectangular_and_circular() -> None:
    _, bridge = call(
        "sketch_pattern",
        {
            "sketch": SKETCH,
            "entities": ["C=="],
            "kind": "rectangular",
            "count_x": 3,
            "spacing_x": 20,
        },
    )
    assert bridge.last.method == "sketch.pattern"
    assert bridge.last.params == {
        "sketch": SKETCH,
        "entities": ["C=="],
        "kind": "rectangular",
        "count_x": 3,
        "spacing_x": 20.0,
        "count_y": 1,
        "spacing_y": 0,
        "angle": 0,
        "return_profiles": True,
    }
    _, bridge = call(
        "sketch_pattern",
        {"sketch": SKETCH, "entities": ["C=="], "kind": "circular", "center": "origin", "count": 6},
    )
    assert bridge.last.params == {
        "sketch": SKETCH,
        "entities": ["C=="],
        "kind": "circular",
        "center": "origin",
        "count": 6,
        "angle": 360,
        "return_profiles": True,
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"kind": "rectangular", "spacing_x": 20},
        {"kind": "rectangular", "count_x": 2, "spacing_x": 20, "center": [0, 0]},
        {"kind": "circular", "count": 6},
        {"kind": "circular", "center": [0, 0], "count": 1},
        {"kind": "circular", "center": [0, 0], "count": 6, "count_x": 2},
        {"kind": "hexagonal", "count": 6},
    ],
)
def test_sketch_pattern_rejected(fields: dict[str, Any]) -> None:
    rejected("sketch_pattern", {"sketch": SKETCH, "entities": ["C=="], **fields})


def test_sketch_project() -> None:
    _, bridge = call("sketch_project", {"sketch": SKETCH, "entities": ["FACE=="]})
    assert bridge.last.method == "sketch.project"
    assert bridge.last.params == {
        "sketch": SKETCH,
        "entities": ["FACE=="],
        "mode": "project",
        "return_profiles": True,
    }
    _, bridge = call("sketch_project", {"sketch": SKETCH, "entities": ["B=="], "mode": "cut_edges"})
    assert bridge.last.params["mode"] == "cut_edges"
    rejected("sketch_project", {"sketch": SKETCH, "entities": ["B=="], "mode": "wrap"})


def test_every_sketch_tool_has_schema_and_docstring() -> None:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    sketch_tools = {t.name: t for t in tools if t.name.startswith("sketch_")}
    assert len(sketch_tools) == 18
    for name, t in sketch_tools.items():
        assert t.description and "Returns" in t.description, name
        assert t.input_schema["type"] == "object", name

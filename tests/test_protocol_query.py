"""``fusion_mcp.protocol.query``: parse, match, select (c03 1.2, 1.3, D9)."""

from typing import Any

import pytest

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.protocol.query import QuerySpec, matches, parse_query, select


def invalid(params: dict[str, Any]) -> BridgeError:
    with pytest.raises(BridgeError) as info:
        parse_query(params)
    assert info.value.code is ErrorCode.INVALID_PARAMS
    return info.value


# --- 1.2 parse_query -----------------------------------------------------------------------


def test_defaults() -> None:
    spec = parse_query({"kind": "face"})
    assert spec == QuerySpec(kind="face")
    assert spec.normal_tolerance_deg == 1.0
    assert spec.direction_tolerance_deg == 1.0
    assert spec.order == "desc"
    assert spec.limit == 50


def test_full_face_query_parses() -> None:
    spec = parse_query(
        {
            "kind": "face",
            "scope": ["tok"],
            "surface_type": "plane",
            "normal": [0, 0, 1],
            "normal_tolerance_deg": 5,
            "area_min_mm2": 1,
            "area_max_mm2": 10,
            "within_box": {"min_mm": [0, 0, 0], "max_mm": [1, 1, 1]},
            "sort_by": "area",
            "order": "asc",
            "limit": 1000,
            "units": "mm",
            "radius_min_mm": None,
        }
    )
    assert spec.normal == (0.0, 0.0, 1.0)
    assert spec.within_box == ((0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert spec.scope == ["tok"]
    assert spec.limit == 1000


def test_kind_is_required_and_checked() -> None:
    assert invalid({}).details["param"] == "kind"
    assert invalid({"kind": "solid"}).details["param"] == "kind"


def test_unknown_key() -> None:
    error = invalid({"kind": "face", "colour": "red"})
    assert error.details["param"] == "colour"
    assert "unknown" in error.message


def test_filter_not_for_kind() -> None:
    error = invalid({"kind": "vertex", "surface_type": "plane"})
    assert error.details["param"] == "surface_type"
    assert invalid({"kind": "face", "length_min_mm": 1}).details["param"] == "length_min_mm"
    assert invalid({"kind": "edge", "normal": [0, 0, 1]}).details["param"] == "normal"


def test_sort_key_not_for_kind() -> None:
    assert invalid({"kind": "vertex", "sort_by": "area"}).details["param"] == "sort_by"
    assert invalid({"kind": "face", "sort_by": "volume"}).details["param"] == "sort_by"
    assert parse_query({"kind": "body", "sort_by": "volume"}).sort_by == "volume"


def test_min_greater_than_max() -> None:
    error = invalid({"kind": "face", "area_min_mm2": 100, "area_max_mm2": 10})
    assert error.details["param"] == "area_min_mm2"
    invalid({"kind": "edge", "length_min_mm": 5, "length_max_mm": 1})
    invalid({"kind": "edge", "radius_min_mm": 5, "radius_max_mm": 1})


@pytest.mark.parametrize(
    "vector", [[0, 0], [0, 0, 0], "z", [0, 0, "1"], [True, 0, 0], [0, 0, float("nan")]]
)
def test_bad_vector(vector: Any) -> None:
    assert invalid({"kind": "face", "normal": vector}).details["param"] == "normal"


def test_bad_box_and_scalars() -> None:
    invalid({"kind": "face", "within_box": {"min_mm": [0, 0, 0]}})
    invalid({"kind": "face", "within_box": {"min_mm": [1, 0, 0], "max_mm": [0, 1, 1]}})
    invalid({"kind": "face", "surface_type": "blob"})
    invalid({"kind": "edge", "curve_type": "spiral"})
    invalid({"kind": "face", "order": "random"})
    invalid({"kind": "face", "sort_by": "colour"})
    invalid({"kind": "face", "area_min_mm2": "big"})
    invalid({"kind": "face", "normal_tolerance_deg": -1})
    invalid({"kind": "face", "scope": "tok"})
    invalid({"kind": "face", "scope": [""]})


@pytest.mark.parametrize("limit", [0, 1001, 2.5, True, "10"])
def test_bad_limit(limit: Any) -> None:
    assert invalid({"kind": "face", "limit": limit}).details["param"] == "limit"


# --- 1.3 matches / select on brief dicts of a 10x20x30 mm box -------------------------------


def box_faces() -> list[dict[str, Any]]:
    w, h, d = 10.0, 20.0, 30.0
    faces = []
    for axis, (a, b) in enumerate([(h, d), (w, d), (w, h)]):
        for side in (0, 1):
            normal = [0.0, 0.0, 0.0]
            normal[axis] = 1.0 if side else -1.0
            centroid = [w / 2, h / 2, d / 2]
            centroid[axis] = (w, h, d)[axis] * side
            faces.append(
                {
                    "ref": f"face{'+' if side else '-'}{'xyz'[axis]}",
                    "type": "BRepFace",
                    "surface_type": "plane",
                    "area_mm2": a * b,
                    "centroid_mm": centroid,
                    "normal": normal,
                }
            )
    return faces


def box_edges() -> list[dict[str, Any]]:
    edges = []
    for axis, length in enumerate((10.0, 20.0, 30.0)):
        direction = [0.0, 0.0, 0.0]
        direction[axis] = 1.0
        for i in range(4):
            edges.append(
                {
                    "ref": f"e{'xyz'[axis]}{i}",
                    "type": "BRepEdge",
                    "curve_type": "line",
                    "length_mm": length,
                    "mid_mm": [float(i), 0.0, 0.0],
                    "direction": direction,
                }
            )
    return edges


def query(params: dict[str, Any], items: list[dict[str, Any]]) -> tuple[list[str], int]:
    found, total = select(parse_query(params), items)
    return [i["ref"] for i in found], total


def test_top_face_by_normal() -> None:
    refs, total = query({"kind": "face", "surface_type": "plane", "normal": [0, 0, 1]}, box_faces())
    assert refs == ["face+z"]
    assert total == 1


def test_normal_is_directional_with_tolerance() -> None:
    faces = box_faces()
    assert query({"kind": "face", "normal": [0, 0, -1]}, faces)[0] == ["face-z"]
    tilted = [0.0, 0.01, 1.0]  # ~0.57°
    assert query({"kind": "face", "normal": tilted}, faces)[0] == ["face+z"]
    assert query({"kind": "face", "normal": tilted, "normal_tolerance_deg": 0.1}, faces)[1] == 0


def test_long_edges() -> None:
    refs, total = query({"kind": "edge", "length_min_mm": 25}, box_edges())
    assert total == 4
    assert all(r.startswith("ez") for r in refs)


def test_direction_matches_both_ways() -> None:
    edges = box_edges()
    assert query({"kind": "edge", "direction": [0, -1, 0]}, edges)[1] == 4
    assert query({"kind": "edge", "direction": [0, 1, 0]}, edges)[1] == 4


def test_largest_face() -> None:
    found, total = select(parse_query({"kind": "face", "sort_by": "area", "limit": 1}), box_faces())
    assert total == 6
    assert len(found) == 1
    assert found[0]["area_mm2"] == 600.0


def test_sort_ascending_and_by_coordinate() -> None:
    refs, _ = query({"kind": "face", "sort_by": "area", "order": "asc", "limit": 2}, box_faces())
    assert sorted(refs) == ["face+z", "face-z"]
    refs, _ = query({"kind": "face", "sort_by": "z", "limit": 1}, box_faces())
    assert refs == ["face+z"]


def test_within_box() -> None:
    box = {"min_mm": [-1, -1, 29], "max_mm": [11, 21, 31]}
    assert query({"kind": "face", "within_box": box}, box_faces())[0] == ["face+z"]


def test_radius_and_axis_filters() -> None:
    items: list[dict[str, Any]] = [
        {"ref": "hole", "surface_type": "cylinder", "radius_mm": 5.0, "axis": [0, 0, -1]},
        {"ref": "boss", "surface_type": "cylinder", "radius_mm": 10.0, "axis": [0, 0, 1]},
        {"ref": "flat", "surface_type": "plane", "area_mm2": 1.0},
    ]
    spec = {"kind": "face", "surface_type": "cylinder", "radius_min_mm": 4.9, "radius_max_mm": 5.1}
    assert query(spec, items)[0] == ["hole"]
    assert query({"kind": "face", "axis": [0, 0, 1]}, items)[0] == ["hole", "boss"]
    # Items without the filtered value never match a range.
    assert query({"kind": "face", "radius_max_mm": 100}, items)[0] == ["hole", "boss"]


def test_items_without_sort_value_go_last() -> None:
    items: list[dict[str, Any]] = [
        {"ref": "a"},
        {"ref": "b", "radius_mm": 1.0},
        {"ref": "c", "radius_mm": 2.0},
    ]
    refs, _ = query({"kind": "face", "sort_by": "radius"}, items)
    assert refs == ["c", "b", "a"]
    refs, _ = query({"kind": "face", "sort_by": "radius", "order": "asc"}, items)
    assert refs == ["b", "c", "a"]


def test_body_position_from_bbox() -> None:
    body = {"ref": "b", "volume_mm3": 1.0, "bbox": {"min_mm": [0, 0, 0], "max_mm": [2, 2, 2]}}
    spec = parse_query({"kind": "body", "within_box": {"min_mm": [0, 0, 0], "max_mm": [1, 1, 1]}})
    assert matches(spec, body)
    assert not matches(spec, {"ref": "nowhere"})

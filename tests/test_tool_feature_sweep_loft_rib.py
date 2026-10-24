"""``feature_sweep`` / ``feature_loft`` / ``feature_rib`` / ``feature_web`` tools on
``FakeBridge`` (c07 D12-D14; tasks 6.4, 7.3, 8.3)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, get_args

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from fusion_mcp.protocol import features as vocab
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text
from fusion_mcp.server.tools import _feature_models as m


class Rejected:
    """Arguments refused by the tool's input schema (``ToolError``) before the bridge."""

    is_error = True

    def __init__(self, message: str) -> None:
        self.message = message


def call(name: str, arguments: dict[str, Any], bridge: FakeBridge | None = None) -> Any:
    bridge = bridge or FakeBridge()
    try:
        result = asyncio.run(call_tool(create_server(bridge), name, arguments))
    except ToolError as exc:
        return Rejected(str(exc)), bridge
    return result, bridge


def tool(name: str) -> Any:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    return next(t for t in tools if t.name == name)


def result_of(kind: str) -> dict[str, Any]:
    return {
        "feature": {"ref": "feat-1", "type": kind, "name": f"{kind[:-7]}1"},
        "health": {"state": "ok", "message": ""},
        "created_bodies": [{"ref": "body-1", "type": "BRepBody", "name": "Body1"}],
        "modified_bodies": [],
        "removed_bodies": [],
    }


PROFILE = "profile:sk-1@10,10"


@pytest.mark.parametrize(
    ("literal", "values"),
    [
        (m.SweepOrientation, vocab.SWEEP_ORIENTATIONS),
        (m.RailScaling, vocab.RAIL_SCALINGS),
        (m.LoftEnd, vocab.LOFT_END_CONDITIONS),
        (m.RibThicknessLocation, vocab.RIB_THICKNESS_LOCATIONS),
    ],
)
def test_literals_match_protocol_tuples(literal: Any, values: tuple[str, ...]) -> None:
    assert get_args(literal) == values


def test_rib_depth_tags_match_protocol() -> None:
    members = get_args(get_args(m.RibDepth)[0])
    tags = tuple(get_args(c.model_fields["type"].annotation)[0] for c in members)
    assert tags == vocab.RIB_DEPTH_TYPES


# --- 6.4 feature_sweep ---------------------------------------------------------------------


def test_sweep_defaults_and_result_pass_through() -> None:
    result_data = result_of("SweepFeature")
    bridge = FakeBridge({"feature.sweep": result_data})
    result, _ = call("feature_sweep", {"profiles": [PROFILE], "path": ["c1", "c2"]}, bridge)
    assert bridge.last.method == "feature.sweep"
    assert bridge.last.params == {
        "profiles": [PROFILE],
        "path": ["c1", "c2"],
        "path_chain": True,
        "orientation": "perpendicular",
        "twist_angle": 0.0,
        "taper_angle": 0.0,
        "path_extent": 1.0,
        "flip": False,
        "operation": "new_body",
        "units": "mm",
    }
    assert result.is_error is False
    assert json.loads(result_text(result)) == result_data


def test_sweep_twist_and_options_are_sent() -> None:
    bridge = FakeBridge({"feature.sweep": result_of("SweepFeature")})
    result, _ = call(
        "feature_sweep",
        {
            "profiles": [PROFILE],
            "path": ["c1"],
            "path_chain": False,
            "orientation": "parallel",
            "twist_angle": 90,
            "taper_angle": "draft",
            "path_extent": 0.5,
            "flip": True,
            "operation": "cut",
            "participant_bodies": ["body-1"],
            "units": "in",
        },
        bridge,
    )
    assert result.is_error is False, result_text(result)
    params = bridge.last.params
    assert params["twist_angle"] == 90.0 and params["taper_angle"] == "draft"
    assert params["path_chain"] is False and params["orientation"] == "parallel"
    assert params["path_extent"] == 0.5 and params["flip"] is True
    assert params["operation"] == "cut" and params["participant_bodies"] == ["body-1"]
    assert params["units"] == "in"
    assert "guide_rail" not in params and "rail_scaling" not in params


def test_sweep_guide_rail_sends_scaling_not_twist() -> None:
    bridge = FakeBridge({"feature.sweep": result_of("SweepFeature")})
    call(
        "feature_sweep",
        {"profiles": [PROFILE], "path": ["c1"], "guide_rail": ["r1"], "rail_scaling": "stretch"},
        bridge,
    )
    params = bridge.last.params
    assert params["guide_rail"] == ["r1"]
    assert params["rail_scaling"] == "stretch"
    assert "twist_angle" not in params and "taper_angle" not in params
    call("feature_sweep", {"profiles": [PROFILE], "path": ["c1"], "guide_rail": ["r1"]}, bridge)
    assert bridge.last.params["rail_scaling"] == "scale"


def test_sweep_rail_rules_fail_without_bridge() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        ({"guide_rail": ["r1"], "taper_angle": 2}, "taper_angle"),
        ({"guide_rail": ["r1"], "twist_angle": 45}, "twist_angle"),
        ({"guide_rail": ["r1"], "twist_angle": "turns"}, "twist_angle"),
        ({"rail_scaling": "none"}, "rail_scaling"),
    ]
    for arguments, param in cases:
        result, bridge = call("feature_sweep", {"profiles": [PROFILE], "path": ["c1"], **arguments})
        assert bridge.calls == [], arguments
        assert result.is_error is True
        text = result_text(result)
        assert text.startswith("INVALID_PARAMS:"), text
        assert f'"param": "{param}"' in text


def test_sweep_schema_rejects_bad_shapes() -> None:
    for arguments in (
        {"profiles": [PROFILE]},
        {"profiles": [PROFILE], "path": []},
        {"profiles": [PROFILE], "path": ["c1"], "path_extent": 0},
        {"profiles": [PROFILE], "path": ["c1"], "path_extent": 1.5},
        {"profiles": [PROFILE], "path": ["c1"], "orientation": "normal"},
        {"profiles": [PROFILE], "path": ["c1"], "guide_rail": []},
        {"profiles": [PROFILE], "path": ["c1"], "rail_scaling": "squash"},
    ):
        result, bridge = call("feature_sweep", arguments)
        assert result.is_error is True, arguments
        assert bridge.calls == []


def test_sweep_bridge_error_and_schema() -> None:
    error = BridgeError(
        ErrorCode.FUSION_API_ERROR,
        "Fusion could not build a path from 'path': gap",
        {"param": "path", "count": 2},
    )
    result, _ = call(
        "feature_sweep",
        {"profiles": [PROFILE], "path": ["c1", "c2"]},
        FakeBridge({"feature.sweep": error}),
    )
    assert result.is_error is True
    assert result_text(result).startswith("FUSION_API_ERROR: Fusion could not build a path")
    sweep = tool("feature_sweep")
    assert set(sweep.input_schema["required"]) == {"profiles", "path"}
    for name in ("guide_rail", "rail_scaling", "orientation", "twist_angle", "path_extent"):
        assert name in sweep.input_schema["properties"]
    doc = sweep.description or ""
    assert "degrees" in doc and "guide_rail" in doc and "created_bodies" in doc


# --- 7.3 feature_loft ----------------------------------------------------------------------


def test_loft_with_rail_is_sent() -> None:
    result_data = result_of("LoftFeature")
    bridge = FakeBridge({"feature.loft": result_data})
    result, _ = call(
        "feature_loft",
        {"sections": [{"entity": PROFILE}, {"entity": "profile:sk-2@0,0"}], "rails": ["r1"]},
        bridge,
    )
    assert bridge.last.method == "feature.loft"
    assert bridge.last.params == {
        "sections": [
            {"entity": PROFILE, "end_condition": "free", "angle": 0.0, "weight": 1.0},
            {"entity": "profile:sk-2@0,0", "end_condition": "free", "angle": 0.0, "weight": 1.0},
        ],
        "rails": ["r1"],
        "closed": False,
        "merge_tangent_edges": True,
        "operation": "new_body",
        "units": "mm",
    }
    assert json.loads(result_text(result)) == result_data


def test_loft_all_options_are_sent() -> None:
    bridge = FakeBridge({"feature.loft": result_of("LoftFeature")})
    result, _ = call(
        "feature_loft",
        {
            "sections": [
                {"entity": "face-1", "end_condition": "direction", "angle": 10, "weight": 2},
                {"entity": {"sketch": "sk-2", "point": [1, 2]}, "end_condition": "smooth"},
                {"entity": "point-1", "end_condition": "point_tangent", "weight": 0.5},
            ],
            "centerline": "edge-1",
            "closed": False,
            "merge_tangent_edges": False,
            "operation": "join",
            "participant_bodies": ["body-1"],
        },
        bridge,
    )
    assert result.is_error is False, result_text(result)
    params = bridge.last.params
    assert params["sections"][0] == {
        "entity": "face-1",
        "end_condition": "direction",
        "angle": 10.0,
        "weight": 2.0,
    }
    assert params["sections"][1]["entity"] == {"sketch": "sk-2", "point": [1.0, 2.0]}
    assert params["centerline"] == "edge-1"
    assert "rails" not in params
    assert params["merge_tangent_edges"] is False
    assert params["participant_bodies"] == ["body-1"]


def test_loft_rails_and_centerline_fail_without_bridge() -> None:
    result, bridge = call(
        "feature_loft",
        {
            "sections": [{"entity": PROFILE}, {"entity": PROFILE}],
            "rails": ["r1"],
            "centerline": "c1",
        },
    )
    assert bridge.calls == []
    text = result_text(result)
    assert text.startswith("INVALID_PARAMS:") and '"param": "centerline"' in text


def test_loft_schema_rejects_bad_shapes() -> None:
    two = [{"entity": PROFILE}, {"entity": PROFILE}]
    cases: tuple[dict[str, Any], ...] = (
        {"sections": [{"entity": PROFILE}]},
        {"sections": []},
        {"sections": [{"entity": PROFILE, "end_condition": "spiky"}, {"entity": PROFILE}]},
        {"sections": [{"entity": PROFILE, "angle": 5}, {"entity": PROFILE}]},
        {"sections": [{"entity": PROFILE, "weight": 0}, {"entity": PROFILE}]},
        {"sections": [{"entity": PROFILE, "color": "red"}, {"entity": PROFILE}]},
        {"sections": two, "operation": "merge"},
    )
    for arguments in cases:
        result, bridge = call("feature_loft", arguments)
        assert result.is_error is True, arguments
        assert bridge.calls == []


def test_loft_point_in_middle_error_reaches_the_agent() -> None:
    error = BridgeError(
        ErrorCode.INVALID_PARAMS,
        "parameter 'sections[1].entity': a point section must be the first or the last section",
        {"param": "sections[1].entity", "reason": "point_in_middle"},
    )
    result, _ = call(
        "feature_loft",
        {"sections": [{"entity": PROFILE}, {"entity": "pt"}, {"entity": PROFILE}]},
        FakeBridge({"feature.loft": error}),
    )
    assert result.is_error is True
    assert '"reason": "point_in_middle"' in result_text(result)
    loft = tool("feature_loft")
    assert set(loft.input_schema["required"]) == {"sections"}
    doc = loft.description or ""
    assert "point" in doc and "centerline" in doc and "created_bodies" in doc


# --- 8.3 feature_rib / feature_web ---------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "method", "kind"),
    [("feature_rib", "feature.rib", "RibFeature"), ("feature_web", "feature.web", "WebFeature")],
)
def test_rib_defaults_and_result_pass_through(name: str, method: str, kind: str) -> None:
    result_data = result_of(kind)
    bridge = FakeBridge({method: result_data})
    result, _ = call(name, {"curves": ["line-1"], "thickness": 3}, bridge)
    assert bridge.last.method == method
    assert bridge.last.params == {
        "curves": ["line-1"],
        "thickness": 3.0,
        "thickness_location": "symmetric",
        "flip": False,
        "units": "mm",
    }
    assert json.loads(result_text(result)) == result_data


@pytest.mark.parametrize(
    ("name", "method"), [("feature_rib", "feature.rib"), ("feature_web", "feature.web")]
)
def test_rib_all_fields_are_sent(name: str, method: str) -> None:
    bridge = FakeBridge({method: result_of("RibFeature")})
    result, _ = call(
        name,
        {
            "curves": ["l1", "l2"],
            "thickness": "wall",
            "thickness_location": "side1",
            "depth": {"type": "distance", "distance": 15},
            "flip": True,
            "units": "cm",
        },
        bridge,
    )
    assert result.is_error is False, result_text(result)
    assert bridge.last.params == {
        "curves": ["l1", "l2"],
        "thickness": "wall",
        "thickness_location": "side1",
        "depth": {"type": "distance", "distance": 15.0},
        "flip": True,
        "units": "cm",
    }
    call(name, {"curves": ["l1"], "thickness": 1, "depth": {"type": "to_next"}}, bridge)
    assert bridge.last.params["depth"] == {"type": "to_next"}


@pytest.mark.parametrize("name", ["feature_rib", "feature_web"])
def test_rib_schema_rejects_bad_shapes(name: str) -> None:
    for arguments in (
        {"curves": [], "thickness": 3},
        {"curves": ["l1"]},
        {"curves": ["l1"], "thickness": True},
        {"curves": ["l1"], "thickness": 3, "thickness_location": "middle"},
        {"curves": ["l1"], "thickness": 3, "depth": {"type": "blind"}},
        {"curves": ["l1"], "thickness": 3, "depth": {"type": "distance"}},
        {"curves": ["l1"], "thickness": 3, "depth": {"type": "to_next", "distance": 2}},
    ):
        result, bridge = call(name, arguments)
        assert result.is_error is True, arguments
        assert bridge.calls == []


@pytest.mark.parametrize(
    ("name", "method", "label"),
    [("feature_rib", "feature.rib", "Rib"), ("feature_web", "feature.web", "Web")],
)
def test_rib_not_available_reaches_the_agent(name: str, method: str, label: str) -> None:
    error = BridgeError(
        ErrorCode.FUSION_API_ERROR,
        f"{label} features are not available in this Fusion API version",
        {"feature_type": f"{label}Feature"},
    )
    result, _ = call(name, {"curves": ["l1"], "thickness": 3}, FakeBridge({method: error}))
    assert result.is_error is True
    assert result_text(result).startswith(f"FUSION_API_ERROR: {label} features are not available")
    described = tool(name)
    assert set(described.input_schema["required"]) == {"curves", "thickness"}
    assert "operation" not in described.input_schema["properties"]
    doc = described.description or ""
    assert "mm" in doc and "not" in doc and "modified_bodies" in doc

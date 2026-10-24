"""``pattern_rectangular`` / ``pattern_circular`` / ``pattern_path`` / ``pattern_mirror`` tools on
``FakeBridge`` (c08 D3-D7; tasks 3.7-3.10)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text


class Rejected:
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


def result_of(kind: str, **extra: Any) -> dict[str, Any]:
    return {
        "feature": {"ref": "feat-1", "type": kind, "name": f"{kind[:-7]}1"},
        "health": {"state": "ok", "message": ""},
        "created_bodies": [{"ref": "body-2", "type": "BRepBody", "name": "Body2"}],
        "modified_bodies": [],
        "removed_bodies": [],
        **extra,
    }


def assert_schema_rejects(name: str, cases: list[dict[str, Any]]) -> None:
    for arguments in cases:
        result, bridge = call(name, arguments)
        assert result.is_error is True, arguments
        assert bridge.calls == []


def assert_rules(name: str, cases: list[tuple[dict[str, Any], str]]) -> None:
    for arguments, param in cases:
        result, bridge = call(name, arguments)
        assert bridge.calls == [], arguments
        assert result.is_error is True
        text = result_text(result)
        assert text.startswith("INVALID_PARAMS:"), text
        assert f'"param": "{param}"' in text, text


# --- 3.7 pattern_rectangular ---------------------------------------------------------------


def test_rectangular_is_sent_with_defaults() -> None:
    data = result_of("RectangularPatternFeature")
    bridge = FakeBridge({"pattern.rectangular": data})
    result, _ = call(
        "pattern_rectangular",
        {"entities": ["b1"], "direction1": "origin:X", "distance1": 30},
        bridge,
    )
    assert bridge.last.method == "pattern.rectangular"
    assert bridge.last.params == {
        "entities": ["b1"],
        "direction1": "origin:X",
        "quantity1": 2,
        "distance1": 30.0,
        "distance_type": "spacing",
        "symmetric1": False,
        "compute": "adjust",
        "units": "mm",
    }
    assert result.is_error is False
    assert json.loads(result_text(result)) == data


def test_rectangular_two_directions() -> None:
    bridge = FakeBridge({"pattern.rectangular": result_of("RectangularPatternFeature")})
    call(
        "pattern_rectangular",
        {
            "entities": ["b1"],
            "direction1": "origin:X",
            "quantity1": 3,
            "distance1": 30,
            "direction2": "origin:Y",
            "quantity2": 2,
            "distance2": "w + 5 mm",
            "symmetric2": True,
            "units": "in",
        },
        bridge,
    )
    params = bridge.last.params
    assert params["direction2"] == "origin:Y"
    assert params["quantity2"] == 2
    assert params["distance2"] == "w + 5 mm"
    assert params["symmetric2"] is True
    assert params["units"] == "in"


def test_rectangular_rules_and_schema() -> None:
    base = {"entities": ["b1"], "direction1": "origin:X", "distance1": 30}
    assert_rules(
        "pattern_rectangular",
        [
            ({**base, "direction2": "origin:Y", "quantity2": 2}, "distance2"),
            ({**base, "quantity2": 3}, "direction2"),
            ({**base, "distance2": 5}, "direction2"),
        ],
    )
    assert_schema_rejects(
        "pattern_rectangular",
        [
            {**base, "entities": []},
            {**base, "distance_type": "pitch"},
            {**base, "compute": "fast"},
            {"entities": ["b1"], "direction1": "origin:X"},
        ],
    )


def test_rectangular_bridge_error_is_returned() -> None:
    error = BridgeError(ErrorCode.INVALID_PARAMS, "mixed", {"kinds": ["feature", "body"]})
    bridge = FakeBridge({"pattern.rectangular": error})
    result, _ = call(
        "pattern_rectangular", {"entities": ["b1", "f1"], "direction1": "e", "distance1": 1}, bridge
    )
    assert result.is_error is True
    assert "kinds" in result_text(result)


def test_rectangular_docstring_and_schema() -> None:
    t = tool("pattern_rectangular")
    assert "one kind" in (t.description or "")
    assert "created_occurrences" in (t.description or "")
    assert set(t.input_schema["required"]) == {"entities", "direction1", "distance1"}


# --- 3.8 pattern_circular ------------------------------------------------------------------


def test_circular_is_sent() -> None:
    bridge = FakeBridge({"pattern.circular": result_of("CircularPatternFeature")})
    result, _ = call(
        "pattern_circular", {"entities": ["f1"], "axis": "origin:Z", "quantity": 6}, bridge
    )
    assert result.is_error is False
    assert bridge.last.method == "pattern.circular"
    assert bridge.last.params == {
        "entities": ["f1"],
        "axis": "origin:Z",
        "quantity": 6,
        "total_angle": 360.0,
        "symmetric": False,
        "compute": "adjust",
    }
    assert_schema_rejects(
        "pattern_circular",
        [{"entities": ["f1"], "axis": "origin:Z"}, {"entities": ["f1"], "quantity": 6}],
    )
    assert "units" not in tool("pattern_circular").input_schema["properties"]


# --- 3.9 pattern_path ----------------------------------------------------------------------


def test_path_is_sent() -> None:
    bridge = FakeBridge({"pattern.path": result_of("PathPatternFeature")})
    result, _ = call(
        "pattern_path",
        {
            "entities": ["b1"],
            "path": ["c1"],
            "quantity": 4,
            "distance": 100,
            "orientation_along_path": True,
            "start": 0.5,
        },
        bridge,
    )
    assert result.is_error is False
    assert bridge.last.method == "pattern.path"
    assert bridge.last.params == {
        "entities": ["b1"],
        "path": ["c1"],
        "path_chain": True,
        "quantity": 4,
        "distance": 100.0,
        "distance_type": "extent",
        "start": 0.5,
        "flip": False,
        "orientation_along_path": True,
        "symmetric": False,
        "compute": "adjust",
        "units": "mm",
    }
    base = {"entities": ["b1"], "path": ["c1"], "quantity": 4, "distance": 100}
    assert_schema_rejects(
        "pattern_path", [{**base, "start": 1.5}, {**base, "path": []}, {**base, "start": -0.1}]
    )


# --- 3.10 pattern_mirror -------------------------------------------------------------------


def test_mirror_is_sent() -> None:
    bridge = FakeBridge({"pattern.mirror": result_of("MirrorFeature")})
    result, _ = call(
        "pattern_mirror", {"entities": ["b1"], "plane": "origin:YZ", "combine": True}, bridge
    )
    assert result.is_error is False
    assert bridge.last.method == "pattern.mirror"
    assert bridge.last.params == {
        "entities": ["b1"],
        "plane": "origin:YZ",
        "combine": True,
        "compute": "adjust",
    }
    assert_schema_rejects("pattern_mirror", [{"entities": ["b1"]}, {"plane": "origin:XY"}])
    assert "Bodies only" in json.dumps(tool("pattern_mirror").input_schema)

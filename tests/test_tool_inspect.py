"""``inspect_*`` tools on ``FakeBridge`` (c03 5.1-5.5, D12)."""

import asyncio
import json
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from fusion_mcp.server.app import create_server
from fusion_mcp.server.testing import FakeBridge, call_tool, result_text


class Rejected:
    """Arguments refused by the tool's input schema (``ToolError``) before the bridge."""

    is_error = True

    def __init__(self, message: str) -> None:
        self.message = message


def call(name: str, arguments: dict[str, Any], response: Any = None) -> tuple[Any, FakeBridge]:
    bridge = FakeBridge()
    method = name.replace("inspect_", "inspect.", 1)
    bridge.respond(method, response if response is not None else {"ok": name})
    try:
        result = asyncio.run(call_tool(create_server(bridge), name, arguments))
    except ToolError as exc:
        return Rejected(str(exc)), bridge
    return result, bridge


def tool(name: str) -> Any:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    return next(t for t in tools if t.name == name)


# --- 5.1 inspect_tree --------------------------------------------------------------------------


def test_tree_sends_only_given_params_and_returns_result_as_is() -> None:
    answer = {
        "design_type": "parametric",
        "root": {"ref": "r"},
        "item_count": 1,
        "truncated": False,
    }
    result, bridge = call("inspect_tree", {"depth": 1, "include": ["bodies"]}, answer)
    assert result.is_error is False
    assert bridge.last.method == "inspect.tree"
    assert bridge.last.params == {"depth": 1, "include": ["bodies"], "max_items": 500}
    assert json.loads(result_text(result)) == answer
    assert result.structured_content == answer


def test_tree_defaults_and_docstring() -> None:
    _, bridge = call("inspect_tree", {})
    assert bridge.last.params == {"max_items": 500}
    description = tool("inspect_tree").description or ""
    assert "origin:XY" in description
    assert "Example" in description


def test_tree_rejects_unknown_include() -> None:
    result, bridge = call("inspect_tree", {"include": ["meshes"]})
    assert result.is_error is True
    assert bridge.calls == []


def test_bridge_error_is_a_tool_error() -> None:
    error = BridgeError(ErrorCode.NO_ACTIVE_DESIGN, "no active Fusion design")
    result, _ = call("inspect_tree", {}, error)
    assert result.is_error is True
    assert result_text(result).startswith("NO_ACTIVE_DESIGN")


# --- 5.2 inspect_describe ----------------------------------------------------------------------


def test_describe_default_detail_full() -> None:
    _, bridge = call("inspect_describe", {"refs": ["a", "origin:xy"]})
    assert bridge.last.method == "inspect.describe"
    assert bridge.last.params == {"refs": ["a", "origin:xy"], "detail": "full"}


def test_describe_brief_and_validation() -> None:
    _, bridge = call("inspect_describe", {"refs": ["a"], "detail": "brief"})
    assert bridge.last.params["detail"] == "brief"
    bad_arguments: list[dict[str, Any]] = [{"refs": []}, {"refs": ["a"], "detail": "all"}, {}]
    for bad in bad_arguments:
        result, bridge = call("inspect_describe", bad)
        assert result.is_error is True
        assert bridge.calls == []


# --- 5.3 inspect_bounding_box ------------------------------------------------------------------


def test_bounding_box_with_and_without_refs() -> None:
    _, bridge = call("inspect_bounding_box", {})
    assert bridge.last.method == "inspect.bounding_box"
    assert bridge.last.params == {}
    _, bridge = call("inspect_bounding_box", {"refs": ["b1", "b2"]})
    assert bridge.last.params == {"refs": ["b1", "b2"]}


# --- 5.4 inspect_physical_properties ----------------------------------------------------------


def test_physical_properties_default_accuracy() -> None:
    _, bridge = call("inspect_physical_properties", {})
    assert bridge.last.method == "inspect.physical_properties"
    assert bridge.last.params == {"accuracy": "high"}
    _, bridge = call("inspect_physical_properties", {"refs": ["b"], "accuracy": "low"})
    assert bridge.last.params == {"refs": ["b"], "accuracy": "low"}


def test_physical_properties_invalid_accuracy_rejected_by_schema() -> None:
    schema = tool("inspect_physical_properties").input_schema
    assert "very_high" in json.dumps(schema["properties"]["accuracy"])
    result, bridge = call("inspect_physical_properties", {"accuracy": "max"})
    assert result.is_error is True
    assert bridge.calls == []


# --- 5.5 inspect_query -------------------------------------------------------------------------


def test_query_only_given_filters_reach_the_bridge() -> None:
    _, bridge = call("inspect_query", {"kind": "face", "sort_by": "area", "limit": 1})
    assert bridge.last.method == "inspect.query"
    assert bridge.last.params == {"kind": "face", "sort_by": "area", "order": "desc", "limit": 1}


def test_query_tolerance_only_with_its_filter() -> None:
    _, bridge = call(
        "inspect_query",
        {"kind": "face", "surface_type": "plane", "normal": [0, 0, 1], "normal_tolerance_deg": 5},
    )
    assert bridge.last.params == {
        "kind": "face",
        "surface_type": "plane",
        "normal": [0.0, 0.0, 1.0],
        "normal_tolerance_deg": 5.0,
        "order": "desc",
        "limit": 50,
    }
    _, bridge = call("inspect_query", {"kind": "edge", "direction": [0, 0, 1], "length_min_mm": 25})
    assert bridge.last.params["direction_tolerance_deg"] == 1.0
    assert "normal_tolerance_deg" not in bridge.last.params
    assert bridge.last.params["length_min_mm"] == 25


@pytest.mark.parametrize(
    "arguments",
    [{}, {"kind": "solid"}, {"kind": "face", "normal": [0, 1]}, {"kind": "face", "limit": 0}],
)
def test_query_schema_validation(arguments: dict[str, Any]) -> None:
    result, bridge = call("inspect_query", arguments)
    assert result.is_error is True
    assert bridge.calls == []


def test_query_docstring_examples() -> None:
    description = tool("inspect_query").description or ""
    for needle in ("largest face", "facing +Z", "longer than", 'sort_by="area"'):
        assert needle in description

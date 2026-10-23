"""``document_*`` tools on ``FakeBridge`` (c04 tasks 5.1-5.3)."""

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


def call(name: str, arguments: dict[str, Any], bridge: FakeBridge | None = None) -> Any:
    bridge = bridge or FakeBridge()
    try:
        result = asyncio.run(call_tool(create_server(bridge), name, arguments))
    except ToolError as exc:
        return Rejected(str(exc)), bridge
    return result, bridge


def tool_description(name: str) -> str:
    tools = asyncio.run(create_server(FakeBridge()).list_tools())
    return next(t.description or "" for t in tools if t.name == name)


INFO = {
    "ref": "document:file:urn:1",
    "type": "Document",
    "name": "Bracket",
    "is_active": True,
    "is_saved": True,
    "is_modified": False,
    "file_id": "urn:1",
    "version": 3,
    "design_type": "parametric",
    "units": "mm",
}


# --- 5.1 list / activate -----------------------------------------------------------------------


def test_document_list() -> None:
    bridge = FakeBridge({"document.list": {"documents": [INFO]}})
    result, _ = call("document_list", {}, bridge)
    assert result.is_error is False
    assert bridge.last.method == "document.list"
    assert bridge.last.params == {}
    assert json.loads(result_text(result)) == {"documents": [INFO]}


def test_document_activate() -> None:
    bridge = FakeBridge({"document.activate": INFO})
    result, _ = call("document_activate", {"document": "document:file:urn:1"}, bridge)
    assert bridge.last.method == "document.activate"
    assert bridge.last.params == {"document": "document:file:urn:1"}
    assert json.loads(result_text(result))["is_active"] is True


def test_document_activate_errors() -> None:
    result, bridge = call("document_activate", {})
    assert result.is_error is True and bridge.calls == []
    error = BridgeError(
        ErrorCode.INVALID_PARAMS,
        "ambiguous",
        {"candidates": ["document:Untitled", "document:Untitled"]},
    )
    bridge = FakeBridge({"document.activate": error})
    result, _ = call("document_activate", {"document": "document:Untitled"}, bridge)
    assert result.is_error is True
    assert result_text(result).startswith("INVALID_PARAMS")
    assert "candidates" in result_text(result)


# --- 5.2 new / open ----------------------------------------------------------------------------


def test_document_new_defaults() -> None:
    bridge = FakeBridge({"document.new": {**INFO, "is_saved": False}})
    result, _ = call("document_new", {}, bridge)
    assert result.is_error is False
    assert bridge.last.method == "document.new"
    assert bridge.last.params == {"design_type": "parametric", "units": "mm"}


def test_document_new_direct_in_inches() -> None:
    bridge = FakeBridge()
    call("document_new", {"design_type": "direct", "units": "in"}, bridge)
    assert bridge.last.params == {"design_type": "direct", "units": "in"}


@pytest.mark.parametrize("args", [{"design_type": "sheet"}, {"units": "yd"}])
def test_document_new_rejects(args: dict[str, Any]) -> None:
    result, bridge = call("document_new", args)
    assert result.is_error is True and bridge.calls == []


def test_document_open_one_source_each() -> None:
    bridge = FakeBridge({"document.open": {**INFO, "already_open": False}})
    call("document_open", {"file_id": "urn:1"}, bridge)
    assert bridge.last.method == "document.open"
    assert bridge.last.params == {"file_id": "urn:1"}
    call("document_open", {"local_path": "C:/parts/a.f3d"}, bridge)
    assert bridge.last.params == {"local_path": "C:/parts/a.f3d"}


def test_document_open_both_sources_reach_addin_check() -> None:
    error = BridgeError(ErrorCode.INVALID_PARAMS, "pass exactly one of 'file_id' or 'local_path'")
    bridge = FakeBridge({"document.open": error})
    result, _ = call("document_open", {"file_id": "urn:1", "local_path": "a.f3d"}, bridge)
    assert bridge.last.params == {"file_id": "urn:1", "local_path": "a.f3d"}
    assert result.is_error is True
    assert "exactly one" in result_text(result)


# --- 5.3 save / close --------------------------------------------------------------------------


def test_document_save_first_and_later() -> None:
    bridge = FakeBridge({"document.save": {**INFO, "upload": "pending"}})
    result, _ = call("document_save", {"name": "Bracket", "folder_path": "Parts"}, bridge)
    assert bridge.last.method == "document.save"
    assert bridge.last.params == {"description": "", "name": "Bracket", "folder_path": "Parts"}
    assert json.loads(result_text(result))["upload"] == "pending"
    call("document_save", {"document": "document:file:urn:1", "description": "v2"}, bridge)
    assert bridge.last.params == {"document": "document:file:urn:1", "description": "v2"}


def test_document_close_flags() -> None:
    answer = {
        "closed": {"ref": "document:Draft", "type": "Document", "name": "Draft"},
        "active": None,
    }
    bridge = FakeBridge({"document.close": answer})
    result, _ = call("document_close", {"discard_changes": True}, bridge)
    assert bridge.last.method == "document.close"
    assert bridge.last.params == {"save_changes": False, "discard_changes": True}
    assert json.loads(result_text(result)) == answer
    call("document_close", {"document": "document:Draft"}, bridge)
    assert bridge.last.params == {
        "document": "document:Draft",
        "save_changes": False,
        "discard_changes": False,
    }


def test_document_close_guard_error_is_readable() -> None:
    error = BridgeError(
        ErrorCode.INVALID_PARAMS,
        "unsaved changes: pass save_changes=true or discard_changes=true",
    )
    result, _ = call("document_close", {}, FakeBridge({"document.close": error}))
    assert result.is_error is True
    assert "discard_changes=true" in result_text(result)


@pytest.mark.parametrize("name", ["document_save", "document_close"])
def test_save_close_warn_to_call_only_on_request(name: str) -> None:
    action = "save" if name == "document_save" else "close"
    assert tool_description(name).startswith(
        f"Call ONLY when the user explicitly asked to {action} the document."
    )

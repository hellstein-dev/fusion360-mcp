"""Smoke tests of the sheet metal tools on a live Fusion 360 (``pytest -m fusion``, c13 6.1-6.2).

``test_bracket_flanges_bends``: ``sheet_metal_capabilities``; a 2 mm rule
(``sheet_metal_create_rule``) made active (``sheet_metal_assign_rule``; Fusion may refuse it on
a component without sheet metal data, design R3 - then the rule goes to the first flange); an
open L sketch (c05) becomes a contour flange, an edge flange is added on an outer edge of the
leg (c03 ``inspect_query``), the bends are listed, unfolded and refolded.

``test_flat_pattern_export_and_convert``: the same bracket gets a flat pattern from a planar
face (``bend_count >= 1``), the next call (``inspect_tree``) still sees the design (design R4),
the flat pattern is exported to DXF in a temporary folder (non-empty file) and deleted; a 2 mm
plate (c07 extrude) is converted with ``sheet_metal_convert``.

A step whose capability is ``false`` is skipped with the reason (design R1); a step whose
capability is ``true`` must work. After every feature the timeline has no feature in error.
Every document is closed without saving. Skipped when the bridge is not reachable.
"""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult

from fusion_mcp.server.app import create_server
from fusion_mcp.server.bridge import BridgeClient
from fusion_mcp.server.testing import result_text

pytestmark = pytest.mark.fusion

NEW_DOCUMENT = """
doc = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
design = adsk.fusion.Design.cast(app.activeProduct)
design.designType = adsk.fusion.DesignTypes.ParametricDesignType
result = {"document": doc.name}
"""
CLOSE = """
for i in range(app.documents.count):
    d = app.documents.item(i)
    if d.name == params["document"]:
        d.close(False)
        break
"""
TOLERANCE = 1e-3  # mm
RULE = "MCP Steel 2mm"


def call_raw(name: str, arguments: dict[str, Any]) -> CallToolResult:
    async def go() -> CallToolResult:
        bridge = BridgeClient()
        try:
            result = await create_server(bridge).call_tool(name, arguments)
        finally:
            await bridge.close()
        assert isinstance(result, CallToolResult)
        return result

    return asyncio.run(go())


def call(name: str, arguments: dict[str, Any]) -> Any:
    result = call_raw(name, arguments)
    text = result_text(result)
    assert not result.is_error, text
    return json.loads(text)


def token(value: Any) -> str:
    while isinstance(value, dict):
        value = value["ref"]
    assert isinstance(value, str)
    return value


@pytest.fixture(scope="module")
def bridge() -> None:
    probe = call_raw("fusion_status", {})
    if probe.is_error and result_text(probe).startswith("NOT_CONNECTED"):
        pytest.skip(f"Fusion bridge not reachable: {result_text(probe).splitlines()[0]}")


@pytest.fixture
def document(bridge: None) -> Iterator[dict[str, Any]]:
    built = call("execute_python", {"code": NEW_DOCUMENT, "timeout_s": 60})["result"]
    try:
        yield built
    finally:
        call("execute_python", {"code": CLOSE, "params": {"document": built["document"]}})


# --- helpers ---------------------------------------------------------------------------------


def capabilities() -> dict[str, Any]:
    caps: dict[str, Any] = call("sheet_metal_capabilities", {})
    assert caps["sketch_bend"] is False
    return caps


def need(caps: dict[str, Any], *names: str) -> None:
    missing = [n for n in names if not caps.get(n)]
    if missing:
        pytest.skip(
            f"sheet metal API not available in Fusion {caps.get('fusion_version')}: {missing}"
        )


def timeline_ok() -> None:
    health = call("timeline_health", {})
    assert health["ok"] is True, health


def step(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = call(name, arguments)
    assert out["health"]["state"] == "ok", out["health"]
    timeline_ok()
    return out


def draw(plane: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    sketch = token(call("sketch_create", {"plane": plane})["sketch"])
    drawn: dict[str, Any] = call("sketch_draw", {"sketch": sketch, "items": items})
    return drawn


def only(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    assert len(bodies) == 1, bodies
    return bodies[0]


def query(kind: str, body: str, **filters: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = call("inspect_query", {"kind": kind, "scope": [body], **filters})[
        "items"
    ]
    return items


def planar_face(body: str) -> str:
    """A planar face of the horizontal leg of the bracket (normal along -Y or +Y)."""
    for normal in ([0, -1, 0], [0, 1, 0]):
        found = query("face", body, normal=normal)
        if found:
            return token(found[0])
    raise AssertionError("no planar face normal to Y")


def rule_ready() -> str | None:
    """Create the 2 mm rule and try to make it active; the rule name to pass to the first
    flange when Fusion refuses the assignment (design R3), else ``None``."""
    created = call("sheet_metal_create_rule", {"name": RULE, "thickness": 2, "k_factor": 0.44})
    assert created["rule"]["thickness"]["mm"] == pytest.approx(2.0)
    assigned = call_raw("sheet_metal_assign_rule", {"rule": RULE})
    if assigned.is_error:
        assert result_text(assigned).startswith("FUSION_API_ERROR"), result_text(assigned)
        return RULE
    assert json.loads(result_text(assigned))["rule"]["name"] == RULE
    return None


def bracket(rule: str | None) -> str:
    """Open L sketch on XY (40 mm along X, 30 mm along Y) -> contour flange 50 mm."""
    drawn = draw(
        "origin:XY",
        [
            {"kind": "line", "id": "a", "start": [0, 0], "end": [40, 0]},
            # Starts on a's end point: two lines that only meet at equal coordinates are not
            # connected, and the contour flange of such an open profile loses its profile
            # reference (live Fusion warning "Tool body creation failed", c15 6.1).
            {"kind": "line", "id": "b", "start": "a.end", "end": [40, 30]},
        ],
    )
    arguments: dict[str, Any] = {
        "profiles": [token(drawn["refs"]["a"]), token(drawn["refs"]["b"])],
        "distance": 50,
    }
    if rule is not None:
        arguments["rule"] = rule
    out = step("sheet_metal_contour_flange", arguments)
    body = only(out["created_bodies"])
    assert body["is_sheet_metal"] is True
    assert out["rule"] == RULE
    return token(body)


# --- tests -----------------------------------------------------------------------------------


def test_bracket_flanges_bends(document: dict[str, Any]) -> None:
    caps = capabilities()
    need(caps, "rules", "contour_flange")
    body = bracket(rule_ready())

    if caps.get("edge_flange"):
        leg_end = [
            e
            for e in query("edge", body)
            if abs(e["mid_mm"][1] - 30) < 3 and abs(e["mid_mm"][2] - 25) < 1
        ]
        assert leg_end, "no outer edge at the end of the leg"
        out = step("sheet_metal_edge_flange", {"edges": [token(leg_end[0])], "height": 20})
        changed = out["modified_bodies"] or out["created_bodies"]
        body = token(changed[0])

    need(caps, "list_bends")
    bends = call("sheet_metal_list_bends", {"body": body})
    assert bends["count"] >= 1
    assert bends["bends"][0]["radius_mm"] > 0

    need(caps, "unfold", "refold")
    unfold = step("sheet_metal_unfold", {"stationary_face": planar_face(body)})
    refold = step("sheet_metal_refold", {"unfold": token(unfold["feature"])})
    assert refold["feature"]["type"] == "RefoldFeature"


def test_flat_pattern_export_and_convert(document: dict[str, Any], tmp_path: Path) -> None:
    caps = capabilities()
    need(caps, "rules", "contour_flange", "flat_pattern")
    body = bracket(rule_ready())

    flat = call("sheet_metal_create_flat_pattern", {"stationary_face": planar_face(body)})
    assert flat["exists"] is True and flat["bend_count"] >= 1
    got = call("sheet_metal_get_flat_pattern", {})
    assert got["bend_count"] == flat["bend_count"]
    tree = call("inspect_tree", {})
    assert tree, "inspect_tree after the flat pattern must see the design"

    if caps.get("flat_pattern_dxf"):
        path = str(tmp_path / "bracket.dxf")
        exported = call("sheet_metal_export_flat_pattern", {"path": path})
        assert exported["files"][0]["size_bytes"] > 0
        assert Path(path).stat().st_size > 0

    assert call("sheet_metal_delete_flat_pattern", {})["deleted"] is True
    assert call("sheet_metal_get_flat_pattern", {})["exists"] is False

    need(caps, "convert")
    drawn = draw(
        "origin:XY",
        [{"kind": "rectangle", "mode": "two_point", "corner1": [100, 0], "corner2": [140, 20]}],
    )
    plate_out = step(
        "feature_extrude",
        {
            "profiles": [token(only(drawn["profiles"])["ref"])],
            "side1": {"type": "distance", "distance": 2},
        },
    )
    plate = token(only(plate_out["created_bodies"]))
    top = token(only(query("face", plate, normal=[0, 0, 1])))
    converted = call(
        "sheet_metal_convert",
        {"body": plate, "base_face": top, "rule": RULE, "rule_source": "design"},
    )
    assert converted["is_sheet_metal"] is True
    timeline_ok()

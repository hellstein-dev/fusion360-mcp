"""``viewport.screenshot`` with a fake viewport (c03 4.3, D11)."""

import base64
import os
from typing import Any

import pytest

import fusion_addin
from fusion_addin.core.context import Context
from fusion_addin.core.registry import REGISTRY
from fusion_addin.handlers import viewport
from fusion_mcp.protocol.errors import BridgeError, ErrorCode
from tests.fakes.brep import PNG_SIGNATURE, FakeViewport
from tests.fakes.design import FakeApp

adsk = fusion_addin.adsk


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def do_events() -> bool:
        calls.append("doEvents")
        return True

    monkeypatch.setattr(adsk, "doEvents", do_events)
    return calls


def shoot(params: dict[str, Any], vp: Any = None) -> tuple[dict[str, Any], FakeViewport]:
    vp = vp if vp is not None else FakeViewport()
    app = FakeApp()
    app.activeViewport = vp  # type: ignore[attr-defined]
    return viewport.viewport_screenshot(Context(app, params), params), vp


def test_registered_read_only() -> None:
    spec = REGISTRY.get("viewport.screenshot")
    assert spec is not None
    assert spec.mutating is False


def test_defaults(events: list[str]) -> None:
    out, vp = shoot({})
    assert base64.b64decode(out["image_base64"]).startswith(PNG_SIGNATURE)
    assert out["mime"] == "image/png"
    assert (out["width"], out["height"], out["orientation"]) == (1024, 768, "iso_top_right")
    camera = vp.camera
    assert camera.viewOrientation == adsk.core.ViewOrientations.IsoTopRightViewOrientation
    assert camera.isFitView is True
    assert camera.isSmoothTransition is False
    assert vp.camera_sets == 1
    assert vp.refreshes == 1
    assert events == ["doEvents"]
    path, width, height = vp.saved[0]
    assert (width, height) == (1024, 768)
    assert path.endswith(".png")
    assert not os.path.exists(path)


@pytest.mark.parametrize(
    ("name", "enum"),
    [
        ("front", "FrontViewOrientation"),
        ("back", "BackViewOrientation"),
        ("top", "TopViewOrientation"),
        ("bottom", "BottomViewOrientation"),
        ("left", "LeftViewOrientation"),
        ("right", "RightViewOrientation"),
        ("iso_top_left", "IsoTopLeftViewOrientation"),
        ("iso_bottom_right", "IsoBottomRightViewOrientation"),
        ("iso_bottom_left", "IsoBottomLeftViewOrientation"),
    ],
)
def test_orientation_mapping(events: list[str], name: str, enum: str) -> None:
    _, vp = shoot({"orientation": name})
    assert vp.camera.viewOrientation == getattr(adsk.core.ViewOrientations, enum)


def test_current_orientation_keeps_camera_and_no_fit(events: list[str]) -> None:
    vp = FakeViewport()
    vp._camera.viewOrientation = 3
    out, _ = shoot({"orientation": "current", "fit": False}, vp)
    assert out["orientation"] == "current"
    assert vp.camera.viewOrientation == 3
    assert vp.camera.isFitView is False


@pytest.mark.parametrize(
    ("style", "enum"),
    [
        ("shaded", "ShadedVisualStyle"),
        ("shaded_edges", "ShadedWithVisibleEdgesOnlyVisualStyle"),
        ("wireframe", "WireframeVisualStyle"),
    ],
)
def test_visual_style(events: list[str], style: str, enum: str) -> None:
    _, vp = shoot({"visual_style": style})
    assert vp.visualStyle == getattr(adsk.core.VisualStyles, enum)


def test_size_bounds(events: list[str]) -> None:
    out, vp = shoot({"width": 64, "height": 2048})
    assert vp.saved[0][1:] == (64, 2048)
    assert (out["width"], out["height"]) == (64, 2048)


@pytest.mark.parametrize(
    "params",
    [
        {"width": 63},
        {"height": 2049},
        {"width": 100.5},
        {"width": True},
        {"orientation": "isometric"},
        {"visual_style": "toon"},
        {"fit": "yes"},
    ],
)
def test_invalid(events: list[str], params: dict[str, Any]) -> None:
    with pytest.raises(BridgeError) as info:
        shoot(params)
    assert info.value.code is ErrorCode.INVALID_PARAMS


def test_no_active_viewport() -> None:
    app = FakeApp()
    app.activeViewport = None  # type: ignore[attr-defined]
    with pytest.raises(BridgeError) as info:
        viewport.viewport_screenshot(Context(app, {}), {})
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert info.value.message == "no active viewport"


def test_save_returns_false_and_file_is_removed(events: list[str]) -> None:
    vp = FakeViewport()
    vp.save_result = False
    with pytest.raises(BridgeError) as info:
        shoot({}, vp)
    assert info.value.code is ErrorCode.FUSION_API_ERROR
    assert not os.path.exists(vp.saved[0][0])


def test_save_raises_and_file_is_removed(events: list[str]) -> None:
    vp = FakeViewport()
    vp.save_error = "viewport busy"
    with pytest.raises(RuntimeError):
        shoot({}, vp)
    assert not os.path.exists(vp.saved[0][0])

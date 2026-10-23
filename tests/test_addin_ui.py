"""``fusion_addin.core.ui``: Start/Stop button and status label (task 3.2)."""

from typing import Any

import pytest

from fusion_addin.core.ui import COMMAND_ID, PANEL_ID, BridgeUI, status_label
from tests.fakes.fusion_app import FakeFusionUI


class FakeBridge:
    def __init__(self) -> None:
        self.running = False
        self.port: int | None = None
        self.fail: str | None = None

    def start(self) -> int:
        if self.fail:
            raise RuntimeError(self.fail)
        self.running, self.port = True, 7432
        return 7432

    def stop(self) -> None:
        self.running, self.port = False, None


@pytest.fixture
def setup() -> tuple[FakeFusionUI, FakeBridge, BridgeUI]:
    ui, bridge = FakeFusionUI(), FakeBridge()
    owner = BridgeUI(ui, bridge)
    owner.install()
    return ui, bridge, owner


def definition(ui: FakeFusionUI) -> Any:
    found = ui.commandDefinitions.itemById(COMMAND_ID)
    assert found is not None
    return found


def test_labels() -> None:
    bridge = FakeBridge()
    assert status_label(bridge) == "FusionMCP: Stopped"
    bridge.start()
    assert status_label(bridge) == "FusionMCP: Running :7432"


def test_install_adds_button_to_addins_panel(setup) -> None:
    ui, _bridge, _owner = setup
    assert definition(ui).name == "FusionMCP: Stopped"
    panel = ui.allToolbarPanels.itemById(PANEL_ID)
    assert panel is not None
    assert panel.controls.itemById(COMMAND_ID) is not None


def test_click_toggles_and_relabels(setup) -> None:
    ui, bridge, _owner = setup
    definition(ui).click()
    assert bridge.running
    assert definition(ui).name == "FusionMCP: Running :7432"
    definition(ui).click()
    assert not bridge.running
    assert definition(ui).name == "FusionMCP: Stopped"


def test_toggle_failure_shows_message(setup) -> None:
    ui, bridge, owner = setup
    bridge.fail = "port busy"
    owner.toggle()
    assert "port busy" in ui.messages[0]
    assert definition(ui).name == "FusionMCP: Stopped"


def test_install_reuses_existing_definition_and_uninstall_cleans(setup) -> None:
    ui, bridge, owner = setup
    owner.uninstall()
    assert ui.commandDefinitions.itemById(COMMAND_ID) is None
    panel = ui.allToolbarPanels.itemById(PANEL_ID)
    assert panel is not None
    assert panel.controls.itemById(COMMAND_ID) is None
    ui.commandDefinitions.addButtonDefinition(COMMAND_ID, "old", "tip")
    again = BridgeUI(ui, bridge)
    again.install()
    assert definition(ui).name == "FusionMCP: Stopped"


def test_missing_panel_is_tolerated() -> None:
    ui = FakeFusionUI(panels=[])
    owner = BridgeUI(ui, FakeBridge())
    owner.install()
    assert owner.control is None
    assert definition(ui).name == "FusionMCP: Stopped"

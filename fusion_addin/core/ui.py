"""Start/Stop button in Utilities -> Add-ins with the bridge status in its name (design D12)."""

from __future__ import annotations

import contextlib
import traceback
from typing import Any

import adsk.core

COMMAND_ID = "FusionMCP_StartStop"
# Design workspace, Utilities tab -> Add-ins panel.
PANEL_ID = "SolidScriptsAddinsPanel"
TOOLTIP = "Start or stop the FusionMCP bridge for the fusion-mcp MCP server"


def status_label(bridge: Any) -> str:
    if bridge.running:
        return f"FusionMCP: Running :{bridge.port}"
    return "FusionMCP: Stopped"


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, owner: BridgeUI) -> None:
        super().__init__()
        self._owner = owner

    def notify(self, args: Any) -> None:
        self._owner.toggle()


class _CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self, owner: BridgeUI) -> None:
        super().__init__()
        self._owner = owner

    def notify(self, args: Any) -> None:
        handler = _ExecuteHandler(self._owner)
        args.command.execute.add(handler)
        # Fusion keeps only weak references: hold the handler.
        self._owner.handlers.append(handler)


class BridgeUI:
    def __init__(self, ui: Any, bridge: Any) -> None:
        self.ui = ui
        self.bridge = bridge
        self.command_definition: Any = None
        self.control: Any = None
        self.handlers: list[Any] = []

    def install(self) -> None:
        definitions = self.ui.commandDefinitions
        definition = definitions.itemById(COMMAND_ID)
        if definition is None:
            definition = definitions.addButtonDefinition(
                COMMAND_ID, status_label(self.bridge), TOOLTIP
            )
        created = _CreatedHandler(self)
        definition.commandCreated.add(created)
        self.handlers.append(created)
        self.command_definition = definition
        panel = self.ui.allToolbarPanels.itemById(PANEL_ID)
        if panel is not None:
            control = panel.controls.itemById(COMMAND_ID)
            self.control = control or panel.controls.addCommand(definition)
        self.refresh()

    def refresh(self) -> None:
        if self.command_definition is not None:
            self.command_definition.name = status_label(self.bridge)

    def toggle(self) -> None:
        try:
            if self.bridge.running:
                self.bridge.stop()
            else:
                self.bridge.start()
        except Exception:
            with contextlib.suppress(Exception):
                self.ui.messageBox("FusionMCP:\n" + traceback.format_exc())
        self.refresh()

    def uninstall(self) -> None:
        if self.control is not None:
            with contextlib.suppress(Exception):
                self.control.deleteMe()
            self.control = None
        if self.command_definition is not None:
            with contextlib.suppress(Exception):
                self.command_definition.deleteMe()
            self.command_definition = None
        self.handlers.clear()

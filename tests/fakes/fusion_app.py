"""Fake Fusion application with CustomEvents and the toolbar UI, for add-in lifecycle tests.

``fireCustomEvent`` only queues, like Fusion; ``pump()`` delivers queued events to the
registered handlers on the calling thread, standing in for Fusion's main thread.
"""

from __future__ import annotations

import threading
from typing import Any

from .design import FakeApp, FakeUI


class FakeEvent:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def add(self, handler: Any) -> bool:
        self.handlers.append(handler)
        return True

    def remove(self, handler: Any) -> bool:
        self.handlers.remove(handler)
        return True


class FakeEventArgs:
    def __init__(self, additional_info: str = "", command: Any = None) -> None:
        self.additionalInfo = additional_info
        self.command = command


class FakeCommandDefinition:
    def __init__(self, owner: FakeCommandDefinitions, id: str, name: str, tooltip: str) -> None:
        self._owner = owner
        self.id = id
        self.name = name
        self.tooltip = tooltip
        self.commandCreated = FakeEvent()
        self.deleted = False

    def click(self) -> None:
        """What Fusion does when the button is pressed: commandCreated, then execute."""
        command = FakeEntityWithExecute()
        for handler in list(self.commandCreated.handlers):
            handler.notify(FakeEventArgs(command=command))
        for handler in list(command.execute.handlers):
            handler.notify(FakeEventArgs(command=command))

    def deleteMe(self) -> bool:
        self.deleted = True
        self._owner.items.pop(self.id, None)
        return True


class FakeEntityWithExecute:
    def __init__(self) -> None:
        self.execute = FakeEvent()


class FakeCommandDefinitions:
    def __init__(self) -> None:
        self.items: dict[str, FakeCommandDefinition] = {}

    def itemById(self, id: str) -> FakeCommandDefinition | None:
        return self.items.get(id)

    def addButtonDefinition(self, id: str, name: str, tooltip: str) -> FakeCommandDefinition:
        if id in self.items:
            raise RuntimeError(f"command definition {id} already exists")
        definition = FakeCommandDefinition(self, id, name, tooltip)
        self.items[id] = definition
        return definition


class FakeControl:
    def __init__(self, owner: FakeControls, definition: FakeCommandDefinition) -> None:
        self._owner = owner
        self.id = definition.id
        self.commandDefinition = definition

    def deleteMe(self) -> bool:
        self._owner.items.pop(self.id, None)
        return True


class FakeControls:
    def __init__(self) -> None:
        self.items: dict[str, FakeControl] = {}

    def itemById(self, id: str) -> FakeControl | None:
        return self.items.get(id)

    def addCommand(self, definition: FakeCommandDefinition) -> FakeControl:
        control = FakeControl(self, definition)
        self.items[definition.id] = control
        return control


class FakePanel:
    def __init__(self, id: str) -> None:
        self.id = id
        self.controls = FakeControls()


class FakePanels:
    def __init__(self, ids: list[str]) -> None:
        self.items = {i: FakePanel(i) for i in ids}

    def itemById(self, id: str) -> FakePanel | None:
        return self.items.get(id)


class FakeFusionUI(FakeUI):
    def __init__(self, panels: list[str] | None = None) -> None:
        super().__init__()
        self.commandDefinitions = FakeCommandDefinitions()
        self.allToolbarPanels = FakePanels(
            panels if panels is not None else ["SolidScriptsAddinsPanel"]
        )


class FakeFusionApp(FakeApp):
    def __init__(self, design: Any = None, ui: FakeFusionUI | None = None) -> None:
        super().__init__(design)
        self.userInterface = ui if ui is not None else FakeFusionUI()
        self.events: dict[str, FakeEvent] = {}
        self._queue: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self.fail_register = False

    def registerCustomEvent(self, event_id: str) -> FakeEvent:
        if self.fail_register:
            raise RuntimeError("registerCustomEvent failed")
        if event_id in self.events:
            raise RuntimeError(f"custom event {event_id} already registered")
        event = FakeEvent()
        self.events[event_id] = event
        return event

    def unregisterCustomEvent(self, event_id: str) -> bool:
        del self.events[event_id]
        return True

    def fireCustomEvent(self, event_id: str, additional_info: str = "") -> bool:
        with self._lock:
            self._queue.append((event_id, additional_info))
        return True

    def pump(self) -> int:
        """Deliver queued events on the calling ("main") thread; return how many."""
        with self._lock:
            queued, self._queue = self._queue, []
        for event_id, info in queued:
            event = self.events.get(event_id)
            for handler in list(event.handlers if event else []):
                handler.notify(FakeEventArgs(info))
        return len(queued)

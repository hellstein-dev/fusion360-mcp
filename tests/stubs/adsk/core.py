"""Stub of ``adsk.core``: ``Application.get()``, event handler base classes, ``ValueInput``."""

from . import Unavailable


class Application:
    @staticmethod
    def get() -> Unavailable:
        return Unavailable("adsk.core.Application")


class _EventHandler:
    """Base of Fusion's event handler classes; the add-in subclasses them and overrides
    ``notify``."""

    def __init__(self) -> None:
        pass

    def notify(self, args: object) -> None:
        raise NotImplementedError


class CustomEventHandler(_EventHandler):
    pass


class CommandCreatedEventHandler(_EventHandler):
    pass


class CommandEventHandler(_EventHandler):
    pass


class ValueInput:
    """Records how it was built: ``kind`` is ``"real"`` or ``"string"``."""

    def __init__(self, kind: str, value: object) -> None:
        self.kind = kind
        self.value = value

    @staticmethod
    def createByReal(value: float) -> "ValueInput":
        return ValueInput("real", value)

    @staticmethod
    def createByString(value: str) -> "ValueInput":
        return ValueInput("string", value)

"""Stub of ``adsk.core``: enough for ``import adsk.core`` and ``Application.get()``."""

from . import Unavailable


class Application:
    @staticmethod
    def get() -> Unavailable:
        return Unavailable("adsk.core.Application")

"""Stub of ``adsk.fusion``: enough for ``import adsk.fusion`` and ``Design.cast()``."""

from . import Unavailable


class Design:
    @staticmethod
    def cast(obj: object) -> Unavailable:
        return Unavailable("adsk.fusion.Design")

"""Stub of ``adsk.fusion``: ``Design.cast()`` and ``DesignTypes``."""

from . import Unavailable


class DesignTypes:
    # Values match Fusion's enum.
    DirectDesignType = 0
    ParametricDesignType = 1


class Design:
    """Fakes of a design subclass this, so ``cast`` recognises them like Fusion would."""

    @staticmethod
    def cast(obj: object) -> object:
        if isinstance(obj, Design):
            return obj
        if obj is None:
            return Unavailable("adsk.fusion.Design")
        return None

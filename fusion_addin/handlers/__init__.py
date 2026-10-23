"""Handler groups: importing this package registers every ``<group>.<action>`` handler."""

from . import script, status

__all__ = ["script", "status"]

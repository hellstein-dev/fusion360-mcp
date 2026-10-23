"""Handler groups: importing this package registers every ``<group>.<action>`` handler."""

from . import document, inspect, measure, script, status, viewport

__all__ = ["document", "inspect", "measure", "script", "status", "viewport"]

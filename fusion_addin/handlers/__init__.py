"""Handler groups: importing this package registers every ``<group>.<action>`` handler."""

from . import inspect, script, status

__all__ = ["inspect", "script", "status"]

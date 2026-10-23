"""Handler groups: importing this package registers every ``<group>.<action>`` handler."""

from . import design, document, inspect, measure, parameter, script, status, viewport

__all__ = [
    "design",
    "document",
    "inspect",
    "measure",
    "parameter",
    "script",
    "status",
    "viewport",
]

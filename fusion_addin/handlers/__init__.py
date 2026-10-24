"""Handler groups: importing this package registers every ``<group>.<action>`` handler."""

from . import (
    construction,
    design,
    document,
    feature_sketch,
    inspect,
    measure,
    parameter,
    script,
    sketch,
    status,
    viewport,
)

__all__ = [
    "construction",
    "design",
    "document",
    "feature_sketch",
    "inspect",
    "measure",
    "parameter",
    "script",
    "sketch",
    "status",
    "viewport",
]

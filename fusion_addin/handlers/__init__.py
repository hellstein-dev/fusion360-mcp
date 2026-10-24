"""Handler groups: importing this package registers every ``<group>.<action>`` handler."""

from . import (
    construction,
    design,
    document,
    feature_hole,
    feature_modify,
    feature_sketch,
    inspect,
    measure,
    parameter,
    pattern,
    script,
    sketch,
    status,
    viewport,
)

__all__ = [
    "construction",
    "design",
    "document",
    "feature_hole",
    "feature_modify",
    "feature_sketch",
    "inspect",
    "measure",
    "parameter",
    "pattern",
    "script",
    "sketch",
    "status",
    "viewport",
]

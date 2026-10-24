"""Vocabularies and limits of the ``form.*`` methods (c14 D1, D2, D5-D7), shared by the MCP
server and the add-in. Stdlib only.

The server writes the tuples out as ``Literal[...]`` types (a test keeps them in sync); the
add-in re-checks raw params with ``fusion_mcp.protocol.features.check_choice``.

``SHAPE_SIZES`` / ``SHAPE_FACES`` are the size (mm, numbers only) and face count parameters
each T-spline primitive takes (D5); a parameter of another shape is ``INVALID_PARAMS``.
"""

from __future__ import annotations

SHAPES = ("box", "plane", "cylinder", "sphere", "torus", "quadball")
ELEMENT_KINDS = ("vertex", "edge", "face")
INSERTION_MODES = ("simple", "exact")
MOVE_KINDS = ("vertex", "face", "body")
INSPECT_PARTS = ("vertices", "edges", "faces")
SYMMETRY_AXES = ("x", "y", "z")

SHAPE_SIZES: dict[str, tuple[str, ...]] = {
    "box": ("x_size", "y_size", "z_size"),
    "plane": ("x_size", "y_size"),
    "cylinder": ("radius", "height"),
    "sphere": ("radius",),
    "torus": ("major_radius", "minor_radius"),
    "quadball": ("radius",),
}
SHAPE_FACES: dict[str, tuple[str, ...]] = {
    "box": ("x_faces", "y_faces", "z_faces"),
    "plane": ("x_faces", "y_faces"),
    "cylinder": ("radial_faces", "length_faces"),
    "sphere": ("longitude_faces", "latitude_faces"),
    "torus": ("major_faces", "minor_faces"),
    "quadball": ("faces",),
}
# Shapes that accept ``symmetry = "circular"`` (+ ``symmetric_faces``).
CIRCULAR_SHAPES = ("cylinder", "sphere", "torus")

ALL_SIZES = tuple(dict.fromkeys(p for params in SHAPE_SIZES.values() for p in params))
ALL_FACES = tuple(dict.fromkeys(p for params in SHAPE_FACES.values() for p in params))

TSM_EXTENSIONS = (".tsm",)
TSM_INPUT_MAX_BYTES = 8 * 1024 * 1024
TSM_OUTPUT_MAX_BYTES = 1024 * 1024

DEFAULT_TOLERANCE_MM = 0.5
AMBIGUITY_MM = 1e-6
NEAREST_LIMIT = 5
DEFAULT_INSPECT_LIMIT = 500

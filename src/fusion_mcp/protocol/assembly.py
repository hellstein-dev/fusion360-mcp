"""Joint vocabularies and request parsing (c10 D4, D5), shared by server and add-in. Stdlib only.

``parse_joint_geometry`` checks a ``JointGeometrySpec`` dict (where a joint or joint origin
snaps) and fills the default key point; ``parse_motion`` checks the ``motion`` dict of a
joint type and fills the default directions. ``DOFS_BY_TYPE`` lists the degrees of freedom
of each joint type; rotational ones are in degrees, the others in mm (``ROTATIONAL_DOFS``).
The add-in maps the names here to ``adsk.fusion`` enums and ``JointMotion`` attributes.
"""

from __future__ import annotations

from typing import Any

from .errors import BridgeError, ErrorCode

JOINT_TYPES = ("rigid", "revolute", "slider", "cylindrical", "pin_slot", "planar", "ball")

# ``adsk.fusion.JointTypes`` member of each joint type (``jointMotion.jointType``).
JOINT_TYPE_ENUMS: dict[str, str] = {
    "rigid": "RigidJointType",
    "revolute": "RevoluteJointType",
    "slider": "SliderJointType",
    "cylindrical": "CylindricalJointType",
    "pin_slot": "PinSlotJointType",
    "planar": "PlanarJointType",
    "ball": "BallJointType",
}

DOFS = ("rotation", "slide", "primary_slide", "secondary_slide", "pitch", "yaw", "roll")
DOFS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "rigid": (),
    "revolute": ("rotation",),
    "slider": ("slide",),
    "cylindrical": ("rotation", "slide"),
    "pin_slot": ("rotation", "slide"),
    "planar": ("primary_slide", "secondary_slide", "rotation"),
    "ball": ("pitch", "yaw", "roll"),
}
ROTATIONAL_DOFS = frozenset({"rotation", "pitch", "yaw", "roll"})

# ``JointMotion`` attribute stem of each dof: ``<stem>Value`` and ``<stem>Limits``.
DOF_ATTRS: dict[str, str] = {
    "rotation": "rotation",
    "slide": "slide",
    "primary_slide": "primarySlide",
    "secondary_slide": "secondarySlide",
    "pitch": "pitch",
    "yaw": "yaw",
    "roll": "roll",
}

GEOMETRY_KINDS = ("point", "planar_face", "nonplanar_face", "curve", "profile", "joint_origin")
KEY_POINTS = ("start", "middle", "end", "center")
# ``adsk.fusion.JointKeyPointTypes`` member of each key point.
KEY_POINT_ENUMS: dict[str, str] = {
    "start": "StartKeyPoint",
    "middle": "MiddleKeyPoint",
    "end": "EndKeyPoint",
    "center": "CenterKeyPoint",
}
# Default key point per kind (``None``: the kind takes no key point) and its optional refs.
DEFAULT_KEY_POINT: dict[str, str | None] = {
    "point": None,
    "planar_face": "center",
    "nonplanar_face": "center",
    "curve": "middle",
    "profile": "center",
    "joint_origin": None,
}
KIND_REFS: dict[str, tuple[str, ...]] = {
    "point": (),
    "planar_face": ("edge",),
    "nonplanar_face": (),
    "curve": (),
    "profile": ("curve",),
    "joint_origin": (),
}

AXIS_DIRECTIONS = ("x", "y", "z")
# ``adsk.fusion.JointDirections`` member of each named direction.
DIRECTION_ENUMS: dict[str, str] = {
    "x": "XAxisJointDirection",
    "y": "YAxisJointDirection",
    "z": "ZAxisJointDirection",
}
CUSTOM_DIRECTION_ENUM = "CustomJointDirection"

# Motion keys per joint type with their defaults (D5). ``primary_slide_direction`` is a ref
# or ``None``; every other key is ``x|y|z`` or a ref used as a custom direction.
MOTION_KEYS: dict[str, dict[str, str | None]] = {
    "rigid": {},
    "revolute": {"axis": "z"},
    "slider": {"direction": "z"},
    "cylindrical": {"axis": "z"},
    "pin_slot": {"axis": "z", "slide_direction": "x"},
    "planar": {"normal": "z", "primary_slide_direction": None},
    "ball": {"pitch_direction": "z", "yaw_direction": "x"},
}
REF_ONLY_MOTION_KEYS = frozenset({"primary_slide_direction"})


def _invalid(message: str, **details: Any) -> BridgeError:
    return BridgeError(ErrorCode.INVALID_PARAMS, message, details)


def check_joint_type(joint_type: Any, param: str = "type") -> str:
    if joint_type not in JOINT_TYPES:
        raise _invalid(
            f"parameter '{param}' must be one of {', '.join(JOINT_TYPES)}, got {joint_type!r}",
            param=param,
            allowed=list(JOINT_TYPES),
        )
    return str(joint_type)


def check_dof(joint_type: str, dof: Any, param: str = "dof") -> str:
    """``dof`` must be a degree of freedom of ``joint_type`` (``DOFS_BY_TYPE``)."""
    allowed = DOFS_BY_TYPE[joint_type]
    if dof not in allowed:
        listed = ", ".join(allowed) if allowed else "none"
        raise _invalid(
            f"a {joint_type} joint has no degree of freedom {dof!r} (available: {listed})",
            param=param,
            allowed=list(allowed),
        )
    return str(dof)


def value_key(dof: str) -> str:
    """Key of a dof value in responses: ``rotation_deg``, ``slide_mm``, ..."""
    return f"{dof}_deg" if dof in ROTATIONAL_DOFS else f"{dof}_mm"


def _ref(value: Any, param: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"parameter '{param}' must be a non-empty ref string", param=param)
    return value


def parse_joint_geometry(spec: Any, param: str = "geometry") -> dict[str, Any]:
    """``{"kind", "entity", "key_point", "edge", "curve"}`` with the default key point
    filled in; refs a kind does not take are ``None``. Errors -> ``INVALID_PARAMS``."""
    if not isinstance(spec, dict):
        raise _invalid(f"parameter '{param}' must be an object with 'kind' and 'entity'")
    kind = spec.get("kind")
    if kind not in GEOMETRY_KINDS:
        raise _invalid(
            f"{param}.kind must be one of {', '.join(GEOMETRY_KINDS)}, got {kind!r}",
            param=f"{param}.kind",
            allowed=list(GEOMETRY_KINDS),
        )
    default_key = DEFAULT_KEY_POINT[kind]
    allowed = {"kind", "entity", *KIND_REFS[kind]} | ({"key_point"} if default_key else set())
    unknown = sorted(k for k, v in spec.items() if k not in allowed and v is not None)
    if unknown:
        raise _invalid(
            f"{param}: {', '.join(unknown)} not allowed for kind '{kind}' "
            f"(allowed: {', '.join(sorted(allowed))})",
            param=param,
            unknown=unknown,
        )
    if spec.get("entity") is None:
        raise _invalid(f"missing required parameter '{param}.entity'", param=f"{param}.entity")
    key_point = spec.get("key_point")
    if key_point is None:
        key_point = default_key
    elif key_point not in KEY_POINTS:
        raise _invalid(
            f"{param}.key_point must be one of {', '.join(KEY_POINTS)}, got {key_point!r}",
            param=f"{param}.key_point",
        )
    result: dict[str, Any] = {
        "kind": kind,
        "entity": _ref(spec["entity"], f"{param}.entity"),
        "key_point": key_point,
        "edge": None,
        "curve": None,
    }
    for name in KIND_REFS[kind]:
        if spec.get(name) is not None:
            result[name] = _ref(spec[name], f"{param}.{name}")
    return result


def parse_motion(joint_type: str, motion: Any, param: str = "motion") -> dict[str, str | None]:
    """``motion`` keys of ``joint_type`` (D5) with defaults; an unknown key -> ``INVALID_PARAMS``.
    Directions are ``x|y|z`` or a ref (custom direction)."""
    check_joint_type(joint_type)
    if motion is None:
        motion = {}
    if not isinstance(motion, dict):
        raise _invalid(f"parameter '{param}' must be an object", param=param)
    keys = MOTION_KEYS[joint_type]
    unknown = sorted(set(motion) - set(keys))
    if unknown:
        listed = ", ".join(keys) if keys else "none"
        raise _invalid(
            f"{param}: {', '.join(unknown)} not valid for a {joint_type} joint (allowed: {listed})",
            param=param,
            unknown=unknown,
            allowed=list(keys),
        )
    result: dict[str, str | None] = {}
    for key, default in keys.items():
        value = motion.get(key)
        if value is None:
            result[key] = default
            continue
        value = _ref(value, f"{param}.{key}")
        if key in REF_ONLY_MOTION_KEYS and value.lower() in AXIS_DIRECTIONS:
            raise _invalid(
                f"{param}.{key} must be a ref (edge, axis or face), not an axis name",
                param=f"{param}.{key}",
            )
        result[key] = value.lower() if value.lower() in AXIS_DIRECTIONS else value
    return result

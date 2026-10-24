"""Fake assembly objects for the c10 tests (design D9), on top of tests/fakes/brep.py.

- ``FakeProxy(native, occurrence)``: an entity seen through an occurrence. It has its own
  token, ``nativeObject`` and ``assemblyContext``; other attributes come from the native
  entity. Native fakes build it with ``createForAssemblyContext`` (``brep.Proxyable``).
- ``FakeOccurrences``: ``Component.occurrences`` with ``addNewComponent`` /
  ``addExistingComponent`` / ``addNewComponentCopy``; new occurrences are registered in
  ``root.allOccurrences`` (nested ones as root-context copies, like Fusion's proxies).
- ``FakeSnapshots``: ``Design.snapshots``.
- ``fake_assembly_design()``: ``fake_part_design`` plus the above, ``activateRootComponent``
  and ``add_component``. Matrices are the stub ``adsk.core.Matrix3D``.
- Joints (c10 4.3): ``FakeJointGeometry`` (the stub ``adsk.fusion.JointGeometry``, whose
  factories record their arguments), ``FakeJoints`` / ``FakeJointInput`` (records the
  ``setAs*JointMotion`` call), ``FakeAsBuiltJoints``, ``FakeJointOrigins``,
  ``FakeJointMotion`` (values and ``FakeJointLimits`` per dof, optionally clamped to the
  limits) and ``FakeRigidGroups``. Created objects add a timeline item in a parametric
  design and remove it again in ``deleteMe``.
"""

from __future__ import annotations

import itertools
from typing import Any

import fusion_addin
from fusion_mcp.protocol.assembly import DOF_ATTRS, DOFS_BY_TYPE, JOINT_TYPE_ENUMS

from .brep import FakeComponent, FakeOccurrence, fake_part_design, register_body
from .design import FakeCollection, FakeDesign

adsk = fusion_addin.adsk
_ids = itertools.count(1)


class FakeProxy:
    """``native`` in the context of ``occurrence`` (a ``FakeOccurrence``)."""

    def __init__(self, native: Any, occurrence: Any) -> None:
        self.nativeObject = native
        self.assemblyContext = occurrence
        self.objectType = native.objectType
        self.entityToken = f"proxy-{next(_ids)}"

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes not set in __init__: delegate to the native entity.
        return getattr(self.__dict__["nativeObject"], name)

    def createForAssemblyContext(self, occurrence: Any) -> FakeProxy:
        return FakeProxy(self.nativeObject, occurrence)


class FakeSnapshots:
    """``Design.snapshots``: ``add()`` captures a pending position change."""

    def __init__(self) -> None:
        self.hasPendingSnapshot = False
        self.added = 0

    def add(self) -> bool:
        self.added += 1
        self.hasPendingSnapshot = False
        return True


class FakeOccurrences(FakeCollection):
    """``Component.occurrences`` of ``owner`` in ``design``; records the matrices it got."""

    def __init__(self, owner: FakeComponent, design: FakeDesign) -> None:
        super().__init__()
        self.owner = owner
        self.design = design
        self.calls: list[tuple[str, Any]] = []
        self.copy_supported = True

    def _insert(self, component: FakeComponent, transform: Any) -> FakeOccurrence:
        root = self.design.rootComponent
        index = 1 + sum(1 for o in self if o.component is component)
        occurrence = FakeOccurrence(component, None, transform=transform.copy(), index=index)
        self.add(occurrence)
        make_activatable(self.design, occurrence)
        if self.owner is root:
            root.allOccurrences.add(occurrence)
        else:
            # Seen from root: one copy per occurrence of the owner component.
            for parent in list(root.allOccurrences):
                if parent.component is self.owner:
                    copy = occurrence.createForAssemblyContext(parent)
                    make_activatable(self.design, copy)
                    parent.childOccurrences.add(copy)
                    root.allOccurrences.add(copy)
        return occurrence

    def addNewComponent(self, transform: Any) -> FakeOccurrence:
        self.calls.append(("addNewComponent", transform))
        component = new_component(self.design, f"Component{len(self.design.allComponents._items)}")
        return self._insert(component, transform)

    def addExistingComponent(self, component: FakeComponent, transform: Any) -> FakeOccurrence:
        self.calls.append(("addExistingComponent", transform))
        return self._insert(component, transform)

    def addNewComponentCopy(self, component: FakeComponent, transform: Any) -> FakeOccurrence:
        if not self.copy_supported:
            raise AttributeError("'Occurrences' object has no attribute 'addNewComponentCopy'")
        self.calls.append(("addNewComponentCopy", transform))
        copy = new_component(self.design, f"{component.name} (1)")
        for body in component.bRepBodies:
            copy.bRepBodies.add(body)
        return self._insert(copy, transform)


def new_component(design: FakeDesign, name: str) -> FakeComponent:
    """A component registered in ``design.allComponents`` with its own ``FakeOccurrences``
    and joint collections (``install_joints``)."""
    component = FakeComponent(name)
    component.occurrences = FakeOccurrences(component, design)
    install_joints(design, component)
    component.sketches.design = design
    design.allComponents.add(component)
    design.add_entity(component)
    for element in component.origin_elements().values():
        design.add_entity(element)
    return component


def add_component(
    design: FakeDesign, name: str, *bodies: Any, parent: Any = None, transform: Any = None
) -> tuple[FakeComponent, FakeOccurrence]:
    """New component ``name`` holding ``bodies``, inserted once under ``parent`` (an
    occurrence; default root). Returns the component and its (root-context) occurrence."""
    import fusion_addin

    component = new_component(design, name)
    for body in bodies:
        component.add_body(body)
        register_body(design, body)
    matrix = transform if transform is not None else fusion_addin.adsk.core.Matrix3D.create()
    root = design.rootComponent
    if parent is None:
        return component, root.occurrences.addExistingComponent(component, matrix)
    parent.component.occurrences.addExistingComponent(component, matrix)
    found = [o for o in root.allOccurrences if o.component is component]
    return component, found[-1]


def fake_assembly_design(*bodies: Any, parametric: bool = True) -> Any:
    """``fake_part_design`` with ``FakeOccurrences`` on root, ``snapshots``,
    ``activeOccurrence`` and ``activateRootComponent``."""
    design = fake_part_design(*bodies, parametric=parametric)
    root = design.rootComponent
    root.occurrences = FakeOccurrences(root, design)
    install_joints(design, root)
    design.add_entity(root)
    design.snapshots = FakeSnapshots()  # type: ignore[attr-defined]
    design.activeOccurrence = None  # type: ignore[attr-defined]
    design.activateRootComponent = lambda: _activate_root(design)  # type: ignore[attr-defined]
    return design


def _activate_root(design: FakeDesign) -> bool:
    design.activeComponent = None
    design.activeOccurrence = None  # type: ignore[attr-defined]
    return True


def make_activatable(design: FakeDesign, occurrence: FakeOccurrence) -> None:
    """``occurrence.activate()`` makes it the design's active occurrence/component."""

    def activate() -> bool:
        occurrence.activations += 1
        design.activeComponent = occurrence.component
        design.activeOccurrence = occurrence  # type: ignore[attr-defined]
        return True

    occurrence.activate = activate  # type: ignore[method-assign]


def _move_body(body: Any, target: FakeComponent) -> None:
    owner = body.parentComponent
    owner.bRepBodies._items.remove(body)
    target.add_body(body)


def make_movable(design: FakeDesign, body: Any, create_returns: str | None = "body") -> None:
    """``BRepBody.createComponent()`` / ``moveToComponent(occurrence)`` on ``body``.

    ``createComponent`` inserts a new component into the body's owner and moves the body
    there; it returns the moved body as a proxy (``"body"``) or the new occurrence
    (``"occurrence"``); ``None`` leaves the method out (old Fusion versions).
    """

    def move_to_component(occurrence: Any) -> Any:
        body.move_calls = getattr(body, "move_calls", 0) + 1
        _move_body(body, occurrence.component)
        return body.createForAssemblyContext(occurrence)

    def create_component() -> Any:
        import fusion_addin

        owner = body.parentComponent
        matrix = fusion_addin.adsk.core.Matrix3D.create()
        occurrence = owner.occurrences.addNewComponent(matrix)
        _move_body(body, occurrence.component)
        if create_returns == "occurrence":
            return occurrence
        return body.createForAssemblyContext(occurrence)

    body.moveToComponent = move_to_component
    if create_returns is not None:
        body.createComponent = create_component


# --- joints (c10 4.3) --------------------------------------------------------------------------

FakeJointGeometry = adsk.fusion.JointGeometry

# ``setAs*JointMotion`` method -> joint type.
MOTION_METHODS = {
    "setAsRigidJointMotion": "rigid",
    "setAsRevoluteJointMotion": "revolute",
    "setAsSliderJointMotion": "slider",
    "setAsCylindricalJointMotion": "cylindrical",
    "setAsPinSlotJointMotion": "pin_slot",
    "setAsPlanarJointMotion": "planar",
    "setAsBallJointMotion": "ball",
}


class FakeJointLimits:
    """``JointLimits``: all limits off, values 0 (internal units)."""

    def __init__(self) -> None:
        self.isMinimumValueEnabled = False
        self.minimumValue = 0.0
        self.isMaximumValueEnabled = False
        self.maximumValue = 0.0
        self.isRestValueEnabled = False
        self.restValue = 0.0


class FakeJointMotion:
    """``*JointMotion`` of ``joint_type``: ``<stem>Value`` and ``<stem>Limits`` per dof.

    With ``clamp`` a value set beyond an enabled limit is clamped (like Fusion's solver);
    ``fail_on[stem] = message`` makes the next set of that value raise once.
    """

    _stems: set[str]

    def __init__(self, joint_type: str, clamp: bool = True) -> None:
        stems = {DOF_ATTRS[d] for d in DOFS_BY_TYPE[joint_type]}
        object.__setattr__(self, "_stems", stems)
        self.jointType = getattr(adsk.fusion.JointTypes, JOINT_TYPE_ENUMS[joint_type])
        self.clamp = clamp
        self.fail_on: dict[str, str] = {}
        for stem in stems:
            object.__setattr__(self, f"{stem}Limits", FakeJointLimits())
            object.__setattr__(self, f"{stem}Value", 0.0)

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes that were never set: a dof the joint type lacks.
        raise AttributeError(f"{type(self).__name__} has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        stem = name[: -len("Value")] if name.endswith("Value") else ""
        if stem in self._stems:
            message = self.fail_on.pop(stem, None)
            if message:
                raise RuntimeError(message)
            if self.clamp:
                limits = getattr(self, f"{stem}Limits")
                if limits.isMaximumValueEnabled:
                    value = min(value, limits.maximumValue)
                if limits.isMinimumValueEnabled:
                    value = max(value, limits.minimumValue)
        object.__setattr__(self, name, value)


class FakeJointInput:
    """``JointInput`` / ``AsBuiltJointInput``: records the motion call and the settings."""

    def __init__(self, *args: Any) -> None:
        self.args = args
        self.motion_call: tuple[str, tuple[Any, ...]] | None = None
        self.offset: Any = None
        self.angle: Any = None
        self.isFlipped = False
        self.motion_result = True

    def __getattr__(self, name: str) -> Any:
        if name not in MOTION_METHODS:
            raise AttributeError(name)

        def set_motion(*args: Any) -> bool:
            self.motion_call = (name, args)
            return bool(self.motion_result)

        return set_motion


def _context_of(entity: Any) -> Any:
    if entity is None:
        return None
    if isinstance(entity, adsk.fusion.JointGeometry):
        entity = entity.args[0]
    return getattr(entity, "assemblyContext", None)


def _motion_of(joint_input: FakeJointInput) -> FakeJointMotion:
    method = joint_input.motion_call[0] if joint_input.motion_call else ""
    return FakeJointMotion(MOTION_METHODS.get(method, "rigid"))


class _Created:
    """Base of joints, joint origins and rigid groups: token, name, timeline item, delete."""

    objectType = ""

    def __init__(self, collection: Any, name: str) -> None:
        self.collection = collection
        self.name = name
        self.entityToken = f"{type(self).__name__.lower()}-{next(_ids)}"
        self.isSuppressed = False
        self.healthState = adsk.fusion.FeatureHealthStates.HealthyFeatureHealthState
        self.errorOrWarningMessage = ""
        self.deleted = False
        self.delete_result = True
        self.parentComponent = collection.owner
        design = collection.design
        self.timeline_item = design.timeline.add(name) if design.parametric else None
        design.add_entity(self)

    def deleteMe(self) -> bool:
        if not self.delete_result:
            return False
        self.deleted = True
        if self in self.collection._items:
            self.collection._items.remove(self)
        item = self.timeline_item
        timeline = self.collection.design._timeline
        if item is not None and item in timeline._items:
            timeline._remove(item)
        return True


class FakeJoint(_Created):
    objectType = "adsk::fusion::Joint"

    def __init__(self, collection: Any, joint_input: FakeJointInput, name: str) -> None:
        super().__init__(collection, name)
        self.jointMotion = _motion_of(joint_input)
        self.joint_input = joint_input
        self.geometryOrOriginOne, self.geometryOrOriginTwo = joint_input.args[:2]
        self.occurrenceOne = _context_of(joint_input.args[0])
        self.occurrenceTwo = _context_of(joint_input.args[1])


class FakeAsBuiltJoint(_Created):
    objectType = "adsk::fusion::AsBuiltJoint"

    def __init__(self, collection: Any, joint_input: FakeJointInput, name: str) -> None:
        super().__init__(collection, name)
        self.jointMotion = _motion_of(joint_input)
        self.joint_input = joint_input
        self.occurrenceOne, self.occurrenceTwo, self.geometry = joint_input.args


class _CreatedCollection(FakeCollection):
    """``Joints`` and friends: ``add`` creates the object unless ``fail_add`` is set."""

    created_class: Any = None
    prefix = ""

    def __init__(self, owner: FakeComponent, design: FakeDesign) -> None:
        super().__init__()
        self.owner = owner
        self.design = design
        self.inputs: list[Any] = []
        self.fail_add: str | None = None
        # Health state the next created object gets (e.g. the error state).
        self.next_health: int | None = None

    def createInput(self, *args: Any) -> Any:
        joint_input = FakeJointInput(*args)
        self.inputs.append(joint_input)
        return joint_input

    def add(self, item: Any) -> Any:
        if self.fail_add is not None:
            raise RuntimeError(self.fail_add)
        created = self.created_class(self, item, f"{self.prefix}{len(self._items) + 1}")
        if self.next_health is not None:
            created.healthState = self.next_health
            created.errorOrWarningMessage = "the joint is in the error state"
        self._items.append(created)
        return created


class FakeJoints(_CreatedCollection):
    created_class = FakeJoint
    prefix = "Joint"


class FakeAsBuiltJoints(_CreatedCollection):
    created_class = FakeAsBuiltJoint
    prefix = "As-built Joint"


class FakeJointOriginInput:
    def __init__(self, geometry: Any) -> None:
        self.geometry = geometry
        self.offsetX: Any = None
        self.offsetY: Any = None
        self.offsetZ: Any = None
        self.angle: Any = None
        self.isFlipped = False


class FakeJointOrigin(_Created):
    objectType = "adsk::fusion::JointOrigin"

    def __init__(self, collection: Any, origin_input: FakeJointOriginInput, name: str) -> None:
        super().__init__(collection, name)
        self.origin_input = origin_input
        self.geometry = origin_input.geometry
        self.assemblyContext: Any = None
        self.nativeObject: Any = None

    def createForAssemblyContext(self, occurrence: Any) -> FakeProxy:
        return FakeProxy(self, occurrence)


class FakeJointOrigins(_CreatedCollection):
    created_class = FakeJointOrigin
    prefix = "Joint Origin"

    def createInput(self, *args: Any) -> Any:
        origin_input = FakeJointOriginInput(*args)
        self.inputs.append(origin_input)
        return origin_input


class FakeRigidGroup(_Created):
    objectType = "adsk::fusion::RigidGroup"

    def __init__(self, collection: Any, occurrences: Any, name: str) -> None:
        super().__init__(collection, name)
        self.occurrences = FakeCollection(list(occurrences))
        self.include_children = collection.include_children


class FakeRigidGroups(_CreatedCollection):
    created_class = FakeRigidGroup
    prefix = "Rigid"
    include_children = True

    def add(self, item: Any, includeChildren: bool = True) -> Any:
        self.include_children = includeChildren
        return super().add(item)


def install_joints(design: FakeDesign, component: FakeComponent) -> None:
    """``joints``, ``asBuiltJoints``, ``jointOrigins`` and ``rigidGroups`` on ``component``."""
    component.joints = FakeJoints(component, design)  # type: ignore[attr-defined]
    component.asBuiltJoints = FakeAsBuiltJoints(component, design)  # type: ignore[attr-defined]
    component.jointOrigins = FakeJointOrigins(component, design)  # type: ignore[attr-defined]
    component.rigidGroups = FakeRigidGroups(component, design)  # type: ignore[attr-defined]

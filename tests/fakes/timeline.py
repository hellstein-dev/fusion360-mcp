"""``FakeTimeline``: the parts of ``adsk.fusion.Timeline`` handlers rely on.

Like Fusion, ``add`` inserts at ``markerPosition`` and advances the marker; deleting an
item's entity removes the item. Shared by later changes' handler tests.

c09 additions: ``moveToBeginning/End``, ``timelineGroups.add`` (a group is inserted in front
of its members, which stay in the timeline with ``parentGroup`` set), ``rollTo``,
``isSuppressed``/``canSuppress``, ``isRolledBack`` and health derived from dependencies:
an item whose dependency is suppressed, in error or deleted is in error itself, like
Fusion after a recompute. ``add_feature`` builds an item with a typed, token-addressable
entity.
"""

from __future__ import annotations

from typing import Any

import fusion_addin

adsk = fusion_addin.adsk
STATES = adsk.fusion.FeatureHealthStates


class FakeEntity:
    """The ``entity`` of a timeline item (feature, sketch...); ``name`` is the item's name."""

    def __init__(
        self,
        timeline: FakeTimeline,
        item: FakeTimelineObject,
        object_type: str = "adsk::fusion::Feature",
        token: str | None = None,
        **attrs: Any,
    ) -> None:
        self._timeline = timeline
        self._item = item
        self.objectType = object_type
        self.entityToken = token
        self.parentComponent: Any = None
        self.delete_result = True
        # Set to make renaming fail like a read-only name in Fusion.
        self.rename_error: str | None = None
        for key, value in attrs.items():
            setattr(self, key, value)

    @property
    def name(self) -> str:
        return self._item.name

    @name.setter
    def name(self, value: str) -> None:
        if self.rename_error is not None:
            raise RuntimeError(self.rename_error)
        self._item.name = value

    @property
    def timelineObject(self) -> FakeTimelineObject | None:
        return self._item if self._item in self._timeline._items else None

    def deleteMe(self) -> bool:
        if not self.delete_result:
            return False
        self._timeline._remove(self._item)
        return True


class FakeTimelineObject:
    def __init__(
        self,
        timeline: FakeTimeline,
        name: str,
        is_group: bool = False,
        depends_on: tuple[FakeTimelineObject, ...] | list[FakeTimelineObject] = (),
    ) -> None:
        self._timeline = timeline
        self.name = name
        self.isGroup = is_group
        self.entity: Any = None if is_group else FakeEntity(timeline, self)
        self.parentGroup: Any = None
        self.canSuppress = True
        self.depends_on: list[FakeTimelineObject] = list(depends_on)
        self._suppressed = False
        self._health = STATES.HealthyFeatureHealthState
        self._message = ""

    # --- position ------------------------------------------------------------------------

    @property
    def index(self) -> int:
        return self._timeline._items.index(self)

    @property
    def isRolledBack(self) -> bool:
        return self.index >= self._timeline.markerPosition

    def rollTo(self, rollBefore: bool) -> bool:
        if self._timeline.fail_roll:
            return False
        self._timeline.markerPosition = self.index if rollBefore else self.index + 1
        return True

    # --- suppression and health ------------------------------------------------------------

    @property
    def isSuppressed(self) -> bool:
        return self._suppressed

    @isSuppressed.setter
    def isSuppressed(self, value: bool) -> None:
        if not self.canSuppress:
            raise RuntimeError(f"{self.name} cannot be suppressed")
        if self.name in self._timeline.fail_suppress:
            raise RuntimeError(f"cannot change suppression of {self.name}")
        self._suppressed = bool(value)

    def _broken_dependency(self) -> FakeTimelineObject | None:
        for dep in self.depends_on:
            if dep not in self._timeline._items or dep.isSuppressed:
                return dep
            if dep.healthState == STATES.ErrorFeatureHealthState:
                return dep
        return None

    @property
    def healthState(self) -> int:
        if self._suppressed:
            return STATES.SuppressedFeatureHealthState
        if self._broken_dependency() is not None:
            return STATES.ErrorFeatureHealthState
        return self._health

    @healthState.setter
    def healthState(self, value: int) -> None:
        self._health = value

    @property
    def errorOrWarningMessage(self) -> str:
        if not self._suppressed:
            dep = self._broken_dependency()
            if dep is not None:
                return f"lost reference to {dep.name}"
        return self._message

    @errorOrWarningMessage.setter
    def errorOrWarningMessage(self, value: str) -> None:
        self._message = value

    def deleteMe(self, deleteGroupAndContents: bool = True) -> bool:
        """Only groups have this form in Fusion (``TimelineGroup.deleteMe``)."""
        if not self.isGroup:
            raise RuntimeError("deleteMe(bool) is a TimelineGroup method")
        self._timeline.group_deletions.append((self.name, deleteGroupAndContents))
        self._timeline._remove(self)
        return True

    def __repr__(self) -> str:
        return f"<{self.name}>"


class FakeTimelineGroup(FakeTimelineObject):
    """``TimelineGroup``: ``count``/``item`` over its members, ``isCollapsed``, ``deleteMe``."""

    objectType = "adsk::fusion::TimelineGroup"

    def __init__(
        self, timeline: FakeTimeline, name: str, members: list[FakeTimelineObject] | None = None
    ) -> None:
        super().__init__(timeline, name, is_group=True)
        self.members: list[FakeTimelineObject] = list(members or [])
        self.isCollapsed = False
        self.delete_result = True
        for member in self.members:
            member.parentGroup = self

    @property
    def count(self) -> int:
        return len(self.members)

    def item(self, index: int) -> FakeTimelineObject:
        return self.members[index]

    def deleteMe(self, deleteGroupAndContents: bool = True) -> bool:
        if not self.delete_result:
            return False
        self._timeline.group_deletions.append((self.name, deleteGroupAndContents))
        members, self.members = self.members, []
        for member in members:
            member.parentGroup = None
        self._timeline._remove(self)
        if deleteGroupAndContents:
            for member in members:
                self._timeline._remove(member)
        return True


class FakeTimelineGroups:
    """``Timeline.timelineGroups``: ``add(startIndex, endIndex)`` groups a top-level range."""

    def __init__(self, timeline: FakeTimeline) -> None:
        self._timeline = timeline
        self.added: list[tuple[int, int]] = []
        self.fail_add = False

    def _groups(self) -> list[FakeTimelineGroup]:
        return [i for i in self._timeline._items if isinstance(i, FakeTimelineGroup)]

    @property
    def count(self) -> int:
        return len(self._groups())

    def item(self, index: int) -> FakeTimelineGroup:
        return self._groups()[index]

    def add(self, startIndex: int, endIndex: int) -> FakeTimelineGroup | None:
        timeline = self._timeline
        self.added.append((startIndex, endIndex))
        if self.fail_add:
            return None
        if not 0 <= startIndex <= endIndex < timeline.count:
            raise RuntimeError(f"invalid group range {startIndex}..{endIndex}")
        members = timeline._items[startIndex : endIndex + 1]
        if any(m.isGroup or m.parentGroup is not None for m in members):
            raise RuntimeError("groups cannot be nested")
        timeline._group_number += 1
        group = FakeTimelineGroup(timeline, f"Group{timeline._group_number}", members)
        timeline._items.insert(startIndex, group)
        if startIndex < timeline._marker:
            timeline._marker += 1
        return group


class FakeTimeline:
    def __init__(self, names: list[str] | tuple[str, ...] = (), marker: int | None = None) -> None:
        self._items: list[FakeTimelineObject] = [FakeTimelineObject(self, n) for n in names]
        self._marker = len(self._items) if marker is None else marker
        self.group_deletions: list[tuple[str, bool]] = []
        # Set to make the next deletion fail, like a Fusion API error during rollback.
        self.fail_delete: str | None = None
        # Names of items whose suppression cannot be changed (raises).
        self.fail_suppress: set[str] = set()
        # Make ``rollTo`` return False.
        self.fail_roll = False
        self.timelineGroups = FakeTimelineGroups(self)
        self._group_number = 0

    @property
    def count(self) -> int:
        return len(self._items)

    @property
    def markerPosition(self) -> int:
        return self._marker

    @markerPosition.setter
    def markerPosition(self, value: int) -> None:
        if not 0 <= value <= len(self._items):
            raise RuntimeError(f"marker position {value} out of range")
        self._marker = value

    def moveToBeginning(self) -> bool:
        self._marker = 0
        return True

    def moveToEnd(self) -> bool:
        self._marker = len(self._items)
        return True

    def item(self, index: int) -> FakeTimelineObject:
        if not 0 <= index < len(self._items):
            raise RuntimeError(f"timeline index {index} out of range")
        return self._items[index]

    def add(self, name: str, is_group: bool = False) -> FakeTimelineObject:
        """Insert at the marker, as Fusion does for a new feature."""
        item = FakeTimelineGroup(self, name) if is_group else FakeTimelineObject(self, name)
        self._items.insert(self._marker, item)
        self._marker += 1
        return item

    def add_feature(
        self,
        name: str,
        object_type: str = "adsk::fusion::ExtrudeFeature",
        token: str | None = None,
        depends_on: tuple[FakeTimelineObject, ...] | list[FakeTimelineObject] = (),
        design: Any = None,
        **attrs: Any,
    ) -> FakeTimelineObject:
        """``add`` with a typed entity; ``design`` makes the token resolvable."""
        item = self.add(name)
        item.depends_on = list(depends_on)
        item.entity = FakeEntity(self, item, object_type, token, **attrs)
        if design is not None and token:
            design.add_entity(item.entity)
        return item

    def names(self) -> list[str]:
        return [item.name for item in self._items]

    def _remove(self, item: FakeTimelineObject) -> None:
        if self.fail_delete is not None:
            message, self.fail_delete = self.fail_delete, None
            raise RuntimeError(message)
        index = self._items.index(item)
        del self._items[index]
        group = item.parentGroup
        if isinstance(group, FakeTimelineGroup) and item in group.members:
            group.members.remove(item)
        if index < self._marker:
            self._marker -= 1

"""``FakeTimeline``: the parts of ``adsk.fusion.Timeline`` the rollback logic relies on.

Like Fusion, ``add`` inserts at ``markerPosition`` and advances the marker; deleting an
item's entity removes the item. Shared by later changes' handler tests.
"""

from __future__ import annotations

from typing import Any


class FakeEntity:
    def __init__(self, timeline: FakeTimeline, item: FakeTimelineObject) -> None:
        self._timeline = timeline
        self._item = item

    def deleteMe(self) -> bool:
        self._timeline._remove(self._item)
        return True


class FakeTimelineObject:
    def __init__(self, timeline: FakeTimeline, name: str, is_group: bool = False) -> None:
        self._timeline = timeline
        self.name = name
        self.isGroup = is_group
        self.entity: Any = None if is_group else FakeEntity(timeline, self)

    def deleteMe(self, deleteGroupAndContents: bool = True) -> bool:
        """Only groups have this form in Fusion (``TimelineGroup.deleteMe``)."""
        if not self.isGroup:
            raise RuntimeError("deleteMe(bool) is a TimelineGroup method")
        self._timeline.group_deletions.append((self.name, deleteGroupAndContents))
        self._timeline._remove(self)
        return True

    def __repr__(self) -> str:
        return f"<{self.name}>"


class FakeTimeline:
    def __init__(self, names: list[str] | tuple[str, ...] = (), marker: int | None = None) -> None:
        self._items: list[FakeTimelineObject] = [FakeTimelineObject(self, n) for n in names]
        self._marker = len(self._items) if marker is None else marker
        self.group_deletions: list[tuple[str, bool]] = []
        # Set to make the next deletion fail, like a Fusion API error during rollback.
        self.fail_delete: str | None = None

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

    def item(self, index: int) -> FakeTimelineObject:
        if not 0 <= index < len(self._items):
            raise RuntimeError(f"timeline index {index} out of range")
        return self._items[index]

    def add(self, name: str, is_group: bool = False) -> FakeTimelineObject:
        """Insert at the marker, as Fusion does for a new feature."""
        item = FakeTimelineObject(self, name, is_group)
        self._items.insert(self._marker, item)
        self._marker += 1
        return item

    def names(self) -> list[str]:
        return [item.name for item in self._items]

    def _remove(self, item: FakeTimelineObject) -> None:
        if self.fail_delete is not None:
            message, self.fail_delete = self.fail_delete, None
            raise RuntimeError(message)
        index = self._items.index(item)
        del self._items[index]
        if index < self._marker:
            self._marker -= 1

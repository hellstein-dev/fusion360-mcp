"""Material and appearance lookup (c12 D4).

``find_material`` / ``find_appearance`` look a key up first as an ``id`` (in the design and in
every loaded library, or only in the named one), then as an exact name. A name found in
several places is ``INVALID_PARAMS`` with ``details.candidates = [{"id", "name",
"library"}]``; nothing found is ``ENTITY_NOT_FOUND``. The same item in the design and in a
library (a library item already copied into the design keeps its id) counts once, as the
design copy. A library is named by its name or id. Descriptions are ``{"id", "name",
"library": <library name> | "design"}``; library items are never modified here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode

DESIGN = "design"
MATERIALS = "materials"
APPEARANCES = "appearances"
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000


def _items(collection: Any) -> Iterator[Any]:
    if collection is None:
        return
    for index in range(collection.count):
        yield collection.item(index)


def iter_libraries(app: Any) -> list[Any]:
    """Every loaded material library, in Fusion's order."""
    return list(_items(app.materialLibraries))


def find_library(app: Any, key: Any) -> Any:
    """The library whose name or id is ``key``; ``ENTITY_NOT_FOUND`` otherwise."""
    libraries = app.materialLibraries
    library = None
    if isinstance(key, str) and key:
        library = libraries.itemById(key) or libraries.itemByName(key)
    if library is None:
        raise BridgeError(
            ErrorCode.ENTITY_NOT_FOUND,
            f"material library {key!r} not found: see material_list_libraries",
            {"param": "library", "library": key},
        )
    return library


def _sources(ctx: Any, kind: str, library: Any) -> list[tuple[str, Any]]:
    """``(label, collection)`` to search: the design first, then the libraries."""
    design = ctx.design
    sources: list[tuple[str, Any]] = [(DESIGN, getattr(design, kind))]
    libraries = [find_library(ctx.app, library)] if library is not None else iter_libraries(ctx.app)
    sources.extend((lib.name, getattr(lib, kind)) for lib in libraries)
    return sources


def describe_item(item: Any, library: str | None) -> dict[str, Any]:
    return {"id": item.id, "name": item.name, "library": library}


def _find(ctx: Any, kind: str, key: Any, library: Any, param: str) -> tuple[Any, str]:
    what = "material" if kind == MATERIALS else "appearance"
    if not isinstance(key, str) or not key:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"parameter '{param}' must be a non-empty {what} id or name",
            {"param": param},
        )
    sources = _sources(ctx, kind, library)
    for label, collection in sources:
        item = collection.itemById(key)
        if item is not None:
            return item, label
    matches: list[tuple[Any, str]] = []
    seen: set[str] = set()
    for label, collection in sources:
        for item in _items(collection):
            if item.name == key and item.id not in seen:
                seen.add(item.id)
                matches.append((item, label))
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"{len(matches)} {what}s are named {key!r}: pass the id or 'library'",
            {
                "param": param,
                "value": key,
                "candidates": [describe_item(item, label) for item, label in matches],
            },
        )
    where = f"library {library!r}" if library is not None else "the design or any library"
    raise BridgeError(
        ErrorCode.ENTITY_NOT_FOUND,
        f"{what} {key!r} not found in {where}: see {what}_list",
        {"param": param, "value": key},
    )


def find_material(ctx: Any, key: Any, library: Any = None) -> tuple[Any, str]:
    """``(material, library label)`` for an id or exact name (D4)."""
    return _find(ctx, MATERIALS, key, library, "material")


def find_appearance(ctx: Any, key: Any, library: Any = None, param: str = "appearance") -> Any:
    """``(appearance, library label)`` for an id or exact name (D4)."""
    return _find(ctx, APPEARANCES, key, library, param)


def library_of(ctx: Any, item: Any, kind: str) -> str | None:
    """Where ``item`` lives: ``"design"``, a library name, or ``None`` if not found."""
    item_id = getattr(item, "id", None)
    if not isinstance(item_id, str):
        return None
    for label, collection in _sources(ctx, kind, None):
        if collection.itemById(item_id) is not None:
            return label
    return None


def describe_material(ctx: Any, item: Any, kind: str = MATERIALS) -> dict[str, Any] | None:
    """``{"id", "name", "library"}`` of a material/appearance an entity has, or ``None``."""
    if item is None:
        return None
    return describe_item(item, library_of(ctx, item, kind))


def _int(params: dict[str, Any], name: str, default: int, low: int, high: int | None) -> int:
    value = params.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < low:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"parameter '{name}' must be an integer >= {low}",
            {"param": name, "value": repr(value)},
        )
    if high is not None and value > high:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            f"parameter '{name}' must be at most {high}",
            {"param": name, "value": value},
        )
    return value


def list_items(ctx: Any, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Items of one library (``library``), of the design (``in_design``) or of every library,
    filtered by ``query`` (case-insensitive substring of the name) and paginated:
    ``{"items": [{"id", "name", "library"}], "total", "truncated"}``."""
    library = params.get("library")
    in_design = ctx.require(params, "in_design", bool, False)
    query = ctx.require(params, "query", str, "")
    limit = _int(params, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
    offset = _int(params, "offset", 0, 0, None)
    if in_design and library is not None:
        raise BridgeError(
            ErrorCode.INVALID_PARAMS,
            "pass either 'library' or 'in_design', not both",
            {"param": "library"},
        )
    if in_design:
        sources = [(DESIGN, getattr(ctx.design, kind))]
    elif library is not None:
        found = find_library(ctx.app, library)
        sources = [(found.name, getattr(found, kind))]
    else:
        sources = [(lib.name, getattr(lib, kind)) for lib in iter_libraries(ctx.app)]
    needle = query.lower()
    matched = [
        describe_item(item, label)
        for label, collection in sources
        for item in _items(collection)
        if needle in item.name.lower()
    ]
    page = matched[offset : offset + limit]
    return {"items": page, "total": len(matched), "truncated": offset + len(page) < len(matched)}

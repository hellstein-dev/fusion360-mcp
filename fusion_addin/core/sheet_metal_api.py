"""The only access to the preview sheet-metal API of Fusion (c13 D1, design R1).

Flanges, unfold / refold, ``BRepBody.convertToSheetMetal`` / ``getBendFaces`` and the STEP
export of a flat pattern are preview API: absent or with changed signatures in some Fusion
versions. Every call goes through a thin wrapper here, so a signature change is fixed in one
file. ``require_api`` turns a missing method into ``FUSION_API_ERROR`` with
``details = {"reason": "api_unavailable", "api": <name>}`` before anything changes;
``has_api`` is the same probe for ``sheet_metal.capabilities``.
"""

from __future__ import annotations

from typing import Any

from fusion_mcp.protocol.errors import BridgeError, ErrorCode


def _lookup(obj: Any, attr: str) -> Any:
    if obj is None:
        return None
    try:
        return getattr(obj, attr, None)
    except Exception:
        return None


def has_api(obj: Any, attr: str) -> bool:
    """Whether ``obj`` has ``attr`` (``False`` for ``obj = None`` or a raising getter)."""
    return _lookup(obj, attr) is not None


def api_unavailable(api_name: str) -> BridgeError:
    return BridgeError(
        ErrorCode.FUSION_API_ERROR,
        f"{api_name} is not available in this Fusion API version",
        {"reason": "api_unavailable", "api": api_name},
    )


def require_api(obj: Any, attr: str, api_name: str) -> Any:
    """``obj.<attr>``; missing -> ``FUSION_API_ERROR`` ``api_unavailable`` naming ``api_name``
    (e.g. ``"UnfoldFeatures.createInput"``)."""
    value = _lookup(obj, attr)
    if value is None:
        raise api_unavailable(api_name)
    return value


def _api_failed(api_name: str, why: str) -> BridgeError:
    return BridgeError(ErrorCode.FUSION_API_ERROR, f"{api_name} failed: {why}", {"api": api_name})


# --- collections -----------------------------------------------------------------------------


def flange_features(component: Any) -> Any:
    return require_api(component.features, "flangeFeatures", "Features.flangeFeatures")


def unfold_features(component: Any) -> Any:
    return require_api(component.features, "unfoldFeatures", "Features.unfoldFeatures")


def refold_features(component: Any) -> Any:
    return require_api(component.features, "refoldFeatures", "Features.refoldFeatures")


# --- flanges ---------------------------------------------------------------------------------


def as_list(entities: Any) -> list[Any]:
    """A Python list of ``entities`` (one entity, a list/tuple or an ``ObjectCollection``):
    the flange inputs take ``std::vector`` arrays and reject an ``ObjectCollection`` with a
    ``TypeError`` on the live Fusion (c15 6.1)."""
    if isinstance(entities, list | tuple):
        return list(entities)
    count = _lookup(entities, "count")
    item = _lookup(entities, "item")
    if isinstance(count, int) and callable(item):
        return [item(i) for i in range(count)]
    return [entities]


def create_base_flange_input(component: Any, profiles: Any) -> Any:
    create = require_api(
        flange_features(component), "createBaseFlangeInput", "FlangeFeatures.createBaseFlangeInput"
    )
    return create(as_list(profiles))


def create_contour_flange_input(component: Any, profiles: Any, distance: Any) -> Any:
    create = require_api(
        flange_features(component),
        "createContourFlangeInput",
        "FlangeFeatures.createContourFlangeInput",
    )
    return create(as_list(profiles), distance)


def create_edge_flange_input(component: Any, edges: Any, distance: Any) -> Any:
    create = require_api(
        flange_features(component), "createEdgeFlangeInput", "FlangeFeatures.createEdgeFlangeInput"
    )
    return create(as_list(edges), distance)


def add_flange(component: Any, feature_input: Any) -> Any:
    return require_api(flange_features(component), "add", "FlangeFeatures.add")(feature_input)


# --- bodies ----------------------------------------------------------------------------------


def convert_to_sheet_metal(body: Any, base_face: Any, rule: Any) -> bool:
    convert = require_api(body, "convertToSheetMetal", "BRepBody.convertToSheetMetal")
    return bool(convert(base_face, rule))


def get_bend_faces(body: Any) -> tuple[list[Any], list[Any]]:
    """``(inner faces, outer faces)`` of the bends of ``body``, paired by index."""
    get = require_api(body, "getBendFaces", "BRepBody.getBendFaces")
    answer = get()
    ok, inner, outer = answer if len(answer) == 3 else (True, *answer)
    if ok is False:
        raise _api_failed("BRepBody.getBendFaces", "Fusion reported failure")
    return list(inner or []), list(outer or [])


# --- unfold / refold -------------------------------------------------------------------------


def create_unfold_input(component: Any, stationary_face: Any) -> Any:
    create = require_api(unfold_features(component), "createInput", "UnfoldFeatures.createInput")
    return create(stationary_face)


def add_unfold(component: Any, feature_input: Any) -> Any:
    return require_api(unfold_features(component), "add", "UnfoldFeatures.add")(feature_input)


def create_refold_input(component: Any, unfold: Any) -> Any:
    create = require_api(refold_features(component), "createInput", "RefoldFeatures.createInput")
    return create(unfold)


def add_refold(component: Any, feature_input: Any) -> Any:
    return require_api(refold_features(component), "add", "RefoldFeatures.add")(feature_input)


# --- flat pattern export ---------------------------------------------------------------------


def create_flat_pattern_step_options(manager: Any, filename: str, flat_pattern: Any) -> Any:
    create = require_api(
        manager,
        "createSTEPExportOptionsForFlatPattern",
        "ExportManager.createSTEPExportOptionsForFlatPattern",
    )
    return create(filename, flat_pattern)

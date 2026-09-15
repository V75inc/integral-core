"""Auto-pagination for agent list tools.

The web UI uses ``fetchAllByCursor`` to walk every page of ``GET /tracks`` and
``GET /apps``. Agent tools historically issued a single call and inherited the
route handler's default ``limit`` (20 tracks / 50 apps), so filing introspection
saw a truncated subset. When the caller omits ``cursor`` and ``limit``, dispatch
expands list reads to the full scoped set (capped at ``MAX_LIMIT`` per page).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from app.services.pagination import MAX_LIMIT

# Manifest tool name -> response list key.
_AGENT_AUTO_PAGINATE: Dict[str, str] = {
    "integral_list_tracks": "tracks",
    "integral_list_apps": "apps",
}

# Manifest tool name -> fields kept on each listed item.
#
# Auto-pagination above hands the agent every item, and jvagent then elides the
# result at ``observation_max_chars`` and tells the model to re-run the tool —
# so breadth bought here was being spent on fields no caller reads. Measured on
# the dev graph: a Track via ``export_node`` is 639 chars across 16 fields, an
# App 944 across 22. Projecting to what a *listing* is for cuts both ~4x
# (Track 639 -> 167, App 944 -> 233), which is the difference between a
# workspace's tracks arriving whole and arriving truncated mid-list.
#
# The dropped fields are presentation and provenance — ``icon``,
# ``accent_color``, ``position``, ``title_fold`` (a duplicate of ``title``),
# ``settings_schema``, ``created_at``/``updated_at``, ``*_library_*`` ids. They
# serve the web UI, which calls the ROUTE, not this projection: only the agent
# dispatch path is narrowed, so the REST contract is untouched.
#
# Field names differ per type and are NOT interchangeable — App uses
# ``name``/``description`` where Track uses ``title``/``purpose``. Projecting
# both with one field list silently reduces every app to a bare id; that is why
# this is keyed per tool rather than shared.
#
# Adding a field here is cheap and safe; removing one can blind a skill. If a
# skill starts needing a dropped field on a LISTING (as opposed to fetching the
# item), add it here rather than reverting the projection.
_AGENT_LIST_FIELDS: Dict[str, tuple[str, ...]] = {
    "integral_list_tracks": ("id", "title", "purpose", "kind"),
    "integral_list_apps": ("id", "name", "description", "lifecycle_state", "version"),
}


def project_agent_list_items(tool_name: str, data: Any) -> Any:
    """Narrow a list tool's items to the fields a listing is actually for.

    Applied to the FINAL payload in dispatch — after any page expansion — so it
    covers both the auto-paginated path and an explicitly paginated one. A tool
    with no entry in ``_AGENT_LIST_FIELDS``, or a payload that is not the
    expected shape, passes through untouched.

    Empty values are dropped rather than serialized as ``""``/``null``: an
    absent ``purpose`` is noise in a listing the model has to read.
    """
    list_key = _AGENT_AUTO_PAGINATE.get(tool_name)
    keep = _AGENT_LIST_FIELDS.get(tool_name)
    if not list_key or not keep or not isinstance(data, dict):
        return data
    items = data.get(list_key)
    if not isinstance(items, list):
        return data

    projected = []
    for item in items:
        if not isinstance(item, dict):
            # Not the shape we expect — keep it verbatim rather than dropping a
            # row the caller may depend on.
            projected.append(item)
            continue
        projected.append(
            {k: item[k] for k in keep if item.get(k) not in (None, "", [], {})}
        )

    out = dict(data)
    out[list_key] = projected
    return out


def agent_list_kwargs(
    tool_name: str,
    safe_args: Dict[str, Any],
    mapped_kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply agent-friendly defaults before the first list page fetch."""
    if tool_name not in _AGENT_AUTO_PAGINATE:
        return mapped_kwargs
    if safe_args.get("cursor") or safe_args.get("limit") is not None:
        return mapped_kwargs
    out = dict(mapped_kwargs)
    out.setdefault("limit", MAX_LIMIT)
    return out


async def expand_agent_list_pages(
    tool_name: str,
    *,
    safe_args: Dict[str, Any],
    first_page: Dict[str, Any],
    fetch_page: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    """Merge subsequent pages when the caller did not request explicit pagination."""
    list_key = _AGENT_AUTO_PAGINATE.get(tool_name)
    if not list_key or not isinstance(first_page, dict):
        return first_page
    if safe_args.get("cursor") or safe_args.get("limit") is not None:
        return first_page
    if not first_page.get("has_more"):
        return first_page

    merged = list(first_page.get(list_key) or [])
    cursor: Optional[str] = first_page.get("next_cursor")
    while cursor:
        page = await fetch_page({"cursor": cursor, "limit": MAX_LIMIT})
        if not isinstance(page, dict) or page.get("error"):
            break
        merged.extend(page.get(list_key) or [])
        if not page.get("has_more"):
            cursor = None
            break
        cursor = page.get("next_cursor")

    out = dict(first_page)
    out[list_key] = merged
    out["has_more"] = False
    out["next_cursor"] = None
    if "total" in out:
        out["total"] = len(merged)
    return out

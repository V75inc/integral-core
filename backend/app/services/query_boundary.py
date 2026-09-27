"""One query boundary for generic entry reads (W3.0).

Workspace-authored tracks accept generic reads while their App is active.
An installed package does not: business fields, rankings, and traversals
belong to a declared query. Skill text and a caller-supplied App id do
not open that path. Pause and uninstall close generic reads. Export of an
active or paused App stays the retention exception and does not use this
gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple


@dataclass(frozen=True)
class ReadDecision:
    """Whether a generic entry read may return rows."""

    allowed: bool
    code: str

    def public(self, **extra: Any) -> dict:
        """Refusal body. Carries no entry titles, fields, ids, or App state."""
        body = {
            "code": self.code,
            "declared_query_required": self.code == "app_domain",
        }
        body.update(extra)
        return body


def decide_app(app: Any) -> ReadDecision:
    """Classify an App. A track outside any App is workspace-authored, so open."""
    if app is None:
        return ReadDecision(True, "open")
    state = str(getattr(app, "lifecycle_state", "") or "active")
    slug = str(getattr(app, "installed_package_slug", "") or "").strip()
    if state != "active":
        return ReadDecision(False, "app_unavailable")
    if slug:
        return ReadDecision(False, "app_domain")
    return ReadDecision(True, "open")


async def parent_app_for_track(track: Any) -> Any:
    """The App that contains this track, or None."""
    if track is None or not callable(getattr(track, "nodes", None)):
        return None
    from app.models.edges import CONTAINS

    parents = await track.nodes(
        edge=[CONTAINS], node=["WorkspaceApp"], direction="in", limit=4
    )
    return parents[0] if parents else None


async def generic_entry_read(track: Any) -> ReadDecision:
    """Decision for a generic read of entries on this track."""
    return decide_app(await parent_app_for_track(track))


async def relation_visible(
    anchor: Any, other: Any, cache: Optional[dict] = None
) -> bool:
    """One-hop relation row.

    Same App stays visible, including a packaged App's own screen, while
    that App is active or paused. A hop that touches a packaged App from
    outside it does not. Uninstalled Apps show neither. Pass one ``cache``
    (track id to App) across a loop of rows.
    """
    from app.models.nodes import Track

    cache = {} if cache is None else cache
    apps = []
    for entry in (anchor, other):
        tid = str(getattr(entry, "track_id", "") or "")
        if tid not in cache:
            track = await Track.get(tid) if tid else None
            cache[tid] = await parent_app_for_track(track)
        apps.append(cache[tid])
    anchor_app, other_app = apps
    if (
        anchor_app is not None
        and other_app is not None
        and anchor_app.id == other_app.id
    ):
        state = str(getattr(anchor_app, "lifecycle_state", "") or "active")
        return state in ("active", "paused")
    return decide_app(anchor_app).allowed and decide_app(other_app).allowed


async def keep_open_entries(entries: List[Any]) -> Tuple[List[Any], int]:
    """Drop entries on tracks a generic read may not return.

    The count is tracks, not entries, and the dropped rows are not returned.
    A graph Entry whose track cannot be resolved is dropped. A test stub
    that is not a graph Node cannot be App-domain, so it stays.
    """
    from jvspatial.core import Node as GraphNode

    from app.models.edges import CONTAINS
    from app.models.nodes import Track

    cache: dict = {}
    kept: List[Any] = []
    excluded: set = set()
    for entry in entries:
        tid = str(getattr(entry, "track_id", "") or "")
        if tid and tid in cache:
            allowed = cache[tid]
        else:
            track = await Track.get(tid) if tid else None
            if track is None and isinstance(entry, GraphNode):
                parents = await entry.nodes(
                    edge=[CONTAINS], node=["Track"], direction="in", limit=1
                )
                track = parents[0] if parents else None
            if track is not None:
                allowed = (await generic_entry_read(track)).allowed
            else:
                allowed = not isinstance(entry, GraphNode)
            if tid:
                cache[tid] = allowed
        if allowed:
            kept.append(entry)
        else:
            excluded.add(tid or str(id(entry)))
    return kept, len(excluded)

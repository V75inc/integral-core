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
from typing import Any, List, Tuple


@dataclass(frozen=True)
class ReadDecision:
    """Whether a generic entry read may return rows."""

    allowed: bool
    code: str
    lifecycle_state: str

    def public(self, **extra: Any) -> dict:
        """Refusal body. Carries no entry titles, fields, or ids."""
        body = {
            "code": self.code,
            "declared_query_required": self.code == "app_domain",
            "lifecycle_state": self.lifecycle_state,
        }
        body.update(extra)
        return body


def decide_app(app: Any) -> ReadDecision:
    """Classify an App. A missing App is treated as open."""
    if app is None:
        return ReadDecision(True, "open", "active")
    state = str(getattr(app, "lifecycle_state", "") or "active")
    slug = str(getattr(app, "installed_package_slug", "") or "").strip()
    if state != "active":
        return ReadDecision(False, "app_unavailable", state)
    if slug:
        return ReadDecision(False, "app_domain", state)
    return ReadDecision(True, "open", state)


async def parent_app_for_track(track: Any) -> Any:
    """The App that contains this track, or None."""
    if track is None:
        return None
    from app.models.edges import CONTAINS

    try:
        parents = await track.nodes(
            edge=[CONTAINS], node=["WorkspaceApp"], direction="in", limit=4
        )
    except Exception:  # noqa: BLE001
        return None
    return parents[0] if parents else None


async def generic_entry_read(track: Any) -> ReadDecision:
    """Decision for a generic read of entries on this track."""
    return decide_app(await parent_app_for_track(track))


async def relation_visible(anchor: Any, other: Any) -> bool:
    """One-hop relation row.

    Same App stays visible, including a packaged App's own screen, while
    that App is active or paused. A hop that touches a packaged App from
    outside it does not. Uninstalled Apps show neither.
    """
    anchor_app = await parent_app_for_track(
        await _track(getattr(anchor, "track_id", ""))
    )
    other_app = await parent_app_for_track(await _track(getattr(other, "track_id", "")))
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
    """
    from app.models.nodes import Track

    cache: dict = {}
    kept: List[Any] = []
    excluded: set = set()
    for entry in entries:
        tid = str(getattr(entry, "track_id", "") or "")
        if tid not in cache:
            track = await Track.get(tid) if tid else None
            # A row whose track is not in the graph was already authorized by
            # the caller (unit doubles). Only a persisted track can be packaged.
            if track is None:
                cache[tid] = True
            else:
                cache[tid] = (await generic_entry_read(track)).allowed
        if cache[tid]:
            kept.append(entry)
        else:
            excluded.add(tid or str(id(entry)))
    return kept, len(excluded)


async def _track(track_id: str) -> Any:
    if not track_id:
        return None
    from app.models.nodes import Track

    return await Track.get(track_id)

"""One draft suggestion for the focused track, entry, or view."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.models.edges import CONTAINS
from app.models.nodes import Entry, Track, View
from app.services.app_graph import get_track_attached_operational_model


def _tier(manifest: Dict[str, Any]) -> Dict[str, Any]:
    track = manifest.get("track")
    return track if isinstance(track, dict) else manifest


def suggestions_for_track(
    manifest: Dict[str, Any], entries: List[Entry]
) -> List[Dict[str, Any]]:
    """Suggestions from repeated stored values and from field types."""
    out: List[Dict[str, Any]] = []
    tier = _tier(manifest)
    view_keys = {
        str(view.get("key") or "")
        for view in (tier.get("views") or [])
        if isinstance(view, dict)
    }
    for entry_type in tier.get("entry_types") or []:
        if not isinstance(entry_type, dict):
            continue
        type_key = str(entry_type.get("key") or "")
        for field in entry_type.get("fields") or []:
            if not isinstance(field, dict):
                continue
            key = str(field.get("key") or "")
            if not key:
                continue
            name = str(field.get("name") or key)
            kind = str(field.get("type") or "")
            counts: Dict[str, int] = {}
            for entry in entries:
                raw = (entry.custom_fields or {}).get(key)
                if raw in (None, ""):
                    continue
                label = str(raw)
                counts[label] = counts.get(label, 0) + 1
            repeated = [label for label, count in counts.items() if count >= 2]
            if repeated and kind == "text":
                out.append(
                    {
                        "op": "add_field",
                        "rationale": (
                            f"{name} repeats ({repeated[0]}). "
                            "A select can hold those values."
                        ),
                        "patch_args": {
                            "entry_type": type_key,
                            "field": {
                                "key": f"{key}_choice",
                                "name": f"{name} choice",
                                "type": "select",
                                "options": repeated,
                            },
                        },
                    }
                )
            view_key = f"{key}_table"
            if kind == "number" and view_key not in view_keys:
                out.append(
                    {
                        "op": "add_view",
                        "rationale": (
                            f"{name} is a number. A table lists the rows "
                            "behind a total."
                        ),
                        "patch_args": {
                            "spec": {
                                "key": view_key,
                                "type": "table",
                                "name": f"{name} table",
                            }
                        },
                    }
                )
    return out


async def focus_track(
    *,
    track_id: str = "",
    view_id: str = "",
    entry_id: str = "",
) -> Optional[Track]:
    """The track the Improve this affordance is pointing at."""
    if track_id.startswith("n."):
        track = await Track.get(track_id)
        if isinstance(track, Track):
            return track
    if view_id.startswith("n."):
        view = await View.get(view_id)
        if isinstance(view, View) and view.track_id:
            track = await Track.get(view.track_id)
            if isinstance(track, Track):
                return track
    if entry_id.startswith("n."):
        entry = await Entry.get(entry_id)
        if isinstance(entry, Entry) and entry.track_id:
            track = await Track.get(entry.track_id)
            if isinstance(track, Track):
                return track
    return None


async def stage_one_improvement(
    *,
    user_id: str,
    session_id: Optional[str],
    app_id: str,
    track: Track,
) -> str:
    """Stage the first suggestion as one draft revision."""
    from app.agentive.staging import create_staged_change
    from app.services.operational_model_authoring import get_or_create_draft

    model = await get_track_attached_operational_model(track)
    if model is None:
        return ""
    entries = [
        row
        for row in await track.nodes(
            edge=[CONTAINS], direction="out", node=["Entry"], limit=40
        )
        if isinstance(row, Entry)
    ]
    suggestions = suggestions_for_track(model.manifest or {}, entries)
    if not suggestions:
        return "Nothing on this track needs a change."
    chosen = suggestions[0]
    draft_info = await get_or_create_draft(
        user_id=user_id, operational_model_id=model.id
    )
    if isinstance(draft_info, dict) and draft_info.get("error"):
        return ""
    draft_id = (draft_info.get("draft") or {}).get("id")
    operation = {"op": chosen["op"], **(chosen.get("patch_args") or {})}
    payload = {
        "app_id": app_id,
        "track_id": track.id,
        "draft_id": draft_id,
        "operations": [operation],
        "rationale": chosen["rationale"],
    }
    await create_staged_change(
        user_id=user_id,
        session_id=session_id,
        kind="propose_profile_revision",
        summary=f"Improve {track.title}",
        diff_human=chosen["rationale"],
        diff_machine={"op": chosen["op"], **payload},
        payload=payload,
    )
    return f"One suggestion for {track.title} is waiting for approval."

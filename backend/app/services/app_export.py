"""Core generic App export — retention/export policy (F3 / foundation closeout).

Permission-checked JSON dump of an App's tracks, entries, and attachment
metadata. Allowed for ``active`` and ``paused`` Apps so entitlement-loss
(pause) still leaves customer data retrievable via Core.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.api.errors import BadRequestError, ResourceNotFoundError
from app.api.utils import export_node
from app.models.edges import CONTAINS, HAS_ATTACHMENT
from app.models.nodes import App, Entry
from app.utils.time import utc_now_iso


async def export_app_bundle(*, app_id: str) -> Dict[str, Any]:
    """Return a JSON-serializable export bundle for ``app_id``."""
    app_node = await App.get(app_id)
    if app_node is None:
        raise ResourceNotFoundError(
            message=f"App {app_id!r} not found",
            details={"app_id": app_id},
        )
    state = str(getattr(app_node, "lifecycle_state", "") or "")
    if state not in ("active", "paused"):
        raise BadRequestError(
            message=(
                f"App {app_id!r} in lifecycle_state={state!r}; "
                "export requires active or paused"
            ),
            details={"app_id": app_id, "lifecycle_state": state},
        )

    tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"])
    track_rows: List[Dict[str, Any]] = []
    entry_rows: List[Dict[str, Any]] = []
    attachment_rows: List[Dict[str, Any]] = []

    for track in tracks:
        track_rows.append(await export_node(track))
        entries = await Entry.find({"context.track_id": track.id})
        if not entries:
            # Fallback: graph walk when scalar track_id missing on legacy rows.
            entries = await track.nodes(edge=[CONTAINS], node=["Entry"])
        for entry in entries:
            entry_rows.append(await export_node(entry))
            try:
                attachments = await entry.nodes(
                    edge=[HAS_ATTACHMENT], node=["Attachment"]
                )
            except Exception:  # noqa: BLE001
                attachments = []
            for att in attachments:
                attachment_rows.append(await export_node(att))

    return {
        "exported_at": utc_now_iso(),
        "policy": {
            "data_access": "core_generic_read",
            "retention": "retain_until_uninstall",
            "format": "json",
        },
        "app": await export_node(app_node),
        "tracks": track_rows,
        "entries": entry_rows,
        "attachments": attachment_rows,
    }

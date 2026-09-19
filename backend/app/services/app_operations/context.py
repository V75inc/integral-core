"""OperationContext — ToolContext + app-scoped operation metadata."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.services.hooks.registry import ToolContext

logger = logging.getLogger(__name__)


@dataclass
class OperationContext(ToolContext):
    """Runtime context injected into app operation handlers."""

    app_id: str = ""
    operation_key: str = ""
    idempotency_key: Optional[str] = None
    correlation_id: Optional[str] = None

    async def create_entry(
        self,
        *,
        track_id: str,
        entry_type_key: str,
        title: str,
        custom_fields: Optional[Dict[str, Any]] = None,
        body: str = "",
    ) -> Optional[Any]:
        """Create a typed Entry in a Track owned by this operation's App.

        App operation handlers receive this narrow write capability instead of
        a generic ``ToolContext`` create primitive.  The target must be a
        Track contained by the installed App represented by ``app_id`` and in
        this context's workspace; the acting principal must retain an editing
        role on that Track.  Creation then uses the shared full entry-create
        service, preserving field validation, relation materialisation, graph
        wiring, hooks, and change events.

        ``None`` is deliberately the handler-facing failure value.  Reference
        App tools can turn it into their stable domain error without exposing
        internal policy or validation details to a user.
        """
        from app.models.edges import CONTAINS
        from app.models.nodes import App, Track
        from app.schemas.policy import Resource, Subject
        from app.services.content_profile_runtime import slug_manifest_key
        from app.services.entry_create import create_entry_in_track
        from app.services.entry_type_resolver import resolve_entry_type_id_by_key
        from app.services.permissions import resolve_role
        from app.services.policy_engine import evaluate as policy_evaluate

        target_track_id = str(track_id or "").strip()
        type_key = str(entry_type_key or "").strip()
        if not (
            target_track_id
            and type_key
            and str(self.app_id or "").strip()
            and str(self.workspace_id or "").strip()
            and str(self.user_id or "").strip()
        ):
            return None

        try:
            app = await App.get(self.app_id)
            if (
                app is None
                or str(getattr(app, "workspace_id", "") or "") != self.workspace_id
                or str(getattr(app, "lifecycle_state", "active") or "active")
                != "active"
            ):
                return None
            owned_tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
            track = next(
                (
                    candidate
                    for candidate in owned_tracks
                    if candidate.id == target_track_id
                ),
                None,
            )
            if not isinstance(track, Track) or (
                str(getattr(track, "workspace_id", "") or "") != self.workspace_id
            ):
                return None
            if await resolve_role(self.user_id, "track", target_track_id) not in (
                "owner",
                "admin",
                "editor",
            ):
                return None
            decision = await policy_evaluate(
                subject=Subject(kind="human", id=self.user_id),
                action="entry.create",
                resource=Resource(
                    kind="entry", id="", scope=f"track:{target_track_id}"
                ),
            )
            if not decision.allowed:
                return None
            type_id = await resolve_entry_type_id_by_key(target_track_id, type_key)
            if not type_id:
                return None
            return await create_entry_in_track(
                track=track,
                user_id=self.user_id,
                title=str(title or ""),
                body=str(body or ""),
                custom_fields=dict(custom_fields or {}),
                type_id=type_id,
                workspace_id=self.workspace_id,
                actor_kind="human",
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "OperationContext.create_entry failed (app=%s track=%s type=%s)",
                self.app_id,
                target_track_id,
                slug_manifest_key(type_key),
            )
            return None

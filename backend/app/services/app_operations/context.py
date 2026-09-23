"""OperationContext — ToolContext + app-scoped operation metadata."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services.hooks.registry import ToolContext

logger = logging.getLogger(__name__)


@dataclass
class OperationContext(ToolContext):
    """Runtime context injected into app operation handlers."""

    app_id: str = ""
    operation_key: str = ""
    idempotency_key: Optional[str] = None
    correlation_id: Optional[str] = None
    deferred_change_events: Optional[List[Dict[str, Any]]] = None

    async def notify_once(
        self, *, dedupe_key: str, title: str, body: str
    ) -> Optional[str]:
        """Persist one in-app notice for this principal and dedupe key.

        A repeated call with the same key returns the existing notice. Apps
        use that to keep a scheduled routine from posting a second notice
        after a retry or process restart.
        """
        key = str(dedupe_key or "").strip()
        user_id = str(self.user_id or "").strip()
        if not key or not user_id:
            return None
        from app.models.nodes import Notification
        from app.services.app_graph import create_notification

        existing = await Notification.find({"user_id": user_id})
        for note in existing or []:
            meta = getattr(note, "metadata", None) or {}
            if isinstance(meta, dict) and meta.get("dedupe_key") == key:
                return str(note.id)
        created = await create_notification(
            user_id=user_id,
            type="info",
            content=body or title,
            metadata={
                "dedupe_key": key,
                "title": title,
                "app_id": self.app_id,
                "operation_key": self.operation_key,
            },
        )
        return str(created.id)

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
        from app.services.entry_create import create_entry_in_track
        from app.services.entry_type_resolver import resolve_entry_type_id_by_key
        from app.services.operational_model_runtime import slug_manifest_key
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
            # The typed operation facade is a governed route to protected App
            # state.  It can also be used by a declared operation's helper
            # after the dispatcher has returned control, so make the narrowly
            # scoped write authority explicit here instead of relying on the
            # dispatcher's surrounding context manager.
            from app.services.app_invariant_guards import (
                reset_operation_write_active,
                set_operation_write_active,
            )

            write_token = set_operation_write_active(True)
            try:
                return await create_entry_in_track(
                    track=track,
                    user_id=self.user_id,
                    title=str(title or ""),
                    body=str(body or ""),
                    custom_fields=dict(custom_fields or {}),
                    type_id=type_id,
                    workspace_id=self.workspace_id,
                    actor_kind="human",
                    change_event_sink=(
                        self.deferred_change_events.append
                        if self.deferred_change_events is not None
                        else None
                    ),
                )
            finally:
                reset_operation_write_active(write_token)
        except Exception:  # noqa: BLE001
            logger.exception(
                "OperationContext.create_entry failed (app=%s track=%s type=%s)",
                self.app_id,
                target_track_id,
                slug_manifest_key(type_key),
            )
            return None

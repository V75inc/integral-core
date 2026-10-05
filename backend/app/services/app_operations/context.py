"""OperationContext — ToolContext + app-scoped operation metadata."""

from __future__ import annotations

import hashlib
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
    read_only: bool = False

    async def put_attachment(
        self,
        entry_id: str,
        content: bytes,
        filename: str,
        mime_type: str = "application/octet-stream",
    ) -> Optional[str]:
        """Refuse attachment storage until it participates in command commit.

        Object storage cannot join the graph/receipt transaction. Read
        operations and durable mutations therefore cannot create attachments
        through this facade and leave an untracked effect on rollback.
        """
        if self.read_only or self.deferred_change_events is not None:
            return None
        return await super().put_attachment(
            entry_id, content, filename, mime_type=mime_type
        )

    async def notify_once(
        self, *, dedupe_key: str, title: str, body: str
    ) -> Optional[str]:
        """Persist one in-app notice for this principal and dedupe key.

        A repeated call with the same key returns the existing notice. Apps
        use that to keep a scheduled routine from posting a second notice
        after a retry or process restart.
        """
        key = str(dedupe_key or "").strip()
        if self.read_only:
            return None
        user_id = str(self.user_id or "").strip()
        if not key or not user_id:
            return None
        from app.services.app_graph import create_notification_once

        identity_material = "\0".join(
            (user_id, str(self.workspace_id or ""), str(self.app_id or ""), key)
        )
        identity = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()
        created = await create_notification_once(
            user_id=user_id,
            identity=identity,
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
            if self.read_only:
                return None
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

    async def update_entry_fields(
        self,
        entry_id: str,
        custom_fields: Dict[str, Any],
        *,
        expected_record_revision: Optional[int] = None,
    ) -> bool:
        """Update an Entry through the same schema and scope rules as HTTP."""
        if self.read_only:
            return False
        from app.contracts.information import schema_revision_from_profile_version
        from app.models.edges import CONTAINS
        from app.models.nodes import App, Entry, EntryType, Track
        from app.schemas.policy import Resource, Subject
        from app.services.app_invariant_guards import enforce_protected_field_write
        from app.services.entry_conditional_update import (
            update_entry_custom_fields_if_revision,
        )
        from app.services.migration_write_guard import assert_track_schema_writable
        from app.services.operational_model_entry_fields import (
            validate_and_materialize_entry_custom_fields,
        )
        from app.services.operational_model_graph import sync_relation_edges
        from app.services.operational_model_runtime import resolve_track_runtime_profile
        from app.services.permissions import resolve_role
        from app.services.policy_engine import evaluate as policy_evaluate

        if not all((self.user_id, self.workspace_id, self.app_id, entry_id)):
            return False
        entry = await Entry.get(entry_id)
        if entry is None:
            return False
        track = await Track.get(entry.track_id) if entry.track_id else None
        if (
            track is None
            or str(getattr(track, "workspace_id", "")) != self.workspace_id
        ):
            return False
        app = await App.get(self.app_id)
        if (
            app is None
            or str(getattr(app, "workspace_id", "")) != self.workspace_id
            or str(getattr(app, "lifecycle_state", "active") or "active") != "active"
        ):
            return False
        tracks = await app.nodes(edge=[CONTAINS], node=["Track"], direction="out")
        if not any(str(candidate.id) == str(track.id) for candidate in tracks):
            return False
        if await resolve_role(self.user_id, "entry", entry_id) not in (
            "owner",
            "admin",
            "editor",
        ):
            return False
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=self.user_id),
            action="entry.update",
            resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
        )
        if not decision.allowed:
            return False
        await assert_track_schema_writable(track)
        operational_model, runtime_tier, _ = await resolve_track_runtime_profile(track)
        schema_revision = schema_revision_from_profile_version(
            getattr(operational_model, "version_number", None)
        )
        entry_type = await EntryType.get(entry.type_id) if entry.type_id else None
        if entry_type is None:
            return False
        from app.services.operational_model_compile import _slug

        entry_type_key = _slug(
            str(
                (entry_type.form_schema or {}).get("_manifest_entry_type_key")
                or entry_type.name
            )
        )
        from app.services.app_invariant_guards import (
            reset_operation_write_active,
            set_operation_write_active,
        )

        write_token = set_operation_write_active(True)
        try:
            await enforce_protected_field_write(
                workspace_id=self.workspace_id,
                entry_type_key=entry_type_key,
                proposed_custom_fields=custom_fields,
            )
        finally:
            reset_operation_write_active(write_token)
        merged = {**(entry.custom_fields or {}), **custom_fields}
        validated, relation_refs = await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=entry_type,
            custom_fields=merged,
            runtime_tier=runtime_tier,
            entry=entry,
            actor_user_id=self.user_id,
            actor_kind="agent",
            source_entry_title=str(getattr(entry, "title", "") or ""),
        )
        observed_revision = int(getattr(entry, "record_revision", 1) or 1)
        if (
            expected_record_revision is not None
            and int(expected_record_revision) != observed_revision
        ):
            return False
        saved, _error = await update_entry_custom_fields_if_revision(
            entry=entry,
            expected_revision=observed_revision,
            updates=validated,
            schema_revision=schema_revision,
            user_id=self.user_id,
            scope=f"tool:{self.scope}",
            event_sink=(
                self.deferred_change_events.append
                if self.deferred_change_events is not None
                else None
            ),
        )
        if not saved:
            return False
        await sync_relation_edges(source_entry=entry, relation_refs=relation_refs)
        return True

    async def conditional_update_entry_fields(
        self,
        entry_id: str,
        *,
        state_field: str,
        expected_state: str,
        updates: Dict[str, Any],
    ) -> tuple[bool, Optional[str]]:
        """Validate and scope a conditional Entry update before atomic CAS."""
        if self.read_only:
            return False, "read_only"
        from app.models.edges import CONTAINS
        from app.models.nodes import App, Entry, EntryType, Track
        from app.schemas.policy import Resource, Subject
        from app.services.app_invariant_guards import enforce_protected_field_write
        from app.services.entry_conditional_update import (
            conditional_update_entry_custom_fields,
        )
        from app.services.migration_write_guard import assert_track_schema_writable
        from app.services.operational_model_entry_fields import (
            validate_and_materialize_entry_custom_fields,
        )
        from app.services.operational_model_graph import sync_relation_edges
        from app.services.operational_model_runtime import resolve_track_runtime_profile
        from app.services.permissions import resolve_role
        from app.services.policy_engine import evaluate as policy_evaluate

        if not all((self.user_id, self.workspace_id, self.app_id, entry_id)):
            return False, "write_denied"
        entry = await Entry.get(entry_id)
        track = await Track.get(entry.track_id) if entry and entry.track_id else None
        app = await App.get(self.app_id)
        if (
            entry is None
            or track is None
            or app is None
            or str(getattr(app, "lifecycle_state", "active") or "active") != "active"
            or str(getattr(track, "workspace_id", "")) != self.workspace_id
            or str(getattr(app, "workspace_id", "")) != self.workspace_id
        ):
            return False, "write_denied"
        tracks = await app.nodes(edge=[CONTAINS], node=["Track"], direction="out")
        if not any(str(candidate.id) == str(track.id) for candidate in tracks):
            return False, "write_denied"
        if await resolve_role(self.user_id, "entry", entry_id) not in (
            "owner",
            "admin",
            "editor",
        ):
            return False, "write_denied"
        decision = await policy_evaluate(
            subject=Subject(kind="human", id=self.user_id),
            action="entry.update",
            resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
        )
        if not decision.allowed:
            return False, "write_denied"
        await assert_track_schema_writable(track)
        entry_type = await EntryType.get(entry.type_id) if entry.type_id else None
        if entry_type is None:
            return False, "not_found"
        operational_model, runtime_tier, _ = await resolve_track_runtime_profile(track)
        from app.contracts.information import schema_revision_from_profile_version

        schema_revision = schema_revision_from_profile_version(
            getattr(operational_model, "version_number", None)
        )
        from app.services.operational_model_compile import _slug

        type_key = _slug(
            str(
                (entry_type.form_schema or {}).get("_manifest_entry_type_key")
                or entry_type.name
            )
        )
        proposed = {**(entry.custom_fields or {}), **dict(updates or {})}
        from app.services.app_invariant_guards import (
            reset_operation_write_active,
            set_operation_write_active,
        )

        write_token = set_operation_write_active(True)
        try:
            await enforce_protected_field_write(
                workspace_id=self.workspace_id,
                entry_type_key=type_key,
                proposed_custom_fields=updates,
            )
        finally:
            reset_operation_write_active(write_token)
        _, relation_refs = await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=entry_type,
            custom_fields=proposed,
            runtime_tier=runtime_tier,
            entry=entry,
            actor_user_id=self.user_id,
            actor_kind="agent",
            source_entry_title=str(getattr(entry, "title", "") or ""),
        )
        ok, error = await conditional_update_entry_custom_fields(
            user_id=self.user_id,
            entry_id=entry_id,
            state_field=state_field,
            expected_state=expected_state,
            updates=dict(updates or {}),
            scope=f"operation:{self.app_id}:{self.operation_key}",
            schema_revision=schema_revision,
            event_sink=(
                self.deferred_change_events.append
                if self.deferred_change_events is not None
                else None
            ),
        )
        if ok:
            await sync_relation_edges(source_entry=entry, relation_refs=relation_refs)
        return ok, error

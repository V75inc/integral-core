"""Fast cascade delete for entries.

Why this exists
---------------
``Node.delete()`` in jvspatial defaults to ``cascade=True``, which runs a
recursive graph-traversal "is this neighbour only connected to my deletion
set?" check. For an Entry that's tagged with a heavily-used Tag, that
walk explodes — it loads every Entry tagged with the same Tag, then
recursively each of THEIR neighbours, all one DB round-trip at a time. In
practice this turned a one-row delete into 10–30 seconds against SQLite
when a tag had a few hundred siblings.

We don't actually need that traversal. The Entry ↔ Track / Tag /
Attachment relationships are well-known up front:

* **CONTAINS** (Track → Entry, incoming) — Track stays.
* **TAGGED_WITH** (Entry → Tag, outgoing) — Tags stay; the edges are
  removed automatically by ``cascade=False`` (which still cleans up
  outgoing edges, just not dependent nodes).
* **HAS_ATTACHMENT** (Entry → Attachment, outgoing) — Attachments may be
  shared across entries, so we leave them and only drop the edge (also
  handled by ``cascade=False``).
* **HAS_COMMENT** (Entry → Comment, outgoing) — Comments are
  entry-scoped; we explicitly delete them (recursively, since comments
  can be threaded via Comment → Comment ``HAS_COMMENT``).
* **HAS_UPLOAD_SESSION** (Entry → UploadSession, outgoing) — sessions are
  transient children of the entry; we cancel (drops staged chunks) and
  delete them.
* **HAS_SHARE_LINK** (Entry → ShareLink) / **INVITED_TO** (Invitation →
  Entry) — entry-scoped side-cars; removed via
  ``app_deletion.delete_resource_side_cars``.
* **Embedding row** — soft-deleted (I-RET-03: never hard-deleted) so
  audit-log + agent-memory recall keep working.

``delete_entry_fast`` is the SINGLE entry-deletion primitive: every
caller (HTTP delete handler, approval re-run via ``entry_writer``, track
and app cascades, seed unplanting) routes through it so the cleanup set
above is applied uniformly.

The same pattern is used in ``app/services/space_deletion.py`` for the
same perf reason — keep this module aligned with that approach if the
graph schema changes.

Public API
----------
``delete_entry_fast(entry)`` — drop-in replacement for
``await entry.delete()`` from the perspective of the API handlers; orders
of magnitude faster on tagged entries.
"""

from __future__ import annotations

import logging
from typing import List

from app.models.edges import ANCHORS
from app.models.nodes import Comment, Entry

logger = logging.getLogger(__name__)

# Phase 3.1 Plan 03.1-03 ANC-05 — cascade hook delegates to
# ``policy_engine.evaluate``. Imports are deferred to call-time inside
# ``delete_entry_fast`` to keep the module loadable without policy_engine
# in environments that disable policy evaluation (rare, but mirrors the
# Phase 3 Plan 03-01 import contract — entry_deletion is free to call
# evaluate but must NOT import it at module top to avoid circular deps).
#
# The cascade walks Entry → ANCHORS → Track edges; for each anchored Track
# the engine evaluates ``anchor.cascade``. On allow, the Track + nested
# entries are hard-deleted via ``delete_track_and_nested_content`` (the
# Phase 1 idiom used by space_deletion.py). On deny, the Track is preserved
# (graceful — other anchored Tracks may still be allowed); the source
# Entry deletion proceeds either way (existing Plan 0 behavior).


async def _collect_descendant_comments(entry: Entry) -> List[Comment]:
    """Capture roots and replies once, including the Entry's reply index edges."""
    from jvspatial.core import Walker
    from jvspatial.core.context import get_default_context
    from jvspatial.core.decorators import on_visit

    from app.exceptions import AuthorizationUnavailableError
    from app.models.edges import HAS_COMMENT
    from app.services.authority_facts import primary_node_record

    class CommentInventory(Walker):
        comments: List[Comment] = []
        visited_ids: set[str] = set()
        indexed_ids: set[str] = set()

        @on_visit(Entry, Comment)
        async def collect(self, here):
            if here.id in self.visited_ids:
                raise AuthorizationUnavailableError()
            self.visited_ids.add(here.id)
            if len(self.visited_ids) > 10000:
                raise AuthorizationUnavailableError()
            database = get_default_context().database
            if isinstance(here, Comment):
                self.comments.append(here)
            edges = await database.find(
                "edge",
                {"source": here.id, "entity": HAS_COMMENT._entity_name()},
                limit=10001,
            )
            if len(edges) > 10000:
                raise AuthorizationUnavailableError()
            children = []
            for edge in edges:
                target = edge.get("target")
                if not isinstance(target, str) or not target:
                    raise AuthorizationUnavailableError()
                parents = await database.find(
                    "edge",
                    {"target": target, "entity": HAS_COMMENT._entity_name()},
                    limit=3,
                )
                sources = [parent.get("source") for parent in parents]
                if (
                    not 1 <= len(sources) <= 2
                    or len(set(sources)) != len(sources)
                    or here.id not in sources
                ):
                    raise AuthorizationUnavailableError()
                if isinstance(here, Entry):
                    self.indexed_ids.add(target)
                    if len(sources) == 2:
                        # Every current reply is indexed directly by its Entry
                        # AND linked from its parent Comment. Traverse only the
                        # thread roots here; following both paths mistakes this
                        # legitimate diamond for a cycle or shared ownership.
                        parent_id = next(
                            source for source in sources if source != here.id
                        )
                        if (
                            await primary_node_record(database, Comment, parent_id)
                            is None
                        ):
                            raise AuthorizationUnavailableError()
                        continue
                elif any(source not in {here.id, entry.id} for source in sources):
                    # A reply may have its Entry index edge, or just the legacy
                    # thread edge. Neither permits another Entry/thread to be
                    # pulled into this authorized deletion subtree.
                    raise AuthorizationUnavailableError()
                record = await primary_node_record(database, Comment, target)
                if record is None:
                    raise AuthorizationUnavailableError()
                child = await get_default_context()._deserialize_entity(Comment, record)
                if child is None:
                    raise AuthorizationUnavailableError()
                children.append(child)
            await self.visit(children)

    walker = CommentInventory()
    await walker.spawn(entry)
    if any(
        isinstance(item, dict) and "hook_error" in item
        for item in await walker.get_report()
    ) or not walker.indexed_ids.issubset(walker.visited_ids):
        # Unreachable indexed replies identify a cycle or a foreign parent;
        # skipping their index edge must never hide incomplete deletion.
        raise AuthorizationUnavailableError()
    return walker.comments


async def soft_delete_entry_embedding(entry_id: str) -> None:
    """Best-effort soft-delete of the embedding row keyed on ``entry_id``.

    Routes to ``EmbeddingStore.soft_delete`` (I-RET-03 — entry deletes
    NEVER hard-delete the embedding row; soft-delete preserves audit-log
    + agent-memory recall). NEVER raises.

    Note: ``app/api/entries.py`` carries an identical private helper
    (``_soft_delete_embedding``). It is not imported here because a service
    must not depend on the API layer; the HTTP handler's extra call after
    ``delete_entry_fast`` is an idempotent no-op on an already soft-deleted
    row and can be dropped when that file is next touched.
    """
    from app.services.retrieval import get_embedding_store

    try:
        await get_embedding_store().soft_delete(entry_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "entry_deletion: embedding soft-delete failed for %s "
            "(entry delete succeeded): %s",
            entry_id,
            exc,
        )


async def _delete_upload_sessions(entry: Entry) -> list[str]:
    """Delete the complete named session set; defer physical files until commit."""
    from jvspatial.core.context import get_default_context

    from app.exceptions import AuthorizationUnavailableError
    from app.models.edges import HAS_UPLOAD_SESSION
    from app.models.nodes import UploadSession
    from app.services.authority_facts import primary_node_record

    context = get_default_context()
    rows = await context.database.find(
        "edge",
        {"source": entry.id, "entity": HAS_UPLOAD_SESSION._entity_name()},
        limit=1001,
    )
    if len(rows) > 1000:
        raise AuthorizationUnavailableError()
    identifiers = []
    for row in rows:
        target = row.get("target")
        if not isinstance(target, str) or not target or target in identifiers:
            raise AuthorizationUnavailableError()
        parents = await context.database.find(
            "edge",
            {"target": target, "entity": HAS_UPLOAD_SESSION._entity_name()},
            limit=2,
        )
        record = await primary_node_record(context.database, UploadSession, target)
        if len(parents) != 1 or parents[0].get("source") != entry.id or record is None:
            raise AuthorizationUnavailableError()
        session = await context._deserialize_entity(UploadSession, record)
        if session is None or session.entry_id != entry.id:
            raise AuthorizationUnavailableError()
        await session.delete(cascade=False)
        identifiers.append(target)
    return identifiers


async def delete_entry_fast(
    entry: Entry,
    *,
    actor_user_id: str = "",
    actor_kind: str = "human",
    expected_record_revision: int | None = None,
) -> None:
    """Delete using a fresh entity on the command's held transaction."""
    from app.exceptions import AuthorizationUnavailableError
    from app.models.nodes import EntryType, Track
    from app.services.app_invariant_guards import enforce_protected_field_write
    from app.services.entry_write_scope import entry_write_scope
    from app.services.operational_model_compile import slug_manifest_key
    from app.services.resource_cascade import (
        cascade_manages_tracks,
        entry_cascade_permitted,
    )

    async with entry_write_scope(entry.id):
        current = await Entry.find_one({"id": entry.id})
        if current is None:
            raise AuthorizationUnavailableError()
        if (
            expected_record_revision is not None
            and current.record_revision != expected_record_revision
        ):
            from app.api.errors import ResourceConflictError

            raise ResourceConflictError(
                message="Entry has changed since approval review"
            )
        if await entry_cascade_permitted(actor_user_id, current.id):
            await _delete_admitted_entry(
                current,
                actor_user_id=actor_user_id,
                actor_kind=actor_kind,
                cascade_anchors=not cascade_manages_tracks(),
            )
            return
        track = await Track.find_one({"id": current.track_id})
        entry_type = (
            await EntryType.find_one({"id": current.type_id})
            if current.type_id
            else None
        )
        if track is None:
            raise AuthorizationUnavailableError()
        key = (
            slug_manifest_key(
                str(
                    (entry_type.form_schema or {}).get("_manifest_entry_type_key")
                    or entry_type.name
                )
            )
            if entry_type
            else ""
        )
        from app.services.entry_effect_scope import entry_effect_scope

        async with entry_effect_scope(
            user_id=actor_user_id,
            actor_kind=actor_kind,
            actor_id=actor_user_id if actor_kind in {"agent", "connector"} else None,
            action="entry.delete",
            target_kind="entry",
            target_id=current.id,
            workspace_id=track.workspace_id,
            payload={
                "entry_id": current.id,
                **(
                    {"expected_record_revision": expected_record_revision}
                    if expected_record_revision is not None
                    else {}
                ),
            },
            entry_type_key=key,
        ):
            await enforce_protected_field_write(
                principal_id=actor_user_id,
                workspace_id=track.workspace_id,
                entry_type_key=key,
                proposed_custom_fields=current.custom_fields,
                track_id=track.id,
                entry_id=current.id,
                mutation_kind="delete",
                proposed_fields={
                    "title": current.title,
                    "body": current.body,
                    "status": current.status,
                },
            )
            from app.agentive.services.staging_execution import staged_command_effect

            async with staged_command_effect(
                user_id=actor_user_id,
                workspace_id=track.workspace_id,
                kind="delete_entry",
                targets={"entry_id": current.id},
            ) as complete:
                if complete is not None:
                    from app.agentive.services.staging_execution import (
                        staged_command_preconditions,
                    )
                    from app.api.errors import ResourceConflictError

                    expected = staged_command_preconditions().get(
                        "expected_record_revision"
                    )
                    if expected is not None and (
                        type(expected) is not int or expected != current.record_revision
                    ):
                        raise ResourceConflictError(
                            message="Entry has changed since approval review"
                        )
                    schema_expected = staged_command_preconditions().get(
                        "expected_schema_revision"
                    )
                    if schema_expected is not None:
                        from app.contracts.information import (
                            schema_revision_from_profile_version,
                        )
                        from app.services.operational_model_runtime import (
                            resolve_track_runtime_profile,
                        )

                        model, _, _ = await resolve_track_runtime_profile(track)
                        live_schema = schema_revision_from_profile_version(
                            getattr(model, "version_number", None)
                        )
                        if (
                            type(schema_expected) is not int
                            or schema_expected != live_schema
                        ):
                            raise ResourceConflictError(
                                message="Entry schema has changed since approval review"
                            )
                before = (
                    await current.export(flat=True) if complete is not None else None
                )
                await _delete_admitted_entry(
                    current, actor_user_id=actor_user_id, actor_kind=actor_kind
                )
                if complete is not None:
                    from app.agentive.services.staging_execution import (
                        capture_staging_change_event,
                    )

                    await capture_staging_change_event(
                        {
                            "actor_kind": "human",
                            "actor_id": actor_user_id,
                            "action": "entry.delete",
                            "resource_type": "Entry",
                            "resource_id": current.id,
                            "before": before,
                            "after": None,
                            "scope": f"track:{track.id}",
                        }
                    )
                    await complete(
                        {"entry_id": current.id, "track_id": track.id, "deleted": True}
                    )


async def _delete_admitted_entry(
    entry: Entry,
    *,
    actor_user_id: str = "",
    actor_kind: str = "human",
    cascade_anchors: bool = True,
) -> None:
    """Delete an Entry and its entry-scoped descendants quickly.

    Avoids jvspatial's expensive ``cascade=True`` graph traversal.

    Order of operations matters:
      0. Phase 3.1 Plan 03.1-03 ANC-05 — walk Entry → ANCHORS → Track edges
         and, for each anchored Track, evaluate ``anchor.cascade``. On allow,
         hard-delete the anchored Track + nested entries and emit
         ``anchor.cascade``. On deny, preserve the Track (Phase 3 Plan 03-05's
         denial-emit fires ``policy.deny`` automatically; other anchors continue).
      1. Collect (don't delete) every descendant comment first — once
         the entry is gone, walking ``entry.nodes(...)`` returns
         nothing.
      2. Delete the entry with ``cascade=False`` — this drops both
         incoming edges (CONTAINS from Track) and outgoing edges
         (HAS_COMMENT, HAS_ATTACHMENT, TAGGED_WITH, ANCHORS) in O(edge count)
         without traversing.
      3. Delete the collected comments child first, once each (also with
         ``cascade=False``).
      4. Deliver the retained post-commit cleanup, including the entry's
         embedding soft-delete (I-RET-03).

    Before step 2 the entry's UploadSessions are cancelled + deleted and
    its ShareLinks / Invitations are removed (they are only reachable via
    the entry and would otherwise dangle once the edges are dropped).

    The canonical command runs this work in its transaction. Incomplete
    comment inventory or a failed deletion aborts the command, preserving
    the entry and its discussion instead of reporting partial success.

    Phase 3.1 cascade kwargs:
      ``actor_user_id`` + ``actor_kind`` thread the authenticated principal
      through to ``policy_engine.evaluate``. Defaults preserve the Plan 0
      call signature (existing call sites that pass only ``entry`` see the
      cascade fall through to ``Subject(kind='human', id='')``, which evaluates
      via the default-human path's anchor.* dispatch → can_edit_track on the
      parent Track id, matching the source Entry's permission gate).
    """
    # Phase 3.1 Plan 03.1-03 — anchor cascade. Walk first (BEFORE the entry
    # is deleted, since once it's gone we can't enumerate its ANCHORS edges).
    try:
        # App/workspace cascades delete their captured Tracks themselves.
        # Following ANCHORS here could delete a later sibling twice or reach
        # a shared Track outside that command's destructive perimeter.
        anchored_tracks = (
            await entry.nodes(edge=[ANCHORS], direction="out", node=["Track"])
            if cascade_anchors
            else []
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning(
            "entry_deletion: failed to enumerate ANCHORS for entry %s: %s",
            getattr(entry, "id", "?"),
            exc,
        )
        anchored_tracks = []

    if anchored_tracks:
        from typing import cast

        from app.schemas.policy import Resource, Subject
        from app.schemas.provenance import ActorKind
        from app.services.app_deletion import delete_track_and_nested_content
        from app.services.change_event import emit_change_event
        from app.services.policy_engine import evaluate as policy_evaluate

        parent_track_id = getattr(entry, "track_id", "") or ""
        scope = f"track:{parent_track_id}" if parent_track_id else "*"
        # Use system-subject when no actor is supplied (internal scaffolding /
        # tests). Human callers from the API layer thread actor_user_id.
        if actor_user_id:
            _eff_kind: ActorKind = cast(ActorKind, actor_kind)
            _eff_id: str = actor_user_id
        else:
            _eff_kind = "system"
            _eff_id = "entry_deletion"

        for anchored in anchored_tracks:
            anchor_id = getattr(anchored, "id", None) or ""
            try:
                decision = await policy_evaluate(
                    subject=Subject(kind=_eff_kind, id=_eff_id),
                    action="anchor.cascade",
                    resource=Resource(
                        kind="track",
                        id=anchor_id,
                        scope=scope,
                    ),
                )
            except Exception as exc:  # pragma: no cover — fail-loud
                logger.warning(
                    "anchor.cascade evaluate failed for Track %s; preserving: %s",
                    anchor_id,
                    exc,
                )
                continue

            if not decision.allowed:
                # Phase 3 Plan 03-05's denial-emit fires policy.deny
                # automatically inside policy_engine.evaluate. We do not
                # additionally emit anchor.deny here (the cascade gate is a
                # per-Track loop; emitting one anchor.deny per denied Track
                # would defeat the WS broadcast skip safeguard). The
                # policy.deny ChangeEvent already carries failed_action so
                # forensic visibility is preserved.
                logger.info(
                    "anchor.cascade denied for Track %s; preserving (reason=%s)",
                    anchor_id,
                    decision.reason,
                )
                continue

            # Allow → hard-delete the anchored Track + its nested entries.
            try:
                await delete_track_and_nested_content(
                    anchored, actor_user_id=actor_user_id
                )
            except Exception as exc:
                logger.warning(
                    "delete_track_and_nested_content failed for anchored Track %s "
                    "(continuing): %s",
                    anchor_id,
                    exc,
                )
                continue

            # Emit anchor.cascade ChangeEvent — WS broadcast is skipped per
            # change_event.py BROADCAST_SKIP_ACTIONS set.
            try:
                await emit_change_event(
                    actor_kind=_eff_kind,
                    actor_id=_eff_id,
                    action="anchor.cascade",
                    resource_type="Track",
                    resource_id=anchor_id,
                    before=None,
                    after=None,
                    scope=scope,
                    details={
                        "anchor_source_entry_id": getattr(entry, "id", ""),
                        "cascade_reason": "entry.delete",
                    },
                )
            except Exception as exc:  # pragma: no cover — best-effort
                logger.warning(
                    "anchor.cascade ChangeEvent emit failed for Track %s: %s",
                    anchor_id,
                    exc,
                )

    comments = await _collect_descendant_comments(entry)
    entry_id = getattr(entry, "id", "") or ""

    # Entry-scoped side-cars — must be enumerated BEFORE the entry (and its
    # edges) are gone.
    session_ids = await _delete_upload_sessions(entry)
    from app.services.app_deletion import delete_resource_side_cars

    await delete_resource_side_cars(entry)

    from app.services.entry_cleanup_outbox import retain_cleanup

    await retain_cleanup(entry_id=entry_id, session_ids=session_ids)
    await entry.delete(cascade=False)

    for comment in reversed(comments):
        await comment.delete(cascade=False)

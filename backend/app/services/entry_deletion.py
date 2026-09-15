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

import asyncio
import logging
from typing import List

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
    """Walk the comment subtree and return every descendant Comment.

    Traverses Entry → HAS_COMMENT → Comment → HAS_COMMENT → … .

    Bounded by what the user actually wrote, not by global graph fan-out.
    Uses BFS so deeply nested threads don't blow the recursion limit; each
    layer's children are fetched concurrently (``asyncio.gather``) so a
    deep thread doesn't pay one round-trip per level.
    """
    collected: List[Comment] = []
    seen_ids: set[str] = set()

    # Top-level comments off the entry.
    frontier: List[Comment] = list(
        await entry.nodes(edge=["HAS_COMMENT"], direction="out", node=["Comment"])
    )

    while frontier:
        # Dedupe — defensive against cycles (shouldn't happen with
        # threaded-comments semantics, but cheap insurance).
        layer: List[Comment] = []
        for c in frontier:
            cid = getattr(c, "id", None)
            if not cid or cid in seen_ids:
                continue
            seen_ids.add(cid)
            layer.append(c)
            collected.append(c)
        if not layer:
            break

        # Fetch the next layer concurrently.
        children_lists = await asyncio.gather(
            *(
                c.nodes(edge=["HAS_COMMENT"], direction="out", node=["Comment"])
                for c in layer
            ),
            return_exceptions=True,
        )
        next_frontier: List[Comment] = []
        for result in children_lists:
            if isinstance(result, BaseException):
                # A single failing query shouldn't abort the whole
                # cascade; log and keep going. Worst case: an orphan
                # comment is left behind, which is invisible to the
                # user (no entry to view it through).
                logger.warning("entry_deletion: child-comment fetch failed: %s", result)
                continue
            next_frontier.extend(result)  # type: ignore[arg-type]
        frontier = next_frontier

    return collected


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


async def _delete_upload_sessions(entry: Entry) -> None:
    """Cancel + delete every UploadSession hanging off ``entry``.

    ``cancel_session`` removes the staged chunk directory; the node is then
    dropped with ``cascade=False`` (removes the HAS_UPLOAD_SESSION edge).
    Best-effort — a stale session row must never block the entry delete.
    """
    try:
        sessions = await entry.nodes(
            edge=["HAS_UPLOAD_SESSION"], direction="out", node=["UploadSession"]
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "entry_deletion: failed to enumerate upload sessions for %s: %s",
            getattr(entry, "id", "?"),
            exc,
        )
        return
    if not sessions:
        return
    from app.services.chunked_upload import cancel_session

    async def _one(session) -> None:  # type: ignore[type-arg]
        try:
            await cancel_session(session)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "entry_deletion: upload session cancel failed for %s: %s",
                getattr(session, "id", "?"),
                exc,
            )
        try:
            await session.delete(cascade=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "entry_deletion: upload session delete failed for %s: %s",
                getattr(session, "id", "?"),
                exc,
            )

    await asyncio.gather(*(_one(s) for s in sessions))


async def delete_entry_fast(
    entry: Entry,
    *,
    actor_user_id: str = "",
    actor_kind: str = "human",
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
      3. Delete the collected comments (also with ``cascade=False``)
         concurrently.
      4. Soft-delete the entry's embedding row (I-RET-03).

    Before step 2 the entry's UploadSessions are cancelled + deleted and
    its ShareLinks / Invitations are removed (they are only reachable via
    the entry and would otherwise dangle once the edges are dropped).

    Comment delete failures are logged but never raised — leaving an
    orphaned comment row in the DB is preferable to a 500 to the user
    when the entry itself is already gone. Periodic orphan cleanup can
    address those out of band.

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
        anchored_tracks = await entry.nodes(
            edge=["ANCHORS"], direction="out", node=["Track"]
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
                await delete_track_and_nested_content(anchored)
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
    await _delete_upload_sessions(entry)
    from app.services.app_deletion import delete_resource_side_cars

    await delete_resource_side_cars(entry)

    await entry.delete(cascade=False)

    if comments:

        async def _safe_delete(c: Comment) -> None:
            try:
                await c.delete(cascade=False)
            except Exception as exc:
                logger.warning(
                    "entry_deletion: comment delete failed for %s: %s",
                    getattr(c, "id", "?"),
                    exc,
                )

        await asyncio.gather(*(_safe_delete(c) for c in comments))

    if entry_id:
        await soft_delete_entry_embedding(entry_id)

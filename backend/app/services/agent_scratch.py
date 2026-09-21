"""Per-user agent scratch-space helpers — MEM-01 + MEM-02 + MEM-03.

Substrate-tier (always loaded; does NOT depend on ``AGENTIVE_ENABLED``).
Provides:

- :func:`provision_scratch_track` — idempotent per-process LRU-cached
  resolver: returns the caller's scratch ``Track`` (mints one in the
  user's personal workspace on first call). Marked
  ``Track.kind = "agent_scratch"`` so the discriminator is graph-queryable
  (CONTEXT lock #1 / MEM-01).
- :func:`promote_scratch_entry` — moves an entry from the scratch Track to
  a domain Track, gates on
  ``policy_engine.evaluate(entry.read on source)`` AND
  ``policy_engine.evaluate(entry.create on target)``, preserves
  ``provenance.derived_from`` (MEM-03), and archives the source entry via
  ``Entry.status = "archived"`` (CONTEXT lock #2 — no new field).

Phase 2 D-05 single-emission convention:

- Provision emits ``track.create`` exactly once, on the create branch
  (cache-hit and existing-track branches emit nothing).
- Promote emits ``entry.create`` for the new target-side Entry AND
  ``entry.update`` for the soft-archived source — both via the existing
  ``emit_change_event`` path.

The embedding store soft-delete (Plan 04-01 territory) is best-effort: if
the embedding store module is not yet available (Wave 1 parallel-execution
window), the soft-delete is silently skipped with a warning log. The
contract is "either the embedding is invalidated OR a warning is logged" —
never a raised exception that breaks the promote operation.

Module layout:

- ``AGENT_SCRATCH_TRACK_KIND`` / ``AGENT_SCRATCH_TRACK_TITLE`` — locked
  string constants for the graph-queryable discriminator and the default
  Track title (purely cosmetic; not used for lookup).
- ``_SCRATCH_TRACK_ID_CACHE`` — per-process dict mapping
  ``user_id -> scratch_track_id``. NOT a true ``functools.lru_cache``
  because we need to invalidate on stale-hit detection (CP got deleted
  mid-test, for instance). The "LRU" naming in the plan refers to the
  semantic — at most one entry per user — not the eviction policy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.models.edges import CONTAINS, IS_OF_TYPE, OWNS
from app.models.nodes import Entry, EntryType, OperationalModel, Track
from app.schemas.policy import Resource, Subject
from app.schemas.provenance import Provenance
from app.services.app_graph import (
    catalog_track,
    ensure_track_attached_operational_model,
    get_track_attached_operational_model,
)
from app.services.change_event import emit_change_event
from app.services.operational_model_merge import (
    merge_library_manifest_into_operational_model,
)
from app.services.permissions import get_user_node
from app.services.personal_workspace import ensure_personal_workspace
from app.services.policy_engine import evaluate as policy_evaluate

AGENT_SCRATCH_NAME = "Agent Scratch"  # matches package.name in app/packages/agent-scratch/operational-model.yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Graph-queryable discriminator stored on ``Track.kind`` for scratch tracks.
AGENT_SCRATCH_TRACK_KIND = "agent_scratch"

#: Cosmetic title for the auto-provisioned Track; users never see this string
#: unless they open the scratch Track in the UI (out of scope for v1).
AGENT_SCRATCH_TRACK_TITLE = "Agent Scratch"

# ---------------------------------------------------------------------------
# Per-process scratch-track resolution cache
# ---------------------------------------------------------------------------

# user_id -> scratch_track_id. Mutated under the asyncio single-thread
# scheduler; no lock needed. Cleared by tests via the
# ``_reset_scratch_cache`` autouse fixture.
_SCRATCH_TRACK_ID_CACHE: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _find_existing_scratch_track(
    user_id: str, workspace_id: str
) -> Optional[Track]:
    """Return the user's existing scratch Track in their personal workspace.

    Walks ``OWNS`` from the User node — the scratch Track is always owned
    by its user (mirrors the personal-workspace ownership idiom). Filters
    on ``kind == AGENT_SCRATCH_TRACK_KIND`` AND
    ``workspace_id == <personal_ws>`` so a stale Track lingering in a
    deleted workspace doesn't shadow a freshly-minted one.
    """
    user = await get_user_node(user_id)
    if user is None:
        return None
    try:
        owned = await user.nodes(edge=[OWNS], node=["Track"])
    except Exception:  # noqa: BLE001
        logger.exception("scratch-track lookup traversal failed for user %s", user_id)
        return None
    for tr in owned:
        if (
            getattr(tr, "kind", "") == AGENT_SCRATCH_TRACK_KIND
            and getattr(tr, "workspace_id", "") == workspace_id
        ):
            return tr
    return None


async def _resolve_agent_scratch_library_cp() -> Optional[OperationalModel]:
    """Find the seeded ``Agent Scratch`` library OperationalModel.

    Returns ``None`` if the seeding step has not yet run (e.g. a test that
    constructs the agent_scratch module without booting the app graph).
    In that case provision still completes, the Track just has the
    default empty CP instead of the four scratch entry types — the next
    request will see the package once seeding lands.
    """
    from app.services.core_seed_installer import resolve_core_package_library

    return await resolve_core_package_library(
        slug="agent-scratch",
        fallback_name=AGENT_SCRATCH_NAME,
    )


async def _soft_delete_embedding(entry_id: str) -> None:
    """Best-effort soft-delete of the embedding for ``entry_id``.

    Routes through ``app.services.retrieval.get_embedding_store``. When
    the ``null`` driver is active (Wave 2 of the SaaS-deployment plan —
    no vector store provisioned) the underlying soft_delete is a no-op,
    so this path is naturally cheap. Never raises — promote must not
    fail because retrieval cleanup is unavailable.
    """
    try:
        from app.services.retrieval import get_embedding_store
    except ImportError:
        logger.debug(
            "embedding_store unavailable; skipping soft-delete for %s", entry_id
        )
        return
    try:
        store = get_embedding_store()
        soft_delete = getattr(store, "soft_delete", None)
        if soft_delete is None:
            return
        result = soft_delete(entry_id)
        # Support both sync and async store implementations.
        if hasattr(result, "__await__"):
            await result
    except Exception:  # noqa: BLE001
        logger.warning(
            "best-effort embedding soft-delete failed for %s", entry_id, exc_info=True
        )


# ---------------------------------------------------------------------------
# provision_scratch_track — MEM-01 / MEM-02
# ---------------------------------------------------------------------------


async def provision_scratch_track(*, user_id: str) -> Track:
    """Return the caller's scratch Track, minting one on first call.

    Idempotent:

    - Cache hit → reload the cached Track, return if still
      ``kind == "agent_scratch"``; on stale hit, fall through to DB walk.
    - DB hit → cache the id, return.
    - Miss → mint a fresh Track in the user's personal workspace, attach
      the agent-scratch library CP, emit ``track.create``, cache, return.

    Raises :class:`ResourceNotFoundError` if ``user_id`` does not resolve
    to a User node (either by direct id or by ``AuthUser → User`` lookup).
    """
    # ----- Cache hit fast path -----
    cached_id = _SCRATCH_TRACK_ID_CACHE.get(user_id)
    if cached_id:
        cached = await Track.get(cached_id)
        if (
            cached is not None
            and getattr(cached, "kind", "") == AGENT_SCRATCH_TRACK_KIND
        ):
            return cached
        # Stale cache — fall through to DB lookup.
        _SCRATCH_TRACK_ID_CACHE.pop(user_id, None)

    # ----- Resolve User + personal workspace -----
    user = await get_user_node(user_id)
    if user is None:
        raise ResourceNotFoundError(message=f"User {user_id} not found")

    workspace = await ensure_personal_workspace(user)

    # ----- DB lookup for existing scratch Track -----
    existing = await _find_existing_scratch_track(
        user_id=user.id, workspace_id=workspace.id
    )
    if existing is not None:
        _SCRATCH_TRACK_ID_CACHE[user_id] = existing.id
        return existing

    # ----- Create branch — mint fresh scratch Track -----
    now = datetime.now(timezone.utc).isoformat()
    title = AGENT_SCRATCH_TRACK_TITLE
    track = await Track.create(
        title=title,
        title_fold=title.casefold(),
        owner_id=user.id,
        purpose="Per-user agent working memory (MEM-01).",
        icon="brain",
        visibility="inherit",
        workspace_id=workspace.id,
        kind=AGENT_SCRATCH_TRACK_KIND,
        created_at=now,
        updated_at=now,
    )

    # Ownership + workspace containment edges (mirror api/tracks.py::create_track
    # idioms). The Track lives in the personal workspace; there is no parent
    # App (single-parent rule satisfied via Workspace→CONTAINS→Track).
    await user.connect(track, edge=OWNS, role="owner", granted_at=now)
    await workspace.connect(track, edge=CONTAINS, added_at=now)

    # Catalog under the workspace's tracks branch registry.
    try:
        await catalog_track(track)
    except Exception:  # noqa: BLE001
        logger.exception("catalog_track failed for scratch track %s", track.id)

    # Ensure a default attached CP exists, then merge the agent-scratch
    # library on top. Using the same path as ``api/tracks.py::create_track``
    # with ``library_operational_model_id`` so EntryType/Tag/View nodes are
    # materialized identically.
    attached = await get_track_attached_operational_model(track)
    if attached is None:
        attached = await ensure_track_attached_operational_model(track)

    library = await _resolve_agent_scratch_library_cp()
    if library is not None and attached is not None:
        try:
            await merge_library_manifest_into_operational_model(
                library, attached, track, for_space=False
            )
            track.library_merge_source_id = library.id
            await track.save()
        except Exception:  # noqa: BLE001
            logger.exception(
                "agent-scratch library merge failed for track %s", track.id
            )

    # D-05 single-emission path. CREATE BRANCH ONLY — cache hits emit
    # nothing.
    try:
        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="track.create",
            resource_type="Track",
            resource_id=track.id,
            before=None,
            after={"id": track.id, "kind": track.kind, "title": track.title},
            scope=f"workspace:{workspace.id}",
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "track.create emission failed for scratch track %s",
            track.id,
            exc_info=True,
        )

    _SCRATCH_TRACK_ID_CACHE[user_id] = track.id
    return track


# ---------------------------------------------------------------------------
# promote_scratch_entry — MEM-03
# ---------------------------------------------------------------------------


async def promote_scratch_entry(
    *,
    user_id: str,
    entry_id: str,
    target_track_id: str,
) -> Entry:
    """Promote a scratch Entry to a domain Track (MEM-03).

    Permission gates (CONTEXT §Locked: promote operation):

    1. ``policy_engine.evaluate(entry.read on source.scope=track:<src>)``
    2. ``policy_engine.evaluate(entry.create on target.scope=track:<tgt>)``

    Either denial raises :class:`InsufficientPermissionsError`; the
    policy engine itself emits ``policy.deny`` ChangeEvents on denial
    (Phase 3 D-12 path) — no additional emission needed here.

    Provenance contract (MEM-03):

    - The new Entry's ``provenance.source`` is ``"agent"`` (the promote
      operation is agent-initiated, even when triggered by a human
      caller — the entry it carries forward is the agent's scratch work).
    - ``provenance.derived_from`` is a single-element list containing the
      source entry id. ``Provenance.derived_from`` is a ``List[str]`` per
      the schema, not an ``Optional[str]``.

    Source-archive contract (CONTEXT lock #2):

    - ``source.status = "archived"`` (no new field).
    - ``emit_change_event("entry.update")`` records the status flip.
    - Best-effort embedding soft-delete via the embedding-store module
      (silently skipped if Plan 04-01 has not yet landed).

    Raises :class:`ResourceNotFoundError` on missing source/target.
    """
    # ----- Resolve source + target Tracks (existence checks come BEFORE -----
    # ----- policy gates so 404 wins over 403 when the resource is gone) ----
    source = await Entry.get(entry_id)
    if source is None:
        raise ResourceNotFoundError(message="Source entry not found")

    target = await Track.get(target_track_id)
    if target is None:
        raise ResourceNotFoundError(message="Target track not found")

    source_track_id = getattr(source, "track_id", "") or ""

    # ----- Scratch-ownership gate (I-SCRATCH-01/02) -----
    # ``entry.read`` on the source is far too weak on its own: this
    # operation ARCHIVES the source, so gating only on read let any viewer
    # archive any readable entry from any track. The source MUST live in a
    # ``kind == "agent_scratch"`` Track and that Track MUST be the caller's
    # own (scratch tracks are per-user, owner_id = the User node id).
    source_track = await Track.get(source_track_id) if source_track_id else None
    caller = await get_user_node(user_id)
    caller_ids = {user_id}
    if caller is not None:
        caller_ids.add(caller.id)
        caller_ids.add(str(getattr(caller, "user_id", "") or ""))
    if (
        source_track is None
        or getattr(source_track, "kind", "") != AGENT_SCRATCH_TRACK_KIND
        or (getattr(source_track, "owner_id", None) or "") not in caller_ids
    ):
        raise InsufficientPermissionsError(
            message="Only entries in your own agent scratch track can be promoted"
        )

    # ----- Permission gate 1: read on source -----
    decision_read = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=source.id,
            scope=f"track:{source_track_id}",
        ),
    )
    if not decision_read.allowed:
        raise InsufficientPermissionsError(
            message="Cannot read source entry for promotion"
        )

    # ----- Permission gate 2: create on target -----
    decision_create = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.create",
        resource=Resource(
            kind="entry",
            id="",
            scope=f"track:{target_track_id}",
        ),
    )
    if not decision_create.allowed:
        raise InsufficientPermissionsError(
            message="Cannot create entry in target track"
        )

    # ----- Resolve / default the target EntryType -----
    target_entry_type: Optional[EntryType] = None
    found = await EntryType.find({"context.track_id": target_track_id})
    if not found:
        await ensure_track_attached_operational_model(target)
        found = await EntryType.find({"context.track_id": target_track_id})
    if found:
        # Prefer an EntryType matching the source's type by name; otherwise
        # take the first available.
        source_type_name = ""
        if source.type_id:
            src_type = await EntryType.get(source.type_id)
            if src_type is not None:
                source_type_name = str(getattr(src_type, "name", "") or "").lower()
        if source_type_name:
            target_entry_type = next(
                (
                    t
                    for t in found
                    if str(getattr(t, "name", "")).lower() == source_type_name
                ),
                None,
            )
        if target_entry_type is None:
            target_entry_type = found[0]
    if target_entry_type is None:
        raise ResourceNotFoundError(message="Target track has no entry types")

    # ----- Build the new-Entry provenance -----
    promoted_provenance = Provenance(
        source="agent",
        source_id=user_id,
        confidence=1.0,
        derived_from=[source.id],
        synced_at=datetime.now(timezone.utc),
    )

    # ----- Materialize the new Entry on the target Track -----
    now = datetime.now(timezone.utc).isoformat()
    new_entry = await Entry.create(
        type_id=target_entry_type.id,
        title=source.title or "Promoted entry",
        author_id=user_id,
        track_id=target_track_id,
        tags=list(source.tags or []),
        custom_fields=dict(source.custom_fields or {}),
        status="active",
        body=source.body or "",
        attachment_ids=list(source.attachment_ids or []),
        provenance=promoted_provenance,
        created_at=now,
        updated_at=now,
    )

    await target.connect(new_entry, edge=CONTAINS, added_at=now)
    await new_entry.connect(target_entry_type, edge=IS_OF_TYPE, assigned_at=now)

    # ----- Archive the source entry (CONTEXT lock #2 — status, not delete) -----
    before_source: Dict[str, Any] = {
        "id": source.id,
        "status": source.status,
        "track_id": source.track_id,
    }
    source.status = "archived"
    source.updated_at = now
    await source.save()

    # ----- Best-effort embedding cleanup (Plan 04-01 wave-1 caveat) -----
    await _soft_delete_embedding(source.id)

    # ----- D-05 single-emission: TWO ChangeEvents (target create + source archive) ----
    try:
        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="entry.create",
            resource_type="Entry",
            resource_id=new_entry.id,
            before=None,
            after={
                "id": new_entry.id,
                "title": new_entry.title,
                "track_id": new_entry.track_id,
                "provenance": {
                    "source": "agent",
                    "derived_from": [source.id],
                },
            },
            scope=f"track:{target_track_id}",
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "entry.create emission failed for promoted entry %s",
            new_entry.id,
            exc_info=True,
        )
    try:
        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="entry.update",
            resource_type="Entry",
            resource_id=source.id,
            before=before_source,
            after={
                "id": source.id,
                "status": "archived",
                "track_id": source.track_id,
            },
            scope=f"track:{source_track_id}",
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "entry.update emission failed for archived source %s",
            source.id,
            exc_info=True,
        )

    return new_entry


__all__ = [
    "AGENT_SCRATCH_TRACK_KIND",
    "AGENT_SCRATCH_TRACK_TITLE",
    "provision_scratch_track",
    "promote_scratch_entry",
]

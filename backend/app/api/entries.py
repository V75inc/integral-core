"""Entry CRUD API endpoints (access governed by track/app permissions only)."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.api.utils import (
    attach_author_exports,
    export_node,
    public_user_view,
    resolve_principal_id,
)
from app.models.edges import (
    CONTAINS,
    IS_OF_TYPE,
    MENTIONS,
    TAGGED_WITH,
    WATCHES,
)
from app.models.nodes import Entry, EntryType, Tag, Track
from app.schemas.policy import Resource, Subject
from app.services import notification_router
from app.services.app_graph import ensure_track_attached_content_profile
from app.services.change_event import emit_change_event
from app.services.content_moderation import validate_no_profanity
from app.services.content_profile_derived_fields import (
    resolve_derived_fields_for_entry,
)
from app.services.content_profile_runtime import (
    resolve_entry_type_spec,
    resolve_track_runtime_profile,
    sync_relation_edges,
    transition_custom_fields_on_type_change,
    validate_and_materialize_entry_custom_fields,
    validate_tags_apply_to_entry_type,
    validate_taxonomy_constraints,
)
from app.services.entry_comment_stats import (
    apply_prefetched_comment_count,
    attach_comment_count,
    prefetch_comment_counts,
)
from app.services.entry_context import (
    attach_backlinks,
    attach_track_and_space,
    prefetch_attachments_for_entries,
    prefetch_tags_for_entries,
    prefetch_tracks_and_spaces_for_entries,
)
from app.services.entry_listing import fetch_accessible_entries_page
from app.services.hooks.entry_save_runtime import (
    run_entry_save_hooks,
)
from app.services.mentions import resolve_mentions
from app.services.notification_paths import entry_path
from app.services.permissions import (
    ROLE_RANK,
    get_user_node,
    resolve_role,
)
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.request_scope import resolve_workspace_id_from_request
from app.services.retrieval import get_embedding_store, semantic_retrieval_available
from app.services.retrieval.embedding_model import embed_entry_text
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RET-02 — re-embed / soft-delete hooks.
#
# Wired immediately after every entry.create / entry.update / entry.delete
# ``emit_change_event`` call site (L371 / L547 / L600). Failure discipline
# mirrors ``policy_engine.evaluate``'s denial-emit path (L403-431): wrap in
# try/except Exception, log a WARNING, NEVER re-raise. A retrieval re-embed
# failure MUST NOT roll back the parent write — the embedding store is a
# projection of the graph, not a primary system-of-record.
#
# Tag-only mutations (``add_tag_to_entry`` / ``remove_tag_from_entry`` /
# ``add_reaction`` / ``remove_reaction``) DO NOT route here — embed input
# is ``title + body + string-valued custom_fields`` only (CONTEXT lock #4).
# ---------------------------------------------------------------------------


async def _dispatch_entry_mentions(
    *,
    entry: Entry,
    track: Track,
    text: str,
    actor_user_id: str,
    actor_user: Optional[Any],
    now: str,
) -> None:
    """Resolve + dispatch @mention notifications for an entry body / title.

    Best-effort — failures in resolution / MENTIONS edge write /
    notification dispatch are swallowed so the entry create / update
    parent path never rolls back on a mention-side failure. Mirrors
    the comment-side wiring in ``api/comments.py``.
    """
    actor_name = ""
    if actor_user:
        actor_name = (
            getattr(actor_user, "display_name", "")
            or getattr(actor_user, "email", "")
            or actor_user_id
        )
    try:
        mentioned_users = await resolve_mentions(
            text or "",
            exclude_user_id=actor_user_id,
            track_id=getattr(track, "id", None) or None,
        )
    except Exception:
        return
    for mentioned in mentioned_users:
        try:
            await entry.connect(mentioned, edge=MENTIONS, mentioned_at=now)
        except Exception:
            pass
        try:
            await notification_router.dispatch(
                user_id=mentioned.user_id or mentioned.id,
                kind="mention",
                payload={
                    "entry_id": entry.id,
                    "entry_title": entry.title or "",
                    "actor_id": actor_user_id,
                    "actor_name": actor_name,
                    "snippet": (text or "")[:280],
                    "track_id": track.id,
                },
                actor_id=actor_user_id,
                actor_kind="human",
                # Idempotency key includes ``now`` so a follow-up update
                # that adds new mentions does not dedupe against the
                # original create — every dispatch is a new notification
                # (router-side dedupe is per-key, not per-(user,kind)).
                idempotency_key=f"entry_mention:{entry.id}:{mentioned.id}:{now}",
            )
        except Exception:
            pass


async def _reembed_entry(entry: Entry) -> None:
    """Best-effort re-embed of ``entry``. NEVER rolls back the parent write.

    Skips entirely when ``semantic_retrieval_available()`` is False (the
    graph-only / null-store path — Wave 2 of the SaaS-deployment plan).
    The early-return avoids loading the embedding model on a deployment
    that has no vector store to write to.
    """

    if not semantic_retrieval_available():
        return

    try:
        vector = await embed_entry_text(entry)
        await get_embedding_store().upsert(
            entry_id=entry.id,
            vector=vector,
            metadata={
                "track_id": entry.track_id or "",
                "type_id": entry.type_id or "",
            },
        )
    except Exception as exc:  # noqa: BLE001 — see module-level comment.
        logger.warning("retrieval re-embed failed (entry write succeeded): %s", exc)


async def _soft_delete_embedding(entry_id: str) -> None:
    """Best-effort soft-delete of the embedding row keyed on ``entry_id``.

    Routes to ``EmbeddingStore.soft_delete`` (I-RET-03 — entry deletes
    NEVER hard-delete the embedding row; soft-delete preserves audit-log
    + agent-memory recall). NEVER rolls back the parent write.
    """

    try:
        await get_embedding_store().soft_delete(entry_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "retrieval embedding soft-delete failed " "(entry delete succeeded): %s",
            exc,
        )


def _slugify_entry_type_key(value: str) -> str:
    import re as _re

    s = str(value or "").strip().lower()
    s = _re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


async def _materialize_entry_types_from_tier(track: Track) -> List[EntryType]:
    """Compat shim — prefer ``materialize_entry_types_from_tier`` in services."""
    from app.services.entry_type_service import materialize_entry_types_from_tier

    return await materialize_entry_types_from_tier(track)


async def _apply_view_entry_type_filter(
    view_node: Any, entries: List[Entry]
) -> List[Entry]:
    """Filter entries to those whose EntryType slug is in ``view.entry_type_keys``.

    No-op when the view is unconstrained (empty ``entry_type_keys``). Resolution
    walks each entry's IS_OF_TYPE edge once via a track-scoped EntryType cache.
    """
    track_id = getattr(view_node, "track_id", "") or ""
    if not track_id:
        return entries
    track = await Track.get(track_id)
    if track is None:
        return entries

    # Hydrate legacy Views that pre-date entry_type_keys from the manifest
    # the first time they're queried. Idempotent + no-op when already set.
    if not (getattr(view_node, "entry_type_keys", None) or []):
        from app.services.content_profile_runtime import (
            backfill_view_entry_type_constraints_from_manifest,
        )

        await backfill_view_entry_type_constraints_from_manifest(track, [view_node])

    keys = list(getattr(view_node, "entry_type_keys", None) or [])
    if not keys or not entries:
        return entries
    allowed_keys = {k for k in (_slugify_entry_type_key(x) for x in keys) if k}
    if not allowed_keys:
        return entries

    cp = await ensure_track_attached_content_profile(track)
    if cp is None:
        return entries
    ets = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    et_id_to_key = {
        et.id: _slugify_entry_type_key(getattr(et, "name", ""))
        for et in ets
        if getattr(et, "id", None)
    }
    allowed_ids = {tid for tid, slug in et_id_to_key.items() if slug in allowed_keys}
    if not allowed_ids:
        return []

    return [e for e in entries if (getattr(e, "type_id", "") or "") in allowed_ids]


async def enrich_entry_page_for_response(
    page_entries: List[Entry],
) -> List[Dict[str, Any]]:
    """Export and hydrate one page of entries for list/feed responses."""
    track_by_id, space_by_track = await prefetch_tracks_and_spaces_for_entries(
        page_entries
    )
    entry_datas: List[Dict[str, Any]] = list(
        await asyncio.gather(*(export_node(e) for e in page_entries))
    )

    await attach_author_exports(entry_datas)

    tag_lookup = await prefetch_tags_for_entries(page_entries, entry_datas)
    comment_counts = await prefetch_comment_counts(page_entries)
    attachment_lookup = await prefetch_attachments_for_entries(page_entries)

    type_ids = list({e.type_id for e in page_entries if getattr(e, "type_id", None)})
    type_slug_by_id: Dict[str, str] = {}
    if type_ids:
        et_nodes = await EntryType.find({"id": {"$in": type_ids}})
        for et in et_nodes:
            tid_et = getattr(et, "id", None)
            if tid_et:
                type_slug_by_id[tid_et] = _slugify_entry_type_key(
                    getattr(et, "name", "") or ""
                )

    async def _enrich_one(e: Entry, ed: Dict[str, Any]) -> Dict[str, Any]:
        tid = (e.track_id or "").strip()
        tr = track_by_id.get(tid) if tid else None
        if tr is not None:
            sp = space_by_track.get(tid)
            await attach_track_and_space(
                ed,
                e,
                track=tr,
                prefetched_app_export=sp,
                use_prefetched_track_app=True,
                tag_lookup=tag_lookup,
                attachment_lookup=attachment_lookup,
            )
        else:
            await attach_track_and_space(
                ed,
                e,
                tag_lookup=tag_lookup,
                attachment_lookup=attachment_lookup,
            )
        apply_prefetched_comment_count(ed, e, comment_counts)
        et_id = getattr(e, "type_id", "") or ""
        if et_id and et_id in type_slug_by_id:
            ed["type"] = type_slug_by_id[et_id]
        return ed

    return list(
        await asyncio.gather(
            *(_enrich_one(e, ed) for e, ed in zip(page_entries, entry_datas))
        )
    )


@endpoint("/entries", methods=["GET"], auth=True, tags=["Entries"])
async def list_entries(
    request: Request,
    track_id: Optional[str] = None,
    view_id: Optional[str] = None,
    status: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 20,
    q: Optional[str] = None,
    include_total: bool = True,
) -> Dict[str, Any]:
    """List all entries accessible to the current user (cursor pagination).

    When ``view_id`` is provided, applies that view's filters and sort from its
    saved config before pagination.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        # Peer handlers raise here. These four used to return an empty
        # collection. auth=True means an authenticated request whose
        # principal still failed to resolve — surface it (see
        # test_list_endpoints_auth_envelope).
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    view_node = None
    if view_id:
        from app.models.nodes import View as ViewNode

        view_node = await ViewNode.get(view_id)

    page_entries, response = await fetch_accessible_entries_page(
        user_id,
        track_id=track_id,
        workspace_id=workspace_id,
        view_node=view_node,
        status=status,
        q=q,
        cursor=cursor,
        limit=limit,
        include_total=include_total,
    )
    response["entries"] = await enrich_entry_page_for_response(page_entries)
    return response


@endpoint("/entries", methods=["POST"], auth=True, tags=["Entries"])
async def create_entry(
    request: Request,
    track_id: str,
    title: str = "",
    type_id: str = "",
    body: Optional[str] = None,
    description: Optional[str] = None,
    attachment_ids: Optional[List[str]] = None,
    custom_fields: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a new entry in a track."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    # entry.create gates on parent track's edit permission (engine maps the
    # action via resource.scope=track:<id>; resource.id is the not-yet-created
    # entry placeholder per Plan 03-01 D-03).
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.create",
        resource=Resource(
            kind="entry",
            id="",
            scope=f"track:{track_id}",
        ),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")
    entry_type = await EntryType.get(type_id) if type_id else None
    if entry_type and entry_type.track_id and entry_type.track_id != track_id:
        raise InsufficientPermissionsError(
            message="Entry type does not belong to this track"
        )

    entry_body = body if body is not None else (description or "")
    workspace_id = await resolve_workspace_id_from_request(request, user_id)
    from app.services.entry_create import create_entry_in_track

    entry = await create_entry_in_track(
        track=track,
        user_id=user_id,
        title=title,
        body=entry_body,
        custom_fields=custom_fields,
        tags=tags,
        entry_type=entry_type,
        type_id=type_id or "",
        attachment_ids=attachment_ids,
        workspace_id=workspace_id or "",
        actor_kind=getattr(request.state, "actor_kind", "human") or "human",
    )

    entry_data = await export_node(entry)
    await attach_author_exports([entry_data])
    await attach_track_and_space(entry_data, entry, track=track)
    entry_data["comment_count"] = 0

    user = await get_user_node(user_id)
    try:
        from app.services.notification_router import notify_entry_watchers

        await notify_entry_watchers(
            entry_id=entry.id,
            action="create",
            actor_id=user_id,
            actor_kind=getattr(request.state, "actor_kind", "human"),
            idempotency_suffix=entry.created_at,
        )
    except Exception as exc:
        logger.warning(
            "Failed to trigger create notifications for entry %s watchers: %s",
            entry.id,
            exc,
        )

    # Phase 9 NOTIF-02 — fan out @mention notifications from entry
    # body + title (best-effort; failures swallowed). Resolution chain
    # at services/mentions.resolve_mentions: id → user_id →
    # display_name → email local-part. Self-mention suppressed.
    now = utc_now_iso()
    await _dispatch_entry_mentions(
        entry=entry,
        track=track,
        text=f"{title or ''}\n{entry_body}",
        actor_user_id=user_id,
        actor_user=user,
        now=now,
    )

    # RET-02 hook — best-effort re-embed (see _reembed_entry docstring).
    await _reembed_entry(entry)

    return {"entry": entry_data, "message": "Entry created successfully"}


@endpoint("/entries/{entry_id}", methods=["GET"], auth=True, tags=["Entries"])
async def get_entry(request: Request, entry_id: str) -> Dict[str, Any]:
    """Get a specific entry by ID."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(
            kind="entry",
            id=entry_id,
            scope=f"track:{entry.track_id or ''}",
        ),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")
    entry_data = await export_node(entry)
    await attach_author_exports([entry_data])
    await attach_track_and_space(entry_data, entry)
    await attach_comment_count(entry_data, entry)
    # Standard backlinks: anchor source entry + incoming REFERENCES. Always
    # present (None / [] when absent) so the frontend can rely on a stable
    # contract for the "Linked from" / "Referenced by" sections in EntryDetail.
    await attach_backlinks(entry_data, entry)
    # Plan 03 — Phase 4: surface ``expose_metadata`` projections from
    # any file/files custom fields. Read-time so the values stay
    # current with attachment metadata refreshes.
    derived = await resolve_derived_fields_for_entry(entry)
    if derived:
        entry_data["derived_fields"] = derived
    track_id = str(entry.track_id or "").strip()
    if track_id:
        entry_data["action_url"] = entry_path(entry_id, track_id)
    return {"entry": entry_data}


@endpoint("/entries/{entry_id}", methods=["PUT"], auth=True, tags=["Entries"])
async def update_entry(
    request: Request,
    entry_id: str,
    title: Optional[str] = None,
    body: Optional[str] = None,
    description: Optional[str] = None,
    attachment_ids: Optional[List[str]] = None,
    custom_fields: Optional[Dict[str, Any]] = None,
    status: Optional[str] = None,
    type_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    expected_record_revision: Optional[int] = None,
) -> Dict[str, Any]:
    """Update an entry."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")
    current_revision = int(getattr(entry, "record_revision", 1) or 1)
    if (
        expected_record_revision is not None
        and expected_record_revision != current_revision
    ):
        raise ResourceConflictError(
            message="Entry has changed since it was read",
            details={
                "error_code": "record_revision_conflict",
                "expected_record_revision": expected_record_revision,
                "current_record_revision": current_revision,
            },
        )

    # Phase 5 Plan 05-03 — mirror_only read-only gate (locked decision §Q10).
    # Refuse direct writes to connector-sourced entries when the bound
    # Connector's ``conflict_policy == "mirror_only"`` — the source system is
    # truth, local edits would be silently lost on the next sync tick.
    # Provenance shape uses ``source="connector"`` + ``source_id="<id>:<ext>"``
    # (I-CON-01 split). Best-effort: any failure to resolve the connector or
    # its subclass falls through to the normal write path (defensive — never
    # block writes on stale provenance).
    _prov = getattr(entry, "provenance", None)
    # jvspatial may hand back either a typed Provenance instance OR a bare
    # dict depending on the round-trip path.
    if _prov is not None:
        if isinstance(_prov, dict):
            _prov_source = _prov.get("source")
            _prov_source_id = _prov.get("source_id")
        else:
            _prov_source = getattr(_prov, "source", None)
            _prov_source_id = getattr(_prov, "source_id", None)
        if _prov_source == "connector" and _prov_source_id:
            connector_id = str(_prov_source_id).split(":", 1)[0]
            if connector_id:
                try:
                    from app.agentive.nodes import Connector as _Connector
                    from app.services.connectors.registry import (
                        get_sync_connector as _get_sync_connector,
                    )

                    _connector = await _Connector.get(connector_id)
                    _slug = (
                        getattr(_connector, "subclass_slug", "") or ""
                        if _connector is not None
                        else ""
                    )
                    if _connector is not None and _slug:
                        # I-CON-02 — subclass dispatch via subclass_slug.
                        _sub = _get_sync_connector(_slug.strip())
                        if getattr(_sub, "conflict_policy", "") == "mirror_only":
                            raise InsufficientPermissionsError(
                                message=(
                                    "Entry is sourced from a mirror_only "
                                    "connector — direct edits refused"
                                ),
                                details={"error_code": "read_only_via_connector"},
                            )
                except InsufficientPermissionsError:
                    raise
                except Exception:
                    # Unknown slug, missing connector, or import failure —
                    # fall through to the normal permission check so a
                    # misconfigured connector does not block ALL writes on
                    # entries that happen to carry a ``connector`` source.
                    pass

    track = await Track.get(entry.track_id) if entry.track_id else None
    if not track:
        raise ResourceNotFoundError(message="Track not found for entry")
    entry_type = await EntryType.get(entry.type_id) if entry.type_id else None
    if not entry_type:
        raise ResourceNotFoundError(message="Entry type not found for entry")

    if custom_fields is not None:
        from app.services.app_invariant_guards import enforce_protected_field_write

        et_key = _slugify_entry_type_key(
            str(
                (entry_type.form_schema or {}).get("_manifest_entry_type_key")
                or entry_type.name
                or ""
            )
        )
        ws_id = str(getattr(track, "workspace_id", "") or "")
        await enforce_protected_field_write(
            workspace_id=ws_id,
            entry_type_key=et_key,
            proposed_custom_fields=custom_fields,
        )

    prior_snapshot = await export_node(entry)  # D-03 before-snapshot
    ctx = await entry.get_context()

    type_changed = False
    old_entry_type = entry_type
    if type_id is not None and type_id != entry.type_id:
        new_entry_type = await EntryType.get(type_id)
        if not new_entry_type:
            raise ResourceNotFoundError(message="Entry type not found")
        if new_entry_type.track_id != track.id:
            raise InsufficientPermissionsError(
                message="Entry type does not belong to this track"
            )
        edges = await ctx.find_edges_between(
            entry.id, entry_type.id, edge_class=IS_OF_TYPE
        )
        for edge in edges:
            await edge.delete()
        entry.type_id = type_id
        await entry.connect(
            new_entry_type,
            edge=IS_OF_TYPE,
            assigned_at=utc_now_iso(),
        )
        entry_type = new_entry_type
        type_changed = True

    if title is not None:
        validate_no_profanity(title, "title")
        entry.title = title
    if body is not None:
        validate_no_profanity(body, "body")
        entry.body = body
    if description is not None:
        validate_no_profanity(description, "body")
        entry.body = description
    if attachment_ids is not None:
        entry.attachment_ids = attachment_ids
    if custom_fields is not None or type_changed:
        _, runtime_tier, _ = await resolve_track_runtime_profile(track)
        merged_custom_fields = (
            {**(entry.custom_fields or {}), **custom_fields}
            if custom_fields is not None
            else dict(entry.custom_fields or {})
        )
        if type_changed:
            merged_custom_fields = transition_custom_fields_on_type_change(
                merged_custom_fields,
                old_entry_type=old_entry_type,
                new_entry_type=entry_type,
                runtime_tier=runtime_tier,
            )
        (
            validated_custom_fields,
            relation_refs,
        ) = await validate_and_materialize_entry_custom_fields(
            track=track,
            entry_type=entry_type,
            custom_fields=merged_custom_fields,
            runtime_tier=runtime_tier,
            entry=entry,
            actor_user_id=user_id,
            actor_kind=getattr(request.state, "actor_kind", "human"),
            source_entry_title=getattr(entry, "title", "") or "",
        )
        entry.custom_fields = validated_custom_fields
        await sync_relation_edges(source_entry=entry, relation_refs=relation_refs)
        workspace_id = await resolve_workspace_id_from_request(request, user_id)
        await run_entry_save_hooks(
            entry=entry,
            workspace_id=workspace_id or "",
            actor_id=user_id,
            hook_point="entry.update",
        )
    if tags is not None:
        new_tag_set = list(dict.fromkeys(tags))
        _, runtime_tier, _ = await resolve_track_runtime_profile(track)
        entry_type_spec = resolve_entry_type_spec(entry_type, runtime_tier)
        await validate_taxonomy_constraints(
            track_id=track.id,
            tag_ids=new_tag_set,
            entry_type_spec=entry_type_spec,
            runtime_tier=runtime_tier,
        )
        await validate_tags_apply_to_entry_type(
            track_id=track.id,
            tag_ids=new_tag_set,
            entry_type=entry_type,
        )
        prev = list(entry.tags)
        to_remove = [t for t in prev if t not in new_tag_set]
        to_add = [t for t in new_tag_set if t not in prev]
        for tid in to_remove:
            tag = await Tag.get(tid)
            if tag:
                rm_edges = await ctx.find_edges_between(
                    entry.id, tag.id, edge_class=TAGGED_WITH
                )
                for e in rm_edges:
                    await e.delete()
        entry.tags = new_tag_set
        now = utc_now_iso()
        for tid in to_add:
            tag = await Tag.get(tid)
            if tag:
                await entry.connect(
                    tag, edge=TAGGED_WITH, tagged_at=now, tagged_by=user_id
                )
    if status is not None:
        entry.status = status

    entry.record_revision = current_revision + 1
    entry.updated_at = utc_now_iso()
    await entry.save()

    # Gated entry.transform auto-dispatch (e.g. opportunity stage=won → project).
    # Best-effort: never rolls back the parent update. Only when custom_fields
    # participated in this write — gates live on custom field values.
    if custom_fields is not None:
        try:
            from app.services.hooks.transform_runtime import (
                maybe_auto_transform_after_update,
            )

            ws_for_xform = getattr(track, "workspace_id", "") or ""
            await maybe_auto_transform_after_update(
                entry=entry,
                workspace_id=ws_for_xform,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "auto_transform after update failed for entry %s: %s",
                entry.id,
                exc,
            )

    try:
        from app.services.notification_router import notify_entry_watchers

        await notify_entry_watchers(
            entry_id=entry.id,
            action="update",
            actor_id=user_id,
            actor_kind=getattr(request.state, "actor_kind", "human"),
            idempotency_suffix=entry.updated_at,
        )
    except Exception as exc:
        logger.warning(
            "Failed to trigger update notifications for entry %s watchers: %s",
            entry.id,
            exc,
        )

    entry_data = await export_node(entry)
    await attach_author_exports([entry_data])
    await attach_track_and_space(entry_data, entry)
    await attach_comment_count(entry_data, entry)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        before=prior_snapshot,
        after=await export_node(entry),
        scope=f"track:{track.id}",
    )

    # Phase 9 NOTIF-02 — fan out @mention notifications on update too.
    # New mentions introduced by an edit are dispatched; previous-state
    # mentions that already fired on create are deduped via the
    # idempotency_key (router consults Notification.metadata.dispatched_to).
    actor_user = await get_user_node(user_id)
    update_now = utc_now_iso()
    await _dispatch_entry_mentions(
        entry=entry,
        track=track,
        text=f"{entry.title or ''}\n{entry.body or ''}",
        actor_user_id=user_id,
        actor_user=actor_user,
        now=update_now,
    )

    # RET-02 hook — best-effort re-embed (see _reembed_entry docstring).
    await _reembed_entry(entry)

    return {"entry": entry_data, "message": "Entry updated successfully"}


@endpoint("/entries/{entry_id}", methods=["DELETE"], auth=True, tags=["Entries"])
async def delete_entry(request: Request, entry_id: str) -> Dict[str, Any]:
    """Delete an entry."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.delete",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    prior_snapshot = await export_node(
        entry
    )  # D-03 before-snapshot (capture before destroy)
    track_id_for_scope = entry.track_id or ""
    # Use the targeted fast-delete helper instead of the default
    # ``entry.delete()``. The default kicks off jvspatial's
    # ``cascade=True`` graph-traversal cleanup, which on tagged entries
    # walks every co-tagged Entry and its neighbours one round-trip at
    # a time — measured at 10–30s on SQLite for entries with even
    # moderately popular tags. The fast helper deletes the entry and
    # its descendant comments explicitly, in O(edges + comments). See
    # app/services/entry_deletion.py for the rationale.
    from app.services.entry_deletion import delete_entry_fast

    await delete_entry_fast(
        entry,
        actor_user_id=user_id,
        actor_kind=getattr(request.state, "actor_kind", "human"),
    )

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="entry.delete",
        resource_type="Entry",
        resource_id=entry_id,
        before=prior_snapshot,
        after=None,
        scope=f"track:{track_id_for_scope}",
    )

    # RET-02 hook — soft-delete the embedding row (I-RET-03: NEVER hard-delete).
    await _soft_delete_embedding(entry_id)

    return {"message": "Entry deleted successfully", "deleted_entry_id": entry_id}


@endpoint("/entries/{entry_id}/tags", methods=["POST"], auth=True, tags=["Entries"])
async def add_tag_to_entry(
    request: Request,
    entry_id: str,
    tag_id: str,
) -> Dict[str, Any]:
    """Add a tag to an entry."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    tag = await Tag.get(tag_id)
    if not tag:
        raise ResourceNotFoundError(message="Tag not found")

    prior_snapshot = await export_node(entry)  # D-03 before-snapshot

    if tag_id not in entry.tags:
        track = await Track.get(entry.track_id) if entry.track_id else None
        entry_type = await EntryType.get(entry.type_id) if entry.type_id else None
        next_tags = list(entry.tags) + [tag_id]
        if track and entry_type:
            _, runtime_tier, _ = await resolve_track_runtime_profile(track)
            entry_type_spec = resolve_entry_type_spec(entry_type, runtime_tier)
            await validate_taxonomy_constraints(
                track_id=track.id,
                tag_ids=next_tags,
                entry_type_spec=entry_type_spec,
                runtime_tier=runtime_tier,
            )
            await validate_tags_apply_to_entry_type(
                track_id=track.id,
                tag_ids=next_tags,
                entry_type=entry_type,
            )
        entry.tags = next_tags
        await entry.save()

    await entry.connect(
        tag,
        edge=TAGGED_WITH,
        tagged_at=utc_now_iso(),
        tagged_by=user_id,
    )

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        before=prior_snapshot,
        after=await export_node(entry),
        scope=f"track:{entry.track_id or ''}",
    )

    return {"message": "Tag added to entry", "entry_id": entry_id, "tag_id": tag_id}


@endpoint(
    "/entries/{entry_id}/tags/{tag_id}", methods=["DELETE"], auth=True, tags=["Entries"]
)
async def remove_tag_from_entry(
    request: Request,
    entry_id: str,
    tag_id: str,
) -> Dict[str, Any]:
    """Remove a tag from an entry."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.update",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    tag = await Tag.get(tag_id)
    if not tag:
        raise ResourceNotFoundError(message="Tag not found")

    prior_snapshot = await export_node(entry)  # D-03 before-snapshot

    if tag_id in entry.tags:
        next_tags = [t for t in entry.tags if t != tag_id]
        track = await Track.get(entry.track_id) if entry.track_id else None
        entry_type = await EntryType.get(entry.type_id) if entry.type_id else None
        if track and entry_type:
            _, runtime_tier, _ = await resolve_track_runtime_profile(track)
            entry_type_spec = resolve_entry_type_spec(entry_type, runtime_tier)
            await validate_taxonomy_constraints(
                track_id=track.id,
                tag_ids=next_tags,
                entry_type_spec=entry_type_spec,
                runtime_tier=runtime_tier,
            )
        entry.tags.remove(tag_id)
        await entry.save()

    ctx = await entry.get_context()
    edges = await ctx.find_edges_between(entry.id, tag.id, edge_class=TAGGED_WITH)
    for edge in edges:
        await edge.delete()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        before=prior_snapshot,
        after=await export_node(entry),
        scope=f"track:{entry.track_id or ''}",
    )

    return {"message": "Tag removed from entry", "entry_id": entry_id, "tag_id": tag_id}


@endpoint(
    "/entries/{entry_id}/reactions", methods=["POST"], auth=True, tags=["Entries"]
)
async def add_reaction(
    request: Request,
    entry_id: str,
    emoji: str,
) -> Dict[str, Any]:
    """Add or toggle a reaction on an entry."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    # Substrate gate: reacting requires role ≥ commenter — viewer is
    # read-only by design (matches comment.create gate in api/comments.py).
    role = await resolve_role(user_id, "entry", entry_id)
    if role is None or ROLE_RANK.get(role, 0) < ROLE_RANK["commenter"]:
        raise InsufficientPermissionsError(
            message="You need commenter access or higher to react."
        )

    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    if not entry.reactions:
        entry.reactions = {}

    prior_snapshot = await export_node(entry)  # D-03 before-snapshot

    if emoji in entry.reactions:
        if user_id in entry.reactions[emoji]:
            entry.reactions[emoji].remove(user_id)
            if not entry.reactions[emoji]:
                del entry.reactions[emoji]
        else:
            entry.reactions[emoji].append(user_id)
    else:
        entry.reactions[emoji] = [user_id]

    await entry.save()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        before=prior_snapshot,
        after=await export_node(entry),
        scope=f"track:{entry.track_id or ''}",
    )

    return {
        "reactions": entry.reactions,
        "message": "Reaction updated",
        "entry_id": entry_id,
    }


@endpoint(
    "/entries/{entry_id}/reactions/{emoji}",
    methods=["DELETE"],
    auth=True,
    tags=["Entries"],
)
async def remove_reaction(
    request: Request,
    entry_id: str,
    emoji: str,
) -> Dict[str, Any]:
    """Remove a reaction from an entry."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    # Deliberately ONE RANK BELOW add_reaction's commenter gate, and not a
    # bug: the body below only ever removes THIS CALLER's id from the emoji
    # list, so the only user this admits that the commenter gate would not is
    # someone demoted commenter→viewer retracting a reaction they legitimately
    # placed — which should work. It cannot touch anyone else's reaction, and
    # an EXCLUDED_FROM user fails entry.read (can_view_entry honours the
    # edge), so there is no write this exposes that read does not already
    # gate. Pinned by test_reaction_gate_asymmetry_is_self_scoped.
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    prior_snapshot = await export_node(entry)  # D-03 before-snapshot

    if entry.reactions and emoji in entry.reactions:
        if user_id in entry.reactions[emoji]:
            entry.reactions[emoji].remove(user_id)
            if not entry.reactions[emoji]:
                del entry.reactions[emoji]
            await entry.save()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        before=prior_snapshot,
        after=await export_node(entry),
        scope=f"track:{entry.track_id or ''}",
    )

    return {
        "reactions": entry.reactions or {},
        "message": "Reaction removed",
        "entry_id": entry_id,
    }


@endpoint("/entries/{entry_id}/watchers", methods=["GET"], auth=True, tags=["Entries"])
async def get_entry_watchers(request: Request, entry_id: str) -> Dict[str, Any]:
    """Get the list of watchers for an entry."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    ctx = await entry.get_context()
    watches_edges = await ctx.find_edges_between(None, entry.id, edge_class=WATCHES)
    watcher_ids = {e.source for e in watches_edges}
    raw_watchers = await entry.nodes(edge=[WATCHES], direction="in", node=["User"])
    watchers = [w for w in raw_watchers if w.id in watcher_ids]

    user_node = await get_user_node(user_id)
    user_node_id = user_node.id if user_node else None

    watcher_exports = []
    is_watching = False
    for w in watchers:
        # Never full-export User in a list other users can read: a raw export
        # carries `preferences` (the email-verification OTP slot),
        # `notification_preferences` (phone_e164), `email_verified` and
        # `active_workspace_id`. The watcher UI reads only display_name,
        # avatar_url, avatar_attachment_id and id — all inside the allowlist —
        # so unlike the members list this needs no field restored.
        watcher_exports.append(await public_user_view(w))
        if user_node_id and w.id == user_node_id:
            is_watching = True

    return {
        "watchers": watcher_exports,
        "is_watching": is_watching,
        "count": len(watchers),
    }


@endpoint("/entries/{entry_id}/watch", methods=["POST"], auth=True, tags=["Entries"])
async def watch_entry(request: Request, entry_id: str) -> Dict[str, Any]:
    """Watch an entry for updates."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    user_node = await get_user_node(user_id)
    if not user_node:
        raise ResourceNotFoundError(message="User not found")

    from app.services.watchers import ensure_watch_edge

    await ensure_watch_edge(user_node, entry)

    return {"message": "Watching entry", "is_watching": True}


@endpoint("/entries/{entry_id}/unwatch", methods=["POST"], auth=True, tags=["Entries"])
async def unwatch_entry(request: Request, entry_id: str) -> Dict[str, Any]:
    """Stop watching an entry."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    entry = await Entry.get(entry_id)
    if not entry:
        raise ResourceNotFoundError(message="Entry not found")

    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="entry.read",
        resource=Resource(kind="entry", id=entry_id, scope=f"entry:{entry_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    user_node = await get_user_node(user_id)
    if not user_node:
        raise ResourceNotFoundError(message="User not found")

    ctx = await entry.get_context()
    existing_edges = await ctx.find_edges_between(
        user_node.id, entry.id, edge_class=WATCHES
    )
    for edge in existing_edges:
        await edge.delete()

    return {"message": "Stopped watching entry", "is_watching": False}

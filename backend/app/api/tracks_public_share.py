"""Public track sharing and unauthenticated operations (Plan 12-01).

POST /api/tracks/{track_id}/public-share
GET /api/tracks/{track_id}/public-share
GET /api/public-share/track/{token}
GET /api/public-share/track/{token}/entries
POST /api/public-share/track/{token}/entries
PATCH /api/public-share/track/{token}/entries/{entry_id}
GET /api/public-share/track/{token}/entries/{entry_id}/comments
POST /api/public-share/track/{token}/entries/{entry_id}/comments
"""

from __future__ import annotations

import logging
import secrets
from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.api.utils import attach_author_exports, export_node, resolve_principal_id
from app.api.views import _list_track_views, normalize_view_list_default_exports
from app.contracts.information import schema_revision_from_profile_version
from app.models.edges import CONTAINS, HAS_COMMENT
from app.models.nodes import (
    Comment,
    Entry,
    EntryType,
    ShareLink,
    Track,
    Workspace,
)
from app.schemas.policy import Resource, Subject
from app.schemas.shares import (
    PublicCommentCreateRequest,
    PublicEntryCreateRequest,
    PublicEntryUpdateRequest,
    PublicReactionCreateRequest,
    UpdatePublicTrackShareRequest,
)
from app.services.change_event import emit_change_event
from app.services.content_moderation import validate_no_profanity
from app.services.content_profile_runtime import (
    resolve_track_runtime_profile,
    validate_and_materialize_entry_custom_fields,
)
from app.services.entry_comment_stats import (
    apply_prefetched_comment_count,
    prefetch_comment_counts,
)
from app.services.entry_context import (
    attach_track_and_space,
    prefetch_tags_for_entries,
)
from app.services.entry_create import create_entry_in_track
from app.services.entry_type_service import materialize_entry_types_from_tier
from app.services.pagination import build_paginated_response, node_key
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.share_links import (
    _hash_token,
    _is_expired,
    _now_iso,
    link_intent,
    mint_share_link,
)
from app.services.track_public_share import declared_public_share
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


# Helper to normalize slug keys
def _slugify_key(value: str) -> str:
    import re as _re

    s = str(value or "").strip().lower()
    s = _re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


async def _track_has_active_public_share(track_id: str) -> bool:
    """True when the track has a non-revoked, non-expired public share link."""
    links = await ShareLink.find(
        {
            "context.resource_type": "track",
            "context.resource_id": track_id,
            "context.intent": "public",
        }
    )
    for link in links:
        if link.revoked_at:
            continue
        if _is_expired(link):
            continue
        return True
    return False


async def _resolve_public_track_link(token: str) -> ShareLink:
    if not token or len(token) < 16:
        raise ResourceNotFoundError(message="Shared track not found")
    token_hash = _hash_token(token)
    matches = await ShareLink.find({"context.token_hash": token_hash})
    link = None
    for candidate in matches:
        if secrets.compare_digest(str(candidate.token_hash or ""), token_hash):
            link = candidate
            break
    if link is None or link.resource_type != "track":
        raise ResourceNotFoundError(message="Shared track not found")
    if link_intent(link) != "public":
        raise ResourceNotFoundError(message="Shared track not found")
    if link.revoked_at or _is_expired(link):
        raise ResourceNotFoundError(message="Shared track not found")
    return link


# --- Authenticated settings endpoints ---


@endpoint(
    "/tracks/{track_id}/public-share", methods=["GET"], auth=True, tags=["Shares"]
)
async def get_public_track_share_settings(
    request: Request, track_id: str
) -> Dict[str, Any]:
    """Get the current public sharing settings for a track."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.share_link.mint",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="Only the track owner or admin may view public sharing settings."
        )

    links = await ShareLink.find(
        {
            "context.resource_type": "track",
            "context.resource_id": track_id,
        }
    )
    active_link = None
    for link in links:
        if (
            link_intent(link) == "public"
            and not link.revoked_at
            and not _is_expired(link)
        ):
            active_link = link
            break

    if active_link:
        return {
            "enabled": True,
            # Never re-serve plaintext; lookup is hash-only on the public path.
            "token": None,
            "public_permissions": active_link.public_permissions or {},
            "permissions_source": "link",
        }

    # No live link. If the App manifest declares public sharing for this track,
    # offer its permissions as the enable default — an app author who chose a
    # write-only intake set must not have it silently replaced by the UI's
    # generic read_entries:true default. Declaration is intent only; the token
    # is minted by the owner's explicit enable.
    declared = await declared_public_share(track)
    if declared:
        return {
            "enabled": False,
            "token": None,
            "public_permissions": declared.get("permissions") or {},
            "permissions_source": "manifest",
        }
    return {
        "enabled": False,
        "token": None,
        "public_permissions": {},
        "permissions_source": "default",
    }


@endpoint(
    "/tracks/{track_id}/public-share", methods=["POST"], auth=True, tags=["Shares"]
)
async def update_public_track_share_settings(
    request: Request, track_id: str
) -> Dict[str, Any]:
    """Update public sharing settings for a track."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.share_link.mint",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(
            message="Only the track owner or admin may manage public sharing."
        )

    raw = await request.json()
    req = UpdatePublicTrackShareRequest.model_validate(raw)

    links = await ShareLink.find(
        {
            "context.resource_type": "track",
            "context.resource_id": track_id,
        }
    )
    active_link = None
    for link in links:
        if (
            link_intent(link) == "public"
            and not link.revoked_at
            and not _is_expired(link)
        ):
            active_link = link
            break

    now_iso = _now_iso()
    before_snap = None
    if active_link:
        before_snap = await export_node(active_link)

    if req.enabled:
        if active_link:
            active_link.public_permissions = req.public_permissions
            # Clear any legacy plaintext; public resolve uses token_hash only.
            if getattr(active_link, "public_token", None):
                active_link.public_token = ""
            await active_link.save()
            link_obj = active_link
            # Existing links: token was shown once at mint; do not re-disclose.
            token = None
        else:
            minted = await mint_share_link(
                actor_user_id=user_id,
                resource_type="track",
                resource_id=track_id,
                role="viewer",
                expires_at=None,
                intent="public",
            )
            link_obj = await ShareLink.get(minted["share_link"]["id"])
            link_obj.public_permissions = req.public_permissions
            # Hash-only persist (mint_share_link already set token_hash).
            link_obj.public_token = ""
            await link_obj.save()
            token = minted["token"]

        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="track.share_link.mint",
            resource_type="ShareLink",
            resource_id=link_obj.id,
            before=before_snap,
            after=await export_node(link_obj),
            scope=f"track:{track_id}",
        )
        return {
            "enabled": True,
            "token": token,
            "public_permissions": link_obj.public_permissions,
            # Same key GET reports, so callers can read one shape from either.
            "permissions_source": "link",
        }
    else:
        # Disable public share
        if active_link:
            active_link.revoked_at = now_iso
            await active_link.save()
            await emit_change_event(
                actor_kind="human",
                actor_id=user_id,
                action="track.share_link.revoke",
                resource_type="ShareLink",
                resource_id=active_link.id,
                before=before_snap,
                after=await export_node(active_link),
                scope=f"track:{track_id}",
            )
        # Mirror what a follow-up GET would report: with the link revoked, any
        # manifest declaration becomes the relevant default again. Keeping the
        # two responses identical means a caller can refresh from either.
        declared = await declared_public_share(track)
        if declared:
            return {
                "enabled": False,
                "token": None,
                "public_permissions": declared.get("permissions") or {},
                "permissions_source": "manifest",
            }
        return {
            "enabled": False,
            "token": None,
            "public_permissions": {},
            "permissions_source": "default",
        }


# --- Unauthenticated public endpoints ---


@endpoint("/public-share/track/{token}", methods=["GET"], auth=False, tags=["Shares"])
async def get_public_track(request: Request, token: str) -> Dict[str, Any]:
    """Retrieve public track configuration details."""
    link = await _resolve_public_track_link(token)
    track = await Track.get(link.resource_id)
    if not track:
        raise ResourceNotFoundError(message="Shared track not found")

    perms = dict(link.public_permissions or {})
    can_read = perms.get("read_entries", True)

    track_data = {
        "id": track.id,
        "title": track.title,
        "purpose": track.purpose,
        "icon": track.icon,
        "accent_color": track.accent_color,
    }

    views_export = []
    if can_read:
        views = await _list_track_views(track)
        views_export = [await export_node(v) for v in views]
        winner_id = next((v.id for v in views if getattr(v, "is_default", False)), None)
        views_export = normalize_view_list_default_exports(views_export, winner_id)

    # Always return EntryTypes so a client can render submit forms or labels.
    # Materialize (write) only when the share allows create/update — otherwise
    # an unauthenticated GET would create nodes / merge schemas (write-on-read).
    # Read-only shares still see already-persisted types; create/update shares
    # materialize so the client receives real type ids before POST.
    if perms.get("create_entries") or perms.get("update_entries"):
        await materialize_entry_types_from_tier(track)
    entry_types = await EntryType.find({"context.track_id": track.id})
    entry_types_export = [await export_node(et) for et in entry_types]

    workspace_data = None
    if getattr(track, "workspace_id", None):
        workspace = await Workspace.get(track.workspace_id)
        if workspace:
            workspace_data = {
                "id": workspace.id,
                "name": workspace.name,
                "accent_color": workspace.accent_color,
                "avatar_url": workspace.avatar_url,
            }

    return {
        "track": track_data,
        "workspace": workspace_data,
        "public_permissions": perms,
        "views": views_export,
        "entry_types": entry_types_export,
    }


@endpoint(
    "/public-share/track/{token}/entries", methods=["GET"], auth=False, tags=["Shares"]
)
async def get_public_track_entries(
    request: Request,
    token: str,
    cursor: Optional[str] = None,
    limit: int = 20,
    q: Optional[str] = None,
) -> Dict[str, Any]:
    """List track entries publicly."""
    link = await _resolve_public_track_link(token)
    perms = dict(link.public_permissions or {})
    if not perms.get("read_entries", True):
        raise InsufficientPermissionsError(message="Public browsing is disabled.")

    track = await Track.get(link.resource_id)
    if not track:
        raise ResourceNotFoundError(message="Shared track not found")

    # Fetch all entries in the track
    entries = await Entry.find({"context.track_id": track.id})
    entries = [e for e in entries if e.status == "active"]

    from app.services.entry_search import filter_entries_by_query

    entries = filter_entries_by_query(entries, q)
    entries.sort(key=lambda e: e.updated_at or e.created_at or "", reverse=True)

    page_entries, response = build_paginated_response(entries, cursor, limit, node_key)
    entry_datas = [await export_node(e) for e in page_entries]
    await attach_author_exports(entry_datas)

    tag_lookup = await prefetch_tags_for_entries(page_entries, entry_datas)
    comment_counts = await prefetch_comment_counts(page_entries)

    type_ids = list({e.type_id for e in page_entries if getattr(e, "type_id", None)})
    type_slug_by_id: Dict[str, str] = {}
    if type_ids:
        et_nodes = await EntryType.find({"id": {"$in": type_ids}})
        for et in et_nodes:
            tid_et = getattr(et, "id", None)
            if tid_et:
                type_slug_by_id[tid_et] = _slugify_key(getattr(et, "name", "") or "")

    enriched = []
    for e, ed in zip(page_entries, entry_datas):
        await attach_track_and_space(ed, e, track=track, tag_lookup=tag_lookup)
        apply_prefetched_comment_count(ed, e, comment_counts)
        et_id = getattr(e, "type_id", "") or ""
        if et_id and et_id in type_slug_by_id:
            ed["type"] = type_slug_by_id[et_id]
        enriched.append(ed)

    response["entries"] = enriched
    response["track_id"] = track.id
    return response


@endpoint(
    "/public-share/track/{token}/relation-options",
    methods=["GET"],
    auth=False,
    tags=["Shares"],
)
async def get_public_track_relation_options(
    request: Request, token: str, entry_type_id: str, field_key: str
) -> Dict[str, Any]:
    """Candidate targets for a relation field on the public entry-create form.

    The public entries list only covers THIS shared track, so a relation field
    that targets a sibling track (``allow_cross_track`` + ``target_track_types``)
    can never resolve any candidates from that endpoint alone — a required
    cross-track relation field would always show zero options and block
    submission. Anonymous visitors get exactly the candidate pool the App
    author already declared valid for this relation (mirrors the same
    ``target_entry_types``/``target_track_types``/``allow_cross_track`` contract
    ``_validate_relation_values`` enforces on submit) — scoped to sibling tracks
    under the SAME App as the shared track, never the wider workspace.
    """
    link = await _resolve_public_track_link(token)
    perms = dict(link.public_permissions or {})
    if not perms.get("create_entries") and not perms.get("update_entries"):
        raise InsufficientPermissionsError(message="Public submission is disabled.")

    track = await Track.get(link.resource_id)
    if not track:
        raise ResourceNotFoundError(message="Shared track not found")

    entry_type = await EntryType.get(entry_type_id)
    if not entry_type or entry_type.track_id != track.id:
        raise ResourceNotFoundError(message="Entry type not found on this track")

    fields = (entry_type.form_schema or {}).get("fields") or []
    field = next((f for f in fields if f.get("key") == field_key), None)
    if not field or field.get("type") != "relation":
        raise ResourceNotFoundError(message="Relation field not found")

    relation = field.get("relation") or {}
    if str(relation.get("target") or "entry") != "entry":
        return {"targets": []}

    target_entry_types = {
        _slugify_key(x) for x in (relation.get("target_entry_types") or [])
    }
    target_track_types = {
        _slugify_key(x) for x in (relation.get("target_track_types") or [])
    }
    allow_cross_track = bool(relation.get("allow_cross_track", False))

    candidate_track_ids = [track.id]
    if allow_cross_track and target_track_types:
        apps = await track.nodes(edge=[CONTAINS], direction="in", node=["WorkspaceApp"])
        sibling_ids = set()
        for app_node in apps:
            siblings = await app_node.nodes(edge=[CONTAINS], node=["Track"])
            for sibling in siblings:
                if sibling.id == track.id:
                    continue
                tkey = _slugify_key(
                    str(
                        getattr(sibling, "template_id", "")
                        or getattr(sibling, "title", "")
                    )
                )
                if tkey in target_track_types and await _track_has_active_public_share(
                    sibling.id
                ):
                    sibling_ids.add(sibling.id)
        candidate_track_ids = [track.id, *sorted(sibling_ids)]

    if not candidate_track_ids:
        return {"targets": []}

    candidate_tracks = await Track.find({"id": {"$in": candidate_track_ids}})
    track_titles = {t.id: (t.title or "") for t in candidate_tracks}

    candidates = await Entry.find({"context.track_id": {"$in": candidate_track_ids}})
    targets: List[Dict[str, Any]] = []
    for e in candidates[:200]:
        if target_entry_types:
            et = await EntryType.get(e.type_id) if e.type_id else None
            if (
                not et
                or _slugify_key(str(getattr(et, "name", ""))) not in target_entry_types
            ):
                continue
        targets.append(
            {
                "id": e.id,
                "title": e.title or e.id,
                "track_title": track_titles.get(e.track_id or "", ""),
            }
        )
    return {"targets": targets}


@endpoint(
    "/public-share/track/{token}/entries", methods=["POST"], auth=False, tags=["Shares"]
)
async def create_public_track_entry(request: Request, token: str) -> Dict[str, Any]:
    """Create an entry on a shared track publicly."""
    link = await _resolve_public_track_link(token)
    perms = dict(link.public_permissions or {})
    if not perms.get("create_entries"):
        raise InsufficientPermissionsError(message="Public submission is disabled.")

    track = await Track.get(link.resource_id)
    if not track:
        raise ResourceNotFoundError(message="Shared track not found")

    raw = await request.json()
    req = PublicEntryCreateRequest.model_validate(raw)

    # Materialize before resolve so anchored/by-reference tracks expose types
    # that live only in the manifest tier (same as get_public_track).
    await materialize_entry_types_from_tier(track)
    entry_type = await EntryType.get(req.type_id)
    if not entry_type or entry_type.track_id != track.id:
        raise ResourceNotFoundError(message="Entry type not found on this track")

    entry = await create_entry_in_track(
        track=track,
        user_id="public",
        title=req.title,
        body=req.body or "",
        custom_fields=req.custom_fields or {},
        entry_type=entry_type,
        workspace_id=getattr(track, "workspace_id", "") or "",
        actor_kind="human",
        skip_profanity=False,
    )

    from app.api.entries import _reembed_entry

    await _reembed_entry(entry)

    entry_data = await export_node(entry)
    entry_data["comment_count"] = 0
    return {"entry": entry_data, "message": "Entry created successfully"}


@endpoint(
    "/public-share/track/{token}/entries/{entry_id}",
    methods=["PATCH"],
    auth=False,
    tags=["Shares"],
)
async def update_public_track_entry(
    request: Request, token: str, entry_id: str
) -> Dict[str, Any]:
    """Update an entry on a shared track publicly."""
    link = await _resolve_public_track_link(token)
    perms = dict(link.public_permissions or {})
    if not perms.get("update_entries"):
        raise InsufficientPermissionsError(message="Public updating is disabled.")

    track = await Track.get(link.resource_id)
    if not track:
        raise ResourceNotFoundError(message="Shared track not found")

    entry = await Entry.get(entry_id)
    if not entry or entry.track_id != track.id:
        raise ResourceNotFoundError(message="Entry not found on this track")

    raw = await request.json()
    req = PublicEntryUpdateRequest.model_validate(raw)

    current_record_revision = int(getattr(entry, "record_revision", 1) or 1)
    if (
        req.expected_record_revision is not None
        and req.expected_record_revision != current_record_revision
    ):
        raise ResourceConflictError(
            message="Entry has changed since it was read",
            details={
                "error_code": "record_revision_conflict",
                "expected_record_revision": req.expected_record_revision,
                "current_record_revision": current_record_revision,
            },
        )
    content_profile, _, _ = await resolve_track_runtime_profile(track)
    current_schema_revision = schema_revision_from_profile_version(
        getattr(content_profile, "version_number", None)
    )
    if (
        req.expected_schema_revision is not None
        and req.expected_schema_revision != current_schema_revision
    ):
        raise ResourceConflictError(
            message="Entry schema has changed since it was read",
            details={
                "error_code": "schema_revision_conflict",
                "expected_schema_revision": req.expected_schema_revision,
                "current_schema_revision": current_schema_revision,
            },
        )

    prior_snapshot = await export_node(entry)

    entry_type = await EntryType.get(entry.type_id)
    if not entry_type:
        raise ResourceNotFoundError(message="Entry type not found")

    if req.custom_fields is not None:
        from app.services.app_invariant_guards import enforce_protected_field_write
        from app.services.content_profile_compile import slug_manifest_key

        entry_type_key = slug_manifest_key(
            str(
                (entry_type.form_schema or {}).get("_manifest_entry_type_key")
                or entry_type.name
                or ""
            )
        )
        await enforce_protected_field_write(
            workspace_id=str(getattr(track, "workspace_id", "") or ""),
            entry_type_key=entry_type_key,
            proposed_custom_fields=req.custom_fields,
        )

    if req.title is not None:
        validate_no_profanity(req.title, "title")
        entry.title = req.title
    if req.body is not None:
        validate_no_profanity(req.body, "body")
        entry.body = req.body
    if req.status is not None:
        entry.status = req.status

    if req.custom_fields is not None:
        _, runtime_tier, _ = await resolve_track_runtime_profile(track)
        merged_cfs = {**(entry.custom_fields or {}), **req.custom_fields}
        validated_cfs, relation_refs = (
            await validate_and_materialize_entry_custom_fields(
                track=track,
                entry_type=entry_type,
                custom_fields=merged_cfs,
                runtime_tier=runtime_tier,
                entry=entry,
                actor_user_id="public",
                actor_kind="human",
                source_entry_title=entry.title,
            )
        )
        entry.custom_fields = validated_cfs
        from app.services.content_profile_runtime import sync_relation_edges

        await sync_relation_edges(source_entry=entry, relation_refs=relation_refs)

    entry.record_revision = current_record_revision + 1
    entry.schema_revision = current_schema_revision
    entry.updated_at = utc_now_iso()
    await entry.save()

    from app.services.hooks.entry_save_runtime import run_entry_save_hooks

    await run_entry_save_hooks(
        entry=entry,
        workspace_id=getattr(track, "workspace_id", "") or "",
        actor_id="public",
        hook_point="entry.update",
    )

    from app.api.entries import _reembed_entry

    await _reembed_entry(entry)

    await emit_change_event(
        actor_kind="human",
        actor_id="public",
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        before=prior_snapshot,
        after=await export_node(entry),
        scope=f"track:{track.id}",
    )

    return {"entry": await export_node(entry), "message": "Entry updated successfully"}


@endpoint(
    "/public-share/track/{token}/entries/{entry_id}/comments",
    methods=["GET"],
    auth=False,
    tags=["Shares"],
)
async def get_public_track_entry_comments(
    request: Request, token: str, entry_id: str
) -> Dict[str, Any]:
    """Retrieve comments for a shared entry publicly."""
    link = await _resolve_public_track_link(token)
    perms = dict(link.public_permissions or {})
    if not perms.get("read_comments"):
        raise InsufficientPermissionsError(message="Reading comments is disabled.")

    track = await Track.get(link.resource_id)
    if not track:
        raise ResourceNotFoundError(message="Shared track not found")

    entry = await Entry.get(entry_id)
    if not entry or entry.track_id != track.id:
        raise ResourceNotFoundError(message="Entry not found on this track")

    comments = await entry.nodes(edge=["HAS_COMMENT"], node=["Comment"])
    items = [await export_node(c) for c in comments]
    await attach_author_exports(items)
    items.sort(key=lambda x: (x.get("created_at") or "", x.get("id") or ""))

    return {"comments": items, "total": len(items), "entry_id": entry_id}


@endpoint(
    "/public-share/track/{token}/entries/{entry_id}/comments",
    methods=["POST"],
    auth=False,
    tags=["Shares"],
)
async def create_public_track_entry_comment(
    request: Request, token: str, entry_id: str
) -> Dict[str, Any]:
    """Post a comment on a shared entry publicly."""
    link = await _resolve_public_track_link(token)
    perms = dict(link.public_permissions or {})
    if not perms.get("create_comments"):
        raise InsufficientPermissionsError(message="Posting comments is disabled.")

    track = await Track.get(link.resource_id)
    if not track:
        raise ResourceNotFoundError(message="Shared track not found")

    entry = await Entry.get(entry_id)
    if not entry or entry.track_id != track.id:
        raise ResourceNotFoundError(message="Entry not found on this track")

    raw = await request.json()
    req = PublicCommentCreateRequest.model_validate(raw)

    validate_no_profanity(req.text, "comment")

    now = utc_now_iso()
    comment = await Comment.create(
        author_id="public",
        parent_id=None,
        text=req.text,
        created_at=now,
        updated_at=now,
    )

    await entry.connect(comment, edge=HAS_COMMENT, created_at=now)

    comment_snapshot = await export_node(comment)
    comment_snapshot["_entry_id"] = entry_id
    comment_snapshot["_entry_title"] = entry.title or ""

    await emit_change_event(
        actor_kind="human",
        actor_id="public",
        action="comment.create",
        resource_type="Comment",
        resource_id=comment.id,
        before=None,
        after=comment_snapshot,
        scope=f"track:{track.id}",
    )

    comment_export = await export_node(comment)
    await attach_author_exports([comment_export])
    return {
        "comment": comment_export,
        "message": "Comment created successfully",
    }


@endpoint(
    "/public-share/track/{token}/entries/{entry_id}/reactions",
    methods=["POST"],
    auth=False,
    tags=["Shares"],
)
async def add_public_track_entry_reaction(
    request: Request, token: str, entry_id: str
) -> Dict[str, Any]:
    """Add or toggle a reaction on a shared entry publicly."""
    link = await _resolve_public_track_link(token)
    perms = dict(link.public_permissions or {})
    if not perms.get("create_comments"):
        raise InsufficientPermissionsError(message="Reacting is disabled.")

    track = await Track.get(link.resource_id)
    if not track:
        raise ResourceNotFoundError(message="Shared track not found")

    entry = await Entry.get(entry_id)
    if not entry or entry.track_id != track.id:
        raise ResourceNotFoundError(message="Entry not found on this track")

    raw = await request.json()
    req = PublicReactionCreateRequest.model_validate(raw)
    emoji = req.emoji

    if not entry.reactions:
        entry.reactions = {}

    prior_snapshot = await export_node(entry)

    user_id = "public"
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

    await emit_change_event(
        actor_kind="human",
        actor_id="public",
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        before=prior_snapshot,
        after=await export_node(entry),
        scope=f"track:{track.id}",
    )

    return {
        "reactions": entry.reactions or {},
        "message": "Reaction updated",
        "entry_id": entry_id,
    }


@endpoint(
    "/public-share/track/{token}/entries/{entry_id}/reactions/{emoji}",
    methods=["DELETE"],
    auth=False,
    tags=["Shares"],
)
async def remove_public_track_entry_reaction(
    request: Request, token: str, entry_id: str, emoji: str
) -> Dict[str, Any]:
    """Remove a reaction from a shared entry publicly."""
    link = await _resolve_public_track_link(token)
    perms = dict(link.public_permissions or {})
    if not perms.get("create_comments"):
        raise InsufficientPermissionsError(message="Reacting is disabled.")

    track = await Track.get(link.resource_id)
    if not track:
        raise ResourceNotFoundError(message="Shared track not found")

    entry = await Entry.get(entry_id)
    if not entry or entry.track_id != track.id:
        raise ResourceNotFoundError(message="Entry not found on this track")

    prior_snapshot = await export_node(entry)

    user_id = "public"
    if entry.reactions and emoji in entry.reactions:
        if user_id in entry.reactions[emoji]:
            entry.reactions[emoji].remove(user_id)
            if not entry.reactions[emoji]:
                del entry.reactions[emoji]
            await entry.save()

    await emit_change_event(
        actor_kind="human",
        actor_id="public",
        action="entry.update",
        resource_type="Entry",
        resource_id=entry.id,
        before=prior_snapshot,
        after=await export_node(entry),
        scope=f"track:{track.id}",
    )

    return {
        "reactions": entry.reactions or {},
        "message": "Reaction removed",
        "entry_id": entry_id,
    }

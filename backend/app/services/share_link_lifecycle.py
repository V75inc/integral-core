"""Atomic source-aware redemption and independent link/source revocation."""

import secrets
from datetime import datetime, timezone

from jvspatial.core.context import get_default_context

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.exceptions import AuthorizationUnavailableError
from app.models.access_lifecycle import SHARE_GRANT
from app.models.edges import HAS_SHARE_LINK
from app.models.nodes import ShareLink
from app.services.authority_facts import primary_node_record
from app.services.identity_admission import resolve_current_profile
from app.services.membership_write_scope import membership_write_scope
from app.services.permissions import get_user_node, resolve_role
from app.services.permissions_process_cache import invalidate_user_aliases
from app.services.resource_location import locate_resource
from app.services.share_grant_sources import share_grant_row, validate_share_grant
from app.services.workspace_membership import _deliver
from app.services.workspace_permissions import read_member_facts
from app.services.workspace_suspension import require_unsuspended
from app.utils.time import utc_now_iso


async def current_link(link_id: str) -> ShareLink:
    """Read exact immutable scope and rooted attachment, without permissive defaults."""
    database = get_default_context().database
    row = await primary_node_record(database, ShareLink, link_id)
    if row is None:
        raise ResourceNotFoundError(message="Share link not found")
    data = row["context"]
    for key, value in data.items():
        if key in row and (type(row[key]) is not type(value) or row[key] != value):
            raise AuthorizationUnavailableError()
    if (
        data.get("intent") not in {"collaborator", "public"}
        or data.get("resource_type") not in {"app", "track", "entry"}
        or data.get("role") not in {"admin", "editor", "commenter", "viewer"}
        or any(
            type(data.get(k)) is not int or data[k] < 0
            for k in ("redemptions", "max_redemptions")
        )
        or any(
            not isinstance(data.get(k), str) or not data[k]
            for k in ("workspace_id", "resource_id", "created_by", "token_hash")
        )
    ):
        raise AuthorizationUnavailableError()
    for key in ("revoked_at", "expires_at"):
        value = data.get(key)
        if value is not None:
            try:
                if (
                    not isinstance(value, str)
                    or datetime.fromisoformat(value).tzinfo is None
                ):
                    raise ValueError("Invalid timestamp")
            except ValueError as exc:
                raise AuthorizationUnavailableError() from exc
    links = await database.find(
        "edge", {"entity": HAS_SHARE_LINK.__name__, "target": link_id}, limit=2
    )
    if len(links) != 1 or links[0].get("source") != data["resource_id"]:
        raise AuthorizationUnavailableError()
    from app.services.resource_location import MANAGEMENT_APP_STATES

    location = await locate_resource(
        data["resource_type"],
        data["resource_id"],
        allowed_app_states=MANAGEMENT_APP_STATES,
    )
    if location is None or location.workspace_id != data["workspace_id"]:
        raise AuthorizationUnavailableError()
    link = await ShareLink.find_one({"id": link_id})
    if link is None:
        raise AuthorizationUnavailableError()
    return link


async def _event(
    link: ShareLink,
    actor_id: str,
    action: str,
    subject_id: str,
    revision: int,
    before,
    after,
) -> str | None:
    """Persist primary audit intent with the source transition."""
    from app.services.app_operations.event_outbox import insert_revision_event
    from app.services.change_event import emit_change_event
    from app.services.entry_write_scope import is_postgres_entry_transaction

    database = get_default_context().database
    event = {
        "actor_kind": "human",
        "actor_id": actor_id,
        "action": action,
        "resource_type": link.resource_type.capitalize(),
        "resource_id": link.resource_id,
        "scope": f"{link.resource_type}:{link.resource_id}",
        "before": before,
        "after": after,
    }
    if is_postgres_entry_transaction(database):
        return await insert_revision_event(
            transaction=database,
            workspace_id=link.workspace_id,
            namespace="share-link-source-v1",
            subject_id=subject_id,
            revision=revision,
            event=event,
        )
    await emit_change_event(**event)
    return None


async def redeem_source_link(actor_user_id: str, plaintext_token: str) -> dict:
    """Each recipient/link lifetime consumes capacity once; old sources cannot revive."""
    from app.services.share_links import _hash_token, _reserve_redemption
    from app.services.sharing import ensure_guest_membership

    if not actor_user_id or type(plaintext_token) is not str or not plaintext_token:
        raise BadRequestError(message="Authenticated token is required")
    token_hash = _hash_token(plaintext_token)
    database = get_default_context().database
    rows = await database.find(
        "node",
        {"entity": ShareLink._entity_name(), "context.token_hash": token_hash},
        limit=2,
    )
    if not rows:
        raise ResourceNotFoundError(message="Invalid share link")
    if len(rows) != 1:
        raise AuthorizationUnavailableError()
    preflight = await current_link(rows[0]["id"])
    outbox = None
    async with membership_write_scope(
        preflight.workspace_id,
        principal_ids=(actor_user_id, preflight.created_by),
        active_principal_ids=(actor_user_id,),
    ):
        link = await current_link(preflight.id)
        if (
            link.workspace_id != preflight.workspace_id
            or link.created_by != preflight.created_by
        ):
            raise AuthorizationUnavailableError()
        if link.intent != "collaborator" or not secrets.compare_digest(
            link.token_hash, token_hash
        ):
            raise BadRequestError(message="This link cannot grant signed-in access")
        recipient = await get_user_node(actor_user_id)
        if recipient is None:
            raise InsufficientPermissionsError(message="Access denied")
        await require_unsuspended(recipient.id, link.workspace_id)
        from app.services.resource_access_blocks import require_unblocked

        await require_unblocked(recipient.id, link.resource_type, link.resource_id)
        from app.services.sharing import _require_policy_allowed

        await _require_policy_allowed(
            recipient.id,
            f"{link.resource_type}.share_link.redeem",
            link.resource_type,
            link.resource_id,
        )
        old = await share_grant_row(link, recipient.id)
        membership = await read_member_facts(recipient, link.workspace_id)
        if old:
            values = validate_share_grant(old, link, recipient.id)
            if (
                values["state"] != "active"
                or membership is None
                or membership["id"] != values["membership_id"]
            ):
                raise ResourceConflictError(
                    message="This source grant was revoked or its membership lifetime ended"
                )
            outcome = "unchanged"
            grant = old
        else:
            if link.revoked_at or (
                link.expires_at
                and datetime.fromisoformat(link.expires_at)
                <= datetime.now(timezone.utc)
            ):
                raise ResourceConflictError(message="Share link disabled or expired")
            from app.services.sharing import _require_share_authority

            await _require_share_authority(
                link.created_by,
                link.resource_type,
                link.resource_id,
                policy_action=f"{link.resource_type}.share_link.mint",
            )
            if not await _reserve_redemption(link):
                raise ResourceConflictError(
                    message="Share link redemption capacity exhausted"
                )
            await ensure_guest_membership(
                recipient,
                link.workspace_id,
                inviter_user_id=link.created_by,
                source_label=f"share_link:{link.id}",
            )
            membership = await read_member_facts(recipient, link.workspace_id)
            if membership is None:
                raise AuthorizationUnavailableError()
            now = utc_now_iso()
            await link.connect(
                recipient,
                edge=SHARE_GRANT,
                role=link.role,
                workspace_id=link.workspace_id,
                resource_type=link.resource_type,
                resource_id=link.resource_id,
                state="active",
                redeemed_at=now,
                changed_at=now,
                revision=1,
                membership_id=membership["id"],
            )
            grant = await share_grant_row(link, recipient.id)
            if grant is None:
                raise AuthorizationUnavailableError()
            outcome = "created"
            outbox = await _event(
                link,
                recipient.id,
                f"{link.resource_type}.share_link.redeem",
                grant["id"],
                1,
                None,
                {
                    "share_link_id": link.id,
                    "grant_id": grant["id"],
                    "role": grant["context"]["role"],
                    "outcome": outcome,
                },
            )
        invalidate_user_aliases(recipient)
        effective = await resolve_role(
            recipient.id, link.resource_type, link.resource_id
        )
        result = {
            "resource_type": link.resource_type,
            "resource_id": link.resource_id,
            "role": grant["context"]["role"],
            "granted_role": grant["context"]["role"],
            "effective_role": effective,
            "created_edge": outcome == "created",
            "outcome": outcome,
            "grant_id": grant["id"],
            "grant_revision": grant["context"]["revision"],
            "redirect_to": f"/{link.resource_type}s/{link.resource_id}",
        }
    await _deliver(outbox)
    return result


async def revoke_source_grant(
    *, actor_id: str, link_id: str, recipient_id: str
) -> dict:
    """Remove one source only; preserve unrelated direct/inherited/link authority."""
    preflight = await current_link(link_id)
    outbox = None
    async with membership_write_scope(
        preflight.workspace_id,
        principal_ids=(actor_id, recipient_id),
        active_principal_ids=(actor_id,),
    ):
        link = await current_link(link_id)
        from app.services.sharing import _require_collaborator_manage_authority

        await _require_collaborator_manage_authority(
            actor_id,
            link.resource_type,
            link.resource_id,
            policy_action=f"{link.resource_type}.share_link.revoke",
        )
        recipient = await resolve_current_profile(recipient_id, require_active=False)
        if recipient is None:
            raise BadRequestError(message="Unknown recipient")
        before = await share_grant_row(link, recipient.id)
        if before is None:
            raise ResourceNotFoundError(message="Share source grant not found")
        changed = before["context"]["state"] == "active"
        revision = before["context"]["revision"]
        if changed:
            context = await link.get_context()
            edges = await context.find_edges_between(
                link.id, recipient.id, edge_class=SHARE_GRANT
            )
            if len(edges) != 1 or edges[0].id != before["id"]:
                raise AuthorizationUnavailableError()
            edges[0].state = "revoked"
            edges[0].revision += 1
            edges[0].changed_at = utc_now_iso()
            await edges[0].save()
            revision += 1
            outbox = await _event(
                link,
                actor_id,
                f"{link.resource_type}.share_link.revoke",
                before["id"],
                revision,
                {"source_grant_id": before["id"], "state": "active"},
                {
                    "source_grant_id": before["id"],
                    "state": "revoked",
                    "recipient_id": recipient.id,
                },
            )
        invalidate_user_aliases(recipient)
        result = {
            "share_link_id": link.id,
            "grant_id": before["id"],
            "state": "revoked",
            "revision": revision,
            "changed": changed,
            "effective_role": await resolve_role(
                recipient.id, link.resource_type, link.resource_id
            ),
        }
    await _deliver(outbox)
    return result


async def disable_source_link(actor_user_id: str, share_link_id: str) -> dict:
    """Disable future redemption while retaining already accepted source lifetimes."""
    preflight = await current_link(share_link_id)
    outbox = None
    async with membership_write_scope(
        preflight.workspace_id, principal_ids=(actor_user_id,)
    ):
        link = await current_link(share_link_id)
        from app.services.sharing import _require_share_authority

        await _require_share_authority(
            actor_user_id,
            link.resource_type,
            link.resource_id,
            policy_action=f"{link.resource_type}.share_link.revoke",
        )
        changed = not bool(link.revoked_at)
        if changed:
            link.revoked_at = utc_now_iso()
            await link.save()
            outbox = await _event(
                link,
                actor_user_id,
                f"{link.resource_type}.share_link.revoke",
                link.id,
                1,
                {"redemption_enabled": True},
                {
                    "share_link_id": link.id,
                    "redemption_enabled": False,
                    "accepted_grants_retained": True,
                },
            )
        result = {
            "share_link_id": link.id,
            "revoked_at": link.revoked_at,
            "changed": changed,
            "accepted_grants_retained": True,
            "outcome": "disabled",
        }
    await _deliver(outbox)
    return result


async def record_mint_event(*, link: ShareLink, actor_id: str, before, after) -> None:
    """Persist source issuance with the mint's enclosing sharing unit."""
    from app.services.sharing_write_scope import register_sharing_outbox

    outbox = await _event(
        link,
        actor_id,
        f"{link.resource_type}.share_link.mint",
        link.id,
        0,
        before,
        after,
    )
    if outbox is not None:
        register_sharing_outbox(outbox)

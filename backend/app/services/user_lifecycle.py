"""Account deletion lifecycle — preview blockers and cascade orchestration."""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Tuple

from jvspatial.api.auth.models import User as AuthUser

from app.api.errors import BadRequestError, ResourceConflictError
from app.models.edges import (
    COLLABORATES_ON,
    EXCLUDED_FROM,
    HAS_ATTACHMENT,
    HAS_NOTIFICATION,
    IS_MEMBER_OF,
    OWNS,
)
from app.models.nodes import Invitation, User, Workspace
from app.schemas.account_deletion import (
    AccountDeletionBlocker,
    AccountDeletionBlockerResource,
    AccountDeletionPreview,
    AccountDeletionResult,
    OrgMembershipSummary,
    PersonalWorkspaceSummary,
)
from app.services.permissions import get_user_node

logger = logging.getLogger(__name__)

_IMPACT_ALWAYS = [
    "Your personal workspace(s) and all apps, tracks, entries, and chat threads inside them will be permanently deleted.",
    "Your profile, avatar, notification preferences, and connected-agent OAuth grants will be removed.",
    "Your sign-in credentials will be deleted. You may register again with the same email later.",
    "Membership in organization workspaces and collaborator access to shared resources will be removed.",
    "Entries and comments you authored in workspaces owned by others may remain without your name attached.",
]

_IMPACT_ADMIN = [
    "Personal workspace(s) and all apps, tracks, entries, and chat threads inside them will be permanently deleted.",
    "Profile, avatar, notification preferences, and connected-agent OAuth grants will be removed.",
    "Sign-in credentials will be deleted. The same email may be used to register again later.",
    "Membership in organization workspaces and collaborator access to shared resources will be removed.",
    "Entries and comments authored in workspaces owned by others may remain without this user's name attached.",
]


async def _auth_email_for_user(user: User) -> str:
    if not user.user_id:
        return ""
    try:
        auth_user = await AuthUser.get(user.user_id)
    except Exception:
        auth_user = None
    if not auth_user:
        return ""
    return (getattr(auth_user, "email", "") or "").strip()


async def _workspace_kind(workspace_id: str, cache: Dict[str, str]) -> str:
    if not workspace_id:
        return ""
    if workspace_id not in cache:
        ws = await Workspace.get(workspace_id)
        cache[workspace_id] = getattr(ws, "kind", "") if ws else ""
    return cache[workspace_id]


async def _member_role(user: User, workspace_id: str) -> str:
    ctx = await user.get_context()
    edges = await ctx.find_edges_between(user.id, workspace_id, edge_class=IS_MEMBER_OF)
    if not edges:
        return "none"
    role = getattr(edges[0], "role", None)
    if role:
        return str(role)
    edge_ctx = getattr(edges[0], "context", None)
    if isinstance(edge_ctx, dict):
        return str(edge_ctx.get("role", "member"))
    return "member"


async def _owned_org_resources(user: User) -> List[AccountDeletionBlockerResource]:
    ws_cache: Dict[str, str] = {}
    out: List[AccountDeletionBlockerResource] = []
    try:
        apps = await user.nodes(edge=["OWNS"], node=["WorkspaceApp"])
    except Exception:
        logger.exception("OWNS app traversal failed for user %s", user.id)
        apps = []
    for app in apps:
        ws_id = getattr(app, "workspace_id", "") or ""
        if await _workspace_kind(ws_id, ws_cache) != "organization":
            continue
        out.append(
            AccountDeletionBlockerResource(
                type="app",
                id=app.id,
                title=getattr(app, "name", "") or "",
            )
        )
    try:
        tracks = await user.nodes(edge=["OWNS"], node=["Track"])
    except Exception:
        logger.exception("OWNS track traversal failed for user %s", user.id)
        tracks = []
    for track in tracks:
        ws_id = getattr(track, "workspace_id", "") or ""
        if await _workspace_kind(ws_id, ws_cache) != "organization":
            continue
        out.append(
            AccountDeletionBlockerResource(
                type="track",
                id=track.id,
                title=getattr(track, "title", "") or "",
            )
        )
    return out


async def _sole_org_owner_blockers(user: User) -> List[AccountDeletionBlocker]:
    blockers: List[AccountDeletionBlocker] = []
    try:
        workspaces = await user.nodes(edge=["IS_MEMBER_OF"], node=["Workspace"])
    except Exception:
        logger.exception("IS_MEMBER_OF traversal failed for user %s", user.id)
        return blockers
    for ws in workspaces:
        if getattr(ws, "kind", "") != "organization":
            continue
        if await _member_role(user, ws.id) != "owner":
            continue
        try:
            members = await ws.nodes(
                edge=["IS_MEMBER_OF"], direction="in", node=["User"]
            )
        except Exception:
            logger.exception("member list failed for workspace %s", ws.id)
            continue
        other_count = sum(1 for m in members if m.id != user.id)
        if other_count < 1:
            continue
        name = getattr(ws, "name", "") or ws.id
        blockers.append(
            AccountDeletionBlocker(
                code="sole_org_owner_with_members",
                message=(
                    f"You are the sole owner of organization workspace "
                    f"'{name}' which has {other_count} other member(s). "
                    "Delete that workspace or transfer ownership before "
                    "deleting your account."
                ),
                workspace_id=ws.id,
                workspace_name=name,
            )
        )
    return blockers


async def _collect_blockers(user: User) -> List[AccountDeletionBlocker]:
    blockers: List[AccountDeletionBlocker] = []
    org_owned = await _owned_org_resources(user)
    if org_owned:
        blockers.append(
            AccountDeletionBlocker(
                code="org_owned_resources",
                message=(
                    "Transfer ownership of the following apps or tracks in "
                    "organization workspaces before deleting your account."
                ),
                resources=org_owned,
            )
        )
    blockers.extend(await _sole_org_owner_blockers(user))
    return blockers


def _admin_blocker_messages(
    user: User, blockers: List[AccountDeletionBlocker]
) -> List[AccountDeletionBlocker]:
    """Rewrite self-service blocker copy for platform-admin deletion preview."""
    display = user.display_name or "This user"
    out: List[AccountDeletionBlocker] = []
    for blocker in blockers:
        if blocker.code == "org_owned_resources":
            resources = blocker.resources or []
            titles = ", ".join(r.title or r.id for r in resources[:5])
            suffix = f" ({titles})" if titles else ""
            out.append(
                AccountDeletionBlocker(
                    code=blocker.code,
                    message=(
                        f"{display} owns apps or tracks in organization "
                        f"workspaces{suffix}. Forced deletion removes ownership "
                        f"links; those resources remain in their workspaces."
                    ),
                    resources=resources,
                )
            )
            continue
        if blocker.code == "sole_org_owner_with_members":
            ws_name = blocker.workspace_name or blocker.workspace_id or "a workspace"
            out.append(
                AccountDeletionBlocker(
                    code=blocker.code,
                    message=(
                        f"{display} is the sole owner of organization workspace "
                        f"'{ws_name}' which has other members. Forced deletion "
                        f"removes their membership without transferring ownership."
                    ),
                    workspace_id=blocker.workspace_id,
                    workspace_name=blocker.workspace_name,
                )
            )
            continue
        out.append(blocker)
    return out


async def _personal_workspace_summaries(user: User) -> List[PersonalWorkspaceSummary]:
    from app.services.workspace_lifecycle import _collect_contained

    summaries: List[PersonalWorkspaceSummary] = []
    try:
        workspaces = await user.nodes(edge=["IS_MEMBER_OF"], node=["Workspace"])
    except Exception:
        return summaries
    for ws in workspaces:
        if getattr(ws, "kind", "") != "personal":
            continue
        if await _member_role(user, ws.id) != "owner":
            continue
        apps = await _collect_contained(ws, kind="apps")
        tracks = await _collect_contained(ws, kind="tracks")
        summaries.append(
            PersonalWorkspaceSummary(
                workspace_id=ws.id,
                name=getattr(ws, "name", "") or "",
                app_count=len(apps),
                track_count=len(tracks),
            )
        )
    return summaries


async def _org_membership_summaries(user: User) -> List[OrgMembershipSummary]:
    summaries: List[OrgMembershipSummary] = []
    try:
        workspaces = await user.nodes(edge=["IS_MEMBER_OF"], node=["Workspace"])
    except Exception:
        return summaries
    for ws in workspaces:
        if getattr(ws, "kind", "") != "organization":
            continue
        summaries.append(
            OrgMembershipSummary(
                workspace_id=ws.id,
                name=getattr(ws, "name", "") or "",
                role=await _member_role(user, ws.id),
            )
        )
    return summaries


async def get_account_deletion_preview(principal_id: str) -> AccountDeletionPreview:
    """Return impact summary and blockers for self-service account deletion."""
    user = await get_user_node(principal_id)
    if not user:
        raise BadRequestError(message="User profile not found")
    blockers = await _collect_blockers(user)
    email = await _auth_email_for_user(user)
    personal = await _personal_workspace_summaries(user)
    org_memberships = await _org_membership_summaries(user)
    impact = list(_IMPACT_ALWAYS)
    if personal:
        total_apps = sum(p.app_count for p in personal)
        total_tracks = sum(p.track_count for p in personal)
        impact.insert(
            0,
            f"{len(personal)} personal workspace(s) with {total_apps} app(s) "
            f"and {total_tracks} track(s) will be permanently deleted.",
        )
    if org_memberships:
        impact.append(
            f"Membership in {len(org_memberships)} organization workspace(s) "
            "will be removed."
        )
    return AccountDeletionPreview(
        can_delete=len(blockers) == 0,
        email=email,
        blockers=blockers,
        personal_workspaces=personal,
        org_memberships=org_memberships,
        impact=impact,
    )


async def get_account_deletion_preview_for_admin(target_user_id: str):
    """Pre-flight impact summary for platform-admin user deletion."""
    from app.schemas.admin import AdminDeletionPreview

    user = await get_user_node(target_user_id)
    if not user:
        user = await User.get(target_user_id)
    if not user:
        raise BadRequestError(message="User profile not found")

    blockers = await _collect_blockers(user)
    email = await _auth_email_for_user(user)
    personal = await _personal_workspace_summaries(user)
    org_memberships = await _org_membership_summaries(user)
    impact = list(_IMPACT_ADMIN)
    if personal:
        total_apps = sum(p.app_count for p in personal)
        total_tracks = sum(p.track_count for p in personal)
        impact.insert(
            0,
            f"{len(personal)} personal workspace(s) with {total_apps} app(s) "
            f"and {total_tracks} track(s) will be permanently deleted.",
        )
    if org_memberships:
        impact.append(
            f"Membership in {len(org_memberships)} organization workspace(s) "
            "will be removed."
        )
    return AdminDeletionPreview(
        can_delete=len(blockers) == 0,
        can_force_delete=bool(email),
        email=email,
        blockers=_admin_blocker_messages(user, blockers),
        personal_workspaces=personal,
        org_memberships=org_memberships,
        impact=impact,
        target_user_id=user.id,
        target_display_name=user.display_name or "",
    )


def _raise_blockers(blockers: List[AccountDeletionBlocker]) -> None:
    err = ResourceConflictError(
        message="Account deletion is blocked until you resolve the listed issues.",
        details={"blockers": [b.model_dump() for b in blockers]},
    )
    err.error_code = "account.deletion_blocked"
    raise err


async def _delete_outgoing_edges(user: User, edge_class: type) -> None:
    ctx = await user.get_context()
    try:
        edges = await ctx.find_edges_between(
            user.id, target_id=None, edge_class=edge_class
        )
    except Exception:
        logger.exception("outgoing edge lookup failed for %s", edge_class.__name__)
        return
    if not edges:
        return
    await asyncio.gather(*(e.delete() for e in edges), return_exceptions=True)


async def _revoke_all_oauth_grants(principal_id: str) -> int:
    from jvspatial.api.auth.oauth import refresh_store
    from jvspatial.api.auth.oauth.models import OAuthRefreshToken

    rows = await OAuthRefreshToken.find(
        {"context.user_id": principal_id, "context.is_active": True}
    )
    revoked = 0
    for rec in rows:
        try:
            await refresh_store.revoke(rec)
            revoked += 1
        except Exception:
            logger.exception("OAuth revoke failed for token %s", getattr(rec, "id", ""))
    return revoked


async def _hard_delete_chat_threads(principal_id: str) -> int:
    from app.services import chat_threads as chat_store
    from app.services.chat_providers import get_registry

    threads = await chat_store.list_threads(user_id=principal_id, include_archived=True)
    deleted = 0
    registry = get_registry()
    for thread in threads:
        provider = registry.get(thread.provider_id)
        provider_session_id = thread.provider_session_id
        if provider is not None and provider_session_id and thread.agent_id:
            try:
                await provider.delete_conversation(
                    agent_id=thread.agent_id,
                    user_id=principal_id,
                    session_id=provider_session_id,
                )
            except Exception:
                logger.exception(
                    "Provider delete_conversation failed for thread %s",
                    thread.id,
                )
        try:
            await chat_store.delete_thread_messages(thread)
            await thread.delete()
            deleted += 1
        except Exception:
            logger.exception("Hard-delete chat thread %s failed", thread.id)
    for provider in registry.list():
        try:
            await provider.purge_user(user_id=principal_id)
        except Exception:
            logger.exception("Provider purge_user failed for %r", provider.id)
    return deleted


async def _delete_avatar_attachments(user: User) -> None:
    from app.services.attachment_storage import get_attachment_storage_service

    storage = get_attachment_storage_service()
    try:
        attachments = await user.nodes(
            edge=[HAS_ATTACHMENT], direction="out", node=["Attachment"]
        )
    except Exception:
        logger.exception("HAS_ATTACHMENT traversal failed for user %s", user.id)
        return
    for att in attachments:
        storage_key = getattr(att, "storage_key", "") or ""
        if storage_key:
            try:
                await storage.delete_attachment(storage_key)
            except Exception:
                logger.exception("delete_attachment failed for %s", storage_key)
        try:
            await att.delete()
        except Exception:
            logger.exception("delete attachment node %s failed", att.id)


async def _delete_notifications(user: User, principal_id: str) -> None:
    try:
        notifications = await user.nodes(
            edge=[HAS_NOTIFICATION], direction="out", node=["Notification"]
        )
    except Exception:
        logger.exception("HAS_NOTIFICATION traversal failed for user %s", user.id)
        return
    ctx = await user.get_context()
    for notification in notifications:
        try:
            edges = await ctx.find_edges_between(
                user.id, notification.id, edge_class=HAS_NOTIFICATION
            )
            await asyncio.gather(*(e.delete() for e in edges), return_exceptions=True)
            await notification.delete()
        except Exception:
            logger.exception(
                "delete notification %s failed for user %s",
                getattr(notification, "id", ""),
                principal_id,
            )


async def _revoke_pending_invitations(user: User, principal_id: str) -> None:
    from app.services.invitations import revoke_invitation

    email = (await _auth_email_for_user(user)).casefold()
    pending: List[Invitation] = []
    if email:
        pending.extend(
            await Invitation.find({"context.email": email, "context.status": "pending"})
        )
    by_uid = await Invitation.find(
        {"context.invited_user_id": user.id, "context.status": "pending"}
    )
    seen = {inv.id for inv in pending}
    for inv in by_uid:
        if inv.id not in seen:
            pending.append(inv)
    for invitation in pending:
        try:
            await revoke_invitation(invitation, actor_user_id=principal_id)
        except Exception:
            logger.exception(
                "revoke invitation %s failed during account deletion",
                invitation.id,
            )


async def _delete_personal_agent_configs(user: User) -> None:
    try:
        from app.agentive.nodes import AgentConfig
        from app.agentive.services.uplink_registry import uplink_registry
    except ImportError:
        return
    auth_id = user.user_id or ""
    if not auth_id:
        return
    matches = await AgentConfig.find(
        {"context.user_id": auth_id, "context.scope": "personal"}
    )
    for cfg in matches:
        cfg_id = getattr(cfg, "id", "")
        try:
            await uplink_registry.unregister(cfg_id)
        except Exception:
            logger.exception("uplink unregister failed for %s", cfg_id)
        try:
            await cfg.delete()
        except Exception:
            logger.exception("delete AgentConfig %s failed", cfg_id)


async def _cascade_owned_workspaces(user: User) -> Tuple[int, int]:
    """Delete personal workspaces and sole-member org workspaces."""
    from app.services.workspace_lifecycle import delete_workspace_cascade

    deleted_personal = 0
    deleted_org = 0
    try:
        workspaces = await user.nodes(edge=["IS_MEMBER_OF"], node=["Workspace"])
    except Exception:
        return deleted_personal, deleted_org
    for ws in list(workspaces):
        kind = getattr(ws, "kind", "")
        role = await _member_role(user, ws.id)
        if kind == "personal" and role == "owner":
            await delete_workspace_cascade(ws, cascade=True)
            deleted_personal += 1
            continue
        from app.services.workspace_kind import is_collaborative_kind

        if not is_collaborative_kind(kind) or role != "owner":
            continue
        try:
            members = await ws.nodes(
                edge=["IS_MEMBER_OF"], direction="in", node=["User"]
            )
        except Exception:
            continue
        others = [m for m in members if m.id != user.id]
        if others:
            continue
        await delete_workspace_cascade(ws, cascade=True)
        deleted_org += 1
    return deleted_personal, deleted_org


async def _remove_membership_edges(user: User) -> None:
    ctx = await user.get_context()
    try:
        edges = await ctx.find_edges_between(
            user.id, target_id=None, edge_class=IS_MEMBER_OF
        )
    except Exception:
        logger.exception("IS_MEMBER_OF edge lookup failed for user %s", user.id)
        return
    if edges:
        await asyncio.gather(*(e.delete() for e in edges), return_exceptions=True)


async def delete_user_account(
    principal_id: str,
    *,
    confirm_email: str,
) -> AccountDeletionResult:
    """Permanently delete the caller's account after validation."""
    return await _execute_account_deletion(
        principal_id,
        confirm_email=confirm_email,
    )


async def delete_user_account_as_admin(
    target_user_id: str,
    *,
    actor_id: str,
    confirm_email: str,
    force: bool = False,
) -> AccountDeletionResult:
    """Permanently delete a user account (platform admin action)."""
    user = await get_user_node(target_user_id)
    if not user:
        user = await User.get(target_user_id)
    if not user:
        raise BadRequestError(message="User profile not found")

    target_principal = user.user_id or user.id
    actor_node = await get_user_node(actor_id)
    actor_graph_id = actor_node.id if actor_node else actor_id
    if user.id == actor_graph_id or (
        user.user_id and actor_node and user.user_id == actor_node.user_id
    ):
        raise BadRequestError(message="Cannot delete your own account via admin API")

    auth_user = None
    if user.user_id:
        try:
            auth_user = await AuthUser.get(user.user_id)
        except Exception:
            auth_user = None
    roles = list(getattr(auth_user, "roles", None) or []) if auth_user else []
    if auth_user and "admin" in roles:
        admin_count = 0
        try:
            for row in await AuthUser.find({}):
                if "admin" in list(getattr(row, "roles", None) or []):
                    admin_count += 1
        except Exception:
            admin_count = 1
        if admin_count <= 1:
            raise ResourceConflictError(
                message="Cannot delete the sole remaining platform admin"
            )

    return await _execute_account_deletion(
        target_principal,
        confirm_email=confirm_email,
        skip_blockers=force,
    )


async def _execute_account_deletion(
    principal_id: str,
    *,
    confirm_email: str,
    skip_blockers: bool = False,
) -> AccountDeletionResult:
    """Shared deletion orchestrator for self-service and admin paths."""
    from app.api.utils import export_node

    user = await get_user_node(principal_id)
    if not user:
        raise BadRequestError(message="User profile not found")

    blockers = await _collect_blockers(user)
    if blockers and not skip_blockers:
        _raise_blockers(blockers)

    auth_email = await _auth_email_for_user(user)
    if not auth_email:
        raise BadRequestError(message="No email address linked to this account")
    if (confirm_email or "").strip().casefold() != auth_email.casefold():
        raise BadRequestError(
            message="Confirmation email does not match your account email"
        )

    prior_snapshot = await export_node(user)
    deleted_user_node_id = user.id
    auth_user_id = user.user_id or principal_id

    await _cascade_owned_workspaces(user)
    await _remove_membership_edges(user)
    await _delete_outgoing_edges(user, COLLABORATES_ON)
    await _delete_outgoing_edges(user, EXCLUDED_FROM)
    await _revoke_all_oauth_grants(principal_id)
    await _hard_delete_chat_threads(principal_id)
    await _delete_avatar_attachments(user)
    await _delete_notifications(user, principal_id)
    await _revoke_pending_invitations(user, principal_id)
    await _delete_personal_agent_configs(user)
    from app.services.model_credentials import delete_credentials_for_user

    if auth_user_id:
        await delete_credentials_for_user(auth_user_id)

    # Remove any remaining OWNS edges (personal resources already cascaded).
    await _delete_outgoing_edges(user, OWNS)

    if auth_user_id:
        try:
            auth_user = await AuthUser.get(auth_user_id)
            if auth_user:
                await auth_user.delete()
        except Exception:
            logger.exception("AuthUser delete failed for %s", auth_user_id)

    await user.delete()

    return AccountDeletionResult(
        deleted_user_id=auth_user_id or principal_id,
        deleted_user_node_id=deleted_user_node_id,
        prior_snapshot=prior_snapshot,
    )

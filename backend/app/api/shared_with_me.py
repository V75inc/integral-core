"""Phase 5 — "Shared with me" cross-workspace surface.

Surfaces resources where the current user has a direct ``COLLABORATES_ON``
edge inside a workspace where they are an ``IS_MEMBER_OF{role:"guest"}``.
This is the consolation surface for the strict workspace-scope enforcement
landed in Phase 0 — guest-membership resources do not appear in the
default workspace switcher's main listings.

Also exposes ``/me/invitations`` — pending invites addressed to the
current user's email (resource-level OR workspace-level).

Phase 8 Plan 08-05 added ``/me/sharing-overview`` — the caller-scoped
OUTBOUND aggregator for SET-08 (Settings -> Sharing index panel).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import resolve_principal_id
from app.models.edges import COLLABORATES_ON, EXCLUDED_FROM, IS_MEMBER_OF, OWNS
from app.models.nodes import App, Entry, Invitation, ShareLink, Track, Workspace
from app.schemas.sharing_overview import (
    OutboundExclusion,
    OutboundInvitation,
    OutboundShareLink,
    SharingOverviewResponse,
)
from app.services.permissions import get_user_node


def _raise_invitation_action_error(code: str) -> None:
    if code == "invitation.not_found":
        raise ResourceNotFoundError(message="Invitation not found")
    if code == "invitation.email_mismatch":
        raise InsufficientPermissionsError(
            message="This invitation was sent to a different email address."
        )
    if code in (
        "invitation.token_expired",
        "invitation.revoked",
        "invitation.declined",
        "invitation.consumed",
    ):
        raise BadRequestError(message=code)
    if code == "invitation.grant_failed":
        raise BadRequestError(
            message="Invitation could not be applied. Please try again or contact support."
        )
    raise BadRequestError(message=code or "invitation.failed")


def _role_from_edge(edge: Any, default: str = "viewer") -> str:
    role = getattr(edge, "role", None)
    if role:
        return str(role).strip().lower()
    ctx = getattr(edge, "context", None)
    if isinstance(ctx, dict) and ctx.get("role"):
        return str(ctx["role"]).strip().lower()
    return default


async def _guest_workspace_ids(user_id: str) -> List[str]:
    """Return workspace ids where the user has IS_MEMBER_OF{role:"guest"}."""
    user = await get_user_node(user_id)
    if not user:
        return []
    out: List[str] = []
    try:
        ctx = await user.get_context()
        edges = await ctx.find_edges_between(
            user.id,
            None,  # type: ignore[arg-type]
            edge_class=IS_MEMBER_OF,
        )
        for edge in edges:
            if _role_from_edge(edge, default="member") != "guest":
                continue
            wid = getattr(edge, "target", None) or ""
            if wid:
                out.append(wid)
    except Exception:
        pass
    return out


async def _direct_collab_resources(user_id: str) -> List[Dict[str, Any]]:
    """Resources where the user has a direct COLLABORATES_ON edge."""
    user = await get_user_node(user_id)
    if not user:
        return []
    out: List[Dict[str, Any]] = []
    try:
        ctx = await user.get_context()
        edges = await ctx.find_edges_between(
            user.id,
            None,  # type: ignore[arg-type]
            edge_class=COLLABORATES_ON,
        )
        for edge in edges:
            tid = getattr(edge, "target", None) or ""
            if not tid:
                continue
            out.append({"resource_id": tid, "role": _role_from_edge(edge)})
    except Exception:
        pass
    return out


def _classify_resource(node: Any) -> str:
    if isinstance(node, App):
        return "app"
    if isinstance(node, Track):
        return "track"
    if isinstance(node, Entry):
        return "entry"
    return "unknown"


@endpoint("/me/shared", methods=["GET"], auth=True, tags=["Sharing"])
async def list_shared_with_me(request: Request) -> Dict[str, Any]:
    """Return cross-workspace resources directly granted to the caller.

    Lists resources where the caller has a guest membership in the workspace.

    Response shape::

        {
            "workspaces": [
                {
                    "workspace": {id, name, kind, accent_color},
                    "apps":   [{id, name, role}, ...],
                    "tracks":   [{id, title, role, app_id?}, ...],
                    "entries":  [{id, title, role, track_id}, ...]
                },
                ...
            ]
        }
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    guest_ws_ids = set(await _guest_workspace_ids(user_id))
    if not guest_ws_ids:
        return {"workspaces": []}

    direct = await _direct_collab_resources(user_id)
    if not direct:
        return {"workspaces": []}

    # I-PERF: original loop did 3 sequential get() per resource (App → Track
    # → Entry) plus a 4th get() per Entry to resolve parent track. Group by
    # entity type once, run the type probes in parallel, and batch the
    # parent-track lookups.
    resource_ids = [row["resource_id"] for row in direct]

    async def _resolve_one(rid: str) -> Any:
        # Three probes in parallel; first non-None wins by type priority.
        app, track, entry = await asyncio.gather(
            App.get(rid), Track.get(rid), Entry.get(rid)
        )
        return app or track or entry

    resolved = await asyncio.gather(*(_resolve_one(rid) for rid in resource_ids))

    # Pre-resolve parent tracks for every Entry resource in one batch.
    entry_track_ids: set[str] = set()
    for node in resolved:
        if node is not None and _classify_resource(node) == "entry":
            tid = getattr(node, "track_id", "") or ""
            if tid:
                entry_track_ids.add(tid)
    track_lookup: Dict[str, Any] = {}
    if entry_track_ids:
        tracks_resolved = await asyncio.gather(
            *(Track.get(tid) for tid in entry_track_ids)
        )
        track_lookup = {
            tid: tr for tid, tr in zip(entry_track_ids, tracks_resolved) if tr
        }

    # Group resources by workspace.
    by_ws: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for row, node in zip(direct, resolved):
        if node is None:
            continue
        kind = _classify_resource(node)
        wid = ""
        if kind in ("app", "track"):
            wid = getattr(node, "workspace_id", "") or ""
        elif kind == "entry":
            tid = getattr(node, "track_id", "") or ""
            track = track_lookup.get(tid) if tid else None
            wid = getattr(track, "workspace_id", "") if track else ""
        if not wid or wid not in guest_ws_ids:
            continue
        bucket = by_ws.setdefault(wid, {"apps": [], "tracks": [], "entries": []})
        if kind == "app":
            bucket["apps"].append(
                {
                    "id": node.id,
                    "name": getattr(node, "name", "") or "",
                    "role": row["role"],
                }
            )
        elif kind == "track":
            bucket["tracks"].append(
                {
                    "id": node.id,
                    "title": getattr(node, "title", "") or "",
                    "role": row["role"],
                }
            )
        elif kind == "entry":
            bucket["entries"].append(
                {
                    "id": node.id,
                    "title": getattr(node, "title", "") or "",
                    "role": row["role"],
                    "track_id": getattr(node, "track_id", "") or "",
                }
            )

    workspaces = []
    ws_ids = list(by_ws.keys())
    ws_by_id: Dict[str, Any] = {}
    if ws_ids:
        for ws in await Workspace.find({"id": {"$in": ws_ids}}):
            ws_by_id[ws.id] = ws
    for wid, bucket in by_ws.items():
        ws = ws_by_id.get(wid)
        if not ws:
            continue
        workspaces.append(
            {
                "workspace": {
                    "id": ws.id,
                    "name": getattr(ws, "name", "") or "",
                    "kind": getattr(ws, "kind", "") or "",
                    "accent_color": getattr(ws, "accent_color", "") or "",
                },
                "apps": bucket["apps"],
                "tracks": bucket["tracks"],
                "entries": bucket["entries"],
            }
        )
    return {"workspaces": workspaces}


@endpoint("/me/invitations", methods=["GET"], auth=True, tags=["Invitations"])
async def list_my_invitations(request: Request) -> Dict[str, Any]:
    """Pending invitations addressed to the caller's email.

    Best-effort lookup via the caller's email; resource-level and
    workspace-level invites both surface here.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    user = await get_user_node(user_id)
    if not user:
        return {"invitations": []}

    # Email lookup via AuthUser bridge.
    email = ""
    try:
        from jvspatial.api.auth.models import User as AuthUser

        auth_users = await AuthUser.find({"id": getattr(user, "user_id", "")})
        if auth_users:
            email = (getattr(auth_users[0], "email", "") or "").strip().casefold()
    except Exception:
        email = ""

    matches: List[Invitation] = []
    if email:
        matches = await Invitation.find(
            {"context.email": email, "context.status": "pending"}
        )
    # Also include invites where invited_user_id was pre-resolved to this user.
    by_uid = await Invitation.find(
        {"context.invited_user_id": user.id, "context.status": "pending"}
    )
    seen = {inv.id for inv in matches}
    for inv in by_uid:
        if inv.id not in seen:
            matches.append(inv)
            seen.add(inv.id)

    out: List[Dict[str, Any]] = []
    ws_ids = list({inv.workspace_id for inv in matches if inv.workspace_id})
    ws_by_id: Dict[str, Any] = {}
    if ws_ids:
        for ws in await Workspace.find({"id": {"$in": ws_ids}}):
            ws_by_id[ws.id] = ws
    for inv in matches:
        workspace_name = ""
        if inv.workspace_id:
            ws = ws_by_id.get(inv.workspace_id)
            if ws:
                workspace_name = getattr(ws, "name", "") or ""
        out.append(
            {
                "id": inv.id,
                "workspace_id": inv.workspace_id,
                "workspace_name": workspace_name,
                "email": inv.email,
                "invited_by_user_id": inv.invited_by_user_id,
                "role": inv.role,
                "target_resource_type": inv.target_resource_type or None,
                "target_resource_id": inv.target_resource_id or None,
                "target_resource_role": inv.target_resource_role or None,
                "status": inv.status,
                "message": inv.message,
                "created_at": inv.created_at,
                "expires_at": inv.expires_at,
            }
        )
    return {"invitations": out}


@endpoint(
    "/me/invitations/{invitation_id}/accept",
    methods=["POST"],
    auth=True,
    tags=["Invitations"],
)
async def post_accept_my_invitation(
    request: Request,
    invitation_id: str,
) -> Dict[str, Any]:
    """Accept a pending invitation addressed to the caller (by invitation id)."""
    from app.services.invitations import accept_invitation_by_id

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    user = await get_user_node(user_id)
    if not user:
        raise InsufficientPermissionsError(message="Access denied")

    invitation, err = await accept_invitation_by_id(invitation_id, user.id)
    if err:
        _raise_invitation_action_error(err)
    assert invitation is not None
    workspace = await Workspace.get(invitation.workspace_id)
    return {
        "invitation": {
            "id": invitation.id,
            "workspace_id": invitation.workspace_id,
            "role": invitation.role,
            "status": invitation.status,
        },
        "workspace": (
            {"id": workspace.id, "name": workspace.name} if workspace else None
        ),
        "role": invitation.target_resource_role or invitation.role,
        "message": "Invitation accepted",
    }


@endpoint(
    "/me/invitations/{invitation_id}/decline",
    methods=["POST"],
    auth=True,
    tags=["Invitations"],
)
async def post_decline_my_invitation(
    request: Request,
    invitation_id: str,
) -> Dict[str, Any]:
    """Decline a pending invitation addressed to the caller (by invitation id)."""
    from app.services.invitations import decline_invitation_by_id

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    user = await get_user_node(user_id)
    if not user:
        raise InsufficientPermissionsError(message="Access denied")

    invitation, err = await decline_invitation_by_id(invitation_id, user.id)
    if err and err != "invitation.declined":
        _raise_invitation_action_error(err)
    if not invitation:
        raise ResourceNotFoundError(message="Invitation not found")
    return {
        "invitation": {
            "id": invitation.id,
            "workspace_id": invitation.workspace_id,
            "status": invitation.status,
        },
        "message": "Invitation declined",
    }


# ---------------------------------------------------------------------------
# Phase 8 Plan 08-05 — outbound sharing aggregator (SET-08).
# ---------------------------------------------------------------------------


def _resource_label_value(node: Any, kind: str) -> str:
    if kind == "app":
        return getattr(node, "name", "") or ""
    return getattr(node, "title", "") or ""


async def _load_resource_by_kind(kind: str, resource_id: str) -> Any:
    if kind == "app":
        return await App.get(resource_id)
    if kind == "track":
        return await Track.get(resource_id)
    if kind == "entry":
        return await Entry.get(resource_id)
    return None


async def _batch_resources_by_kind(
    pairs: List[tuple[str, str]],
) -> Dict[tuple[str, str], Any]:
    """Batch-load (kind, id) pairs; missing resources omitted from the map."""
    app_ids: List[str] = []
    track_ids: List[str] = []
    entry_ids: List[str] = []
    for kind, rid in pairs:
        if kind == "app":
            app_ids.append(rid)
        elif kind == "track":
            track_ids.append(rid)
        elif kind == "entry":
            entry_ids.append(rid)
    out: Dict[tuple[str, str], Any] = {}
    if app_ids:
        for node in await App.find({"id": {"$in": list(dict.fromkeys(app_ids))}}):
            out[("app", node.id)] = node
    if track_ids:
        for node in await Track.find({"id": {"$in": list(dict.fromkeys(track_ids))}}):
            out[("track", node.id)] = node
    if entry_ids:
        for node in await Entry.find({"id": {"$in": list(dict.fromkeys(entry_ids))}}):
            out[("entry", node.id)] = node
    return out


@endpoint("/me/sharing-overview", methods=["GET"], auth=True, tags=["Sharing"])
async def get_sharing_overview(request: Request) -> SharingOverviewResponse:
    """Phase 8 Plan 08-05 — caller-scoped outbound sharing aggregator (SET-08).

    Returns every active ShareLink the caller has minted, every
    ``EXCLUDED_FROM`` edge sourced from a resource the caller owns, and every
    pending resource-level Invitation the caller has issued.

    Per A2 + A4: single aggregator endpoint, NOT a frontend fan-out across
    per-resource ``getAccess`` calls. Workspace-targeted invitations are
    deliberately excluded — they have their own surface under
    Settings -> Workspace Members.

    Caller-scoped strict invariant: no resource outside the caller's
    ``OWNS`` edge tree appears; no share link minted by another user
    appears; no exclusion on a resource the caller does not own appears.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    user = await get_user_node(user_id)

    # ------------------------------------------------------------------
    # 1) Share links the caller has minted (and not revoked).
    # ------------------------------------------------------------------
    share_link_rows: List[OutboundShareLink] = []
    minted = await ShareLink.find({"context.created_by": user_id})
    active_links = [link for link in minted if not getattr(link, "revoked_at", None)]
    link_resource_map = await _batch_resources_by_kind(
        [(link.resource_type, link.resource_id) for link in active_links]
    )
    for link in active_links:
        resource = link_resource_map.get((link.resource_type, link.resource_id))
        if resource is None:
            # Resource may have been deleted — T-08-05-I02 disposition: tolerate.
            label = "(deleted resource)"
        else:
            label = _resource_label_value(resource, link.resource_type)
        share_link_rows.append(
            OutboundShareLink(
                id=link.id,
                resource_type=link.resource_type,
                resource_id=link.resource_id,
                resource_label=label,
                role=link.role,
                created_at=link.created_at,
                expires_at=link.expires_at,
                redemptions=int(link.redemptions or 0),
            )
        )

    # ------------------------------------------------------------------
    # 2) Exclusions on resources the caller owns (walk OWNS -> EXCLUDED_FROM in).
    # ------------------------------------------------------------------
    exclusion_rows: List[OutboundExclusion] = []
    if user is not None:
        try:
            ctx = await user.get_context()
            owns_edges = await ctx.find_edges_between(
                user.id,
                None,  # type: ignore[arg-type]
                edge_class=OWNS,
            )
        except Exception:
            owns_edges = []

        owned_pairs: List[str] = []
        for own_edge in owns_edges:
            owned_id = getattr(own_edge, "target", None) or ""
            if not owned_id:
                continue
            owned_pairs.append(owned_id)

        owned_nodes: Dict[str, Any] = {}
        if owned_pairs:
            unique_owned = list(dict.fromkeys(owned_pairs))
            apps_owned, tracks_owned = await asyncio.gather(
                App.find({"id": {"$in": unique_owned}}),
                Track.find({"id": {"$in": unique_owned}}),
            )
            for node in apps_owned:
                owned_nodes[node.id] = ("app", node)
            for node in tracks_owned:
                owned_nodes[node.id] = ("track", node)

        excluded_user_ids: set[str] = set()
        pending_exclusions: List[tuple[str, str, str, Any, list]] = []

        for own_edge in owns_edges:
            owned_id = getattr(own_edge, "target", None) or ""
            if not owned_id:
                continue
            owned_entry = owned_nodes.get(owned_id)
            if owned_entry is None:
                continue
            kind, owned = owned_entry
            try:
                excl_edges = await ctx.find_edges_between(
                    None,  # type: ignore[arg-type]
                    owned.id,
                    edge_class=EXCLUDED_FROM,
                )
            except Exception:
                excl_edges = []
            for excl in excl_edges:
                excluded_user_id = getattr(excl, "source", None) or ""
                if not excluded_user_id:
                    continue
                excluded_user_ids.add(excluded_user_id)
                pending_exclusions.append(
                    (kind, owned, excluded_user_id, excl, excl_edges)
                )

        user_display_by_id: Dict[str, Optional[str]] = {}
        if excluded_user_ids:
            from app.models.nodes import User as _User

            for u in await _User.find({"id": {"$in": list(excluded_user_ids)}}):
                user_display_by_id[u.id] = getattr(u, "display_name", None)

        for kind, owned, excluded_user_id, excl, _excl_edges in pending_exclusions:
            reason = getattr(excl, "reason", None) or None
            created_at = getattr(excl, "excluded_at", None) or None
            excluded_user_display = user_display_by_id.get(excluded_user_id)
            exclusion_rows.append(
                OutboundExclusion(
                    resource_type=kind,
                    resource_id=owned.id,
                    resource_label=_resource_label_value(owned, kind),
                    excluded_user_id=excluded_user_id,
                    excluded_user_display=excluded_user_display,
                    reason=reason,
                    created_at=created_at,
                )
            )

    # ------------------------------------------------------------------
    # 3) Pending resource-level invitations the caller has created.
    # ------------------------------------------------------------------
    invitation_rows: List[OutboundInvitation] = []
    issued = await Invitation.find(
        {"context.invited_by_user_id": user_id, "context.status": "pending"}
    )
    invitation_targets = [
        (kind, rid)
        for inv in issued
        for kind in [(inv.target_resource_type or "").strip().lower()]
        for rid in [(inv.target_resource_id or "").strip()]
        if kind in ("app", "track", "entry") and rid
    ]
    invitation_resource_map = await _batch_resources_by_kind(invitation_targets)
    for inv in issued:
        kind = (inv.target_resource_type or "").strip().lower()
        rid = (inv.target_resource_id or "").strip()
        if kind not in ("app", "track", "entry") or not rid:
            # Workspace-targeted invitation — excluded from this surface.
            continue
        resource = invitation_resource_map.get((kind, rid))
        label = (
            _resource_label_value(resource, kind)
            if resource is not None
            else "(deleted resource)"
        )
        invitation_rows.append(
            OutboundInvitation(
                id=inv.id,
                resource_type=kind,
                resource_id=rid,
                resource_label=label,
                invitee_email=inv.email or None,
                invitee_user_id=inv.invited_user_id or None,
                role=inv.target_resource_role or inv.role or "viewer",
                status=inv.status,
                created_at=inv.created_at,
                expires_at=inv.expires_at,
            )
        )

    return SharingOverviewResponse(
        share_links=share_link_rows,
        exclusions=exclusion_rows,
        invitations=invitation_rows,
    )

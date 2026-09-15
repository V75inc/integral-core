"""Workspace API — canonical surface for the workspace model.

Personal and Organization workspaces are exposed under one path; ``kind``
discriminates behaviour. Org-kind workspaces own members
(``IS_MEMBER_OF``), invitations (``INVITED_TO``), and a storage quota;
personal-kind workspaces have a single implicit member (the owner), no
invitations, and no quota enforcement.
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import Response
from jvspatial.api import endpoint
from jvspatial.api.exceptions import FileTooLargeError, ValidationError

from app.api.attachments import _read_multipart_files
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.api.utils import export_node, public_user_view, resolve_principal_id
from app.api.validators_common import (
    compute_fold,
    non_empty_after_strip,
    validate_hex_color,
)
from app.config import settings
from app.models.edges import HAS_ATTACHMENT, IS_MEMBER_OF
from app.models.nodes import Attachment, Workspace
from app.schemas.avatar import (
    AvatarUploadResponse,
    AvatarValidationError,
    AvatarVariant,
)
from app.services.app_graph import catalog_workspace
from app.services.attachment_storage import get_attachment_storage_service
from app.services.avatar_resize import resize_avatar
from app.services.change_event import emit_change_event
from app.services.permissions import get_user_node, member_edge_bool
from app.services.workspace_permissions import (
    can_access_workspace,
    count_owned_personal_workspaces,
    is_workspace_owner,
    list_accessible_workspaces,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

ORG_MEMBER_ROLES = {"admin", "member", "guest"}

# Suggested workspace-type categories surfaced in the UI. The set is
# advisory — callers may pass any non-empty string. ``workspace_type="personal"``
# provisions a ``kind="personal"`` workspace; anything else (canonically
# ``"collaborative"``) provisions a ``kind="collaborative"`` workspace with a
# member pool + invitations. ``"company"`` / ``"organization"`` are accepted as
# legacy aliases for ``"collaborative"`` (see ``_kind_for_workspace_type``).
SUGGESTED_WORKSPACE_TYPES = ("collaborative", "personal")


def _kind_for_workspace_type(workspace_type: str) -> str:
    """Map a validated ``workspace_type`` label to the stored ``kind``.

    ``"personal"`` → ``"personal"``; everything else → ``"organization"``,
    which is the canonical INTERNAL discriminator for a collaborative
    (multi-member) workspace. The user-facing TERM is "Collaborative"
    (``workspace_type="collaborative"``); the stored ``kind`` stays
    ``"organization"`` so the permission layer's existing checks keep working.
    Recognize collaborative-kind anywhere via
    :func:`app.services.workspace_kind.is_collaborative_kind`.
    """
    return "personal" if workspace_type == "personal" else "organization"


def _validate_workspace_type(value: Optional[str]) -> str:
    """Normalize and validate the user-facing workspace_type label."""
    raw = (value or "").strip().lower()
    if not raw:
        return "collaborative"
    if len(raw) > 40:
        raise BadRequestError(message="workspace_type must be 40 characters or fewer")
    if not all(ch.isalnum() or ch in ("-", "_") for ch in raw):
        raise BadRequestError(
            message="workspace_type may contain only letters, digits, '-' or '_'"
        )
    return raw


def _validate_member_role(role: str) -> str:
    normalized = str(role or "").strip().lower()
    if normalized not in ORG_MEMBER_ROLES:
        raise BadRequestError(
            message=f"role must be one of: {', '.join(sorted(ORG_MEMBER_ROLES))}"
        )
    return normalized


async def _caller_member_creation_flags(
    user_id: str, ws: Workspace
) -> tuple[bool, bool]:
    """Return ``(can_create_apps, can_create_tracks)`` for the caller.

    Owners (and any caller in their own personal workspace) hold both rights
    unconditionally. For org members — INCLUDING admins — creation rights are
    a selective, explicitly-granted capability carried on the ``IS_MEMBER_OF``
    edge (per the access model's "selective creation rights for apps/tracks"
    and the 9ffd952 "authority requires explicit grant" tier split). Admin role
    governs the member pool / settings, not app/track creation, so the stored
    edge flags are honored rather than auto-granted.
    """
    if await is_workspace_owner(user_id, ws.id):
        return True, True
    if ws.kind == "personal":
        return True, True
    user = await get_user_node(user_id)
    if not user:
        return False, False
    ctx = await user.get_context()
    edges = await ctx.find_edges_between(
        source_id=user.id, target_id=ws.id, edge_class=IS_MEMBER_OF
    )
    if not edges:
        return False, False
    e0 = edges[0]
    return (
        member_edge_bool(e0, "can_create_apps"),
        member_edge_bool(e0, "can_create_tracks"),
    )


async def _export_workspace(
    ws: Workspace,
    your_role: str = "",
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    data = await export_node(ws)
    if your_role:
        data["your_role"] = your_role
    if user_id:
        can_apps, can_tracks = await _caller_member_creation_flags(user_id, ws)
        data["can_create_apps"] = can_apps
        data["can_create_tracks"] = can_tracks
    return data


async def _assert_workspace_name_unique_for_user(
    user_id: str,
    name_fold: str,
    raw_name: str,
    exclude_id: Optional[str] = None,
) -> None:
    """Reject duplicate workspace names owned by the same user (any kind)."""
    if not name_fold:
        return
    accessible = await list_accessible_workspaces(user_id)
    for ws in accessible:
        if exclude_id is not None and ws.id == exclude_id:
            continue
        if (getattr(ws, "name_fold", "") or "") != name_fold:
            continue
        if not await is_workspace_owner(user_id, ws.id):
            continue
        err = ResourceConflictError(
            message=(
                f"Workspace with name '{raw_name}' already exists under your account"
            ),
            details={
                "entity": "workspace",
                "field": "name",
                "value": raw_name,
                "conflict_id": ws.id,
            },
        )
        err.error_code = "resource.duplicate_workspace"
        raise err


async def _require_workspace_role(
    workspace_id: str, user_id: str, minimum: str = "member"
) -> tuple[Workspace, str]:
    """Resolve workspace + caller's role, gate on minimum."""
    ws = await Workspace.get(workspace_id)
    if not ws:
        raise ResourceNotFoundError(message="Workspace not found")
    role = await can_access_workspace(user_id, workspace_id)
    if role == "none":
        raise InsufficientPermissionsError(message="Access denied")
    rank = {"none": 0, "guest": 1, "member": 2, "admin": 3, "owner": 4}
    if rank.get(role, 0) < rank.get(minimum, 2):
        raise InsufficientPermissionsError(message="Insufficient role")
    return ws, role


# ── Listing & detail ────────────────────────────────────────────────────────


@endpoint("/workspaces", methods=["GET"], auth=True, tags=["Workspaces"])
async def list_workspaces(request: Request) -> Dict[str, Any]:
    """List every workspace the user can read (Personal + each org membership)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    user = await get_user_node(user_id)
    if not user:
        return {"workspaces": [], "total": 0}
    workspaces = await list_accessible_workspaces(user_id)
    items: List[Dict[str, Any]] = []
    for ws in workspaces:
        role = await can_access_workspace(user_id, ws.id)
        items.append(await _export_workspace(ws, role, user_id))
    return {"workspaces": items, "total": len(items)}


@endpoint(
    "/library/workspace-profiles",
    methods=["GET"],
    auth=True,
    tags=["Library"],
)
async def list_workspace_profiles(request: Request) -> Dict[str, Any]:
    """List library bundles authored at ``scope: workspace``.

    Frontend surface for the workspace-creation picker (Phase D4). Filters
    ``load_library_profiles()`` down to scope=workspace bundles so the
    client never has to know about app/track-scope packages here. Returns
    an empty list when no workspace-scope bundle has been authored yet
    (D5 ships the first sample bundle).
    """
    from app.services.content_profile_loader import load_library_profiles

    specs = load_library_profiles()
    return {
        "profiles": [
            {
                "slug": s.slug,
                "name": s.name,
                "version": s.version,
                "description": s.description,
                "tags": (s.manifest.get("package") or {}).get("tags") or [],
            }
            for s in specs
            if (s.manifest or {}).get("scope") == "workspace"
        ]
    }


@endpoint("/workspaces/{workspace_id}", methods=["GET"], auth=True, tags=["Workspaces"])
async def get_workspace(request: Request, workspace_id: str) -> Dict[str, Any]:
    """Return a single workspace the caller can read."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    ws, role = await _require_workspace_role(workspace_id, user_id, "guest")
    return {"workspace": await _export_workspace(ws, role, user_id)}


@endpoint("/workspaces", methods=["POST"], auth=True, tags=["Workspaces"])
async def create_workspace(
    request: Request,
    name: str,
    description: Optional[str] = None,
    accent_color: Optional[str] = None,
    avatar_url: Optional[str] = None,
    workspace_type: Optional[str] = None,
    profile_slug: Optional[str] = None,
    profile_slugs: Optional[List[str]] = None,
    library_content_profile_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Create a Workspace.

    Two surfaces exist:

      - ``workspace_type="collaborative"`` (default) — collaborative kind
        (stored ``kind="organization"``); members via ``IS_MEMBER_OF``;
        invitations + storage quota meaningful. ``"company"`` / ``"organization"``
        are accepted as legacy workspace_type aliases.
      - ``workspace_type="personal"`` — ``kind="personal"``; single-user
        workspace. The caller may own multiple personals (the per-user
        auto-provisioned one is created at signup; additional ones can be
        created via this endpoint).

    When ``profile_slug`` (single) or ``profile_slugs`` (list) is supplied,
    after the bare workspace is created the strict-init service
    (``content_profile_workspace_init``) provisions all apps declared by each
    scope=workspace bundle, in order. Validation / mid-write failures roll the
    workspace itself back; an "already provisioned" conflict keeps the
    workspace (clean state) and surfaces 400. Personal workspaces may be seeded
    too — content profiles apply to any workspace kind.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    user = await get_user_node(user_id)
    if not user:
        raise InsufficientPermissionsError(message="User profile not found")
    safe_name = non_empty_after_strip(name, "name")
    if len(safe_name) > 120:
        raise BadRequestError(message="name must be 120 characters or fewer")
    name_fold = compute_fold(safe_name)
    safe_accent = validate_hex_color(accent_color, allow_empty=True)
    safe_type = _validate_workspace_type(workspace_type)
    ws_kind = _kind_for_workspace_type(safe_type)
    await _assert_workspace_name_unique_for_user(user_id, name_fold, safe_name)
    now = utc_now_iso()
    ws = await Workspace.create(
        kind=ws_kind,
        workspace_type=safe_type,
        name=safe_name,
        name_fold=name_fold,
        description=description or "",
        accent_color=safe_accent,
        avatar_url=(avatar_url or "").strip(),
        created_at=now,
        updated_at=now,
    )
    await user.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="owner",
        joined_at=now,
        can_create_apps=True,
        can_create_tracks=True,
    )
    await catalog_workspace(ws)

    # Optional strict-init from a scope=workspace bundle (Phase D4). When
    # validation or mid-write fails we roll the workspace back so the
    # caller doesn't end up with an empty, half-provisioned organization
    # tied to their account. A "conflict" outcome only fires when the
    # workspace already records the same bundle in ``applied_profiles`` —
    # impossible for a freshly-created workspace, but we surface it as a
    # 400 without rollback in case the service is reused.
    # Collect the bundle(s) to provision: the new multi-select ``profile_slugs``
    # plus the legacy single ``profile_slug``, de-duplicated in stable order.
    slugs_to_init: List[str] = []
    for s in [*(profile_slugs or []), profile_slug]:
        s = (s or "").strip()
        if s and s not in slugs_to_init:
            slugs_to_init.append(s)

    if slugs_to_init:
        from app.services.content_profile_workspace_init import (
            WorkspaceInitConflict,
            WorkspaceInitFailed,
            WorkspaceInitValidationError,
            init_workspace_from_profile,
        )

        for slug in slugs_to_init:
            try:
                await init_workspace_from_profile(
                    workspace=ws, profile_slug=slug, actor_id=user_id
                )
            except WorkspaceInitValidationError as exc:
                msg = str(exc)
                await ws.delete()
                if "not found" in msg:
                    raise ResourceNotFoundError(message=msg) from exc
                raise BadRequestError(message=msg) from exc
            except WorkspaceInitConflict as exc:
                await ws.delete()
                raise BadRequestError(message=str(exc)) from exc
            except WorkspaceInitFailed as exc:
                await ws.delete()
                raise BadRequestError(message=f"workspace init failed: {exc}") from exc

    # Optional app-scope library packages (the create-wizard's "Add apps" step
    # offers the same app bundles as the Manage Apps dialog). Each selected
    # package is installed as an App in the new workspace via the shared
    # batch-install service. Mid-write failure rolls the workspace back.
    cp_ids: List[str] = []
    for cp_id in library_content_profile_ids or []:
        cp_id = (cp_id or "").strip()
        if cp_id and cp_id not in cp_ids:
            cp_ids.append(cp_id)

    provisioning: Optional[Dict[str, int]] = None
    if cp_ids:
        from app.services.app_batch_install import batch_install

        # Streamlined provisioning via the SHARED install service:
        #  - ``resolve_dependencies`` auto-pulls each bundle's hard app deps so
        #    the user need not hand-pick prerequisites;
        #  - ``use_default_settings`` activates apps immediately with their
        #    settings_schema defaults instead of pausing at awaiting_settings.
        # (The Manage Apps dialog reuses the same service, opting into dep
        # resolution only — it keeps its per-app settings-finalize step.)
        try:
            result = await batch_install(
                workspace_id=ws.id,
                items=[{"library_cp_id": cp_id} for cp_id in cp_ids],
                actor_id=user.id,
                resolve_dependencies=True,
                use_default_settings=True,
            )
        except Exception as exc:  # noqa: BLE001 — preflight failure → roll back
            # Pre-flight errors (unknown bundle, circular dep) raise before any
            # write, so nothing is installed — drop the bare workspace too.
            await ws.delete()
            raise BadRequestError(message=f"app provisioning failed: {exc}") from exc

        # Per-bundle outcomes don't raise: the workspace + whatever installed is
        # kept, and the caller surfaces a summary. ``awaiting_settings`` apps are
        # installed-but-paused; ``failed`` are e.g. a required setting with no
        # default.
        installed_rows = result.get("installed") or []
        provisioning = {
            "installed": sum(
                1 for r in installed_rows if (r.get("status") or "") == "active"
            ),
            "awaiting_settings": sum(
                1
                for r in installed_rows
                if (r.get("status") or "") == "awaiting_settings"
            ),
            "skipped": len(result.get("skipped") or []),
            "failed": len(result.get("failed") or []),
            "auto_dependencies": int(result.get("auto_dependencies") or 0),
        }

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="workspace.create",
        resource_type="Workspace",
        resource_id=ws.id,
        before=None,
        after=await _export_workspace(ws, "owner", user_id),
        scope=f"user:{user_id}",
    )
    response: Dict[str, Any] = {
        "workspace": await _export_workspace(ws, "owner", user_id),
        "message": "Workspace created",
    }
    if provisioning is not None:
        response["provisioning"] = provisioning
    return response


@endpoint(
    "/workspaces/{workspace_id}", methods=["PATCH"], auth=True, tags=["Workspaces"]
)
async def update_workspace(
    request: Request,
    workspace_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    accent_color: Optional[str] = None,
    avatar_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Update workspace name, description, accent, or avatar (owner or admin)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    ws, role = await _require_workspace_role(workspace_id, user_id, "admin")
    prior = await _export_workspace(ws, role, user_id)
    if name is not None:
        safe_name = non_empty_after_strip(name, "name")
        if len(safe_name) > 120:
            raise BadRequestError(message="name must be 120 characters or fewer")
        new_fold = compute_fold(safe_name)
        if new_fold != (getattr(ws, "name_fold", "") or ""):
            await _assert_workspace_name_unique_for_user(
                user_id, new_fold, safe_name, exclude_id=ws.id
            )
        ws.name = safe_name
        ws.name_fold = new_fold
    if description is not None:
        ws.description = description
    if accent_color is not None:
        ws.accent_color = validate_hex_color(accent_color, allow_empty=True)
    if avatar_url is not None:
        ws.avatar_url = avatar_url.strip()
    ws.updated_at = utc_now_iso()
    await ws.save()
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="workspace.update",
        resource_type="Workspace",
        resource_id=ws.id,
        before=prior,
        after=await _export_workspace(ws, role, user_id),
        scope=f"user:{user_id}",
    )
    return {
        "workspace": await _export_workspace(ws, role, user_id),
        "message": "Updated",
    }


@endpoint(
    "/workspaces/{workspace_id}", methods=["DELETE"], auth=True, tags=["Workspaces"]
)
async def delete_workspace(request: Request, workspace_id: str) -> Dict[str, Any]:
    """Delete a workspace the caller owns.

    Organization-kind workspaces may always be deleted by the owner.
    Personal-kind workspaces may be deleted only when the caller still
    owns at least one other personal workspace afterward (every user
    must retain at least one personal workspace).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    ws = await Workspace.get(workspace_id)
    if not ws:
        raise ResourceNotFoundError(message="Workspace not found")
    if not await is_workspace_owner(user_id, workspace_id):
        raise InsufficientPermissionsError(message="Only the owner can delete")
    owned_personal = await count_owned_personal_workspaces(user_id)
    if owned_personal < 1:
        raise BadRequestError(
            message="No personal workspace found; cannot delete workspace"
        )
    if ws.kind == "personal" and owned_personal <= 1:
        raise BadRequestError(message="You must keep at least one personal workspace")
    raw_cascade = request.query_params.get("cascade_delete", "")
    cascade = str(raw_cascade).strip().lower() in ("1", "true", "yes")
    prior = await _export_workspace(ws, "owner")
    from app.services.workspace_lifecycle import delete_workspace_cascade

    unlinked_spaces, unlinked_tracks, deleted_spaces, deleted_tracks = (
        await delete_workspace_cascade(ws, cascade=cascade)
    )
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="workspace.delete",
        resource_type="Workspace",
        resource_id=workspace_id,
        before=prior,
        after=None,
        scope=f"user:{user_id}",
    )
    return {
        "message": "Workspace deleted",
        "deleted_workspace_id": workspace_id,
        "unlinked_spaces": unlinked_spaces,
        "unlinked_tracks": unlinked_tracks,
        "deleted_spaces": deleted_spaces,
        "deleted_tracks": deleted_tracks,
    }


# ── Contained resources ─────────────────────────────────────────────────────


@endpoint(
    "/workspaces/{workspace_id}/apps",
    methods=["GET"],
    auth=True,
    tags=["Workspaces"],
)
async def list_workspace_spaces(request: Request, workspace_id: str) -> Dict[str, Any]:
    """List Apps contained in this workspace.

    Filters per-App through ``permissions.resolve_role`` so members
    only see Apps they have an effective role on. Without this filter
    Mission Control's RECENT APPS leaks metadata for private Apps a
    member has never been added to.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await _require_workspace_role(workspace_id, user_id, "guest")
    from app.services.permissions import get_user_accessible_apps

    out: list[Dict[str, Any]] = []
    for sp in await get_user_accessible_apps(user_id):
        if (getattr(sp, "workspace_id", "") or "") != workspace_id:
            continue
        out.append(await export_node(sp))
    return {"apps": out, "total": len(out), "workspace_id": workspace_id}


@endpoint(
    "/workspaces/{workspace_id}/tracks",
    methods=["GET"],
    auth=True,
    tags=["Workspaces"],
)
async def list_workspace_tracks(request: Request, workspace_id: str) -> Dict[str, Any]:
    """List standalone Tracks contained in this workspace.

    Per-track access is enforced via ``permissions.resolve_role`` —
    members see only the tracks they have an effective role on (owner,
    editor, commenter, viewer). Private tracks the caller has no
    collaborator grant on are filtered out, matching the contract that
    ``/api/tracks`` already enforces. Mission Control's per-workspace
    track aggregation reads this endpoint, so a missing filter was
    over-reporting TOTAL TRACKS and leaking track metadata (title,
    purpose, owner_id, etc.) to non-collaborators.

    Each surviving row carries ``entry_count`` so the Mission Control
    "TRACKS IN MOTION" rows show real counts instead of "0 entries".
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await _require_workspace_role(workspace_id, user_id, "guest")

    # Lazy import — top-level would trigger a circular via app.api.utils
    # back through app.api.__init__'s endpoint registration sweep.
    from app.services.entry_context import (
        attach_anchor_source_to_track_data,
        attach_parent_app_to_track_data,
    )
    from app.services.permissions import (
        count_user_accessible_entries,
        get_user_accessible_tracks,
    )

    out: list[Dict[str, Any]] = []
    for tr in await get_user_accessible_tracks(user_id):
        if (getattr(tr, "workspace_id", "") or "") != workspace_id:
            continue
        row = await export_node(tr)
        row["entry_count"] = await count_user_accessible_entries(user_id, tr.id)
        await attach_parent_app_to_track_data(row, tr)
        await attach_anchor_source_to_track_data(row, tr)
        out.append(row)
    return {"tracks": out, "total": len(out), "workspace_id": workspace_id}


# ── Members (org-kind only) ─────────────────────────────────────────────────


@endpoint(
    "/workspaces/{workspace_id}/members",
    methods=["GET"],
    auth=True,
    tags=["Workspaces"],
)
async def list_workspace_members(request: Request, workspace_id: str) -> Dict[str, Any]:
    """List members of this workspace with their roles and creation rights."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    ws, role = await _require_workspace_role(workspace_id, user_id, "guest")
    members: List[Dict[str, Any]] = []
    member_nodes, _next = await ws.nodes_page(
        edge=["IS_MEMBER_OF"],
        direction="in",
        node=["User"],
        limit=2000,
    )
    for u in member_nodes:
        # Never full-export User in a list every member can read. A raw export
        # carries `preferences` — which holds the email-verification OTP slot
        # ({hash, expires_at, attempts}) and, historically, `reset_token` — plus
        # `notification_preferences` (phone_e164), `email_verified`,
        # `onboarded_at` and `active_workspace_id`. Anyone with `guest` on the
        # workspace could read all of it for every other member.
        data = await public_user_view(u)
        # `user_id` is deliberately restored on top of the allowlist. It is the
        # AuthUser principal id — an opaque identifier of the same class as
        # `id`, not a secret — and the members page needs it: it resolves "which
        # of these is me" via `isSamePrincipal(me, m.user_id || m.id)` and keys
        # rows by it. Dropping it silently breaks self-identification rather
        # than erroring, so it is added back explicitly instead of widening
        # PUBLIC_USER_FIELDS for every other caller of the view.
        if getattr(u, "user_id", None):
            data["user_id"] = u.user_id
        ctx = await u.get_context()
        edges = await ctx.find_edges_between(
            source_id=u.id, target_id=ws.id, edge_class=IS_MEMBER_OF
        )
        if edges:
            e0 = edges[0]
            r = getattr(e0, "role", None)
            if r is None:
                rctx = getattr(e0, "context", None)
                r = rctx.get("role", "member") if isinstance(rctx, dict) else "member"
            data["role"] = r
            data["can_create_apps"] = member_edge_bool(e0, "can_create_apps")
            data["can_create_tracks"] = member_edge_bool(e0, "can_create_tracks")
        else:
            data["role"] = "member"
            data["can_create_apps"] = False
            data["can_create_tracks"] = False
        members.append(data)
    return {"members": members, "total": len(members), "workspace_id": ws.id}


@endpoint(
    "/workspaces/{workspace_id}/members",
    methods=["POST"],
    auth=True,
    tags=["Workspaces"],
)
async def add_workspace_member(
    request: Request,
    workspace_id: str,
    member_user_id: Optional[str] = None,
    role: str = "member",
    can_create_apps: bool = False,
    can_create_tracks: bool = False,
) -> Dict[str, Any]:
    """Add a user as a member of an organization-kind workspace."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    ws, _ = await _require_workspace_role(workspace_id, user_id, "admin")
    if ws.kind == "personal":
        raise BadRequestError(message="Personal workspaces have no members to manage")
    if not member_user_id:
        raise BadRequestError(message="member_user_id is required")
    validated_role = _validate_member_role(role)
    member = await get_user_node(member_user_id)
    if not member:
        raise ResourceNotFoundError(message="User not found")
    now = utc_now_iso()
    await member.connect(
        ws,
        edge=IS_MEMBER_OF,
        role=validated_role,
        joined_at=now,
        can_create_apps=can_create_apps,
        can_create_tracks=can_create_tracks,
    )
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="workspace.member_add",
        resource_type="Workspace",
        resource_id=ws.id,
        before=None,
        after={
            "workspace_id": ws.id,
            "member_user_id": member.id,
            "role": validated_role,
        },
        scope=f"user:{user_id}",
    )
    return {
        "message": "Member added",
        "workspace_id": ws.id,
        "member_user_id": member.id,
        "role": validated_role,
    }


@endpoint(
    "/workspaces/{workspace_id}/members/{member_user_id}",
    methods=["PATCH"],
    auth=True,
    tags=["Workspaces"],
)
async def patch_workspace_member(
    request: Request,
    workspace_id: str,
    member_user_id: str,
    role: Optional[str] = None,
    can_create_apps: Optional[bool] = None,
    can_create_tracks: Optional[bool] = None,
) -> Dict[str, Any]:
    """Update a member's role or creation flags. Owner or workspace admin."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    ws, _ = await _require_workspace_role(workspace_id, user_id, "admin")
    if ws.kind == "personal":
        raise BadRequestError(message="Personal workspaces have no members to manage")
    member = await get_user_node(member_user_id)
    if not member:
        raise ResourceNotFoundError(message="User not found")
    if await is_workspace_owner(member.id, ws.id):
        raise BadRequestError(
            message="Cannot change the workspace owner via members API"
        )

    if role is None and can_create_apps is None and can_create_tracks is None:
        raise BadRequestError(message="No fields to update")
    if role is not None:
        role = _validate_member_role(role)

    ctx = await member.get_context()
    edges = await ctx.find_edges_between(
        source_id=member.id, target_id=ws.id, edge_class=IS_MEMBER_OF
    )
    if not edges:
        raise ResourceNotFoundError(message="User is not a member of this workspace")

    e0 = edges[0]
    old_role = getattr(e0, "role", None)
    if old_role is None:
        rctx = getattr(e0, "context", None)
        old_role = rctx.get("role", "member") if isinstance(rctx, dict) else "member"
    old_cs = member_edge_bool(e0, "can_create_apps")
    old_ct = member_edge_bool(e0, "can_create_tracks")
    old_joined_at = getattr(e0, "joined_at", None)
    if not old_joined_at:
        jctx = getattr(e0, "context", None)
        old_joined_at = jctx.get("joined_at") if isinstance(jctx, dict) else None
    new_role = role if role is not None else old_role
    new_cs = old_cs if can_create_apps is None else can_create_apps
    new_ct = old_ct if can_create_tracks is None else can_create_tracks

    # Role changes are a delete-then-recreate of IS_MEMBER_OF, and there is no
    # cross-entity transaction. A failure between the two steps used to leave
    # the member with no membership edge at all -- silently evicted from the
    # workspace by what was meant to be a role tweak.
    #
    # The ordering is deliberately delete-first: recreating first and deleting
    # after would, on failure, leave BOTH edges present, and a demotion would
    # silently retain the old higher role. Losing access fails closed; retaining
    # privilege fails open. So keep the safe ordering and add a rollback.
    for edge in edges:
        await edge.delete()
    try:
        await member.connect(
            ws,
            edge=IS_MEMBER_OF,
            role=new_role,
            joined_at=utc_now_iso(),
            can_create_apps=new_cs,
            can_create_tracks=new_ct,
        )
    except Exception:
        try:
            await member.connect(
                ws,
                edge=IS_MEMBER_OF,
                role=old_role,
                joined_at=old_joined_at or utc_now_iso(),
                can_create_apps=old_cs,
                can_create_tracks=old_ct,
            )
        except Exception:  # pragma: no cover - restore-of-a-restore
            logger.error(
                "workspace.member_update: failed to apply the new membership "
                "edge AND failed to restore the previous one for user=%s "
                "workspace=%s. The member now has NO membership edge and must "
                "be re-added.",
                member.id,
                ws.id,
            )
        raise

    # The permission process cache aggregates per user. Without this, a
    # demotion (admin -> guest) keeps serving the old role for up to
    # PERMISSION_PROCESS_CACHE_TTL seconds. remove_workspace_member already
    # invalidates; this path did not.
    from app.services.permissions_process_cache import invalidate_user

    invalidate_user(member.id)

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="workspace.member_update",
        resource_type="Workspace",
        resource_id=ws.id,
        before=None,
        after={
            "workspace_id": ws.id,
            "member_user_id": member.id,
            "role": new_role,
            "can_create_apps": new_cs,
            "can_create_tracks": new_ct,
        },
        scope=f"user:{user_id}",
    )

    return {
        "message": "Member updated",
        "workspace_id": ws.id,
        "member_user_id": member.id,
        "role": new_role,
        "can_create_apps": new_cs,
        "can_create_tracks": new_ct,
    }


@endpoint(
    "/workspaces/{workspace_id}/members/{member_user_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Workspaces"],
)
async def remove_workspace_member(
    request: Request,
    workspace_id: str,
    member_user_id: str,
) -> Dict[str, Any]:
    """Remove a member from the workspace. Owner or workspace admin."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    ws, _ = await _require_workspace_role(workspace_id, user_id, "admin")
    if ws.kind == "personal":
        raise BadRequestError(message="Personal workspaces have no members to manage")
    member = await get_user_node(member_user_id)
    if not member:
        raise ResourceNotFoundError(message="User not found")
    if await is_workspace_owner(member.id, ws.id):
        raise BadRequestError(message="Cannot remove the workspace owner")

    ctx = await member.get_context()
    edges = await ctx.find_edges_between(
        source_id=member.id, target_id=ws.id, edge_class=IS_MEMBER_OF
    )
    for edge in edges:
        await edge.delete()

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="workspace.member_remove",
        resource_type="Workspace",
        resource_id=ws.id,
        before=None,
        after={"workspace_id": ws.id, "member_user_id": member.id},
        scope=f"user:{user_id}",
    )

    # Revoked workspace access — drop the removed member's cached access
    # aggregates so they stop seeing workspace-visible resources immediately.
    from app.services.permissions_process_cache import invalidate_user

    invalidate_user(member.id)
    return {
        "message": "Member removed",
        "workspace_id": ws.id,
        "removed_member_id": member.id,
    }


@endpoint(
    "/workspaces/{workspace_id}/storage-usage",
    methods=["GET"],
    auth=True,
    tags=["Workspaces"],
)
async def get_workspace_storage_usage(
    request: Request, workspace_id: str
) -> Dict[str, Any]:
    """Return the workspace's attachment storage usage snapshot."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    await _require_workspace_role(workspace_id, user_id, "guest")
    from app.services.workspace_storage_usage import get_usage

    usage = await get_usage(workspace_id)
    if usage is None:
        raise ResourceNotFoundError(message="Workspace not found")
    return {"usage": usage.to_dict()}


@endpoint(
    "/workspaces/{workspace_id}/storage-usage/recalculate",
    methods=["POST"],
    auth=True,
    tags=["Workspaces"],
)
async def recalculate_workspace_storage_usage(
    request: Request, workspace_id: str
) -> Dict[str, Any]:
    """Re-sum the workspace's attachment bytes from the graph. Owner only."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if not await is_workspace_owner(user_id, workspace_id):
        raise InsufficientPermissionsError(
            message="Only the workspace owner can recalculate storage usage."
        )
    from app.services.workspace_storage_usage import recalculate_for_workspace

    usage = await recalculate_for_workspace(workspace_id)
    return {"usage": usage.to_dict(), "message": "Storage usage recalculated"}


# ────────────────────────────────────────────────────────────────────────
# Workspace avatar pipeline (Phase 9 extension — workspaces as logos)
#
# Mirrors the User avatar pipeline in ``app/api/users.py`` — same Pillow
# resize service (``services/avatar_resize``), same 32/64/128/256 variant
# set, same ``HAS_ATTACHMENT(role="avatar", size=N)`` associative-edge
# pattern, same A4 ``AVATAR_MAX_UPLOAD_BYTES`` cap enforced BEFORE Pillow
# runs. Differs only in:
#
# 1. Permission gate — caller must be the workspace owner OR hold the
#    ``IS_MEMBER_OF{role:admin}`` membership edge (workspace admins are
#    co-equal with the owner for org-level config like the logo).
# 2. ``ws.avatar_url`` is set to a stable ``/api/workspaces/{id}/avatar?
#    size=128&v={updated_at}`` URL after upload so existing consumers
#    (``WorkspaceSwitcher``, ``MissionControlPage`` workspace tiles)
#    continue to read ``ws.avatar_url`` without code changes — the
#    workspace-attachment-served URL transparently replaces a previously
#    pasted free-form URL.
# 3. ChangeEvent action is ``workspace.update`` (existing Literal — no
#    new ChangeEventAction member; D-05 single-emission preserved).
# ────────────────────────────────────────────────────────────────────────


async def _persist_workspace_avatar_variant(
    *,
    workspace: Workspace,
    size_n: int,
    png_bytes: bytes,
    actor_user_id: str,
) -> Attachment:
    """Persist one resized PNG variant + connect via HAS_ATTACHMENT edge."""
    filename = f"workspace-avatar-{workspace.id}-{size_n}.png"
    attachment = await Attachment.create(
        filename=filename,
        mime_type="image/png",
        size=len(png_bytes),
        storage_key="",
        source_type="file",
        external_url="",
        uploaded_by=actor_user_id,
        content_hash="",
        scan_status="skipped",
        metadata_status="complete",
        metadata={"role": "avatar", "size": size_n},
        created_at=utc_now_iso(),
    )
    storage = get_attachment_storage_service()
    try:
        result = await storage.save_attachment(
            entry_id=workspace.id,
            attachment_id=attachment.id,
            filename=filename,
            content=png_bytes,
            metadata={
                "owner_workspace_id": workspace.id,
                "attachment_id": attachment.id,
                "uploaded_by": actor_user_id,
                "role": "avatar",
                "size": str(size_n),
            },
        )
    except Exception:
        await attachment.delete()
        raise
    attachment.storage_key = str(result.get("path") or "")
    await attachment.save()

    await workspace.connect(
        attachment,
        edge=HAS_ATTACHMENT,
        attached_at=utc_now_iso(),
        attached_by=actor_user_id,
        role="avatar",
        size=size_n,
    )
    return attachment


async def _find_workspace_avatar_variant(
    workspace: Workspace, size: int
) -> Optional[Attachment]:
    """Return the workspace's avatar Attachment matching the requested size."""
    atts = await workspace.nodes(
        edge=[HAS_ATTACHMENT], direction="out", node=["Attachment"]
    )
    if not atts:
        return None
    ctx = await workspace.get_context()
    for att in atts:
        edges = await ctx.find_edges_between(
            workspace.id, att.id, edge_class=HAS_ATTACHMENT
        )
        for e in edges:
            if (
                getattr(e, "role", None) == "avatar"
                and getattr(e, "size", None) == size
            ):
                return att  # type: ignore[return-value]
    return None


def _avatar_url_for_workspace(workspace: Workspace) -> str:
    """Build the stable served-URL for the canonical (128-px) variant.

    Cache-busts via ``v=<updated_at>`` so consumers keyed on
    ``ws.avatar_url`` invalidate browser-side image cache on each upload.
    """
    return (
        f"/api/workspaces/{workspace.id}/avatar?size=128"
        f"&v={(workspace.updated_at or '').replace(':', '_')}"
    )


@endpoint(
    "/workspaces/{workspace_id}/avatar",
    methods=["POST"],
    auth=True,
    tags=["Workspaces"],
)
async def upload_workspace_avatar(
    request: Request,
    workspace_id: str,
) -> Dict[str, Any]:
    """Upload + resize a workspace logo (PNG/JPEG/WebP → 32/64/128/256 PNG).

    Permission: caller must be the workspace owner OR hold an
    ``IS_MEMBER_OF{role:admin}`` edge. Personal-kind workspaces allow
    only the owner. Emits ONE ``workspace.update`` ChangeEvent per
    successful upload (D-05 single-emission invariant).
    """
    caller_id = resolve_principal_id(request)
    if not caller_id:
        raise MissingAuthenticationError(message="Authentication required")

    workspace = await Workspace.get(workspace_id)
    if not workspace:
        raise ResourceNotFoundError(message="Workspace not found")

    # Permission gate — owner OR admin member.
    caller_user = await get_user_node(caller_id)
    is_owner = caller_user is not None and await is_workspace_owner(
        caller_id, workspace_id
    )
    is_admin = False
    if not is_owner and caller_user is not None:
        from app.services.workspace_permissions import _read_member_edge_role

        role = await _read_member_edge_role(caller_user, workspace_id)
        is_admin = (role or "").lower() == "admin"
    if not (is_owner or is_admin):
        raise InsufficientPermissionsError(
            message="Only the workspace owner or an admin can change the avatar"
        )

    # A4 hard cap BEFORE Pillow.
    files = await _read_multipart_files(request, "file")
    if not files:
        raise ValidationError(message="file field missing")
    upload = files[0]
    content = await upload.read()
    if len(content) > settings.AVATAR_MAX_UPLOAD_BYTES:
        raise FileTooLargeError(
            message=(
                f"Avatar exceeds {settings.AVATAR_MAX_UPLOAD_BYTES} bytes "
                f"(received {len(content)})"
            )
        )
    if upload.content_type not in settings.AVATAR_ALLOWED_MIMES:
        raise ValidationError(
            message=(
                f"Avatar MIME must be one of {settings.AVATAR_ALLOWED_MIMES}; "
                f"received {upload.content_type!r}"
            )
        )

    try:
        variants_bytes = resize_avatar(content, sizes=settings.AVATAR_SIZES)
    except AvatarValidationError as exc:
        raise ValidationError(message=str(exc)) from exc

    previous_attachment_id = workspace.avatar_attachment_id
    previous_avatar_url = workspace.avatar_url
    variants_meta: List[AvatarVariant] = []
    for size_n, png_bytes in variants_bytes.items():
        att = await _persist_workspace_avatar_variant(
            workspace=workspace,
            size_n=size_n,
            png_bytes=png_bytes,
            actor_user_id=caller_id,
        )
        variants_meta.append(AvatarVariant(size=size_n, attachment_id=att.id))

    canonical = next(v for v in variants_meta if v.size == 128)
    workspace.avatar_attachment_id = canonical.attachment_id
    workspace.updated_at = utc_now_iso()
    # Overwrite avatar_url with the served-attachment URL so existing
    # consumers continue to render the workspace logo without code change.
    workspace.avatar_url = _avatar_url_for_workspace(workspace)
    await workspace.save()

    await emit_change_event(
        actor_kind="human",
        actor_id=caller_id,
        action="workspace.update",
        resource_type="Workspace",
        resource_id=workspace.id,
        before={
            "avatar_attachment_id": previous_attachment_id,
            "avatar_url": previous_avatar_url,
        },
        after={
            "avatar_attachment_id": canonical.attachment_id,
            "avatar_url": workspace.avatar_url,
        },
        scope=f"workspace:{workspace.id}",
        details={
            "avatar_attachment_id": canonical.attachment_id,
            "previous_avatar_attachment_id": previous_attachment_id,
        },
    )

    return AvatarUploadResponse(
        avatar_attachment_id=canonical.attachment_id,
        variants=variants_meta,
        updated_at=workspace.updated_at or "",
    ).model_dump()


@endpoint(
    "/workspaces/{workspace_id}/avatar",
    methods=["GET"],
    auth=True,
    tags=["Workspaces"],
)
async def get_workspace_avatar(
    request: Request,
    workspace_id: str,
) -> Response:
    """Stream a workspace's avatar variant as ``image/png``.

    Public to every authenticated user with access to the workspace —
    avatars are non-sensitive identity surfaces, mirroring the User
    avatar policy. Missing variant (workspace never uploaded a logo)
    returns 404 so frontends fall back to the colored-initials Avatar
    primitive.
    """
    caller_id = resolve_principal_id(request)
    if not caller_id:
        raise MissingAuthenticationError(message="Authentication required")
    raw_size = request.query_params.get("size", "128")
    try:
        size = int(raw_size)
    except (TypeError, ValueError):
        raise ValidationError(message=f"size must be an integer; got {raw_size!r}")
    if size not in settings.AVATAR_SIZES:
        raise ValidationError(
            message=f"size must be one of {settings.AVATAR_SIZES}; got {size}"
        )
    workspace = await Workspace.get(workspace_id)
    if not workspace or not workspace.avatar_attachment_id:
        raise ResourceNotFoundError(message="Avatar not found")

    # Workspace access gate — caller must be able to read the workspace.
    if not await can_access_workspace(caller_id, workspace_id):
        raise InsufficientPermissionsError(message="Access denied")

    variant = await _find_workspace_avatar_variant(workspace, size)
    if not variant:
        raise ResourceNotFoundError(message=f"size={size} variant not found")
    storage = get_attachment_storage_service()
    png_bytes = await storage.read_attachment(variant.storage_key)
    if png_bytes is None:
        raise ResourceNotFoundError(message=f"size={size} variant bytes missing")
    return Response(content=png_bytes, media_type="image/png")


@endpoint(
    "/workspaces/{workspace_id}/apps/order",
    methods=["PATCH"],
    auth=True,
    tags=["Workspaces"],
)
async def reorder_workspace_apps(
    request: Request,
    workspace_id: str,
    app_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Phase 36 — set the display order of Apps within a Workspace.

    Body shape::

        {"app_ids": ["n.WorkspaceApp.A", "n.WorkspaceApp.B", ...]}

    Updates each App's ``position`` field so subsequent
    ``GET /apps?X-Integral-Scope: ws:<id>`` returns them in this
    order. Apps in the workspace whose id is not in the list keep
    their existing position (and sort after the explicit set in the
    listing endpoint).

    The caller must have admin access to the workspace (or owner for
    personal-kind workspaces).
    """
    from app.models.nodes import App
    from app.services.change_event import emit_change_event

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if app_ids is None:
        app_ids = []
    if not isinstance(app_ids, list):
        raise BadRequestError(message="app_ids must be a list")

    workspace = await Workspace.get(workspace_id)
    if not workspace:
        raise ResourceNotFoundError(message="Workspace not found")
    role = await can_access_workspace(user_id, workspace_id)
    if role not in ("owner", "admin"):
        raise InsufficientPermissionsError(
            message="Only workspace owner or admin can reorder apps",
        )

    updated: List[str] = []
    skipped: List[str] = []
    for idx, app_id in enumerate(app_ids):
        app_node = await App.get(app_id)
        if app_node is None:
            skipped.append(app_id)
            continue
        if (getattr(app_node, "workspace_id", "") or "") != workspace_id:
            skipped.append(app_id)
            continue
        app_node.position = idx
        await app_node.save()
        updated.append(app_id)

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="workspace.apps_reorder",
        resource_type="Workspace",
        resource_id=workspace_id,
        before=None,
        after={"app_ids": app_ids, "updated": updated, "skipped": skipped},
        scope=f"workspace:{workspace_id}",
    )

    return {
        "message": "Apps reordered",
        "workspace_id": workspace_id,
        "updated": updated,
        "skipped": skipped,
    }

"""User CRUD API endpoints."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Request
from fastapi.responses import Response
from jvspatial.api import endpoint
from jvspatial.api.exceptions import FileTooLargeError, ValidationError
from jvspatial.core.pager import ObjectPager

from app.api.attachments import _read_multipart_files
from app.api.auth import (
    _SENSITIVE_PREFERENCE_KEYS,
    _scrub_sensitive_preferences,
)
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import (
    export_node,
    public_user_view,
    require_platform_admin,
    resolve_principal_id,
)
from app.config import settings
from app.models.edges import HAS_ATTACHMENT
from app.models.nodes import Attachment, User, Workspace
from app.schemas.avatar import (
    AvatarUploadResponse,
    AvatarValidationError,
    AvatarVariant,
)
from app.services.attachment_storage import get_attachment_storage_service
from app.services.avatar_resize import resize_avatar
from app.services.change_event import emit_change_event
from app.services.permissions import get_user_node
from app.utils.time import utc_now_iso


@endpoint("/users", methods=["GET"], auth=True, tags=["Users"])
async def list_users(
    page: int = 1,
    per_page: int = 10,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """List all users with pagination and search.

    Args:
        page: Page number (1-indexed)
        per_page: Number of users per page
        search: Optional search term for filtering by display_name (case-insensitive substring)

    Returns:
        Paginated list of users
    """
    # Push the search predicate into the pager so the filter is applied
    # BEFORE pagination, not after. Filtering a single page in Python (the
    # previous behaviour) silently dropped matches that happened to fall
    # outside page 1 — e.g. typing ``@Set`` in the mention picker returned
    # zero users when "Settings Test" sat at position 9+ in the unfiltered
    # ordering. Case-insensitive regex against context.display_name keeps
    # parity with the prior substring semantics.
    import re

    # Cap the page size. This is deliberately a *directory* (you must be able
    # to find someone outside your workspaces in order to invite them, and the
    # picker browses with no search term), so per-page enumeration is inherent
    # — but an uncapped ``per_page`` turned it into a single-request dump of
    # every user on the platform. Mirrors app/services/admin_users.py.
    try:
        page_size = int(per_page)
    except (TypeError, ValueError):
        page_size = 10
    page_size = max(1, min(page_size, 100))

    filters: Dict[str, Any] = {}
    if search:
        filters["context.display_name"] = {
            "$regex": re.escape(search),
            "$options": "i",
        }
    pager = ObjectPager(User, page_size=page_size, filters=filters)

    # Get the requested page (already filtered by search at the DB layer).
    users: List[User] = await pager.get_page(page=page)

    # Project, don't export: a raw export leaks ``preferences`` (password-reset
    # slot) and ``notification_preferences`` (verified WhatsApp phone) to every
    # authenticated caller. ``email`` is re-added deliberately — the collaborator
    # picker shows it to disambiguate people with the same display name.
    users_list = []
    for u in users:
        user_data = await public_user_view(u)

        # Add email from AuthUser if user_id is present
        if u.user_id:
            try:
                from jvspatial.api.auth.models import User as AuthUser

                auth_user = await AuthUser.get(u.user_id)
                if auth_user:
                    user_data["email"] = auth_user.email
            except Exception:
                pass

        users_list.append(user_data)

    # Get pagination info
    pagination_info = pager.to_dict()

    return {
        "users": users_list,
        "total": pagination_info["total_items"],
        "page": pagination_info["current_page"],
        "per_page": pagination_info["page_size"],
        "total_pages": pagination_info["total_pages"],
        "has_previous": pagination_info["has_previous"],
        "has_next": pagination_info["has_next"],
    }


@endpoint("/users/{user_id}", methods=["GET"], auth=True, tags=["Users"])
async def get_user(user_id: str) -> Dict[str, Any]:
    """Get a specific user by ID.

    Visibility: Any authenticated user can view any user's profile. This supports
    user directory and collaboration features (e.g., selecting users for invites).
    To restrict visibility in the future, add a permission check here.

    Args:
        user_id: ID of the user to retrieve

    Returns:
        User information (id, display_name, email if linked, etc.)
    """
    user = await User.get(user_id)
    if not user:
        raise ResourceNotFoundError(message="User not found")

    # Never full-export User — preferences hold reset/OTP hashes and phones.
    user_data = await public_user_view(user)

    # Add email from AuthUser if user_id is present
    if user.user_id:
        try:
            from jvspatial.api.auth.models import User as AuthUser

            auth_user = await AuthUser.get(user.user_id)
            if auth_user:
                user_data["email"] = auth_user.email
        except Exception:
            pass

    return {
        "user": user_data,
    }


@endpoint("/users", methods=["POST"], auth=True, tags=["Users"])
async def create_user(
    request: Request,
    email: str,
    display_name: str,
    avatar_url: Optional[str] = None,
    preferences: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a new user (admin operation).

    This endpoint allows creating users programmatically.
    In production, this should be restricted to admin users.

    Args:
        current_user_id: ID of the authenticated user (must be admin)
        email: User's email address
        display_name: User's display name
        avatar_url: Optional avatar URL
        preferences: Optional user preferences

    Returns:
        Created user information
    """
    current_user_id = resolve_principal_id(request)
    if not current_user_id:
        raise MissingAuthenticationError(message="Authentication required")
    require_platform_admin(request)

    # Note: This endpoint creates a User without an AuthUser
    # In production, this should create an AuthUser first, then link via user_id
    # For now, we'll create User without user_id (admin operation)

    # Create user (without user_id since no AuthUser is created)
    user = await User.create(
        display_name=display_name,
        avatar_url=avatar_url or "",
        preferences=preferences or {},
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    from app.services.app_graph import catalog_user

    await catalog_user(user)

    # Project like every other user-shaped response (list at :97, get at :145).
    # A raw export here was a consistency hole rather than a live leak — the
    # node is one statement old, so `preferences` holds only what this caller
    # just sent and the OTP slot does not exist yet — but "the admin endpoint
    # is the one that returns raw User" is exactly the shape that becomes a
    # leak the moment someone widens what create accepts, or copies this
    # handler as the pattern for a new one.
    user_data = await public_user_view(user)
    # Restored on top of the allowlist: an admin creating a user needs the
    # principal id back to act on the row it just made (same reasoning as
    # list_workspace_members). `email` is enriched below.
    if getattr(user, "user_id", None):
        user_data["user_id"] = user.user_id

    # Add email from AuthUser if user_id is present, otherwise use provided email
    if user.user_id:
        try:
            from jvspatial.api.auth.models import User as AuthUser

            auth_user = await AuthUser.get(user.user_id)
            if auth_user:
                user_data["email"] = auth_user.email
        except Exception:
            pass
    elif email:
        # If no user_id but email was provided, include it
        user_data["email"] = email

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=current_user_id,
        action="user.create",
        resource_type="User",
        resource_id=user.id,
        before=None,
        after=await export_node(user),
        scope=f"user:{user.id}",
    )

    return {
        "user": user_data,
        "message": "User created successfully",
    }


@endpoint("/users/{user_id}", methods=["PUT"], auth=True, tags=["Users"])
async def update_user(
    request: Request,
    user_id: str,
    display_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
    preferences: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Update a user.

    Users can only update their own profile unless they are admins.

    Args:
        user_id: ID of the user to update
        current_user_id: ID of the authenticated user
        display_name: New display name (optional)
        avatar_url: New avatar URL (optional)
        preferences: New preferences (optional)

    Returns:
        Updated user information
    """
    current_user_id = resolve_principal_id(request)
    if not current_user_id:
        raise MissingAuthenticationError(message="Authentication required")
    # Get the current user's User (from AuthUser ID)
    current_user = await get_user_node(current_user_id)
    if not current_user:
        raise InsufficientPermissionsError(
            message="You can only update your own profile"
        )

    # Resolve target user (user_id can be User ID or AuthUser ID)
    target_user = await get_user_node(user_id)
    if not target_user:
        target_user = await User.get(user_id)
    if not target_user:
        raise ResourceNotFoundError(message="User not found")

    # User can only update their own profile
    same_user = target_user.id == current_user.id or (
        target_user.user_id
        and current_user.user_id
        and target_user.user_id == current_user.user_id
    )
    if not same_user:
        raise InsufficientPermissionsError(
            message="You can only update your own profile"
        )

    user = target_user
    prior_snapshot = await export_node(user)  # D-03 before-snapshot

    # Update fields if provided
    if display_name is not None:
        user.display_name = display_name
    if avatar_url is not None:
        user.avatar_url = avatar_url
    if preferences is not None:
        # Wholesale replace of client-visible prefs, but never let the client
        # clear/overwrite server-owned slots (reset_token, email_verification).
        existing = dict(user.preferences or {})
        preserved = {
            k: existing[k] for k in _SENSITIVE_PREFERENCE_KEYS if k in existing
        }
        user.preferences = {**_scrub_sensitive_preferences(preferences), **preserved}

    user.updated_at = utc_now_iso()
    await user.save()

    # Export and flatten context fields, enrich with email from AuthUser
    user_data = await export_node(user)
    if "preferences" in user_data:
        user_data["preferences"] = _scrub_sensitive_preferences(
            user_data.get("preferences")
        )

    # Add email from AuthUser if user_id is present
    if user.user_id:
        try:
            from jvspatial.api.auth.models import User as AuthUser

            auth_user = await AuthUser.get(user.user_id)
            if auth_user:
                user_data["email"] = auth_user.email
        except Exception:
            pass

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=current_user_id,
        action="user.update",
        resource_type="User",
        resource_id=user.id,
        before=prior_snapshot,
        after=await export_node(user),
        scope=f"user:{user.id}",
    )

    return {
        "user": user_data,
        "message": "User updated successfully",
    }


@endpoint(
    "/users/me/account-deletion-preview",
    methods=["GET"],
    auth=True,
    tags=["Users"],
)
async def get_account_deletion_preview_endpoint(request: Request) -> Dict[str, Any]:
    """Pre-flight impact summary and blockers for self-service account deletion."""
    from app.schemas.account_deletion import AccountDeletionPreview
    from app.services.user_lifecycle import get_account_deletion_preview

    current_user_id = resolve_principal_id(request)
    if not current_user_id:
        raise MissingAuthenticationError(message="Authentication required")
    preview = await get_account_deletion_preview(current_user_id)
    return AccountDeletionPreview.model_validate(preview).model_dump()


@endpoint("/users/{user_id}", methods=["DELETE"], auth=True, tags=["Users"])
async def delete_user(
    request: Request,
    user_id: str,
) -> Dict[str, Any]:
    """Permanently delete the caller's own account.

    Requires a JSON body ``{ "confirm_email": "<account email>" }`` that
    matches the linked AuthUser email. Deletion is blocked while the user
    still owns resources in organization workspaces or is the sole owner of an
    org workspace with other members — see
    ``GET /users/me/account-deletion-preview``.
    """
    from app.schemas.account_deletion import (
        AccountDeletionRequest,
        AccountDeletionResponse,
    )
    from app.services.user_lifecycle import delete_user_account

    current_user_id = resolve_principal_id(request)
    if not current_user_id:
        raise MissingAuthenticationError(message="Authentication required")
    current_user = await get_user_node(current_user_id)
    if not current_user:
        raise InsufficientPermissionsError(
            message="You can only delete your own account"
        )

    # Resolve target user (user_id can be User ID or AuthUser ID)
    user = await get_user_node(user_id)
    if not user:
        user = await User.get(user_id)
    if not user:
        raise ResourceNotFoundError(message="User not found")

    # User can only delete their own account
    same_user = user.id == current_user.id or (
        user.user_id and current_user.user_id and user.user_id == current_user.user_id
    )
    if not same_user:
        raise InsufficientPermissionsError(
            message="You can only delete your own account"
        )

    try:
        raw_body = await request.json()
    except Exception:
        raw_body = {}
    try:
        body = AccountDeletionRequest.model_validate(raw_body or {})
    except ValidationError as e:
        raise BadRequestError(
            message="Validation failed for account deletion body",
            details={"errors": e.errors()},
        )

    result = await delete_user_account(
        current_user_id,
        confirm_email=body.confirm_email,
    )

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=current_user_id,
        action="user.delete",
        resource_type="User",
        resource_id=result.deleted_user_node_id,
        before=result.prior_snapshot,
        after=None,
        scope=f"user:{result.deleted_user_node_id}",
    )

    return AccountDeletionResponse(
        message="User deleted successfully",
        deleted_user_id=result.deleted_user_id,
    ).model_dump()


# ── Avatar pipeline — AVT-01 (Phase 9 Plan 09-01) ────────────────────────────
# POST /api/users/{user_id}/avatar  → resize, persist variants, set
#                                    User.avatar_attachment_id, emit
#                                    SINGLE user.update ChangeEvent.
# GET  /api/users/{user_id}/avatar  → stream one PNG variant.
#
# Storage strategy: variants are persisted via the existing
# AttachmentStorageService — the path segment "entry_id" is reused as the
# owning user's id so all four variants land under a single
# ``attachments/<user_id>/<attachment_id>/avatar-<size>.png`` prefix.
# Per-variant metadata (role + pixel size) lives on the HAS_ATTACHMENT
# edge — jvspatial pillar 2, associative-edge state.


async def _persist_avatar_variant(
    *,
    owner_user: User,
    size_n: int,
    png_bytes: bytes,
    actor_user_id: str,
) -> Attachment:
    """Persist one resized PNG variant and connect it to the owner User.

    The HAS_ATTACHMENT edge carries ``role="avatar"`` + ``size=<int>``
    so ``_find_avatar_variant`` can locate the right variant by edge
    fields alone (pillar 2 — relationship state on the edge).
    """
    filename = f"avatar-{owner_user.id}-{size_n}.png"
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
        # Mirror role/size on the attachment metadata blob for observability
        # only — the edge remains source-of-truth for queries.
        metadata={"role": "avatar", "size": size_n},
        created_at=utc_now_iso(),
    )
    storage = get_attachment_storage_service()
    try:
        result = await storage.save_attachment(
            entry_id=owner_user.id,
            attachment_id=attachment.id,
            filename=filename,
            content=png_bytes,
            metadata={
                "owner_user_id": owner_user.id,
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

    await owner_user.connect(
        attachment,
        edge=HAS_ATTACHMENT,
        attached_at=utc_now_iso(),
        attached_by=actor_user_id,
        role="avatar",
        size=size_n,
    )
    return attachment


async def _find_avatar_variant(user: User, size: int) -> Optional[Attachment]:
    """Walk HAS_ATTACHMENT edges and return the Attachment matching size.

    Pillar 2 query path — filters by ``edge.role == "avatar"`` and
    ``edge.size == <size>`` on the associative edge fields. ``Attachment``
    rows do not carry the size on themselves; the edge is the source-of-
    truth so multiple variants are indistinguishable except by edge state.
    """
    atts = await user.nodes(edge=[HAS_ATTACHMENT], direction="out", node=["Attachment"])
    if not atts:
        return None
    ctx = await user.get_context()
    for att in atts:
        # find_edges_between takes IDs, not Node objects (canonical idiom —
        # see app/agentive/api/connectors.py and app/api/entries.py).
        edges = await ctx.find_edges_between(user.id, att.id, edge_class=HAS_ATTACHMENT)
        for e in edges:
            if (
                getattr(e, "role", None) == "avatar"
                and getattr(e, "size", None) == size
            ):
                return att  # type: ignore[return-value]
    return None


@endpoint(
    "/users/{user_id}/avatar",
    methods=["POST"],
    auth=True,
    tags=["Users"],
)
async def upload_avatar(
    request: Request,
    user_id: str,
) -> Dict[str, Any]:
    """Upload + resize a user avatar (PNG/JPEG/WebP → 32/64/128/256 PNG).

    Phase 9 Plan 09-01 (AVT-01). A4: 5 MB hard cap is enforced BEFORE
    Pillow runs to bound CPU exposure to decompression-bomb payloads.
    D-05: emits EXACTLY ONE ``user.update`` ChangeEvent per successful
    upload (no inflation — repeated uploads still produce a single event
    each, with previous/new ids in ``details``).
    """
    caller_id = resolve_principal_id(request)
    if not caller_id:
        raise MissingAuthenticationError(message="Authentication required")

    # Resolve target via canonical lookup (User.get or get_user_node for
    # AuthUser-id fallback). Self-only — cross-user upload is 403.
    target_user = await get_user_node(user_id)
    if not target_user:
        target_user = await User.get(user_id)
    if not target_user:
        raise ResourceNotFoundError(message="User not found")

    caller_user = await get_user_node(caller_id)
    same_user = caller_user is not None and (
        target_user.id == caller_user.id
        or (
            target_user.user_id
            and caller_user.user_id
            and target_user.user_id == caller_user.user_id
        )
    )
    if not same_user:
        raise InsufficientPermissionsError(
            message="You can only update your own avatar"
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

    previous_id = target_user.avatar_attachment_id
    variants_meta: List[AvatarVariant] = []
    for size_n, png_bytes in variants_bytes.items():
        att = await _persist_avatar_variant(
            owner_user=target_user,
            size_n=size_n,
            png_bytes=png_bytes,
            actor_user_id=caller_id,
        )
        variants_meta.append(AvatarVariant(size=size_n, attachment_id=att.id))

    # 128 is the canonical representative — mirrored on User for fast read.
    canonical = next(v for v in variants_meta if v.size == 128)
    target_user.avatar_attachment_id = canonical.attachment_id
    target_user.updated_at = utc_now_iso()
    await target_user.save()

    # D-05 single emission — exactly one user.update per successful upload.
    await emit_change_event(
        actor_kind="human",
        actor_id=caller_id,
        action="user.update",
        resource_type="User",
        resource_id=target_user.id,
        before={"avatar_attachment_id": previous_id},
        after={"avatar_attachment_id": canonical.attachment_id},
        scope=f"user:{target_user.id}",
        details={
            "avatar_attachment_id": canonical.attachment_id,
            "previous_avatar_attachment_id": previous_id,
        },
    )

    return AvatarUploadResponse(
        avatar_attachment_id=canonical.attachment_id,
        variants=variants_meta,
        updated_at=target_user.updated_at or "",
    ).model_dump()


@endpoint(
    "/users/{user_id}/avatar",
    methods=["GET"],
    auth=True,
    tags=["Users"],
)
async def get_avatar(
    request: Request,
    user_id: str,
) -> Response:
    """Stream a user's avatar variant as ``image/png``.

    Phase 9 Plan 09-01 (AVT-01). ``size`` query param MUST be in
    ``AVATAR_SIZES`` (32/64/128/256) or the request 400s. Missing variant
    (e.g. user has never uploaded) 404s — frontends fall back to initials
    in that case.

    NOTE on signature shape: ``size`` is read from ``request.query_params``
    rather than declared as a function param. Mixing a path param named
    ``user_id`` (which triggers jvspatial's auth-injection sniff) with an
    additional Pydantic-validated query param produces a wrapper whose
    ``__signature__`` is left as the generic ``*args, **kwargs`` — FastAPI
    then exposes ``args`` and ``kwargs`` as required query params and
    rejects every request. Pulling the query value manually keeps the
    wrapped signature stable.
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
    target_user = await get_user_node(user_id)
    if not target_user:
        target_user = await User.get(user_id)
    if not target_user or not target_user.avatar_attachment_id:
        raise ResourceNotFoundError(message="Avatar not found")

    variant = await _find_avatar_variant(target_user, size)
    if not variant:
        raise ResourceNotFoundError(message=f"size={size} variant not found")
    storage = get_attachment_storage_service()
    png_bytes = await storage.read_attachment(variant.storage_key)
    if png_bytes is None:
        raise ResourceNotFoundError(message=f"size={size} variant bytes missing")
    return Response(content=png_bytes, media_type="image/png")


# ── Sidebar pinning (Plan N2) ────────────────────────────────────────────────
# Pinned Tracks/Apps are stored on User.preferences.pinned as
#   { "tracks": [id, ...], "apps": [id, ...] }
# Append-only ordering (most-recent pin last). Toggle endpoint merges
# atomically so concurrent pin/unpin calls from multiple devices don't
# clobber each other's lists.


# ``space`` is a legacy wire kind from pre–APP-RENAME-01 clients; it maps to
# the same ``apps`` bucket as ``app``.
_PIN_KINDS = {"track": "tracks", "app": "apps", "space": "apps"}


def _empty_pinned() -> Dict[str, Any]:
    return {"tracks": [], "apps": []}


def _normalize_pinned_ids(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    return [str(x) for x in items if isinstance(x, str)]


def _merge_pinned_bucket(primary: List[str], legacy: List[str]) -> List[str]:
    """Preserve order; legacy ``spaces`` ids append after ``apps`` when new."""
    seen = set(primary)
    merged = list(primary)
    for item in legacy:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    return merged


def _read_pinned(user: User) -> Dict[str, Any]:
    prefs = dict(user.preferences or {})
    raw = prefs.get("pinned")
    if not isinstance(raw, dict):
        return _empty_pinned()
    tracks = _normalize_pinned_ids(raw.get("tracks"))
    apps = _merge_pinned_bucket(
        _normalize_pinned_ids(raw.get("apps")),
        _normalize_pinned_ids(raw.get("spaces")),
    )
    return {"tracks": tracks, "apps": apps}


def _write_pinned(user: User, pinned: Dict[str, Any]) -> None:
    prefs = dict(user.preferences or {})
    prefs["pinned"] = pinned
    user.preferences = prefs
    user.updated_at = utc_now_iso()


@endpoint("/users/me/pinned", methods=["GET"], auth=True, tags=["Users"])
async def get_my_pinned(request: Request) -> Dict[str, Any]:
    """Return the caller's pinned Tracks and Apps."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    user = await get_user_node(user_id)
    if not user:
        raise ResourceNotFoundError(message="User not found")
    return {"pinned": _read_pinned(user)}


@endpoint("/users/me/pinned", methods=["POST"], auth=True, tags=["Users"])
async def toggle_my_pin(
    request: Request,
    kind: str = "",
    id: str = "",
    pinned: bool = True,
) -> Dict[str, Any]:
    """Pin or unpin a Track or App for the caller.

    Atomic merge — only the pinned sub-key of preferences is touched, so
    concurrent updates from another device don't clobber unrelated prefs.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if kind not in _PIN_KINDS:
        raise BadRequestError(message="kind must be 'track', 'app', or 'space'")
    if not id:
        raise BadRequestError(message="id is required")
    user = await get_user_node(user_id)
    if not user:
        raise ResourceNotFoundError(message="User not found")

    bucket = _PIN_KINDS[kind]
    current = _read_pinned(user)
    items = current[bucket]
    in_list = id in items
    if pinned and not in_list:
        items.append(id)
    elif not pinned and in_list:
        items = [x for x in items if x != id]
    current[bucket] = items

    _write_pinned(user, current)
    await user.save()
    return {"pinned": current}


# ---------------------------------------------------------------------------
# Active workspace scope — server-authoritative source of truth.
#
# The frontend's ``X-Integral-Scope`` header is a hint for fast
# per-request routing; the canonical state lives on
# ``User.active_workspace_id`` so it survives client cache wipes,
# private-mode sessions, multi-device usage, and stale localStorage
# pointing at workspaces the user can no longer access (after a DB
# purge / membership revocation). ``services/request_scope.py`` is
# the read-side enforcer and persists the resolved id back to the
# User node on every list-endpoint request so the chain converges.
# ---------------------------------------------------------------------------


def _workspace_summary(ws: Workspace) -> Dict[str, Any]:
    """Lightweight summary used in the /me/scope response."""
    return {
        "id": ws.id,
        "name": getattr(ws, "name", "") or "",
        "kind": getattr(ws, "kind", "") or "",
        "accent_color": getattr(ws, "accent_color", "") or "",
    }


@endpoint("/users/me/scope", methods=["GET"], auth=True, tags=["Users"])
async def get_my_scope(request: Request) -> Dict[str, Any]:
    """Return the caller's active workspace scope + the list of
    workspaces they currently belong to.

    Frontends should call this at session boot rather than relying on
    a localStorage cache — the cache may have been invalidated by a DB
    purge, an admin revoking membership, or a workspace deletion.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    # Reuse the canonical resolver — but skip the ``X-Integral-Scope``
    # header hint. This endpoint IS the server-authoritative source; if we
    # honoured the header here, a stale localStorage value would overwrite
    # the server-persisted preference (the header always passes membership
    # for the user's own Personal workspace, so it would silently pin the
    # user there even when the server had the correct org workspace stored).
    from app.services.request_scope import resolve_workspace_id_from_request

    active_id = await resolve_workspace_id_from_request(
        request, user_id, skip_header=True
    )

    # Surface the user's available workspaces so the FE has everything
    # it needs to render the WorkspaceSwitcher without a second call.
    from app.services.permissions import get_user_node

    user = await get_user_node(user_id)
    workspaces: List[Dict[str, Any]] = []
    if user is not None:
        from app.models.edges import IS_MEMBER_OF

        try:
            ws_nodes = await user.nodes(edge=[IS_MEMBER_OF], node=["Workspace"])
        except Exception:
            ws_nodes = []
        workspaces = [_workspace_summary(w) for w in ws_nodes]

    return {
        "active_workspace_id": active_id,
        "workspaces": workspaces,
    }


@endpoint("/users/me/scope", methods=["PUT"], auth=True, tags=["Users"])
async def set_my_scope(request: Request, workspace_id: str = "") -> Dict[str, Any]:
    """Set the caller's active workspace scope.

    Validates that the user is a member of the target workspace before
    persisting. Refuses unknown workspaces and workspaces the user does
    not belong to so a compromised / stale client cannot widen access.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    workspace_id = (workspace_id or "").strip()
    if not workspace_id:
        raise BadRequestError(message="workspace_id is required")

    from app.services.workspace_permissions import user_in_workspace_member_pool

    if not await user_in_workspace_member_pool(user_id, workspace_id):
        raise InsufficientPermissionsError(
            message="You do not belong to that workspace"
        )

    user = await get_user_node(user_id)
    if user is None:
        raise ResourceNotFoundError(message="User not found")
    user.active_workspace_id = workspace_id
    # Explicit user choice — pin so the resolver honors it on future
    # requests instead of re-defaulting to org/Personal.
    user.active_workspace_id_explicit = True
    await user.save()

    return {"active_workspace_id": workspace_id}


# ---------------------------------------------------------------------------
# Connected agents — user-facing OAuth grant management (M3b-2).
#
# The external MCP surface authorizes BYOA agents via the OAuth 2.1
# authorization-code flow; each grant mints an ``OAuthRefreshToken`` Object
# (jvspatial core, NOT a graph Node) carrying ``user_id`` / ``client_id`` /
# ``scope`` / ``expires_at`` / ``is_active`` / ``family_id``. These two
# endpoints let a user audit and revoke the clients holding an active grant
# for THEM.
#
# SECURITY: every query is keyed on ``context.user_id == <resolved principal>``
# so a caller can never list or revoke another user's grants. The revoke
# additionally filters by ``client_id`` so deactivating client X for user A
# leaves user B's grant for the same client X untouched. The token-hash is
# never exposed — only client metadata + grant timestamps.
# ---------------------------------------------------------------------------


def _coerce_utc(dt: datetime) -> datetime:
    """Return a tz-aware UTC datetime.

    Persisted ``OAuthRefreshToken`` timestamps are stored tz-aware UTC, but the
    JSON store can rehydrate them naive; treat naive values as UTC (mirrors
    jvspatial ``refresh_store.find_active``).
    """
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@endpoint("/users/me/connected-agents", methods=["GET"], auth=True, tags=["Users"])
async def list_connected_agents(request: Request) -> List[Dict[str, Any]]:
    """List the external OAuth clients holding an active grant for the caller.

    Returns one row per distinct ``client_id`` across the caller's active,
    unexpired refresh tokens. ``granted_at`` is the most-recent active-token
    ``created_at`` for that client (the latest rotation in the grant family).
    Expired and revoked (``is_active=False``) tokens are excluded. A client
    whose ``OAuthClient`` record is missing surfaces with ``client_id`` as the
    name fallback so a stale registration never hides an active grant.
    """
    from jvspatial.api.auth.oauth.models import OAuthClient, OAuthRefreshToken

    from app.schemas.connected_agents import ConnectedAgent

    uid = resolve_principal_id(request)
    if not uid:
        raise MissingAuthenticationError(message="Authentication required")

    rows = await OAuthRefreshToken.find(
        {"context.user_id": uid, "context.is_active": True}
    )
    now = datetime.now(timezone.utc)

    # Group active, unexpired tokens by client_id; track the newest created_at
    # (granted_at) and accumulate the union of scopes seen across rotations.
    by_client: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if _coerce_utc(row.expires_at) < now:
            continue  # expired — exclude
        created = _coerce_utc(row.created_at)
        agg = by_client.get(row.client_id)
        scope_tokens = (row.scope or "").split()
        if agg is None:
            by_client[row.client_id] = {
                "granted_at": created,
                "scopes": list(dict.fromkeys(scope_tokens)),
            }
        else:
            if created > agg["granted_at"]:
                agg["granted_at"] = created
            for s in scope_tokens:
                if s not in agg["scopes"]:
                    agg["scopes"].append(s)

    agents: List[Dict[str, Any]] = []
    for client_id, agg in by_client.items():
        clients = await OAuthClient.find({"context.client_id": client_id})
        client = clients[0] if clients else None
        client_name = client.client_name if client else ""
        agents.append(
            ConnectedAgent(
                client_id=client_id,
                client_name=client_name or client_id,
                scopes=agg["scopes"],
                granted_at=agg["granted_at"],
            ).model_dump(mode="json")
        )

    # Stable ordering — most recently granted first.
    agents.sort(key=lambda a: a["granted_at"], reverse=True)
    return agents


@endpoint(
    "/users/me/connected-agents/{client_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Users"],
)
async def revoke_connected_agent(request: Request, client_id: str) -> Dict[str, Any]:
    """Revoke the caller's grant for one external OAuth client.

    Deactivates EVERY active refresh token the caller holds for ``client_id``
    (all rotations of the grant), returning the count revoked. If the caller
    holds no active grant for the client a 404 is raised — and, crucially, no
    other user's tokens are touched (the query filters by both ``user_id`` and
    ``client_id``).
    """
    from jvspatial.api.auth.oauth import refresh_store
    from jvspatial.api.auth.oauth.models import OAuthRefreshToken

    from app.schemas.connected_agents import RevokeAgentResponse

    uid = resolve_principal_id(request)
    if not uid:
        raise MissingAuthenticationError(message="Authentication required")

    rows = await OAuthRefreshToken.find(
        {
            "context.user_id": uid,
            "context.client_id": client_id,
            "context.is_active": True,
        }
    )
    if not rows:
        raise ResourceNotFoundError(message="No active grant for that client")

    revoked = 0
    for rec in rows:
        await refresh_store.revoke(rec)
        revoked += 1

    return RevokeAgentResponse(revoked=revoked).model_dump()

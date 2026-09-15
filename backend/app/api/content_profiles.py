"""Library and attached ContentProfile APIs.

Includes library package management (list/get/publish/update/deprecate/validate)
and attached-profile customization, detach/revert, and derivation endpoints.
"""

import json
from typing import Any, Dict, List, Optional

import yaml
from fastapi import Request
from jvspatial.api import endpoint

from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    MissingAuthenticationError,
    ResourceNotFoundError,
)
from app.api.utils import export_node, resolve_principal_id
from app.api.validators_common import (
    compute_fold,
    non_empty_after_strip,
    validate_semver_ish,
)
from app.models.edges import CATALOGS, CONTAINS
from app.models.nodes import (
    CONTENT_PROFILES_REGISTRY_ID,
    App,
    ContentProfile,
    ContentProfiles,
    EntryType,
    Track,
    View,
)
from app.schemas.content_profiles import ImportPreviewResponse, PackagePreviewItem
from app.schemas.policy import Resource, Subject
from app.services.app_graph import (
    get_app_attached_content_profile,
    get_or_create_views_registry_for_content_profile,
    get_track_attached_content_profile,
)
from app.services.change_event import emit_change_event
from app.services.content_profile_runtime import (
    compile_canonical_manifest,
    normalize_entry_type_form_schema,
    normalize_view_config,
    sync_attached_manifest,
)
from app.services.permissions import (
    can_publish_content_profiles_under_workspace,
    get_user_node,
)
from app.services.policy_engine import evaluate as policy_evaluate
from app.utils.time import utc_now_iso

# These packages were merged/retired, but older development databases can
# still contain their library rows. Keep the rows recoverable for existing
# installs while preventing new UI or agent installs from selecting them.
_RETIRED_LIBRARY_SLUGS = frozenset({"payroll_filings", "guyana-payroll-hr"})
_RETIRED_LIBRARY_NAMES = frozenset({"Payroll Filings", "Guyana Payroll (HR-linked)"})


def _is_visible_library_profile(profile: ContentProfile) -> bool:
    metadata = getattr(profile, "metadata", None) or {}
    package = (getattr(profile, "manifest", None) or {}).get("package") or {}
    slug = str(metadata.get("slug") or package.get("name") or "").strip()
    name = str(getattr(profile, "name", "") or "").strip()
    return slug not in _RETIRED_LIBRARY_SLUGS and name not in _RETIRED_LIBRARY_NAMES


@endpoint("/content-profiles", methods=["GET"], auth=True, tags=["Content profiles"])
async def list_library_content_profiles(
    request: Request,
    type_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """List cataloged library content packages (Phase 6 MCP-04 — ``integral_list_profiles``).

    Optional ``type_hint`` ranks library packages by token-intersection score
    over ``package.name + package.description + cp.name``. Per
    locked-post-research §Q7 + §Q8, the helper is called inline (no MCP
    re-dispatch — Pitfall 6). Response gains ``_type_hint_matches`` (sorted
    descending) when ``type_hint`` is supplied.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    reg = await ContentProfiles.get(CONTENT_PROFILES_REGISTRY_ID)
    if not reg:
        result: Dict[str, Any] = {"content_profiles": [], "total": 0}
        if type_hint:
            result["_type_hint_matches"] = []
        return result
    packages, _next = await reg.nodes_page(
        edge=[CATALOGS],
        node=["ContentProfile"],
        limit=500,
    )
    lib_only = [
        p
        for p in packages
        if getattr(p, "library_package", False)
        and (getattr(p, "metadata", None) or {}).get("seed_status") != "inactive"
        and _is_visible_library_profile(p)
    ]
    items = [await export_node(p) for p in lib_only]
    response: Dict[str, Any] = {"content_profiles": items, "total": len(items)}
    if type_hint:
        response["_type_hint_matches"] = await resolve_type_hint(type_hint)
    return response


@endpoint(
    "/content-profiles/{content_profile_id}",
    methods=["GET"],
    auth=True,
    tags=["Content profiles"],
)
async def get_library_content_profile(
    request: Request, content_profile_id: str
) -> Dict[str, Any]:
    """Get a library package by id (must be cataloged as library)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    cp = await ContentProfile.get(content_profile_id)
    if not cp or not getattr(cp, "library_package", False):
        raise ResourceNotFoundError(message="Content profile not found")
    return {"content_profile": await export_node(cp)}


@endpoint(
    "/workspaces/{workspace_id}/content-profiles",
    methods=["GET"],
    auth=True,
    tags=["Content profiles"],
)
async def list_workspace_content_profiles(
    request: Request,
    workspace_id: str,
) -> Dict[str, Any]:
    """List workspace-private library packages for one workspace."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    user = await get_user_node(user_id)
    if not user:
        raise InsufficientPermissionsError(message="Access denied")
    if not await can_publish_content_profiles_under_workspace(user_id, workspace_id):
        raise InsufficientPermissionsError(message="Access denied")
    cps = await ContentProfile.find(
        {"context.library_package": True, "context.workspace_id": workspace_id}
    )
    return {
        "content_profiles": [await export_node(cp) for cp in cps],
        "total": len(cps),
        "workspace_id": workspace_id,
    }


@endpoint("/content-profiles", methods=["POST"], auth=True, tags=["Content profiles"])
async def publish_content_profile(
    request: Request,
    name: str,
    description: str = "",
    version: Optional[str] = None,
    workspace_id: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
    manifest_yaml: Optional[str] = None,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish a new workspace-private library content profile package."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if not workspace_id:
        raise BadRequestError(message="workspace_id is required for publishing")
    if not await can_publish_content_profiles_under_workspace(user_id, workspace_id):
        raise InsufficientPermissionsError(message="Access denied")
    safe_name = non_empty_after_strip(name, "name")
    if len(safe_name) > 120:
        raise BadRequestError(message="name must be 120 characters or fewer")
    name_fold = compute_fold(safe_name)
    safe_version = validate_semver_ish(version, allow_empty=True) or None
    canonical = compile_canonical_manifest(
        manifest=manifest,
        manifest_yaml=manifest_yaml,
        scope_hint=scope,
    )
    from app.services.uniqueness import assert_unique

    await assert_unique(
        ContentProfile,
        {
            "context.library_package": True,
            "context.workspace_id": workspace_id,
            "context.name_fold": name_fold,
            "context.version": safe_version,
        },
        entity="content_profile",
        field_label="name+version",
        value=f"{safe_name} {safe_version or '(no version)'}",
        scope_label="in this workspace",
    )
    reg = await ContentProfiles.get(CONTENT_PROFILES_REGISTRY_ID)
    if not reg:
        raise ResourceNotFoundError(message="Content profiles registry missing")
    now = utc_now_iso()
    cp = await ContentProfile.create(
        name=safe_name,
        name_fold=name_fold,
        description=description or "",
        version=safe_version,
        workspace_id=workspace_id,
        manifest=canonical,
        scope=str(canonical.get("scope") or ""),
        library_package=True,
        created_at=now,
        updated_at=now,
    )
    await reg.connect(cp, edge=CATALOGS, cataloged_at=now)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.create",
        resource_type="ContentProfile",
        resource_id=cp.id,
        before=None,
        after=await export_node(cp),
        scope=f"user:{user_id}",
    )

    return {
        "content_profile": await export_node(cp),
        "message": "Content profile published",
    }


@endpoint(
    "/content-profiles/{content_profile_id}",
    methods=["PUT"],
    auth=True,
    tags=["Content profiles"],
)
async def update_library_content_profile(
    request: Request,
    content_profile_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    version: Optional[str] = None,
    manifest: Optional[Dict[str, Any]] = None,
    manifest_yaml: Optional[str] = None,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """Update a workspace-private library package, or mutate a draft CP.

    Drafts (``status == "draft"``) are not library packages but go through
    this same endpoint so the PUT contract stays uniform. Permission is
    delegated to ``_resolve_cp_edit_permission`` against the draft's
    published parent.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    cp = await ContentProfile.get(content_profile_id)
    if not cp:
        raise ResourceNotFoundError(message="Content profile not found")

    is_draft = getattr(cp, "status", "published") == "draft"
    if is_draft:
        # Draft mutations: delegate permission to the published parent.
        parent = await ContentProfile.get(cp.draft_of_id) if cp.draft_of_id else None
        if parent is None or not await _resolve_cp_edit_permission(
            user_id=user_id, cp=parent
        ):
            raise InsufficientPermissionsError(message="Access denied")
    else:
        if not getattr(cp, "library_package", False):
            raise ResourceNotFoundError(message="Content profile not found")
        workspace_id = getattr(cp, "workspace_id", None) or ""
        if not workspace_id:
            raise InsufficientPermissionsError(
                message="Only workspace-scoped packages can be updated via this API"
            )
        if not await can_publish_content_profiles_under_workspace(
            user_id, workspace_id
        ):
            raise InsufficientPermissionsError(message="Access denied")
    prior_snapshot = await export_node(cp)  # D-03 before-snapshot
    if name is not None:
        cp.name = name
    if description is not None:
        cp.description = description
    if version is not None:
        cp.version = version
    if manifest is not None or manifest_yaml is not None:
        canonical = compile_canonical_manifest(
            manifest=manifest,
            manifest_yaml=manifest_yaml,
            scope_hint=scope,
        )
        cp.manifest = canonical
        cp.scope = str(canonical.get("scope") or cp.scope or "")
    cp.updated_at = utc_now_iso()
    await cp.save()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.update",
        resource_type="ContentProfile",
        resource_id=cp.id,
        before=prior_snapshot,
        after=await export_node(cp),
        scope=f"user:{user_id}",
    )

    return {
        "content_profile": await export_node(cp),
        "message": "Content profile updated",
    }


@endpoint(
    "/content-profiles/{content_profile_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Content profiles"],
)
async def deprecate_library_content_profile(
    request: Request,
    content_profile_id: str,
) -> Dict[str, Any]:
    """Deprecate a library package by deleting it from the catalog."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    cp = await ContentProfile.get(content_profile_id)
    if not cp or not getattr(cp, "library_package", False):
        raise ResourceNotFoundError(message="Content profile not found")
    workspace_id = getattr(cp, "workspace_id", None) or ""
    if not workspace_id:
        raise InsufficientPermissionsError(
            message="Only workspace-scoped packages can be deleted via this API"
        )
    if not await can_publish_content_profiles_under_workspace(user_id, workspace_id):
        raise InsufficientPermissionsError(message="Access denied")
    prior_snapshot = await export_node(cp)  # D-03 before-snapshot
    await cp.delete()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.delete",
        resource_type="ContentProfile",
        resource_id=content_profile_id,
        before=prior_snapshot,
        after=None,
        scope=f"user:{user_id}",
    )

    return {
        "message": "Content profile deprecated",
        "content_profile_id": content_profile_id,
    }


@endpoint(
    "/content-profiles/validate",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def validate_content_profile_manifest(
    request: Request,
    manifest: Optional[Dict[str, Any]] = None,
    manifest_yaml: Optional[str] = None,
    scope: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate and normalize canonical v1 manifest payloads for agent workflows."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    try:
        canonical = compile_canonical_manifest(
            manifest=manifest, manifest_yaml=manifest_yaml, scope_hint=scope
        )
    except BadRequestError as exc:
        return {
            "valid": False,
            "canonical_manifest": None,
            "issues": [{"code": "manifest_validation_error", "message": str(exc)}],
        }
    return {"valid": True, "canonical_manifest": canonical, "issues": []}


# ---------------------------------------------------------------------------
# In-Place Customization: attached-profile entry types
# ---------------------------------------------------------------------------


@endpoint(
    "/tracks/{track_id}/content-profile/entry-types",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def add_entry_type_to_track_profile(
    request: Request,
    track_id: str,
    name: str,
    icon: str = "document",
    form_schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Add an EntryType to the track-attached ContentProfile (updates manifest + creates node)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    cp = await get_track_attached_content_profile(track)
    if not cp:
        raise ResourceNotFoundError(message="Track has no attached content profile")

    safe_name = (name or "").strip()
    if not safe_name:
        raise BadRequestError(message="name is required")
    name_fold = compute_fold(safe_name)
    from app.services.uniqueness import assert_unique

    await assert_unique(
        EntryType,
        {"context.track_id": track_id, "context.name_fold": name_fold},
        entity="entry_type",
        field_label="name",
        value=safe_name,
        scope_label="in this track",
    )

    now = utc_now_iso()
    entry_type = await EntryType.create(
        name=safe_name,
        name_fold=name_fold,
        icon=icon,
        form_schema=normalize_entry_type_form_schema(form_schema),
        track_id=track_id,
        is_template=False,
        created_at=now,
        updated_at=now,
    )
    prior_cp_snapshot = await export_node(cp)  # D-03 before-snapshot for parent CP
    try:
        await cp.connect(entry_type, edge=CONTAINS, added_at=now)
    except Exception:
        try:
            await entry_type.delete()
        except Exception:
            pass
        raise
    await sync_attached_manifest(cp)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.update",
        resource_type="ContentProfile",
        resource_id=cp.id,
        before=prior_cp_snapshot,
        after=await export_node(cp),
        scope=f"track:{track_id}",
    )

    return {
        "entry_type": await export_node(entry_type),
        "message": "Entry type added to profile",
    }


@endpoint(
    "/tracks/{track_id}/content-profile/entry-types/{entry_type_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Content profiles"],
)
async def remove_entry_type_from_track_profile(
    request: Request,
    track_id: str,
    entry_type_id: str,
) -> Dict[str, Any]:
    """Remove an EntryType from the track-attached ContentProfile (updates manifest + deletes node)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    cp = await get_track_attached_content_profile(track)
    if not cp:
        raise ResourceNotFoundError(message="Track has no attached content profile")

    et = await EntryType.get(entry_type_id)
    if not et or et.track_id != track_id:
        raise ResourceNotFoundError(message="Entry type not found in this track")

    prior_cp_snapshot = await export_node(cp)  # D-03 before-snapshot
    await et.delete()
    await sync_attached_manifest(cp)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.update",
        resource_type="ContentProfile",
        resource_id=cp.id,
        before=prior_cp_snapshot,
        after=await export_node(cp),
        scope=f"track:{track_id}",
    )

    return {
        "message": "Entry type removed from profile",
        "deleted_entry_type_id": entry_type_id,
    }


# ---------------------------------------------------------------------------
# In-Place Customization: attached-profile views
# ---------------------------------------------------------------------------


@endpoint(
    "/tracks/{track_id}/content-profile/views",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def add_view_to_track_profile(
    request: Request,
    track_id: str,
    name: str,
    type: str = "feed",
    view_type: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    is_default: bool = False,
) -> Dict[str, Any]:
    """Add a View to the track-attached ContentProfile (updates manifest + creates node)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    cp = await get_track_attached_content_profile(track)
    if not cp:
        raise ResourceNotFoundError(message="Track has no attached content profile")

    resolved_type = view_type or type
    from app.views import content_profile_view_types as _vtr

    valid_types = set(_vtr.allowed_keys())
    if resolved_type not in valid_types:
        raise BadRequestError(
            message=f"type must be one of: {', '.join(sorted(valid_types))}"
        )

    from app.api.entries import _slugify_entry_type_key
    from app.services.app_graph import catalog_view_under_track

    now = utc_now_iso()
    normalized_config = normalize_view_config(resolved_type, config or {})

    default_entry_type_key = ""
    entry_types_on_track = await EntryType.find({"context.track_id": track_id})
    if entry_types_on_track:
        default_entry_type_key = _slugify_entry_type_key(
            str(getattr(entry_types_on_track[0], "name", "") or "")
        )

    if is_default:
        vreg = await get_or_create_views_registry_for_content_profile(cp, track)
        for v in await vreg.nodes(edge=[CATALOGS], node=["View"]):
            if v.is_default:
                v.is_default = False
                await v.save()

    view = await View.create(
        name=name,
        type=resolved_type,
        config=normalized_config,
        track_id=track_id,
        content_profile_id=cp.id,
        default_entry_type_key=default_entry_type_key,
        is_default=is_default,
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    prior_cp_snapshot = await export_node(cp)  # D-03 before-snapshot
    await catalog_view_under_track(track, view)
    await sync_attached_manifest(cp)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.update",
        resource_type="ContentProfile",
        resource_id=cp.id,
        before=prior_cp_snapshot,
        after=await export_node(cp),
        scope=f"track:{track_id}",
    )

    return {
        "view": await export_node(view),
        "message": "View added to profile",
    }


@endpoint(
    "/tracks/{track_id}/content-profile/views/{view_id}",
    methods=["DELETE"],
    auth=True,
    tags=["Content profiles"],
)
async def remove_view_from_track_profile(
    request: Request,
    track_id: str,
    view_id: str,
) -> Dict[str, Any]:
    """Remove a View from the track-attached ContentProfile (updates manifest + deletes node)."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    cp = await get_track_attached_content_profile(track)
    if not cp:
        raise ResourceNotFoundError(message="Track has no attached content profile")

    view = await View.get(view_id)
    if not view or view.track_id != track_id:
        raise ResourceNotFoundError(message="View not found in this track")

    prior_cp_snapshot = await export_node(cp)  # D-03 before-snapshot
    await view.delete()
    await sync_attached_manifest(cp)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.update",
        resource_type="ContentProfile",
        resource_id=cp.id,
        before=prior_cp_snapshot,
        after=await export_node(cp),
        scope=f"track:{track_id}",
    )

    return {
        "message": "View removed from profile",
        "deleted_view_id": view_id,
    }


# ---------------------------------------------------------------------------
# Profile Detach and Revert
# ---------------------------------------------------------------------------


@endpoint(
    "/tracks/{track_id}/content-profile/detach-library",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def detach_library_from_track_profile(
    request: Request,
    track_id: str,
) -> Dict[str, Any]:
    """Remove library provenance from a track-attached ContentProfile without removing materialized elements.

    Clears ``libraryMergeSourceId`` on the Track so that future re-merge checks
    won't reference the old library package.  The EntryType/Tag/View nodes that
    were materialized from the library merge remain in place.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    if not getattr(track, "library_merge_source_id", None):
        # No-op return — also emit so the audit log records the attempt with no diff.
        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="content_profile.update",
            resource_type="Track",
            resource_id=track.id,
            before=await export_node(track),
            after=await export_node(track),
            scope=f"track:{track_id}",
        )
        return {
            "message": "Track has no library provenance to detach",
            "track_id": track_id,
        }

    prior_track_snapshot = await export_node(track)  # D-03 before-snapshot
    track.library_merge_source_id = None
    track.updated_at = utc_now_iso()
    await track.save()

    # Also clear provenance in the manifest's package section
    cp = await get_track_attached_content_profile(track)
    if cp and cp.manifest:
        manifest = dict(cp.manifest)
        pkg = dict(manifest.get("package", {}))
        pkg.pop("source_library_id", None)
        pkg.pop("source_library_name", None)
        manifest["package"] = pkg
        cp.manifest = manifest
        cp.updated_at = utc_now_iso()
        await cp.save()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.update",
        resource_type="Track",
        resource_id=track.id,
        before=prior_track_snapshot,
        after=await export_node(track),
        scope=f"track:{track_id}",
    )

    return {"message": "Library provenance detached", "track_id": track_id}


@endpoint(
    "/tracks/{track_id}/content-profile/revert-customizations",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def revert_track_profile_customizations(
    request: Request,
    track_id: str,
    force: bool = False,
) -> Dict[str, Any]:
    """Revert local customizations on a track-attached ContentProfile back to the last merged library state.

    This re-applies the library manifest from the ``libraryMergeSourceId``,
    removing any locally added EntryTypes, Tags, or Views that weren't in the
    original library merge. Requires that ``libraryMergeSourceId`` is still set
    on the Track.

    Plan 07-02 / I-LIB-05 reject-gate — when
    ``compute_entry_impact_for_attached`` reports any track with
    ``would_fail_validation > 0`` the endpoint raises ``BadRequestError``
    unless ``force=true`` is passed. Mirrors Phase 5
    ``migrations/reject_gate.detect_unhandled_breaks``.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")

    lib_id = getattr(track, "library_merge_source_id", None)
    if not lib_id:
        raise BadRequestError(
            message="Track has no library provenance — cannot revert customizations"
        )

    lib_cp = await ContentProfile.get(lib_id)
    if not lib_cp or not getattr(lib_cp, "library_package", False):
        raise ResourceNotFoundError(message="Referenced library profile not found")

    # Remove all current materialized nodes under the attached profile
    cp = await get_track_attached_content_profile(track)
    if not cp:
        raise ResourceNotFoundError(message="Track has no attached content profile")

    # Plan 07-02 / I-LIB-05 reject-gate — consult Phase 5 impact helper BEFORE
    # the destructive re-apply. force=true is the only escape past a blocking
    # impact set (mirror of Phase 5 reject_gate.detect_unhandled_breaks).
    from app.services.content_profile_diff import compute_entry_impact_for_attached

    impacts = await compute_entry_impact_for_attached(
        cp=cp, candidate_manifest=lib_cp.manifest or {}
    )
    blocking = [i for i in impacts if int(i.get("would_fail_validation", 0)) > 0]
    if blocking and not force:
        raise BadRequestError(
            message=(
                "Revert would invalidate existing entries; pass force=true to "
                "proceed"
            ),
            details={"blocking_impacts": blocking},
        )

    # Delete current EntryTypes, Tags, and Views concurrently
    import asyncio

    from app.models.edges import Edge

    entry_types = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    tags = await cp.nodes(edge=[CONTAINS], node=["Tag"])
    vregs = await cp.nodes(edge=[Edge], node=["Views"])

    deletions = [et.delete() for et in entry_types]
    deletions.extend(tag.delete() for tag in tags)
    vreg_views = []
    for vreg in vregs or []:
        views = await vreg.nodes(edge=[CATALOGS], node=["View"])
        deletions.extend(v.delete() for v in views)
        vreg_views.append(vreg)

    if deletions:
        await asyncio.gather(*deletions)
    if vreg_views:
        await asyncio.gather(*(vr.delete() for vr in vreg_views))

    # Re-apply the library manifest
    from app.services.content_profile_merge import (
        merge_library_manifest_into_content_profile,
    )

    prior_cp_snapshot = await export_node(cp)  # D-03 before-snapshot
    await merge_library_manifest_into_content_profile(
        lib_cp,
        cp,
        track,
    )

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    # Plan 07-02 — switch to content_profile.update action + details.revert so
    # the audit log distinguishes revert from a fresh merge_library.
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.update",
        resource_type="ContentProfile",
        resource_id=cp.id,
        before=prior_cp_snapshot,
        after=await export_node(cp),
        scope=f"track:{track_id}",
        details={"revert": True, "force": bool(force), "impacts": impacts},
    )

    return {
        "message": "Customizations reverted to library state",
        "track_id": track_id,
        "library_profile_id": lib_id,
        "force": bool(force),
        "impacts": impacts,
    }


# ---------------------------------------------------------------------------
# Derive Library Profile from Track/App
# ---------------------------------------------------------------------------


@endpoint(
    "/content-profiles/from-track/{track_id}",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def derive_library_profile_from_track(
    request: Request,
    track_id: str,
    name: Optional[str] = None,
    description: str = "",
    version: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Derive a new library ContentProfile from a Track's attached profile.

    The attached profile's manifest is used as the basis for the new library
    package.  This enables users to share their customizations back to the
    library.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    track = await Track.get(track_id)
    if not track:
        raise ResourceNotFoundError(message="Track not found")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="track.update",
        resource=Resource(kind="track", id=track_id, scope=f"track:{track_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    cp = await get_track_attached_content_profile(track)
    if not cp:
        raise ResourceNotFoundError(message="Track has no attached content profile")

    await sync_attached_manifest(cp)

    manifest = dict(cp.manifest or {})
    manifest["scope"] = "track"

    # Plan 07-02 / I-LIB-03 — stamp package.provenance before persistence so the
    # derived library package records its source. Round-trip merge into a fresh
    # Track regenerates an equivalent manifest modulo this block. ROADMAP AC#2.
    from app.utils.time import utc_now_iso

    pkg = dict(manifest.get("package") or {})
    pkg["provenance"] = {
        "source": "track",
        "source_id": track_id,
        "derived_at": utc_now_iso(),
        "derived_by": user_id,
    }
    manifest["package"] = pkg

    now = utc_now_iso()
    derived_name = name or f"{track.title} Profile"
    derived = await ContentProfile.create(
        name=derived_name,
        description=description or f"Derived from track: {track.title}",
        version=version or "1.0.0",
        workspace_id=workspace_id,
        manifest=manifest,
        scope="platform" if not workspace_id else "organization",
        library_package=True,
        created_at=now,
        updated_at=now,
    )

    reg = await ContentProfiles.get(CONTENT_PROFILES_REGISTRY_ID)
    if reg:
        await reg.connect(derived, edge=CATALOGS, cataloged_at=now)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.create",
        resource_type="ContentProfile",
        resource_id=derived.id,
        before=None,
        after=await export_node(derived),
        scope=f"track:{track_id}",
    )

    return {
        "content_profile": await export_node(derived),
        "message": "Library profile derived from track",
        "source_track_id": track_id,
    }


@endpoint(
    "/content-profiles/from-app/{app_id}",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def derive_library_profile_from_space(
    request: Request,
    app_id: str,
    name: Optional[str] = None,
    description: str = "",
    version: Optional[str] = None,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Derive a new library ContentProfile from an App's attached profile.

    The attached profile's manifest is used as the basis for the new library
    package.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    app_node = await App.get(app_id)
    if not app_node:
        raise ResourceNotFoundError(message="App not found")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    cp = await get_app_attached_content_profile(app_node)
    if not cp:
        raise ResourceNotFoundError(message="App has no attached content profile")

    await sync_attached_manifest(cp)

    manifest = dict(cp.manifest or {})
    manifest["scope"] = "app"

    # Plan 07-02 / I-LIB-03 — stamp package.provenance before persistence (mirror
    # of from-track block). ROADMAP AC#2.
    from app.utils.time import utc_now_iso

    pkg = dict(manifest.get("package") or {})
    pkg["provenance"] = {
        "source": "app",
        "source_id": app_id,
        "derived_at": utc_now_iso(),
        "derived_by": user_id,
    }
    manifest["package"] = pkg

    now = utc_now_iso()
    derived_name = name or f"{app_node.name} Profile"
    derived = await ContentProfile.create(
        name=derived_name,
        description=description or f"Derived from App: {app_node.name}",
        version=version or "1.0.0",
        workspace_id=workspace_id,
        manifest=manifest,
        scope="platform" if not workspace_id else "organization",
        library_package=True,
        created_at=now,
        updated_at=now,
    )

    reg = await ContentProfiles.get(CONTENT_PROFILES_REGISTRY_ID)
    if reg:
        await reg.connect(derived, edge=CATALOGS, cataloged_at=now)

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.create",
        resource_type="ContentProfile",
        resource_id=derived.id,
        before=None,
        after=await export_node(derived),
        scope=f"app:{app_id}",
    )

    return {
        "content_profile": await export_node(derived),
        "message": "Library profile derived from app",
        "source_app_id": app_id,
    }


# ---------------------------------------------------------------------------
# App-attached Profile Detach and Revert (Plan 07-02, LIB-03)
# ---------------------------------------------------------------------------
#
# Mirror of the track-side handlers at L665-842 for App-attached
# ContentProfiles. Per Phase 7 CONTEXT post-research lock #12, BOTH
# endpoints are NON-CASCADING: they only mutate the App's directly-
# attached ContentProfile and the App.library_merge_source_id field.
# Track-template ContentProfile rows reachable via the DEFINES_TRACK_PROFILE
# edge from the App-attached CP survive untouched (I-LIB-04).
#
# Authorization: both gates on the existing ``app_node.update`` PolicyAction
# (mirrors the track-side ``track.update`` gate). ZERO new PolicyAction
# literals are added by Plan 07-02 (CONTEXT lock #1 + #14).


@endpoint(
    "/apps/{app_id}/content-profile/detach-library",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def detach_library_from_space_profile(
    request: Request,
    app_id: str,
) -> Dict[str, Any]:
    """Remove library provenance from an App-attached ContentProfile.

    Drops ``library_merge_source_id`` on the App and clears
    ``manifest.package.source_library_{id,name}`` on the attached
    ContentProfile while preserving every materialized EntryType / Tag /
    View / track-template ContentProfile (I-LIB-04, non-cascading).

    Idempotent: an App with no prior library merge returns 200 with a
    "no library provenance" message (matches track-side L709-711).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    app_node = await App.get(app_id)
    if not app_node:
        raise ResourceNotFoundError(message="App not found")

    if not getattr(app_node, "library_merge_source_id", None):
        # No-op return — also emit so the audit log records the attempt with
        # no diff. Mirrors track-side L697-707.
        await emit_change_event(
            actor_kind="human",
            actor_id=user_id,
            action="content_profile.update",
            resource_type="App",
            resource_id=app_node.id,
            before=await export_node(app_node),
            after=await export_node(app_node),
            scope=f"app:{app_id}",
        )
        return {
            "message": "App has no library provenance to detach",
            "app_id": app_id,
        }

    prior_space_snapshot = await export_node(app_node)  # D-03 before-snapshot
    app_node.library_merge_source_id = None
    app_node.updated_at = utc_now_iso()
    await app_node.save()

    # Also clear provenance in the manifest's package section.
    cp = await get_app_attached_content_profile(app_node)
    if cp and cp.manifest:
        manifest = dict(cp.manifest)
        pkg = dict(manifest.get("package", {}))
        pkg.pop("source_library_id", None)
        pkg.pop("source_library_name", None)
        manifest["package"] = pkg
        cp.manifest = manifest
        cp.updated_at = utc_now_iso()
        await cp.save()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.update",
        resource_type="App",
        resource_id=app_node.id,
        before=prior_space_snapshot,
        after=await export_node(app_node),
        scope=f"app:{app_id}",
    )

    return {"message": "Library provenance detached", "app_id": app_id}


@endpoint(
    "/apps/{app_id}/content-profile/revert-customizations",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def revert_space_profile_customizations(
    request: Request,
    app_id: str,
    force: bool = False,
) -> Dict[str, Any]:
    """Revert local customizations on an App-attached ContentProfile.

    Re-applies the library manifest from ``library_merge_source_id``,
    removing locally added EntryTypes/Tags/Views that weren't part of the
    original library merge. Per I-LIB-04 the revert is NON-CASCADING:
    track-template ContentProfile rows reachable from the App CP via
    ``DEFINES_TRACK_PROFILE`` are PRESERVED. Child Tracks created from
    those templates keep their independent track-attached profiles.

    I-LIB-05 reject-gate: when ``compute_entry_impact_for_attached`` reports
    any track with ``would_fail_validation > 0`` the endpoint raises
    ``BadRequestError`` unless ``force=true`` is passed. Mirrors the Phase 5
    ``migrations/reject_gate.detect_unhandled_breaks`` pattern.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    _decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="app.update",
        resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
    )
    if not _decision.allowed:
        raise InsufficientPermissionsError(message="Access denied")

    app_node = await App.get(app_id)
    if not app_node:
        raise ResourceNotFoundError(message="App not found")

    lib_id = getattr(app_node, "library_merge_source_id", None)
    if not lib_id:
        raise BadRequestError(
            message=("App has no library provenance — cannot revert customizations")
        )

    lib_cp = await ContentProfile.get(lib_id)
    if not lib_cp or not getattr(lib_cp, "library_package", False):
        raise ResourceNotFoundError(message="Referenced library profile not found")

    cp = await get_app_attached_content_profile(app_node)
    if not cp:
        raise ResourceNotFoundError(message="App has no attached content profile")

    # I-LIB-05 reject-gate — consults the Phase 5 impact helper BEFORE the
    # destructive re-apply. Phase 5 force-flag escape hatch is the ONLY way
    # past a blocking impact set.
    from app.services.content_profile_diff import compute_entry_impact_for_attached

    impacts = await compute_entry_impact_for_attached(
        cp=cp, candidate_manifest=lib_cp.manifest or {}
    )
    blocking = [i for i in impacts if int(i.get("would_fail_validation", 0)) > 0]
    if blocking and not force:
        raise BadRequestError(
            message=(
                "Revert would invalidate existing entries; pass force=true to "
                "proceed"
            ),
            details={"blocking_impacts": blocking},
        )

    # Delete current EntryTypes, Tags, and Views under the App-attached CP.
    # I-LIB-04 — we do NOT touch DEFINES_TRACK_PROFILE edges / track-template
    # CPs. Only the materialized track-tier nodes hanging off the App CP
    # are reset.
    import asyncio

    from app.models.edges import Edge

    entry_types = await cp.nodes(edge=[CONTAINS], node=["EntryType"])
    tags = await cp.nodes(edge=[CONTAINS], node=["Tag"])
    vregs = await cp.nodes(edge=[Edge], node=["Views"])

    deletions = [et.delete() for et in entry_types]
    deletions.extend(tag.delete() for tag in tags)
    vreg_views = []
    for vreg in vregs or []:
        views = await vreg.nodes(edge=[CATALOGS], node=["View"])
        deletions.extend(v.delete() for v in views)
        vreg_views.append(vreg)

    if deletions:
        await asyncio.gather(*deletions)
    if vreg_views:
        await asyncio.gather(*(vr.delete() for vr in vreg_views))

    # Re-apply the library manifest (for_space=True path).
    from app.services.content_profile_merge import (
        merge_library_manifest_into_content_profile,
    )

    prior_cp_snapshot = await export_node(cp)  # D-03 before-snapshot
    await merge_library_manifest_into_content_profile(
        lib_cp,
        cp,
        track=None,
        for_space=True,
    )

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.update",
        resource_type="ContentProfile",
        resource_id=cp.id,
        before=prior_cp_snapshot,
        after=await export_node(cp),
        scope=f"app:{app_id}",
        details={"revert": True, "force": bool(force), "impacts": impacts},
    )

    return {
        "message": "Customizations reverted to library state",
        "app_id": app_id,
        "library_profile_id": lib_id,
        "force": bool(force),
        "impacts": impacts,
    }


# ---------------------------------------------------------------------------
# Draft / publish / diff / discard / substrate (Pillars 2 + 3)
# ---------------------------------------------------------------------------


async def _resolve_cp_edit_permission(
    *,
    user_id: str,
    cp: ContentProfile,
) -> bool:
    """Whether ``user_id`` may fork/publish/discard/diff a draft of ``cp``.

    Track-attached: caller needs ``can_edit_track`` on the owning Track.
    App-attached: caller needs ``can_edit_app`` on the owning App.
    Library:        caller needs publish rights under the CP's workspace.
    """
    if cp.library_package:
        return await can_publish_content_profiles_under_workspace(
            user_id, cp.workspace_id or ""
        )
    # A draft CP holds the parent's scope but is attached to no Track/App; resolve
    # the owning resource via the published parent too, so a draft passed directly
    # (not pre-resolved to its parent by the caller) doesn't deny a user who owns
    # the underlying track/app. Mirrors agent_profiles._user_can_edit_cp.
    candidate_cp_ids = [cp.id]
    draft_of = getattr(cp, "draft_of_id", None)
    if draft_of:
        candidate_cp_ids.append(draft_of)
    if cp.scope == "track":
        for cid in candidate_cp_ids:
            owner_tracks = await Track.find(
                {"context.attached_content_profile_id": cid}
            )
            for t in owner_tracks:  # noqa: SIM110 — short-circuit on await
                _decision = await policy_evaluate(
                    subject=Subject(kind="human", id=user_id),
                    action="track.update",
                    resource=Resource(kind="track", id=t.id, scope=f"track:{t.id}"),
                )
                if _decision.allowed:
                    return True
        return False
    if cp.scope == "app":
        for cid in candidate_cp_ids:
            owner_spaces = await App.find({"context.attached_content_profile_id": cid})
            for sp in owner_spaces:  # noqa: SIM110 — short-circuit on await
                _decision = await policy_evaluate(
                    subject=Subject(kind="human", id=user_id),
                    action="app.update",
                    resource=Resource(kind="app", id=sp.id, scope=f"app:{sp.id}"),
                )
                if _decision.allowed:
                    return True
        return False
    return False


@endpoint(
    "/content-profiles/{content_profile_id}/draft",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def fork_content_profile_draft(
    request: Request,
    content_profile_id: str,
) -> Dict[str, Any]:
    """Fork a draft from a published ContentProfile."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    cp = await ContentProfile.get(content_profile_id)
    if cp is None:
        raise ResourceNotFoundError(message="Content profile not found")
    if not await _resolve_cp_edit_permission(user_id=user_id, cp=cp):
        raise InsufficientPermissionsError(message="Access denied")
    from app.services.content_profile_atomic_swap import fork_draft

    draft = await fork_draft(published=cp, actor_id=user_id)
    return {
        "draft": await export_node(draft),
        "from_id": cp.id,
    }


@endpoint(
    "/content-profiles/{content_profile_id}/publish",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def publish_content_profile_draft(
    request: Request,
    content_profile_id: str,
    run_migrations: bool = True,
    abort_on_migration_failure: bool = True,
    force: bool = False,
) -> Dict[str, Any]:
    """Publish a draft, swapping its manifest onto the published parent.

    Phase 5 Plan 05-02:
      - ``force=true`` query param bypasses the no-migration-path reject
        gate. Forced publishes do NOT migrate existing entries — they
        remain on the prior schema until manually fixed. Documented
        destructive escape (I-MIG-03).
      - Policy gate evaluates ``migration.publish`` (default) or
        ``migration.force_publish`` (when ``force=true``) so a Policy can
        allow normal publish but DENY force (security hardening per
        STRIDE Tampering threat T-05-02-01).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    draft = await ContentProfile.get(content_profile_id)
    if draft is None:
        raise ResourceNotFoundError(message="Content profile not found")
    if draft.status != "draft":
        raise BadRequestError(message="Only draft CPs can be published")
    if not draft.draft_of_id:
        raise BadRequestError(message="Draft is not linked to a published parent")
    parent = await ContentProfile.get(draft.draft_of_id)
    if parent is None:
        raise BadRequestError(message="Draft's published parent has been deleted")
    if not await _resolve_cp_edit_permission(user_id=user_id, cp=parent):
        raise InsufficientPermissionsError(message="Access denied")

    # Phase 5 Plan 05-02 — policy gate (separate actions for normal vs force).
    # ``migration.publish`` / ``migration.force_publish`` are added to
    # PolicyAction by Plan 05-01 (Wave 1 sibling). Until 05-01 merges,
    # ``evaluate`` accepts arbitrary action strings — no runtime breakage.
    action_str = "migration.force_publish" if force else "migration.publish"
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action=action_str,  # type: ignore[arg-type]
        resource=Resource(
            kind="content_profile",
            id=content_profile_id,
            scope=f"content_profile:{content_profile_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message=f"Policy denied {action_str}")

    from app.services.content_profile_atomic_swap import publish_draft

    result = await publish_draft(
        draft=draft,
        published=parent,
        actor_id=user_id,
        run_migrations=run_migrations,
        abort_on_migration_failure=abort_on_migration_failure,
        force=force,
    )
    return result


@endpoint(
    "/content-profiles/{content_profile_id}/diff",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def diff_content_profile(
    request: Request,
    content_profile_id: str,
    against: Optional[str] = None,
    include_entry_impact: bool = True,
    sample_limit: int = 20,
) -> Dict[str, Any]:
    """Structural diff + optional entry-impact for a candidate CP.

    ``against`` defaults to ``"published"`` and resolves to the draft's
    ``draft_of_id`` parent. Pass another CP id to diff arbitrary pairs.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    candidate = await ContentProfile.get(content_profile_id)
    if candidate is None:
        raise ResourceNotFoundError(message="Content profile not found")
    if not await _resolve_cp_edit_permission(user_id=user_id, cp=candidate):
        # Drafts also need edit permission on their published parent.
        if candidate.draft_of_id:
            parent = await ContentProfile.get(candidate.draft_of_id)
            if parent is None or not await _resolve_cp_edit_permission(
                user_id=user_id, cp=parent
            ):
                raise InsufficientPermissionsError(message="Access denied")
        else:
            raise InsufficientPermissionsError(message="Access denied")

    against_id: Optional[str]
    if against in (None, "", "published"):
        against_id = candidate.draft_of_id or candidate.published_id
    else:
        against_id = against
    if not against_id:
        raise BadRequestError(
            message="No reference CP to diff against (provide ?against=<id>)"
        )
    reference = await ContentProfile.get(against_id)
    if reference is None:
        raise ResourceNotFoundError(message="Reference content profile not found")

    from app.services.content_profile_diff import compute_manifest_diff

    diff = compute_manifest_diff(
        before=reference.manifest or {},
        after=candidate.manifest or {},
    )
    payload: Dict[str, Any] = {
        "candidate_id": candidate.id,
        "reference_id": reference.id,
        "diff": diff,
    }
    if include_entry_impact:
        # Impact is measured against the published parent — that's the CP
        # actually attached to live tracks/apps.
        impact_target = reference if reference.status == "published" else candidate
        # NOTE: dotted attribute access (not ``from … import``) so monkeypatch
        # on ``app.services.content_profile_diff.compute_entry_impact_for_attached``
        # is honoured by tests that inject synthetic impact lists.
        from app.services import content_profile_diff as _cpd

        impacts = await _cpd.compute_entry_impact_for_attached(
            cp=impact_target,
            candidate_manifest=candidate.manifest or {},
            sample_limit=sample_limit,
        )
        payload["entry_impact"] = impacts

        # Phase 5 Plan 05-04 — MIG-04 payload extensions.
        # Top-level ``would_need_migration`` boolean rollup over the per-track
        # impact list (each impact carries an int count; True iff any > 0 or
        # any track has ``would_fail_validation > 0``). Top-level
        # ``unhandled_breaks`` plumbs the same Plan 05-02 reject_gate verdict
        # the publish endpoint would 422 on, so authors can preview-then-fix.
        payload["would_need_migration"] = any(
            int((i or {}).get("would_need_migration") or 0) > 0
            or int((i or {}).get("would_fail_validation") or 0) > 0
            for i in (impacts or [])
        )
        try:
            from app.services.migrations.reject_gate import detect_unhandled_breaks

            payload["unhandled_breaks"] = detect_unhandled_breaks(
                impacts=impacts,
                migrations=(candidate.manifest or {}).get("migrations") or [],
            )
        except ImportError:
            # Plan 05-02's reject_gate normally lands before 05-04. If the
            # module is somehow missing at import time the preview is still
            # useful for the structural diff + entry_impact list; surface an
            # empty break list and log so the gap is visible.
            import logging

            logging.getLogger(__name__).warning(
                "preview-update: app.services.migrations.reject_gate "
                "unavailable; unhandled_breaks defaulting to []"
            )
            payload["unhandled_breaks"] = []
    return payload


# Phase 5 Plan 05-04 — MIG-04 alias.
#
# ``POST /api/content-profiles/{id}/preview-update`` is a thin wrapper that
# delegates to ``diff_content_profile`` (locked decision #3, CONTEXT
# <locked_post_research>). The jvspatial @endpoint decorator stores config
# on the target function (single ``_jvspatial_endpoint_config`` slot per
# callable), so decorator stacking is not supported — Option B (wrapper
# function) is the only viable path. The wrapper inherits auth gate, CP
# edit-permission gate, query-param contract, and the full payload
# (including the would_need_migration + unhandled_breaks extensions added
# above) via plain function delegation.
@endpoint(
    "/content-profiles/{content_profile_id}/preview-update",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def preview_update_content_profile(
    request: Request,
    content_profile_id: str,
    against: Optional[str] = None,
    include_entry_impact: bool = True,
    sample_limit: int = 20,
) -> Dict[str, Any]:
    """MIG-04 — alias to ``diff_content_profile``.

    Returns the structural diff + entry_impact + would_need_migration +
    unhandled_breaks payload so authors can preview both the schema delta
    AND the publish-gate verdict before invoking ``/publish``.
    """
    return await diff_content_profile(
        request=request,
        content_profile_id=content_profile_id,
        against=against,
        include_entry_impact=include_entry_impact,
        sample_limit=sample_limit,
    )


@endpoint(
    "/content-profiles/{content_profile_id}/discard-draft",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def discard_content_profile_draft(
    request: Request,
    content_profile_id: str,
) -> Dict[str, Any]:
    """Delete a draft without publishing."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    draft = await ContentProfile.get(content_profile_id)
    if draft is None:
        raise ResourceNotFoundError(message="Content profile not found")
    if draft.status != "draft":
        raise BadRequestError(message="Only drafts can be discarded")
    parent = await ContentProfile.get(draft.draft_of_id) if draft.draft_of_id else None
    if parent is None:
        # Orphan draft — anyone with auth can discard it (cleanup path).
        from app.services.content_profile_atomic_swap import discard_draft

        return await discard_draft(draft=draft, actor_id=user_id)
    if not await _resolve_cp_edit_permission(user_id=user_id, cp=parent):
        raise InsufficientPermissionsError(message="Access denied")
    from app.services.content_profile_atomic_swap import discard_draft

    return await discard_draft(draft=draft, actor_id=user_id)


@endpoint(
    "/content-profile-substrate",
    methods=["GET"],
    auth=True,
    tags=["Content profiles"],
)
async def get_content_profile_substrate(request: Request) -> Dict[str, Any]:
    """Root introspection for the agent + frontend.

    Returns the full set of registered field types, view types (incl. meta-
    widgets), and any code plugins discovered at startup. The agent calls
    this first when authoring a profile so it can compose against what's
    actually installed.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    from app.services import content_profile_field_types as ftr
    from app.services.content_profile_plugins import discovered_plugins
    from app.services.retrieval import semantic_retrieval_available
    from app.services.template_var_resolvers import get_registered_tokens
    from app.views import content_profile_view_types as vtr

    def _field_payload(spec: Any) -> Dict[str, Any]:
        return {
            "type": spec.type,
            "base": spec.base,
            "label": spec.label,
            "description": spec.description,
            "config_schema": spec.config_schema,
            "source": spec.source,
            "signed": spec.signed,
        }

    def _view_payload(spec: Any) -> Dict[str, Any]:
        return {
            "type": spec.type,
            "base": spec.base,
            "label": spec.label,
            "description": spec.description,
            "config_schema": spec.config_schema,
            "source": spec.source,
            "signed": spec.signed,
            "supported_platforms": list(getattr(spec, "supported_platforms", ["web"])),
            "default_always_on": bool(getattr(spec, "default_always_on", False)),
            "palette_group": str(getattr(spec, "palette_group", "general")),
            "configurable": bool(getattr(spec, "configurable", True)),
            "hot_loadable": bool(getattr(spec, "hot_loadable", False)),
        }

    # Live retrieval capability — lets the agent (and frontend) know up
    # front which search modes this deployment actually supports, so it can
    # phrase queries appropriately instead of discovering degradation only
    # after a ``mode=hybrid`` request returns ``degraded: true``.
    sem = semantic_retrieval_available()

    return {
        "field_types": [_field_payload(s) for s in ftr.iter_specs()],
        "view_types": [_view_payload(s) for s in vtr.iter_specs()],
        "plugins": discovered_plugins(),
        "registry_versions": {
            "field_types": ftr.registry_version(),
            "view_types": vtr.registry_version(),
        },
        # Phase 3.1 Plan 03.1-04 (ANC-07 + ANC-09) — resolver vocabulary
        # surfaced to non-agent introspection callers (frontend manifest UI,
        # docs tooling). Mirrors the same key in ``describe_substrate``.
        "template_var_resolvers": get_registered_tokens(),
        "retrieval": {
            "semantic_available": sem,
            "modes": (["graph", "semantic", "hybrid"] if sem else ["graph"]),
            "default_mode": ("hybrid" if sem else "graph"),
            "note": (
                "Semantic concept search is active."
                if sem
                else "Semantic search is OFF (no vector store); queries fall "
                "back to keyword/graph matching — use concise keywords, not "
                "value rankings."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Phase 6 Plan 06-04 — Profile-aware MCP tools (MCP-04)
#
# Three new tools, wired to locked names via MCP_TOOL_NAME_OVERRIDES at
# ``backend/app/agentive/tooling/name_overrides.py``:
#   - POST /api/content-profiles/author          → integral_author_profile
#   - POST /api/content-profiles/{id}/modify     → integral_modify_profile
#   - GET  /api/content-profiles?type_hint=<str> → integral_list_profiles
#
# See I-PROFILE-01..02 in docs/INVARIANTS.md.
# ---------------------------------------------------------------------------


async def resolve_type_hint(hint: str) -> List[Dict[str, Any]]:
    """Resolve a free-text type_hint to ranked library packages.

    Per locked-post-research §Q7 + §Q8 (CONTEXT lock §"Locked: type_hint
    resolution"):

      * Tokenize ``hint`` on whitespace + hyphens (lowercased).
      * For each ``library_package=True`` ContentProfile, build a haystack
        from ``package.name + package.description + cp.name``; tokenize it
        the same way.
      * ``score = len(hint_tokens & haystack_tokens) / len(hint_tokens)``.
      * Filter out score==0; sort descending.

    Caller is responsible for the disambiguation policy (zero-match warning,
    single best-match adoption, tied-top-score 400). Pitfall 6: this is a
    plain async helper — NOT a separate ``@endpoint`` — so callers
    (``list_library_content_profiles`` + ``create_track`` + ``create_app``)
    invoke it directly without an MCP round-trip.
    """
    hint_tokens = {tok for tok in hint.lower().replace("-", " ").split() if tok}
    if not hint_tokens:
        return []
    raw = await ContentProfile.find({"context.library_package": True})
    if raw is None:
        rows: list = []
    elif isinstance(raw, (list, tuple)):
        rows = list(raw)
    else:
        rows = [raw]
    scored: List[Dict[str, Any]] = []
    for cp in rows:
        # Drafts are not "listable" via integral_list_profiles — they
        # surface only after explicit publish (preserves the draft/publish
        # lifecycle semantic gap documented in Test 1a).
        if getattr(cp, "status", "published") != "published":
            continue
        if not _is_visible_library_profile(cp):
            continue
        pkg = (cp.manifest or {}).get("package") or {}
        # Plan 07-01 / I-LIB-01 — include manifest.package.tags in the
        # haystack so MCP-04 keyword resolution catches domain terms
        # absent from the slug/description (e.g. 'zettelkasten' under a
        # personal knowledge-base profile). Tags are free-form domain
        # keywords — see I-LIB-02 for back-compat / no-frontend semantics.
        raw_tags = pkg.get("tags") or []
        tag_blob = " ".join(t for t in raw_tags if isinstance(t, str))
        haystack = (
            f"{pkg.get('name', '')} {pkg.get('description', '')} "
            f"{cp.name or ''} {tag_blob}"
        ).lower()
        haystack_tokens = {tok for tok in haystack.replace("-", " ").split() if tok}
        overlap = hint_tokens & haystack_tokens
        if not overlap:
            continue
        score = len(overlap) / max(len(hint_tokens), 1)
        scored.append(
            {
                "content_profile_id": cp.id,
                "name": cp.name,
                "score": score,
                "package_name": pkg.get("name", ""),
            }
        )
    scored.sort(key=lambda x: -x["score"])
    return scored


@endpoint(
    "/content-profiles/author",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def author_content_profile(
    request: Request,
    description: str = "",
    target_scope: str = "track",
    workspace_id: str = "",
    fields: Optional[List[Dict[str, Any]]] = None,
    as_draft: bool = True,
) -> Dict[str, Any]:
    """integral_author_profile (MCP-04 — Phase 6 Plan 06-04 AC#3).

    v1 deterministic NL → manifest template-fill. NO inline LLM call
    (CONTEXT lock; v2 swaps LLM in via the SAME handler signature).
    Authored profiles land in the library (``library_package=True``).

    Body:

      * ``description`` (str)         — natural-language hint; tokenized
                                        for 7-keyword library-match.
      * ``target_scope`` (str)        — ``"track"`` | ``"app"``.
      * ``workspace_id`` (str)        — EXPLICIT (locked-post-research #4);
                                        never derived from caller state.
      * ``fields`` (Optional[List])   — caller-supplied field payload merged
                                        into the matched package's first
                                        entry_type, or used as the entire
                                        schema for the fallback ``post`` ET.
      * ``as_draft`` (bool, default=True) — when True, the CP lands as a
                                        draft (not yet listable via
                                        ``integral_list_profiles``); when
                                        False, lands published.

    Two-tier gate (Pitfall 5):

      1. ``policy_engine.evaluate(action="profile.author", scope=workspace)``
      2. ``can_publish_content_profiles_under_workspace(user_id, workspace_id)``

    Both must pass. Tier 1 differentiates agent vs human (default-human
    delegates to Tier 2; agent must have a Policy granting
    ``profile.author``).
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    if not description or not str(description).strip():
        raise BadRequestError(message="description is required")
    if target_scope not in ("track", "app"):
        raise BadRequestError(message="target_scope must be 'track' or 'app'")
    if not workspace_id:
        raise BadRequestError(
            message="workspace_id is required (no implicit derivation)"
        )

    # Tier 1 — profile.author policy gate (agent vs human dispatch).
    decision = await policy_evaluate(
        subject=Subject(kind="human", id=user_id),
        action="profile.author",
        resource=Resource(
            kind="content_profile",
            id="*",
            scope=f"workspace:{workspace_id}",
        ),
    )
    if not decision.allowed:
        raise InsufficientPermissionsError(message="profile.author denied")

    # Tier 2 — existing workspace publish gate.
    if not await can_publish_content_profiles_under_workspace(user_id, workspace_id):
        raise InsufficientPermissionsError(
            message="No publish rights under workspace",
        )

    from app.services.content_profile_author import author_profile_from_template

    manifest = await author_profile_from_template(
        description=description,
        target_scope=target_scope,
        fields=fields,
    )

    # Persist as draft or published library package.
    cp_status = "draft" if as_draft else "published"
    now = utc_now_iso()
    name = (manifest.get("package") or {}).get("name") or "authored-profile"
    name_fold = compute_fold(name)
    cp = await ContentProfile.create(
        name=name,
        name_fold=name_fold,
        scope=target_scope,
        workspace_id=workspace_id,
        library_package=True,
        manifest=manifest,
        status=cp_status,
        published_at=now if cp_status == "published" else None,
        created_at=now,
        updated_at=now,
        description=(manifest.get("package") or {}).get("description", "") or "",
    )
    # Catalog under ContentProfiles registry so list_library_content_profiles
    # surfaces it (only when published — drafts stay invisible until
    # explicitly published via the publish endpoint).
    if cp_status == "published":
        reg = await ContentProfiles.get(CONTENT_PROFILES_REGISTRY_ID)
        if reg:
            await reg.connect(cp, edge=CATALOGS, cataloged_at=now)

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.create",
        resource_type="ContentProfile",
        resource_id=cp.id,
        before=None,
        after=await export_node(cp),
        scope=f"workspace:{workspace_id}",
    )

    return {
        "content_profile_id": cp.id,
        "manifest": manifest,
        "status": cp_status,
    }


@endpoint(
    "/content-profiles/{content_profile_id}/modify",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def modify_content_profile(
    request: Request,
    content_profile_id: str,
    operations: Optional[List[Dict[str, Any]]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """integral_modify_profile (MCP-04 — Phase 6 Plan 06-04 AC#4).

    Routes through Phase 3.1 ``agent_profile_patches.apply_operations``
    (declarative DSL; no eval/exec — I-MIG-04 invariant) + Phase 3.1
    ``publish_draft(force=force)`` which fires the Phase 5 reject_gate
    automatically.

    ``force=true`` bypasses the reject_gate (destructive escape per
    Phase 5 I-MIG-03; the underlying ``migration.force_publish`` policy
    gate enforces authorization for forced publishes).

    Per locked-post-research #5 — both paths supported:

      * Library package: publish materializes new version, existing
        attached instances NOT auto-migrated (librarian path).
      * Attached instance (track/app scope): publish triggers Phase 5
        migration runtime over in-flight Entries.

    See I-PROFILE-02 in docs/INVARIANTS.md.
    """
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if not operations or not isinstance(operations, list):
        raise BadRequestError(message="operations must be a non-empty list")

    cp = await ContentProfile.get(content_profile_id)
    if cp is None:
        raise ResourceNotFoundError(message="Content profile not found")
    if not await _resolve_cp_edit_permission(user_id=user_id, cp=cp):
        raise InsufficientPermissionsError(message="Access denied")

    from app.services.agent_profile_patches import apply_operations
    from app.services.content_profile_atomic_swap import fork_draft, publish_draft

    # 1. Fork draft (Phase 3.1).
    draft = await fork_draft(published=cp, actor_id=user_id)

    # 2. Apply patch DSL (Phase 3.1).
    new_manifest = apply_operations(draft.manifest or {}, operations)
    draft.manifest = new_manifest
    await draft.save()

    # 3. Publish (Phase 3.1 + Phase 5 migration runner + reject_gate).
    result = await publish_draft(
        draft=draft,
        published=cp,
        actor_id=user_id,
        force=force,
    )
    return result


def _manifest_preview_stats(canonical: Dict[str, Any]) -> Dict[str, int]:
    """Extract entry_type_count, view_count, tag_count from a canonical manifest."""
    scope = str(canonical.get("scope") or "")
    if scope == "track":
        section = canonical.get("track") or {}
    elif scope == "app":
        section = canonical.get("app") or {}
    else:
        section = {}
    entry_type_count = len(section.get("entry_types") or [])
    view_count = len(section.get("views") or [])
    taxonomy = section.get("taxonomy") or {}
    tag_groups = taxonomy.get("tag_groups") or []
    tag_count = sum(
        len(grp.get("tags") or []) for grp in tag_groups if isinstance(grp, dict)
    )
    return {
        "entry_type_count": entry_type_count,
        "view_count": view_count,
        "tag_count": tag_count,
    }


def _build_package_preview_item(
    canonical: Dict[str, Any],
    validation_errors: Optional[List[str]] = None,
) -> "PackagePreviewItem":
    """Build a ``PackagePreviewItem`` from a compiled canonical manifest."""
    pkg = canonical.get("package") or {}
    package_name: str = str(pkg.get("name") or "")
    package_description: Optional[str] = (
        str(pkg.get("description")) if pkg.get("description") else None
    )
    stats = _manifest_preview_stats(canonical)
    return PackagePreviewItem(
        package_name=package_name,
        package_description=package_description,
        entry_type_count=stats["entry_type_count"],
        view_count=stats["view_count"],
        tag_count=stats["tag_count"],
        validation_errors=validation_errors or [],
    )


async def _publish_single_manifest(
    manifest_dict: Dict[str, Any],
    workspace_id: str,
    user_id: str,
    reg: Any,
    now: str,
) -> Any:
    """Persist one manifest as a library ContentProfile node.

    Returns the created ContentProfile node.
    """
    canonical = compile_canonical_manifest(manifest=manifest_dict)
    pkg = canonical.get("package") or {}
    package_name: str = str(pkg.get("name") or "")
    package_description: Optional[str] = (
        str(pkg.get("description")) if pkg.get("description") else None
    )
    if not package_name:
        raise BadRequestError(
            message="manifest.package.name is required when publishing"
        )
    safe_name = non_empty_after_strip(package_name, "package.name")
    if len(safe_name) > 120:
        raise BadRequestError(message="package.name must be 120 characters or fewer")
    name_fold = compute_fold(safe_name)
    safe_version = (
        validate_semver_ish(str(pkg.get("version") or ""), allow_empty=True) or None
    )

    from app.services.uniqueness import assert_unique

    await assert_unique(
        ContentProfile,
        {
            "context.library_package": True,
            "context.workspace_id": workspace_id,
            "context.name_fold": name_fold,
            "context.version": safe_version,
        },
        entity="content_profile",
        field_label="name+version",
        value=f"{safe_name} {safe_version or '(no version)'}",
        scope_label="in this workspace",
    )

    cp = await ContentProfile.create(
        name=safe_name,
        name_fold=name_fold,
        description=package_description or "",
        version=safe_version,
        workspace_id=workspace_id,
        manifest=canonical,
        scope=str(canonical.get("scope") or ""),
        library_package=True,
        created_at=now,
        updated_at=now,
    )
    await reg.connect(cp, edge=CATALOGS, cataloged_at=now)

    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="content_profile.create",
        resource_type="ContentProfile",
        resource_id=cp.id,
        before=None,
        after=await export_node(cp),
        scope=f"user:{user_id}",
    )
    return cp


@endpoint(
    "/content-profiles/import",
    methods=["POST"],
    auth=True,
    tags=["Content profiles"],
)
async def import_content_profile(
    request: Request,
    workspace_id: Optional[str] = None,
    preview: bool = False,
    manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Import ContentProfile manifests from a YAML/JSON file, or a ZIP archive.

    Accepts:
    - ``multipart/form-data`` with a ``file`` field (.yaml, .json, or .zip).
    - ``application/json`` body with a ``manifest`` key (single manifest only).

    ZIP archives must contain one or more ``<package-name>/profile.yaml``
    bundles (one level deep). Maximum ZIP size is 50 MB.

    When ``?preview=true`` the manifest(s) are validated and stats returned
    without persisting anything. In publish mode new library ``ContentProfile``
    nodes are created and cataloged.

    Preview response shape (always):
      ``{"packages": [...], "archive_type": "single" | "archive"}``

    Publish response shape:
      - Single file: ``{"content_profile": {...}, "message": "..."}``
      - ZIP archive:  ``{"published": N, "profiles": [...], "message": "..."}``
    """
    import pathlib
    import tempfile
    import zipfile

    _MAX_ZIP_BYTES = 50 * 1024 * 1024  # 50 MB

    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    if not workspace_id:
        raise BadRequestError(message="workspace_id is required")
    if not await can_publish_content_profiles_under_workspace(user_id, workspace_id):
        raise InsufficientPermissionsError(message="Access denied")

    content_type = request.headers.get("content-type", "")

    # ------------------------------------------------------------------ #
    # 1. Read the uploaded bytes / manifest dict
    # ------------------------------------------------------------------ #
    manifest_dict: Optional[Dict[str, Any]] = None
    raw_bytes: Optional[bytes] = None
    filename: str = ""

    if "multipart/form-data" in content_type:
        # jvspatial @endpoint does not bind UploadFile params — read form directly.
        try:
            form = await request.form()
        except Exception as e:  # noqa: BLE001
            raise BadRequestError(message=f"Could not parse multipart body: {e}")

        file_parts = form.getlist("file")
        if not file_parts:
            raise BadRequestError(message="multipart body must include a 'file' field")

        upload = file_parts[0]
        filename = getattr(upload, "filename", "") or ""
        is_yaml = filename.endswith(".yaml")
        is_json = filename.endswith(".json")
        is_zip = filename.endswith(".zip")

        if not (is_yaml or is_json or is_zip):
            raise BadRequestError(
                message="Only .yaml, .json, and .zip files are accepted"
            )

        raw_bytes = await upload.read()  # type: ignore[union-attr]

        # Size guard for ZIP uploads
        if is_zip and len(raw_bytes) > _MAX_ZIP_BYTES:
            raise BadRequestError(
                message=f"ZIP archive exceeds the 50 MB limit ({len(raw_bytes)} bytes)"
            )

        if not is_zip:
            # Single YAML / JSON file — decode and parse now
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise BadRequestError(message="File must be UTF-8 encoded")

            if is_yaml:
                try:
                    loaded = yaml.safe_load(text)
                except yaml.YAMLError as e:
                    raise BadRequestError(message=f"Invalid YAML: {e}")
                if loaded is None:
                    loaded = {}
                if not isinstance(loaded, dict):
                    raise BadRequestError(
                        message="YAML file must deserialize to an object"
                    )
                manifest_dict = loaded
            else:
                try:
                    manifest_dict = json.loads(text)
                except json.JSONDecodeError as e:
                    raise BadRequestError(message=f"Invalid JSON: {e}")
                if not isinstance(manifest_dict, dict):
                    raise BadRequestError(message="JSON file must be an object")

    elif "application/json" in content_type:
        # jvspatial @endpoint injects the ``manifest`` param directly from the
        # body when the caller sends ``{"manifest": {...}, "workspace_id": ...}``.
        # Use it when present; fall back to manual body read for callers that
        # don't go through jvspatial's param-model pipeline.
        if manifest is not None:
            manifest_dict = manifest
            if not isinstance(manifest_dict, dict):
                raise BadRequestError(message="'manifest' must be an object")
        else:
            try:
                body = await request.json()
            except Exception as e:  # noqa: BLE001
                raise BadRequestError(message=f"Could not parse JSON body: {e}")
            if not isinstance(body, dict) or "manifest" not in body:
                raise BadRequestError(message="JSON body must have a 'manifest' key")
            manifest_dict = body["manifest"]
            if not isinstance(manifest_dict, dict):
                raise BadRequestError(message="'manifest' must be an object")

    else:
        raise BadRequestError(
            message="Content-Type must be multipart/form-data or application/json"
        )

    # ------------------------------------------------------------------ #
    # 2. ZIP archive path
    # ------------------------------------------------------------------ #
    if filename.endswith(".zip") and raw_bytes is not None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            zip_path = tmp_path / "upload.zip"
            zip_path.write_bytes(raw_bytes)

            try:
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(tmp_path)
            except zipfile.BadZipFile as e:
                raise BadRequestError(message=f"Invalid ZIP archive: {e}")

            # Collect profile.yaml files exactly one level deep
            profile_files = sorted(tmp_path.glob("*/profile.yaml"))
            if not profile_files:
                raise BadRequestError(
                    message=(
                        "No profile.yaml files found in archive. "
                        "Expected structure: <package-name>/profile.yaml"
                    )
                )

            if preview:
                items: List[PackagePreviewItem] = []
                for pf in profile_files:
                    try:
                        raw = yaml.safe_load(pf.read_text(encoding="utf-8"))
                        if not isinstance(raw, dict):
                            raw = {}
                        canonical = compile_canonical_manifest(manifest=raw)
                        items.append(_build_package_preview_item(canonical))
                    except Exception as exc:  # noqa: BLE001
                        items.append(
                            PackagePreviewItem(
                                package_name=pf.parent.name,
                                package_description=None,
                                entry_type_count=0,
                                view_count=0,
                                tag_count=0,
                                validation_errors=[str(exc)],
                            )
                        )
                return ImportPreviewResponse(
                    packages=items, archive_type="archive"
                ).model_dump()

            # Publish mode — persist each package
            reg = await ContentProfiles.get(CONTENT_PROFILES_REGISTRY_ID)
            if not reg:
                raise ResourceNotFoundError(message="Content profiles registry missing")
            now = utc_now_iso()
            published_nodes = []
            for pf in profile_files:
                try:
                    raw = yaml.safe_load(pf.read_text(encoding="utf-8"))
                    if not isinstance(raw, dict):
                        raw = {}
                    cp = await _publish_single_manifest(
                        manifest_dict=raw,
                        workspace_id=workspace_id,
                        user_id=user_id,
                        reg=reg,
                        now=now,
                    )
                    published_nodes.append(await export_node(cp))
                except Exception as exc:  # noqa: BLE001
                    raise BadRequestError(
                        message=f"Failed to publish '{pf.parent.name}': {exc}"
                    )

            return {
                "published": len(published_nodes),
                "profiles": published_nodes,
                "message": f"{len(published_nodes)} content profile(s) imported",
            }

    # ------------------------------------------------------------------ #
    # 3. Single YAML / JSON file path (existing behaviour, preserved)
    # ------------------------------------------------------------------ #
    assert manifest_dict is not None  # guaranteed by branches above

    # Validate via the canonical compile path — raises on failure.
    canonical = compile_canonical_manifest(manifest=manifest_dict)

    if preview:
        return ImportPreviewResponse(
            packages=[_build_package_preview_item(canonical)],
            archive_type="single",
        ).model_dump()

    # Publish path
    reg = await ContentProfiles.get(CONTENT_PROFILES_REGISTRY_ID)
    if not reg:
        raise ResourceNotFoundError(message="Content profiles registry missing")
    now = utc_now_iso()
    cp = await _publish_single_manifest(
        manifest_dict=manifest_dict,
        workspace_id=workspace_id,
        user_id=user_id,
        reg=reg,
        now=now,
    )

    return {
        "content_profile": await export_node(cp),
        "message": "Content profile imported",
    }

"""App service-layer helpers.

Phase 9 Plan 09-05 (B4) — extracted from ``api/apps.py::create_space`` so
agentive tools + onboarding flows can drive App creation in-process
(no HTTP self-call, no MCP recursion per Pitfall 6).

The ``api/apps.py::create_space`` HTTP handler delegates to
``create_app_for_user`` after auth + request parsing. All existing
behaviour is preserved (ContentProfile attach, COLLABORATES_ON owner edge,
CATALOGS registry, prescribed-track provisioning, ChangeEvent emission).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

# Errors imported from app.api.errors mirrors the pre-existing service-
# layer pattern (see services/agent_scratch.py, services/share_links.py,
# services/sharing.py, etc.) — the @endpoint side-effect loop triggered
# by app.api.__init__ is the standard runtime behaviour.
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
)
from app.api.utils import export_node
from app.api.validators import effective_app_visibility
from app.api.validators_common import (
    compute_fold,
    non_empty_after_strip,
    validate_hex_color,
)
from app.models.nodes import App, ContentProfile
from app.services.app_graph import (
    catalog_app,
    wire_app_owner,
)
from app.services.app_install import (
    bundle_identity_key,
    resolve_canonical_bundle_install,
)
from app.services.app_lifecycle import install_app
from app.services.change_event import emit_change_event
from app.services.permissions import (
    can_create_app_under_workspace,
)
from app.services.uniqueness import assert_unique
from app.services.workspace_resolver import resolve_workspace_id
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def _library_default_labels(
    library_package_id: str,
) -> tuple[str, str]:
    """Return (default_name, default_description) for a library bundle.

    Phase 32 — supports batch-install / details-step-optional UX by
    deriving sensible per-app defaults from the manifest's package
    metadata. Returns empty strings when the library lookup fails
    (caller's required-field check will surface the error in the
    standard place).
    """
    lib = await ContentProfile.get(library_package_id)
    if lib is None:
        return ("", "")
    # The content_profile_loader rewrites manifest.package.name to the
    # SLUG at load time (see _assemble_manifest); the human display name
    # lives on the ContentProfile node's .name field, set from the YAML's
    # package.name via LibraryProfileSpec. Read .name first so the
    # default-install label is the package display string, not the slug.
    # Both name + description live on the ContentProfile node directly
    # (library-sync copies them from package.name + package.description
    # in the YAML; the loader strips them from manifest.package). Fall
    # back to manifest.package for in-process manifests that bypass the
    # library loader.
    manifest = lib.manifest or {}
    package = manifest.get("package") or {}
    display_name = str(getattr(lib, "name", "") or "").strip()
    display_description = str(getattr(lib, "description", "") or "").strip()
    return (
        display_name or str(package.get("name") or "").strip(),
        display_description or str(package.get("description") or "").strip(),
    )


async def create_app_for_user(
    user_id: str,
    name: str,
    *,
    description: Optional[str] = None,
    workspace_id: Optional[str] = None,
    visibility: Optional[str] = None,
    library_package_id: Optional[str] = None,
    accent_color: Optional[str] = None,
    include_seed_data: bool = True,
) -> App:
    """In-process App creation for an authenticated principal.

    Mirrors the post-auth body of ``api/apps.py::create_space`` (B4).
    The HTTP handler resolves ``type_hint`` first (it's a user-facing
    affordance) and passes the resolved ``library_content_profile_id``
    here as ``library_package_id``.

    Canonical lookup is ``await User.get(user_id)`` everywhere
    (Plan 09-05 B1 — no other lookup helper).

    Returns the created App node. Emits a single ``app_node.create``
    ChangeEvent on success.
    """
    resolved_workspace_id = await resolve_workspace_id(
        user_id=user_id, workspace_id=workspace_id
    )
    if not await can_create_app_under_workspace(user_id, resolved_workspace_id):
        raise InsufficientPermissionsError(
            message="You are not allowed to create apps in this workspace",
        )
    resolved_visibility = await effective_app_visibility(
        resolved_workspace_id, visibility
    )
    lib: Optional[ContentProfile] = None
    bundle_source_slug: Optional[str] = None
    if library_package_id:
        lib = await ContentProfile.get(library_package_id)
        if not lib or not getattr(lib, "library_package", False):
            raise BadRequestError(message="Library package not found")
        package_meta = (lib.manifest or {}).get("package") or {}
        lib_md = dict(getattr(lib, "metadata", None) or {})
        bundle_source_slug = (
            str(lib_md.get("slug") or package_meta.get("slug") or "").strip() or None
        )
        identity_key = bundle_identity_key(
            bundle_source_slug,
            package_meta,
            str(getattr(lib, "name", "") or "").strip(),
        )
        if identity_key:
            existing = await resolve_canonical_bundle_install(
                resolved_workspace_id,
                identity_key,
                library_package_id,
                user_id,
            )
            if existing is not None:
                logger.info(
                    "create_app_for_user: reusing bundle install %s (%s)",
                    existing.id,
                    identity_key,
                )
                return existing
        # Full lifecycle install (seeds, skills, agents, version, requires_apps).
        lib_default_name, lib_default_desc = await _library_default_labels(
            library_package_id
        )
        effective_name = name or lib_default_name
        effective_desc = (
            description if (description or "").strip() else lib_default_desc
        )
        result = await install_app(
            workspace_id=resolved_workspace_id,
            library_cp_id=library_package_id,
            actor_id=user_id,
            name_override=effective_name or None,
            description_override=effective_desc or None,
            include_seed_data=include_seed_data,
        )
        installed = await App.get(result["app_id"])
        if not installed:
            raise BadRequestError(message="App install failed")
        return installed

    safe_name = non_empty_after_strip(name, "name")
    if len(safe_name) > 200:
        raise BadRequestError(message="name must be 200 characters or fewer")
    name_fold = compute_fold(safe_name)
    safe_accent = validate_hex_color(accent_color, allow_empty=True)
    await assert_unique(
        App,
        {
            "context.workspace_id": resolved_workspace_id,
            "context.name_fold": name_fold,
        },
        entity="app",
        field_label="name",
        value=safe_name,
        scope_label="in this workspace",
    )
    now = utc_now_iso()
    sp = await App.create(
        name=safe_name,
        name_fold=name_fold,
        owner_user_id=user_id,
        description=description or "",
        visibility=resolved_visibility,
        workspace_id=resolved_workspace_id,
        accent_color=safe_accent,
        created_at=now,
        updated_at=now,
    )

    await wire_app_owner(sp, user_id, workspace_id=resolved_workspace_id)
    await catalog_app(sp)

    # D-05 single emission path. Mirrors api/apps.py::create_space.
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="app.create",
        resource_type="App",
        resource_id=sp.id,
        before=None,
        after=await export_node(sp),
        scope=f"app:{sp.id}",
    )

    return sp


# Backwards-compatibility helper for callers that want the same response
# wrapper the HTTP handler returns (export_node + warnings list).
async def create_app_response_payload(
    app_node: App,
    *,
    type_hint_warning: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the ``{"app": …, "message": …, "warnings"?: [...]}`` dict
    returned by the @endpoint HTTP handler."""
    response: Dict[str, Any] = {
        "app": await export_node(app_node),
        "message": "App created successfully",
    }
    if type_hint_warning:
        response["warnings"] = [type_hint_warning]
    return response

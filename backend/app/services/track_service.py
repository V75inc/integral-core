"""Track service-layer helpers.

Phase 9 Plan 09-05 (B4) — extracted from ``api/tracks.py::create_track`` so
agentive tools + onboarding flows can drive Track creation in-process
(no HTTP self-call, no MCP recursion per Pitfall 6).

The ``api/tracks.py::create_track`` HTTP handler delegates to
``create_track_in_space`` after auth + request parsing. All existing
behaviour is preserved (ContentProfile attach, OWNS edge, USES_TEMPLATE,
CONTAINS edge into App, ChangeEvent emission).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Errors imported from app.api.errors mirrors the pre-existing service-
# layer pattern (see services/agent_scratch.py, services/share_links.py,
# services/sharing.py, etc.) — the @endpoint side-effect loop triggered
# by app.api.__init__ is the standard runtime behaviour.
from app.api.errors import (
    BadRequestError,
    InsufficientPermissionsError,
    ResourceNotFoundError,
)
from app.api.utils import export_node
from app.api.validators import (
    normalize_track_accent_color,
    normalize_track_visibility_input,
    validate_track_visibility_workspace,
)
from app.api.validators_common import compute_fold, non_empty_after_strip
from app.models.edges import CONTAINS, OWNS, USES_TEMPLATE
from app.models.nodes import App, ContentProfile, Track
from app.schemas.policy import Resource, Subject
from app.services.app_graph import (
    catalog_track,
    ensure_track_attached_content_profile,
    get_app_attached_content_profile,
    get_track_attached_content_profile,
)
from app.services.change_event import emit_change_event
from app.services.content_profile_merge import (
    apply_space_track_spec_to_track,
    merge_library_manifest_into_content_profile,
    merge_template_content_profile_into_track,
    verify_track_template_in_app,
)
from app.services.content_profile_runtime import (
    compile_canonical_manifest,
    find_app_track_spec_by_key,
)
from app.services.entry_context import (
    attach_content_profile_defaults_to_track_data,
    attach_parent_app_to_track_data,
)
from app.services.permissions import (
    can_create_track_under_workspace,
    get_user_node,
)
from app.services.policy_engine import evaluate as policy_evaluate
from app.services.uniqueness import assert_unique
from app.services.workspace_resolver import resolve_workspace_id
from app.utils.time import utc_now_iso


async def create_track_in_space(
    user_id: str,
    *,
    title: str,
    purpose: Optional[str] = None,
    icon: Optional[str] = None,
    visibility: Optional[str] = None,
    template_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    app_id: Optional[str] = None,
    library_content_profile_id: Optional[str] = None,
    app_track_template_content_profile_id: Optional[str] = None,
    app_track_type_key: Optional[str] = None,
    accent_color: Optional[str] = None,
) -> Track:
    """In-process Track creation for an authenticated principal.

    Mirrors the post-auth body of ``api/tracks.py::create_track`` (B4).
    The HTTP handler resolves ``type_hint`` first (it's a user-facing
    affordance) and passes the resolved ``library_content_profile_id``
    here.

    Canonical lookup is ``await User.get(user_id)`` everywhere
    (Plan 09-05 B1 — no other lookup helper).

    Returns the created Track node. Emits a single ``track.create``
    ChangeEvent on success.
    """
    stk_raw = str(app_track_type_key or "").strip()

    profile_pick_count = sum(
        bool(x)
        for x in (
            stk_raw,
            app_track_template_content_profile_id,
            library_content_profile_id,
        )
    )
    if profile_pick_count > 1:
        raise BadRequestError(
            message=(
                "Use only one of app_track_type_key, "
                "app_track_template_content_profile_id, or library_content_profile_id"
            ),
        )

    space_track_spec: Optional[Dict[str, Any]] = None
    sp_for_spec: Optional[App] = None
    if stk_raw:
        if not app_id:
            raise BadRequestError(
                message="app_id is required when using app_track_type_key",
            )
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="app.update",
            resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(
                message="Cannot add a track to this app",
            )
        sp_for_spec = await App.get(app_id)
        if not sp_for_spec:
            raise ResourceNotFoundError(message="App not found")
        sacp = await get_app_attached_content_profile(sp_for_spec)
        if not sacp or not sacp.manifest:
            raise BadRequestError(message="App has no content profile manifest")
        canonical = compile_canonical_manifest(manifest=dict(sacp.manifest))
        space_track_spec = find_app_track_spec_by_key(canonical, stk_raw)
        if not space_track_spec:
            raise BadRequestError(
                message=f"Unknown track type key for this app: {stk_raw}",
            )

    sp_for_link: Optional[App] = None
    explicit_vis = normalize_track_visibility_input(visibility)
    resolved_vis: str

    if app_id:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="app.update",
            resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(
                message="Cannot add a track to this app",
            )
        sp_for_link = await App.get(app_id)
        if sp_for_link is None and stk_raw:
            sp_for_link = sp_for_spec
        if not sp_for_link:
            raise ResourceNotFoundError(message="App not found")
        track_workspace_id = sp_for_link.workspace_id or await resolve_workspace_id(
            user_id=user_id, workspace_id=workspace_id
        )
    else:
        track_workspace_id = await resolve_workspace_id(
            user_id=user_id, workspace_id=workspace_id
        )

    if explicit_vis is None:
        resolved_vis = "inherit"
    else:
        resolved_vis = explicit_vis

    await validate_track_visibility_workspace(resolved_vis, track_workspace_id)

    if not await can_create_track_under_workspace(user_id, track_workspace_id):
        raise InsufficientPermissionsError(
            message="You are not allowed to create tracks in this workspace",
        )

    effective_template_ref = stk_raw if stk_raw else template_id

    safe_title = non_empty_after_strip(title, "title")
    if len(safe_title) > 200:
        raise BadRequestError(message="title must be 200 characters or fewer")
    title_fold = compute_fold(safe_title)
    await assert_unique(
        Track,
        {
            "context.workspace_id": track_workspace_id,
            "context.title_fold": title_fold,
        },
        entity="track",
        field_label="title",
        value=safe_title,
        scope_label="in this workspace",
    )
    now = utc_now_iso()
    track = await Track.create(
        title=safe_title,
        title_fold=title_fold,
        owner_id=user_id,
        purpose=purpose or "",
        icon=icon or "",
        accent_color=normalize_track_accent_color(accent_color),
        visibility=resolved_vis,
        template_id=effective_template_ref,
        workspace_id=track_workspace_id,
        created_at=now,
        updated_at=now,
    )

    user = await get_user_node(user_id)
    if not user:
        raise ResourceNotFoundError(message="User not found")
    await user.connect(track, edge=OWNS, role="owner", granted_at=now)

    if effective_template_ref:
        template = await Track.get(effective_template_ref)
        if template:
            await track.connect(template, edge=USES_TEMPLATE)

    await catalog_track(track)

    if app_track_template_content_profile_id:
        if not app_id:
            raise BadRequestError(
                message="app_id is required when using app_track_template_content_profile_id",
            )
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="app.update",
            resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(
                message="Cannot use a template from this app",
            )
        tpl = await verify_track_template_in_app(
            app_id, app_track_template_content_profile_id
        )
        await merge_template_content_profile_into_track(tpl, track)

    if library_content_profile_id:
        lib = await ContentProfile.get(library_content_profile_id)
        if not lib or not getattr(lib, "library_package", False):
            raise BadRequestError(message="Library package not found")
        tcp = await get_track_attached_content_profile(track)
        if not tcp:
            raise BadRequestError(message="Track content profile missing")
        await merge_library_manifest_into_content_profile(
            lib, tcp, track, for_space=False
        )
        track.library_merge_source_id = library_content_profile_id
        await track.save()

    if space_track_spec is not None:
        await apply_space_track_spec_to_track(track, space_track_spec)

    if app_id:
        _decision = await policy_evaluate(
            subject=Subject(kind="human", id=user_id),
            action="app.update",
            resource=Resource(kind="app", id=app_id, scope=f"app:{app_id}"),
        )
        if not _decision.allowed:
            raise InsufficientPermissionsError(
                message="Cannot add a track to this app",
            )
        sp = (
            sp_for_link
            if sp_for_link is not None and sp_for_link.id == app_id
            else await App.get(app_id)
        )
        if not sp:
            raise ResourceNotFoundError(message="App not found")
        ctx = await sp.get_context()
        existing = await ctx.find_edges_between(sp.id, track.id, edge_class=CONTAINS)
        if not existing:
            await sp.connect(track, edge=CONTAINS, added_at=now)

    if not await get_track_attached_content_profile(track):
        await ensure_track_attached_content_profile(track)

    # D-05 single emission path. Mirrors api/tracks.py::create_track.
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="track.create",
        resource_type="Track",
        resource_id=track.id,
        before=None,
        after=await export_node(track),
        scope=(f"app:{app_id}" if app_id else f"track:{track.id}"),
    )

    return track


async def create_track_response_payload(
    track: Track,
    *,
    type_hint_warning: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the ``{"track": …, "message": …, "warnings"?: [...]}`` dict
    returned by the @endpoint HTTP handler.
    """
    created = await export_node(track)
    await attach_parent_app_to_track_data(created, track)
    await attach_content_profile_defaults_to_track_data(created, track)
    response: Dict[str, Any] = {
        "track": created,
        "message": "Track created successfully",
    }
    if type_hint_warning:
        response["warnings"] = [type_hint_warning]
    return response

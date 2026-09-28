"""Workspace skills editor API."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import Request
from jvspatial.api import endpoint
from jvspatial.api.exceptions import InsufficientPermissionsError

from app.agentive.services.agent_skills import (
    build_tool_catalogue_for_editor,
    create_workspace_skill,
    delete_workspace_skill,
    get_skill_detail,
    is_core_skill_id,
    list_core_skills,
    list_workspace_skills,
    reset_workspace_skill,
    update_workspace_skill,
)
from app.agentive.tooling.catalogue import build_tool_catalogue
from app.agentive.workspace_agent_profile import compose_workspace_agent_profile
from app.api.errors import MissingAuthenticationError, ResourceNotFoundError
from app.api.utils import resolve_principal_id
from app.models.nodes import Skill
from app.schemas.agentive.agent_skills import (
    EffectiveSkillContextResponse,
    EffectiveSkillEntry,
    EffectiveToolEntry,
    SkillCreateRequest,
    SkillDetailResponse,
    SkillListResponse,
    SkillSummaryResponse,
    SkillUpdateRequest,
    ToolCatalogueResponse,
)
from app.services.change_event import emit_change_event
from app.services.request_scope import resolve_workspace_id_from_request
from app.services.workspace_permissions import is_workspace_admin_or_owner


def _require_user(request: Request) -> str:
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")
    return user_id


async def _require_workspace(request: Request, user_id: str) -> str:
    return await resolve_workspace_id_from_request(request, user_id)


async def _require_manage(request: Request, workspace_id: str, user_id: str) -> None:
    if not await is_workspace_admin_or_owner(user_id, workspace_id):
        raise InsufficientPermissionsError(
            message="workspace admin or owner required to manage skills"
        )


def _group_skills(rows: List[Dict[str, Any]]) -> SkillListResponse:
    core: List[SkillSummaryResponse] = []
    apps: List[SkillSummaryResponse] = []
    workspace: List[SkillSummaryResponse] = []
    for row in rows:
        item = SkillSummaryResponse(**row)
        if item.source == "core":
            core.append(item)
        elif item.source == "workspace":
            workspace.append(item)
        else:
            apps.append(item)
    return SkillListResponse(
        core=core,
        apps=apps,
        workspace=workspace,
        total=len(core) + len(apps) + len(workspace),
    )


@endpoint("/agentive/skills", methods=["GET"], auth=True, tags=["Agentive"])
async def list_skills(request: Request) -> Dict[str, Any]:
    """Unified workspace skill list: core, app bundle, and workspace-authored."""
    user_id = _require_user(request)
    workspace_id = await _require_workspace(request, user_id)
    core = list_core_skills()
    overlay = await list_workspace_skills(workspace_id, user_id=user_id)
    grouped = _group_skills(core + overlay)
    return grouped.model_dump()


@endpoint(
    "/agentive/skills/tool-catalogue", methods=["GET"], auth=True, tags=["Agentive"]
)
async def tool_catalogue(request: Request) -> Dict[str, Any]:
    """Autocomplete feed for skill editor tool picker."""
    _require_user(request)
    tools = await build_tool_catalogue_for_editor()
    return ToolCatalogueResponse(tools=tools, total=len(tools)).model_dump()


@endpoint("/agentive/skills/effective", methods=["GET"], auth=True, tags=["Agentive"])
async def effective_skill_context(
    request: Request, focused_app_id: str = ""
) -> Dict[str, Any]:
    """Return the permission-filtered skill/tool context for this workspace turn."""
    user_id = _require_user(request)
    workspace_id = await _require_workspace(request, user_id)

    # The profile composer is the runtime source of truth. It filters private
    # App skills by the caller's access before any skill names are returned.
    profile = await compose_workspace_agent_profile(
        workspace_id, user_id=user_id, focused_app_id=focused_app_id or None
    )
    accessible_apps = {app.app_id: app for app in profile.apps}
    authorized_focus = focused_app_id if focused_app_id in accessible_apps else None
    if authorized_focus != (focused_app_id or None):
        # Never let a client-selected inaccessible App influence the display.
        profile = await compose_workspace_agent_profile(
            workspace_id, user_id=user_id, focused_app_id=None
        )

    active_docs = [
        doc
        for doc in profile.overlay_skill_docs
        if doc.metadata.get("kind") != "connected_sources"
    ]
    active_keys = {
        (
            str(doc.metadata.get("skill_key") or ""),
            str(doc.metadata.get("app_id") or ""),
            str(doc.metadata.get("origin") or ""),
        ): doc
        for doc in active_docs
    }
    rows: List[EffectiveSkillEntry] = []
    for row in list_core_skills():
        rows.append(
            EffectiveSkillEntry(
                id=row["id"],
                key=row["key"],
                name=row["name"],
                description=row.get("description") or "",
                source="core",
                state="available",
                tools_required=list(row.get("tools_required") or []),
            )
        )

    visible_rows = await list_workspace_skills(workspace_id, user_id=user_id)
    for row in visible_rows:
        key = (
            str(row.get("key") or ""),
            str(row.get("app_id") or ""),
            str(row.get("origin") or ""),
        )
        doc = active_keys.get(key)
        state = (
            "paused"
            if not row.get("enabled", True)
            else (
                "offer_first"
                if doc and doc.metadata.get("out_of_focus")
                else "available" if doc else "unavailable"
            )
        )
        reason = {
            "paused": "Paused in workspace skill settings.",
            "unavailable": "This skill is not loaded for the current turn.",
            "offer_first": "Available after the user confirms this App context.",
        }.get(state)
        app_id = str(row.get("app_id") or "") or None
        app = accessible_apps.get(app_id or "")
        rows.append(
            EffectiveSkillEntry(
                id=str(row.get("id") or ""),
                key=str(row.get("key") or ""),
                name=str(row.get("name") or row.get("key") or "Skill"),
                description=str(row.get("description") or ""),
                source=row.get("source", "workspace"),
                app_id=app_id,
                app_name=app.name if app else None,
                state=state,
                reason=reason,
                tools_required=list(row.get("tools_required") or []),
            )
        )

    tools = [
        EffectiveToolEntry(
            name=str(tool.get("name") or ""),
            description=str(tool.get("description") or ""),
            source="core",
        )
        for tool in build_tool_catalogue()
    ]
    from app.services.hooks.registry import get_workspace_tools

    authorized_app_slugs = {app.slug for app in profile.apps}
    for name, spec in sorted((get_workspace_tools(workspace_id) or {}).items()):
        if isinstance(spec, dict):
            bundle_slug = str(spec.get("_bundle_slug") or "")
            if not bundle_slug or bundle_slug not in authorized_app_slugs:
                continue
            tools.append(
                EffectiveToolEntry(
                    name=str(spec.get("key") or name),
                    description=str(spec.get("description") or ""),
                    source="workspace",
                )
            )
    payload = EffectiveSkillContextResponse(
        workspace_id=workspace_id,
        focused_app_id=authorized_focus,
        apps=[{"id": app.app_id, "name": app.name} for app in profile.apps],
        skills=rows,
        tools=tools,
    )
    return payload.model_dump()


@endpoint("/agentive/skills/{skill_id}", methods=["GET"], auth=True, tags=["Agentive"])
async def get_skill(request: Request, skill_id: str) -> Dict[str, Any]:
    """Full detail (resolved body, tools, staleness) for one skill."""
    user_id = _require_user(request)
    workspace_id = await _require_workspace(request, user_id)
    detail = await get_skill_detail(
        skill_id, workspace_id=workspace_id, user_id=user_id
    )
    return SkillDetailResponse(**detail).model_dump()


@endpoint("/agentive/skills", methods=["POST"], auth=True, tags=["Agentive"])
async def create_skill(request: Request) -> Dict[str, Any]:
    """Author a new workspace-scoped skill (admin/owner only)."""
    user_id = _require_user(request)
    workspace_id = await _require_workspace(request, user_id)
    await _require_manage(request, workspace_id, user_id)

    body = SkillCreateRequest.model_validate(await request.json())
    skill = await create_workspace_skill(
        workspace_id=workspace_id,
        user_id=user_id,
        key=body.key,
        name=body.name,
        description=body.description,
        body_override=body.body_override,
        tools_required=list(body.tools_required or []),
    )
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="skill.created",
        resource_type="skill",
        resource_id=skill.id,
        before=None,
        after={"key": skill.key, "origin": "workspace"},
        scope=workspace_id,
    )
    detail = await get_skill_detail(
        skill.id, workspace_id=workspace_id, user_id=user_id
    )
    return SkillDetailResponse(**detail).model_dump()


@endpoint(
    "/agentive/skills/{skill_id}", methods=["PATCH"], auth=True, tags=["Agentive"]
)
async def patch_skill(
    request: Request,
    skill_id: str,
) -> Dict[str, Any]:
    """Update a workspace skill's fields (customize a bundle/core default or edit a workspace-authored one)."""
    user_id = _require_user(request)
    workspace_id = await _require_workspace(request, user_id)
    await _require_manage(request, workspace_id, user_id)

    body = SkillUpdateRequest.model_validate(await request.json())
    if is_core_skill_id(skill_id):
        raise InsufficientPermissionsError(message="core skills are read-only")

    skill = await Skill.get(skill_id)
    if skill is None or getattr(skill, "workspace_id", "") != workspace_id:
        raise ResourceNotFoundError(message="skill not found")

    before = {"key": skill.key, "enabled": skill.enabled}
    patch = body.model_dump(exclude_unset=True)
    skill = await update_workspace_skill(
        skill,
        workspace_id=workspace_id,
        user_id=user_id,
        patch=patch,
    )
    action = "skill.toggled" if set(patch.keys()) == {"enabled"} else "skill.customized"
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action=action,  # type: ignore[arg-type]
        resource_type="skill",
        resource_id=skill.id,
        before=before,
        after={"key": skill.key, "enabled": skill.enabled},
        scope=workspace_id,
    )
    detail = await get_skill_detail(
        skill.id, workspace_id=workspace_id, user_id=user_id
    )
    return SkillDetailResponse(**detail).model_dump()


@endpoint(
    "/agentive/skills/{skill_id}/reset",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
)
async def reset_skill(request: Request, skill_id: str) -> Dict[str, Any]:
    """Clear a workspace customization, reverting the skill to its bundle default."""
    user_id = _require_user(request)
    workspace_id = await _require_workspace(request, user_id)
    await _require_manage(request, workspace_id, user_id)

    if is_core_skill_id(skill_id):
        raise InsufficientPermissionsError(message="core skills are read-only")

    skill = await Skill.get(skill_id)
    if skill is None or getattr(skill, "workspace_id", "") != workspace_id:
        raise ResourceNotFoundError(message="skill not found")

    before = {"customized": bool(skill.body_override)}
    skill = await reset_workspace_skill(skill, workspace_id=workspace_id)
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="skill.reset",
        resource_type="skill",
        resource_id=skill.id,
        before=before,
        after={"customized": False},
        scope=workspace_id,
    )
    detail = await get_skill_detail(
        skill.id, workspace_id=workspace_id, user_id=user_id
    )
    return SkillDetailResponse(**detail).model_dump()


@endpoint(
    "/agentive/skills/{skill_id}", methods=["DELETE"], auth=True, tags=["Agentive"]
)
async def delete_skill(request: Request, skill_id: str) -> Dict[str, Any]:
    """Delete a workspace-authored skill (workspace-origin only, not a bundle override)."""
    user_id = _require_user(request)
    workspace_id = await _require_workspace(request, user_id)
    await _require_manage(request, workspace_id, user_id)

    if is_core_skill_id(skill_id):
        raise InsufficientPermissionsError(message="core skills are read-only")

    skill = await Skill.get(skill_id)
    if skill is None or getattr(skill, "workspace_id", "") != workspace_id:
        raise ResourceNotFoundError(message="skill not found")

    skill_id_val = skill.id
    key = skill.key
    await delete_workspace_skill(skill, workspace_id=workspace_id)
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="skill.deleted",
        resource_type="skill",
        resource_id=skill_id_val,
        before={"key": key},
        after=None,
        scope=workspace_id,
    )
    return {"ok": True, "id": skill_id_val}

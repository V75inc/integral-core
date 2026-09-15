"""Permission-checked wrappers the resident agent uses to author workspace skills.

app.agentive.services.agent_skills already validates and mutates Skill nodes
(create_workspace_skill / update_workspace_skill / delete_workspace_skill) but
has no permission check of its own — only the HTTP handlers in
app/agentive/api/agent_skills.py gate access, via is_workspace_admin_or_owner,
before calling them. An agent tool that called those functions directly would
let any workspace member (including a guest) create/edit/delete skills that
affect every user in the workspace via chat. These wrappers close that gap.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.agentive.services.agent_skills import (
    create_workspace_skill,
    delete_workspace_skill,
    update_workspace_skill,
)
from app.api.errors import InsufficientPermissionsError, ResourceNotFoundError
from app.models.nodes import Skill
from app.services.workspace_permissions import is_workspace_admin_or_owner


def _slugify_key(name: str) -> str:
    """Derive a lowercase snake_case key from a display name.

    Mirrors SkillEditorModal.tsx's create-time slugify exactly (same regex
    shape) so an agent-authored skill's key follows the same convention as
    a human-authored one from the Settings UI.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower())
    slug = slug.strip("_")
    return slug or "custom_skill"


async def _require_manage(user_id: str, workspace_id: str) -> None:
    if not await is_workspace_admin_or_owner(user_id, workspace_id):
        raise InsufficientPermissionsError(
            message="workspace admin or owner required to manage skills"
        )


async def _load_workspace_skill(skill_id: str, workspace_id: str) -> Skill:
    skill = await Skill.get(skill_id)
    if skill is None or getattr(skill, "workspace_id", "") != workspace_id:
        raise ResourceNotFoundError(message="skill not found")
    return skill


async def author_skill_for_agent(
    *,
    user_id: str,
    workspace_id: str,
    name: str,
    description: str,
    body_override: str,
    key: Optional[str] = None,
    tools_required: Optional[List[str]] = None,
    app_id: Optional[str] = None,
    private: Optional[bool] = None,
) -> Dict[str, Any]:
    """Create a new workspace-scoped declarative skill on the agent's behalf.

    ``key`` is optional — derived from ``name`` (same slugify rule the
    Settings UI applies) when omitted. ``app_id`` scopes the skill to a
    specific App (``private`` defaults True in that case — see
    ``create_workspace_skill``).
    """
    await _require_manage(user_id, workspace_id)
    skill = await create_workspace_skill(
        workspace_id=workspace_id,
        user_id=user_id,
        key=key or _slugify_key(name),
        name=name,
        description=description,
        body_override=body_override,
        tools_required=list(tools_required or []),
        app_id=app_id or "",
        private=private,
    )
    return {"skill_id": skill.id, "key": skill.key}


async def update_skill_for_agent(
    *,
    user_id: str,
    workspace_id: str,
    skill_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    body_override: Optional[str] = None,
    tools_required: Optional[List[str]] = None,
    enabled: Optional[bool] = None,
    private: Optional[bool] = None,
) -> Dict[str, Any]:
    """Patch an existing workspace-origin skill (any workspace skill, not just agent-authored)."""
    await _require_manage(user_id, workspace_id)
    skill = await _load_workspace_skill(skill_id, workspace_id)

    patch: Dict[str, Any] = {}
    if name is not None:
        patch["name"] = name
    if description is not None:
        patch["description"] = description
    if body_override is not None:
        patch["body_override"] = body_override
    if tools_required is not None:
        patch["tools_required"] = tools_required
    if enabled is not None:
        patch["enabled"] = enabled
    if private is not None:
        patch["private"] = private

    updated = await update_workspace_skill(
        skill, workspace_id=workspace_id, user_id=user_id, patch=patch
    )
    return {"skill_id": updated.id, "key": updated.key}


async def delete_skill_for_agent(
    *, user_id: str, workspace_id: str, skill_id: str
) -> Dict[str, Any]:
    """Delete a workspace-origin skill on the agent's behalf."""
    await _require_manage(user_id, workspace_id)
    skill = await _load_workspace_skill(skill_id, workspace_id)
    skill_key = skill.key
    await delete_workspace_skill(skill, workspace_id=workspace_id)
    return {"ok": True, "key": skill_key}

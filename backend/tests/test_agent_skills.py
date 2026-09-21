"""Workspace skills editor API and service tests."""

from __future__ import annotations

import pytest

from app.agentive.services.agent_skills import (
    create_workspace_skill,
    list_core_skills,
    list_workspace_skills,
    validate_skill_key,
)
from app.agentive.services.skill_registry import get_skill_by_key, register_skill
from app.agentive.workspace_agent_profile import compose_workspace_agent_profile
from app.exceptions import SkillRegistrationError
from app.models.edges import CONTAINS, IS_MEMBER_OF
from app.models.nodes import App, Skill, User, Workspace
from app.services.app_graph import (
    catalog_app,
    catalog_user,
    catalog_workspace,
    wire_app_owner,
)
from app.utils.time import utc_now_iso


async def _user(email: str) -> User:
    now = utc_now_iso()
    u = await User.create(
        email=email,
        email_fold=email.casefold(),
        created_at=now,
        updated_at=now,
    )
    await catalog_user(u)
    return u


async def _personal_workspace(user: User) -> Workspace:
    from app.services.personal_workspace import ensure_personal_workspace

    return await ensure_personal_workspace(user)


async def _app_with_skill(
    *,
    name: str,
    slug: str,
    workspace_id: str,
    owner_id: str,
    skill_key: str,
) -> App:
    now = utc_now_iso()
    app = await App.create(
        name=name,
        name_fold=name.casefold(),
        owner_user_id=owner_id,
        workspace_id=workspace_id,
        source_operational_model_slug=slug,
        lifecycle_state="active",
        created_at=now,
        updated_at=now,
    )
    await catalog_app(app)
    await wire_app_owner(app, owner_id, workspace_id=workspace_id)
    await register_skill(
        app_id=app.id,
        workspace_id=workspace_id,
        skill_spec={
            "key": skill_key,
            "name": skill_key.replace("_", " ").title(),
            "kind": "declarative",
            "prompt_template_ref": f"skills/{skill_key}/SKILL.md",
            "tools_required": [],
            "private": False,
        },
    )
    return app


@pytest.mark.asyncio
async def test_list_core_skills_returns_read_only_tier():
    """Every filesystem-defined core skill is read-only and id-namespaced `core:*`."""
    core = list_core_skills()
    assert len(core) >= 12
    assert all(row["source"] == "core" for row in core)
    assert all(row["read_only"] is True for row in core)
    assert all(row["id"].startswith("core:") for row in core)
    assert all(str(row.get("description") or "").strip() for row in core)


@pytest.mark.asyncio
async def test_list_core_skills_description_from_frontmatter():
    """Core skill editor rows expose SKILL.md frontmatter description (when-to-use discovery)."""
    core = {row["key"]: row for row in list_core_skills()}
    assert "integral_models" in core
    assert "Operational Model" in core["integral_models"]["description"]
    assert core["integral_models"]["domain_body"]
    assert "## When to use" in core["integral_models"]["domain_body"]


@pytest.mark.asyncio
async def test_reserved_skill_key_rejected():
    """A workspace skill key colliding with an `integral_*` core skill is rejected."""
    with pytest.raises(SkillRegistrationError):
        validate_skill_key("integral_filing")


@pytest.mark.asyncio
async def test_body_override_wins_in_composed_profile(authenticated_client):
    """A skill's workspace body_override, not the bundle default, reaches the composed agent profile."""
    owner = await _user("skills-owner@example.com")
    ws = await _personal_workspace(owner)
    app = await _app_with_skill(
        name="CRM",
        slug="crm",
        workspace_id=ws.id,
        owner_id=owner.id,
        skill_key="lead_intake",
    )
    skills = await app.nodes(edge=[CONTAINS], node=["Skill"])
    skill = next(s for s in skills if getattr(s, "key", "") == "lead_intake")
    skill.body_override = "CUSTOM OVERRIDE BODY"
    skill.updated_at = utc_now_iso()
    await skill.save()

    profile = await compose_workspace_agent_profile(ws.id, user_id=owner.id)
    doc = next(d for d in profile.overlay_skill_docs if d.name.endswith("lead_intake"))
    assert "CUSTOM OVERRIDE BODY" in doc.body


@pytest.mark.asyncio
async def test_disabled_skill_absent_from_overlay(authenticated_client):
    """A skill with enabled=False is excluded from the composed agent profile overlay."""
    owner = await _user("skills-disabled@example.com")
    ws = await _personal_workspace(owner)
    app = await _app_with_skill(
        name="CRM2",
        slug="crm",
        workspace_id=ws.id,
        owner_id=owner.id,
        skill_key="pipeline_review",
    )
    skills = await app.nodes(edge=[CONTAINS], node=["Skill"])
    skill = next(s for s in skills if getattr(s, "key", "") == "pipeline_review")
    skill.enabled = False
    skill.updated_at = utc_now_iso()
    await skill.save()

    profile = await compose_workspace_agent_profile(ws.id, user_id=owner.id)
    names = [d.name for d in profile.overlay_skill_docs]
    assert not any(n.endswith("pipeline_review") for n in names)


@pytest.mark.asyncio
async def test_disabled_bundle_skill_still_listed_for_editor():
    """A disabled bundle skill must remain in the editor list so it can be
    re-enabled — even though the runtime resolver hides it from the agent."""
    owner = await _user("skills-disabled-list@example.com")
    ws = await _personal_workspace(owner)
    app = await _app_with_skill(
        name="CRM3",
        slug="crm",
        workspace_id=ws.id,
        owner_id=owner.id,
        skill_key="pipeline_audit",
    )
    skills = await app.nodes(edge=[CONTAINS], node=["Skill"])
    skill = next(s for s in skills if getattr(s, "key", "") == "pipeline_audit")
    skill.enabled = False
    skill.updated_at = utc_now_iso()
    await skill.save()

    rows = await list_workspace_skills(ws.id, user_id=owner.id)
    match = next((r for r in rows if r["key"] == "pipeline_audit"), None)
    assert match is not None
    assert match["enabled"] is False


@pytest.mark.asyncio
async def test_workspace_authored_skill_in_overlay():
    """A workspace-authored (non-bundle) skill reaches the composed agent profile."""
    owner = await _user("skills-ws-author@example.com")
    ws = await _personal_workspace(owner)
    await create_workspace_skill(
        workspace_id=ws.id,
        user_id=owner.id,
        key="my_playbook",
        name="My Playbook",
        description="When user asks for help",
        body_override="Do the thing step by step.",
        tools_required=[],
    )

    profile = await compose_workspace_agent_profile(ws.id, user_id=owner.id)
    names = [d.name for d in profile.overlay_skill_docs]
    assert "workspace__my_playbook" in names


@pytest.mark.asyncio
async def test_workspace_isolation_across_two_workspaces():
    """A skill customization in one workspace never leaks into another workspace's profile."""
    owner = await _user("skills-iso-owner@example.com")
    ws_a = await _personal_workspace(owner)
    ws_b = await Workspace.create(
        kind="organization",
        name="Other Org",
        name_fold="other org",
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )
    await catalog_workspace(ws_b)
    await owner.connect(
        ws_b,
        edge=IS_MEMBER_OF,
        role="owner",
        joined_at=utc_now_iso(),
        can_create_apps=True,
        can_create_tracks=True,
    )

    await _app_with_skill(
        name="CRM A",
        slug="crm",
        workspace_id=ws_a.id,
        owner_id=owner.id,
        skill_key="lead_intake",
    )
    app_b = await _app_with_skill(
        name="CRM B",
        slug="crm",
        workspace_id=ws_b.id,
        owner_id=owner.id,
        skill_key="lead_intake",
    )
    skills_b = await app_b.nodes(edge=["CONTAINS"], node=["Skill"])
    skill_b = next(s for s in skills_b if getattr(s, "key", "") == "lead_intake")
    skill_b.body_override = "WORKSPACE B ONLY"
    skill_b.updated_at = utc_now_iso()
    await skill_b.save()

    profile_a = await compose_workspace_agent_profile(ws_a.id, user_id=owner.id)
    profile_b = await compose_workspace_agent_profile(ws_b.id, user_id=owner.id)
    body_a = next(
        d.body for d in profile_a.overlay_skill_docs if d.name.endswith("lead_intake")
    )
    body_b = next(
        d.body for d in profile_b.overlay_skill_docs if d.name.endswith("lead_intake")
    )
    assert "WORKSPACE B ONLY" not in body_a
    assert "WORKSPACE B ONLY" in body_b


@pytest.mark.asyncio
async def test_api_list_and_patch_skill(authenticated_client, test_user):
    """End-to-end via the HTTP API: list, create, patch, then delete a workspace skill."""
    ws = await _personal_workspace(test_user)
    # The ``ws:`` prefix is mandatory. A bare id parses to None, and used to
    # fall back silently to the caller's Personal Workspace — which happens to
    # be this same workspace, so the test passed for the wrong reason. It is a
    # 400 now.
    headers = {"X-Integral-Scope": f"ws:{ws.id}"}

    list_resp = await authenticated_client.get("/api/agentive/skills", headers=headers)
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert "core" in data
    assert len(data["core"]) >= 12

    create_resp = await authenticated_client.post(
        "/api/agentive/skills",
        headers=headers,
        json={
            "key": "team_standup",
            "name": "Team Standup",
            "description": "Weekly standup helper",
            "body_override": "Ask for blockers and wins.",
            "tools_required": [],
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["source"] == "workspace"
    skill_id = created["id"]

    patch_resp = await authenticated_client.patch(
        f"/api/agentive/skills/{skill_id}",
        headers=headers,
        json={"body_override": "Updated standup SOP."},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["customized"] is True

    delete_resp = await authenticated_client.delete(
        f"/api/agentive/skills/{skill_id}",
        headers=headers,
    )
    assert delete_resp.status_code == 200


@pytest.mark.asyncio
async def test_get_core_skill_detail_includes_description():
    """Core skill editor detail must expose frontmatter description (not blank)."""
    from app.agentive.services.agent_skills import get_skill_detail

    # get_skill_detail for core skips workspace gate — pass dummy ids
    detail = await get_skill_detail(
        "core:integral_filing",
        workspace_id="ws-dummy",
        user_id="user-dummy",
    )
    assert detail["description"].strip()
    assert (
        "file" in detail["description"].lower()
        or "content" in detail["description"].lower()
    )


@pytest.mark.asyncio
async def test_get_bundle_skill_detail_without_source_operational_model_slug():
    """Bundle skill editor must load domain body/tools from disk when App lacks slug."""
    from app.agentive.services.agent_skills import get_skill_detail

    owner = await _user("skills-disk@example.com")
    ws = await _personal_workspace(owner)
    now = utc_now_iso()
    app = await App.create(
        name="HR App",
        name_fold="hr app",
        owner_user_id=owner.id,
        workspace_id=ws.id,
        lifecycle_state="active",
        created_at=now,
        updated_at=now,
    )
    await catalog_app(app)
    await wire_app_owner(app, owner.id, workspace_id=ws.id)
    await register_skill(
        app_id=app.id,
        workspace_id=ws.id,
        skill_spec={
            "key": "process_time_off",
            "name": "Process Time Off",
            "kind": "declarative",
            "prompt_template_ref": "skills/process_time_off/SKILL.md",
            "tools_required": ["integral_update_entry"],
            "description": "Process a time-off request.",
            "private": False,
        },
    )
    skill = await get_skill_by_key(app.id, "process_time_off")
    assert skill is not None

    detail = await get_skill_detail(
        skill.id,
        workspace_id=ws.id,
        user_id=owner.id,
    )
    assert "## Procedure" in (detail.get("domain_body") or "")
    assert "integral_whoami" in detail.get("tools_required", [])
    assert "integral_list_apps" in detail.get("tools_required", [])


@pytest.mark.asyncio
async def test_register_skill_upgrade_preserves_override():
    """Re-registering a skill (bundle upgrade) must not clobber an existing workspace override."""
    owner = await _user("skills-upgrade@example.com")
    ws = await _personal_workspace(owner)
    app = await _app_with_skill(
        name="Upgrade App",
        slug="crm",
        workspace_id=ws.id,
        owner_id=owner.id,
        skill_key="lead_intake",
    )
    skills = await app.nodes(edge=[CONTAINS], node=["Skill"])
    skill = next(s for s in skills if getattr(s, "key", "") == "lead_intake")
    skill.body_override = "KEEP ME"
    skill.name = "Custom Name"
    skill.updated_at = utc_now_iso()
    await skill.save()

    await register_skill(
        app_id=app.id,
        workspace_id=ws.id,
        skill_spec={
            "key": "lead_intake",
            "name": "Manifest Name",
            "description": "New manifest description",
            "kind": "declarative",
            "prompt_template_ref": "skills/lead_intake/SKILL.md",
            "tools_required": [],
            "private": False,
        },
    )

    skill = await Skill.get(skill.id)
    assert skill is not None
    assert skill.body_override == "KEEP ME"
    assert skill.name == "Custom Name"
    assert skill.description == "New manifest description"

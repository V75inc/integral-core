"""I-SKILL-SCOPE-01 — overlay skills filtered by user App access."""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from app.agentive.services.skill_registry import get_callable_skills, register_skill
from app.agentive.workspace_agent_profile import (
    compose_workspace_agent_profile,
    invalidate_workspace_profile,
)
from app.models.edges import IS_MEMBER_OF
from app.models.nodes import App, User, Workspace
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


async def _org_workspace(owner: User) -> Workspace:
    now = datetime.now().isoformat()
    ws = await Workspace.create(
        kind="organization",
        name="Skill Scope Org",
        name_fold="skill scope org",
        created_at=now,
        updated_at=now,
    )
    await catalog_workspace(ws)
    await owner.connect(
        ws,
        edge=IS_MEMBER_OF,
        role="owner",
        joined_at=now,
        can_create_apps=True,
        can_create_tracks=True,
    )
    return ws


def _write_skill_bundle(skill_key: str) -> str:
    """Write a minimal on-disk skill bundle and return its bundle dir path.

    Canonical-format skills resolve their body from a SKILL.md on disk
    (inline prompt strings were dropped with the canonical skill format), so
    the fixture provides a real bundle dir for `_resolve_bundle_dir_async`.
    """
    bundle_dir = Path(tempfile.mkdtemp(prefix="skill-scope-bundle-"))
    skill_dir = bundle_dir / "skills" / skill_key
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {skill_key}\n"
        f"description: Test skill {skill_key} for access-scope tests.\n"
        "---\n"
        f"You are running {skill_key}.\n",
        encoding="utf-8",
    )
    return str(bundle_dir)


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
        metadata={"bundle_dir_path": _write_skill_bundle(skill_key)},
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
            "kind": "declarative",
            "prompt_template_ref": f"skills/{skill_key}/SKILL.md",
            "private": False,
        },
    )
    return app


@pytest.mark.asyncio
async def test_get_callable_skills_respects_user_app_access():
    owner = await _user("owner@skill-scope.test")
    outsider = await _user("outsider@skill-scope.test")
    ws = await _org_workspace(owner)

    app_x = await _app_with_skill(
        name="App X",
        slug="app-x",
        workspace_id=ws.id,
        owner_id=owner.id,
        skill_key="skill_x",
    )
    app_y = await _app_with_skill(
        name="App Y",
        slug="app-y",
        workspace_id=ws.id,
        owner_id=owner.id,
        skill_key="skill_y",
    )
    _ = app_y

    owner_skills = await get_callable_skills("", ws.id, user_id=owner.id)
    owner_keys = {s.key for s in owner_skills}
    assert "skill_x" in owner_keys
    assert "skill_y" in owner_keys

    outsider_skills = await get_callable_skills("", ws.id, user_id=outsider.id)
    assert outsider_skills == []


@pytest.mark.asyncio
async def test_compose_profile_excludes_inaccessible_app_skills():
    owner = await _user("owner2@skill-scope.test")
    outsider = await _user("outsider2@skill-scope.test")
    ws = await _org_workspace(owner)

    await _app_with_skill(
        name="Visible App",
        slug="visible-app",
        workspace_id=ws.id,
        owner_id=owner.id,
        skill_key="visible_skill",
    )

    profile_owner = await compose_workspace_agent_profile(ws.id, user_id=owner.id)
    assert any(
        d.name.endswith("visible_skill") for d in profile_owner.overlay_skill_docs
    )

    profile_outsider = await compose_workspace_agent_profile(ws.id, user_id=outsider.id)
    assert profile_outsider.overlay_skill_docs == ()


@pytest.mark.asyncio
async def test_profile_cache_isolated_per_user():
    owner = await _user("cache-owner@skill-scope.test")
    outsider = await _user("cache-outsider@skill-scope.test")
    ws = await _org_workspace(owner)

    await _app_with_skill(
        name="Cache App",
        slug="cache-app",
        workspace_id=ws.id,
        owner_id=owner.id,
        skill_key="cache_skill",
    )

    first_owner = await compose_workspace_agent_profile(ws.id, user_id=owner.id)
    first_outsider = await compose_workspace_agent_profile(ws.id, user_id=outsider.id)
    assert len(first_owner.overlay_skill_docs) == 1
    assert len(first_outsider.overlay_skill_docs) == 0

    cached_owner = await compose_workspace_agent_profile(ws.id, user_id=owner.id)
    cached_outsider = await compose_workspace_agent_profile(ws.id, user_id=outsider.id)
    assert cached_owner is first_owner
    assert cached_outsider is first_outsider

    invalidate_workspace_profile(ws.id)
    second_owner = await compose_workspace_agent_profile(ws.id, user_id=owner.id)
    assert second_owner is not first_owner

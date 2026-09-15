"""Tests for per-workspace Agent Configuration Profile composition."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agentive.services.skill_registry import register_skill
from app.agentive.workspace_agent_profile import (
    clear_turn_workspace_profile,
    compose_workspace_agent_profile,
    get_turn_workspace_profile,
    invalidate_workspace_profile,
    materialize_profile_for_turn,
)
from app.models.nodes import App
from app.utils.time import utc_now_iso

_PROFILES_ROOT = Path(__file__).resolve().parents[1] / "app" / "profiles"

_CAROUSEL_DRAFTER_TOOLS = [
    "integral_create_entry",
    "integral_query",
    "integral_query_entries",
    "integral_describe_profile",
    "integral_get_track_schema",
]


async def _make_app(
    name: str,
    workspace_id: str,
    *,
    source_profile_slug: str | None = None,
    lifecycle_state: str = "active",
) -> App:
    now = utc_now_iso()
    return await App.create(
        name=name,
        name_fold=name.casefold(),
        workspace_id=workspace_id,
        source_profile_slug=source_profile_slug,
        lifecycle_state=lifecycle_state,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_workspace_profile_empty_overlay():
    profile = await compose_workspace_agent_profile("ws_empty_overlay")
    assert profile.workspace_id == "ws_empty_overlay"
    assert profile.apps == ()
    assert profile.overlay_skill_docs == ()


@pytest.mark.asyncio
async def test_workspace_profile_after_install_public_skill():
    ws = "ws_with_skill"
    app = await _make_app("Content Factory", ws, source_profile_slug="content-factory")
    await register_skill(
        app_id=app.id,
        workspace_id=ws,
        skill_spec={
            "key": "carousel_drafter",
            "name": "Carousel Drafter",
            "kind": "declarative",
            "prompt_template_ref": "skills/carousel_drafter/SKILL.md",
            "tools_required": list(_CAROUSEL_DRAFTER_TOOLS),
            "private": False,
        },
    )

    profile = await compose_workspace_agent_profile(ws)
    names = [d.name for d in profile.overlay_skill_docs]
    assert "content-factory__carousel_drafter" in names
    doc = next(
        d for d in profile.overlay_skill_docs if d.name.endswith("carousel_drafter")
    )
    assert "integral_query_entries" in doc.requires_tools
    assert "Draft" in doc.body or "carousel" in doc.body.lower()
    assert "Standard Integral Tool Procedure" in doc.body
    assert "EmbeddedIntegralAction" in doc.requires_actions


@pytest.mark.asyncio
async def test_workspace_profile_private_skill_excluded_for_resident():
    ws = "ws_private_skill"
    app = await _make_app("Sales", ws, source_profile_slug="sales")
    await register_skill(
        app_id=app.id,
        workspace_id=ws,
        skill_spec={
            "key": "scope_from_transcript",
            "name": "Scope",
            "kind": "declarative",
            "prompt_template_ref": "skills/scope_from_transcript/SKILL.md",
            "tools_required": [],
            "private": True,
        },
    )

    profile = await compose_workspace_agent_profile(ws)
    assert profile.overlay_skill_docs == ()


@pytest.mark.asyncio
async def test_workspace_profile_isolation():
    app_a = await _make_app("App A", "ws_iso_a", source_profile_slug="content-factory")
    await register_skill(
        app_id=app_a.id,
        workspace_id="ws_iso_a",
        skill_spec={
            "key": "carousel_drafter",
            "kind": "declarative",
            "prompt_template_ref": "skills/carousel_drafter/SKILL.md",
        },
    )
    app_b = await _make_app("App B", "ws_iso_b", source_profile_slug="content-factory")
    await register_skill(
        app_id=app_b.id,
        workspace_id="ws_iso_b",
        skill_spec={
            "key": "performance_reviewer",
            "kind": "declarative",
            "prompt_template_ref": "skills/performance_reviewer/SKILL.md",
        },
    )

    profile_a = await compose_workspace_agent_profile("ws_iso_a")
    profile_b = await compose_workspace_agent_profile("ws_iso_b")
    names_a = {d.name for d in profile_a.overlay_skill_docs}
    names_b = {d.name for d in profile_b.overlay_skill_docs}
    assert "content-factory__carousel_drafter" in names_a
    assert "content-factory__performance_reviewer" not in names_a
    assert "content-factory__performance_reviewer" in names_b
    assert "content-factory__carousel_drafter" not in names_b


@pytest.mark.asyncio
async def test_workspace_profile_invalidates_on_change():
    ws = "ws_invalidate"
    app = await _make_app("CF", ws, source_profile_slug="content-factory")
    await register_skill(
        app_id=app.id,
        workspace_id=ws,
        skill_spec={
            "key": "carousel_drafter",
            "kind": "declarative",
            "prompt_template_ref": "skills/carousel_drafter/SKILL.md",
        },
    )
    first = await compose_workspace_agent_profile(ws)
    cached = await compose_workspace_agent_profile(ws)
    assert cached.profile_version == first.profile_version

    invalidate_workspace_profile(ws)
    app.updated_at = utc_now_iso()
    await app.save()
    second = await compose_workspace_agent_profile(ws)
    assert second.profile_version != first.profile_version


@pytest.mark.asyncio
async def test_workspace_profile_refreshes_on_skill_change_without_invalidate():
    """Version-hint cache must not serve stale overlay after skill graph mutation."""
    ws = "ws_version_hint"
    app = await _make_app("CF", ws, source_profile_slug="content-factory")
    await register_skill(
        app_id=app.id,
        workspace_id=ws,
        skill_spec={
            "key": "carousel_drafter",
            "kind": "declarative",
            "prompt_template_ref": "skills/carousel_drafter/SKILL.md",
        },
    )
    first = await compose_workspace_agent_profile(ws)
    assert first.overlay_skill_docs

    from app.models.edges import CONTAINS

    skills = await app.nodes(edge=[CONTAINS], node=["Skill"])
    skill = next(s for s in skills if getattr(s, "key", "") == "carousel_drafter")
    skill.body_override = "## Updated overlay body for cache test."
    skill.updated_at = utc_now_iso()
    await skill.save()

    second = await compose_workspace_agent_profile(ws)
    assert second.profile_version != first.profile_version
    doc = next(
        d for d in second.overlay_skill_docs if d.name.endswith("carousel_drafter")
    )
    assert "Updated overlay body" in doc.body


@pytest.mark.asyncio
async def test_materialize_profile_for_turn_contextvar():
    ws = "ws_turn_ctx"
    await materialize_profile_for_turn(ws)
    try:
        profile = get_turn_workspace_profile()
        assert profile is not None
        assert profile.workspace_id == ws
    finally:
        clear_turn_workspace_profile()
    assert get_turn_workspace_profile() is None


@pytest.mark.asyncio
async def test_host_provider_reads_turn_profile():
    from jvagent.action.orchestrator.skill_providers import (
        clear_host_skill_providers,
        register_host_skill_provider,
    )

    from app.agentive.skill_bundle_provider import install_skill_provider_into_jvagent

    clear_host_skill_providers()
    install_skill_provider_into_jvagent()

    ws = "ws_host_provider"
    app = await _make_app("CF", ws, source_profile_slug="content-factory")
    await register_skill(
        app_id=app.id,
        workspace_id=ws,
        skill_spec={
            "key": "carousel_drafter",
            "kind": "declarative",
            "prompt_template_ref": "skills/carousel_drafter/SKILL.md",
            "private": False,
        },
    )
    await materialize_profile_for_turn(ws)
    try:
        from jvagent.action.orchestrator.skill_providers import collect_host_skill_docs

        docs = collect_host_skill_docs(None)
        names = {d.name for d in docs}
        assert "content-factory__carousel_drafter" in names
    finally:
        clear_turn_workspace_profile()
        clear_host_skill_providers()


@pytest.mark.asyncio
async def test_body_override_reapplies_extends():
    """Domain-only body_override must still merge embedded action base SOP."""
    from app.agentive.workspace_agent_profile import _resolve_prompt_body
    from app.models.edges import CONTAINS

    ws = "ws_override_extends"
    app = await _make_app("Content Factory", ws, source_profile_slug="content-factory")
    await register_skill(
        app_id=app.id,
        workspace_id=ws,
        skill_spec={
            "key": "carousel_drafter",
            "name": "Carousel Drafter",
            "kind": "declarative",
            "prompt_template_ref": "skills/carousel_drafter/SKILL.md",
            "tools_required": [],
            "private": False,
        },
    )
    skills = await app.nodes(edge=[CONTAINS], node=["Skill"])
    skill = next(s for s in skills if getattr(s, "key", "") == "carousel_drafter")
    skill.body_override = "## Custom domain\n\nDomain-only procedure text."
    skill.updated_at = utc_now_iso()
    await skill.save()

    bundle_dir = _PROFILES_ROOT / "content-factory"
    body, _ = _resolve_prompt_body(
        skill,
        bundle_dir=bundle_dir,
        tools_required=[],
    )
    assert body is not None
    assert "Standard Integral Tool Procedure" in body
    assert "Custom domain" in body


@pytest.mark.asyncio
async def test_private_app_scoped_workspace_skill_excluded_from_overlay():
    """A private, App-scoped, workspace-origin skill must not overlay for a
    caller without access to that App (S-SKILL-LEAK regression)."""
    from app.models.nodes import Skill

    ws = "ws_private_overlay_leak"
    app = await _make_app("Secret App", ws)
    now = utc_now_iso()
    await Skill.create(
        app_id=app.id,
        workspace_id=ws,
        key="salary_bands",
        name="workspace__salary_bands",
        description="HR salary bands",
        body_override="Confidential comp ranges.",
        origin="workspace",
        private=True,
        enabled=True,
        trust_tier="untrusted",
        created_at=now,
        updated_at=now,
    )
    invalidate_workspace_profile(ws)
    # Outsider: no membership / app-access grant in this workspace.
    profile = await compose_workspace_agent_profile(ws, user_id="outsider_no_access")
    names = {d.name for d in profile.overlay_skill_docs}
    assert "workspace__salary_bands" not in names

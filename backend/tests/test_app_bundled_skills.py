"""App-bundled Skill registry tests — Phase 10 Plan 10-04 (APP-SKILLS-01).

Covers:
- Declarative + custom skill registration
- tools_required validation against the live MCP catalogue
- Resolver-time ``private`` flag scope (Architectural Decision 5)
- Idempotent re-registration (same app_id + key updates the existing row)
- unregister_skills_for_app removes only the targeted App's skills
- Custom-skill compile-time rejection regression (mirror of 10-03 coverage)
- external_apis captured but not enforced (Open Question 5 deferred)

Tests create the App + Skill graph directly via Node CRUD — they don't need
Plan 10-05's install lifecycle to exercise the registry itself.
"""

from __future__ import annotations

import pytest

from app.agentive.nodes import AgentConfig
from app.agentive.services.skill_registry import (
    get_callable_skills,
    get_skill_by_key,
    register_skill,
    unregister_skills_for_app,
)
from app.exceptions import (
    InvalidToolReferenceError,
    OperationalModelValidationError,
    SkillRegistrationError,
)
from app.models.edges import CONTAINS
from app.models.nodes import App, Skill
from app.utils.time import utc_now_iso

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_app(name: str, workspace_id: str = "ws_test") -> App:
    now = utc_now_iso()
    return await App.create(
        name=name,
        name_fold=name.casefold(),
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )


def _known_mcp_tool() -> str:
    """Return one tool name guaranteed to be in the live catalogue.

    Uses the manifest-driven ``build_tool_catalogue`` (the single tool-list
    source of truth) so the test stays in lockstep with the dispatchable
    surface skill registration validates against.
    """
    from app.agentive.tooling.catalogue import build_tool_catalogue

    catalogue = build_tool_catalogue()
    assert catalogue, "tool catalogue is unexpectedly empty"
    return catalogue[0]["name"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_declarative_skill_registered():
    app = await _make_app("App A")
    spec = {
        "key": "summarize_thread",
        "name": "Summarize Thread",
        "description": "Summarize the current chat thread.",
        "kind": "declarative",
        "prompt_template_ref": "prompts/summarize.md",
        "tools_required": [],
        "parameters_schema": {"type": "object", "properties": {}},
    }
    skill = await register_skill(
        app_id=app.id, workspace_id=app.workspace_id, skill_spec=spec
    )
    assert isinstance(skill, Skill)
    assert skill.app_id == app.id
    assert skill.workspace_id == app.workspace_id
    assert skill.key == "summarize_thread"
    assert skill.kind == "declarative"
    assert skill.prompt_template_ref == "prompts/summarize.md"
    assert skill.private is False
    assert skill.trust_tier == "untrusted"

    # CONTAINS edge wired up.
    bundled = await app.nodes(edge=[CONTAINS], node=["Skill"])
    assert any(s.id == skill.id for s in bundled)


@pytest.mark.asyncio
async def test_custom_skill_registered():
    app = await _make_app("App B")
    spec = {
        "key": "rerank_entries",
        "kind": "custom",
        # ADR-002 Phase 1: kind=custom requires trust_tier=trusted. In-repo
        # bundles are first-party and declare it explicitly.
        "trust_tier": "trusted",
        "handler_ref": "app.plugins.rerank.run",
        "tools_required": [],
    }
    skill = await register_skill(
        app_id=app.id, workspace_id=app.workspace_id, skill_spec=spec
    )
    assert skill.kind == "custom"
    assert skill.handler_ref == "app.plugins.rerank.run"


@pytest.mark.asyncio
async def test_custom_skill_handler_ref_path_traversal_rejected():
    """T-10-04-04 mitigation — handler_ref must be a clean dotted path."""
    app = await _make_app("App TraversalGuard")
    bad_refs = [
        "../etc/passwd",
        "/absolute/path",
        "module;rm -rf",
        "mod with spaces",
        "",  # empty
    ]
    for bad in bad_refs:
        with pytest.raises(SkillRegistrationError):
            await register_skill(
                app_id=app.id,
                workspace_id=app.workspace_id,
                skill_spec={
                    "key": f"k_{abs(hash(bad)) % 1000}",
                    "kind": "custom",
                    "handler_ref": bad,
                },
            )


@pytest.mark.asyncio
async def test_unknown_tool_in_tools_required_rejected():
    app = await _make_app("App C")
    spec = {
        "key": "broken",
        "kind": "declarative",
        "prompt_template_ref": "prompts/broken.md",
        "tools_required": ["integral_no_such_tool"],
    }
    with pytest.raises(InvalidToolReferenceError) as excinfo:
        await register_skill(
            app_id=app.id, workspace_id=app.workspace_id, skill_spec=spec
        )
    # Error should expose the unknown name in the details.
    err = excinfo.value
    details = getattr(err, "details", {}) or {}
    assert "integral_no_such_tool" in (details.get("unknown_tools") or [])


@pytest.mark.asyncio
async def test_known_tool_in_tools_required_accepted():
    app = await _make_app("App C2")
    spec = {
        "key": "ok",
        "kind": "declarative",
        "prompt_template_ref": "prompts/ok.md",
        "tools_required": [_known_mcp_tool()],
    }
    skill = await register_skill(
        app_id=app.id, workspace_id=app.workspace_id, skill_spec=spec
    )
    assert skill.tools_required


@pytest.mark.asyncio
async def test_skill_re_registration_idempotent():
    app = await _make_app("App D")
    spec = {
        "key": "same_key",
        "kind": "declarative",
        "prompt_template_ref": "prompts/v1.md",
        "description": "v1",
    }
    first = await register_skill(
        app_id=app.id, workspace_id=app.workspace_id, skill_spec=spec
    )
    spec["description"] = "v2"
    spec["prompt_template_ref"] = "prompts/v2.md"
    second = await register_skill(
        app_id=app.id, workspace_id=app.workspace_id, skill_spec=spec
    )
    assert second.id == first.id  # same row updated
    assert second.description == "v2"
    assert second.prompt_template_ref == "prompts/v2.md"

    # Only one Skill bundled under this App.
    bundled = await app.nodes(edge=[CONTAINS], node=["Skill"])
    bundled_skill_keys = [s.key for s in bundled if isinstance(s, Skill)]
    assert bundled_skill_keys.count("same_key") == 1


@pytest.mark.asyncio
async def test_unregister_skills_for_app_removes_all():
    app_a = await _make_app("App A — un")
    app_b = await _make_app("App B — un")
    for key in ("a1", "a2", "a3"):
        await register_skill(
            app_id=app_a.id,
            workspace_id=app_a.workspace_id,
            skill_spec={
                "key": key,
                "kind": "declarative",
                "prompt_template_ref": f"prompts/{key}.md",
            },
        )
    for key in ("b1", "b2"):
        await register_skill(
            app_id=app_b.id,
            workspace_id=app_b.workspace_id,
            skill_spec={
                "key": key,
                "kind": "declarative",
                "prompt_template_ref": f"prompts/{key}.md",
            },
        )

    count = await unregister_skills_for_app(app_a.id)
    assert count == 3
    a_after = await app_a.nodes(edge=[CONTAINS], node=["Skill"])
    assert len([s for s in a_after if isinstance(s, Skill)]) == 0
    b_after = await app_b.nodes(edge=[CONTAINS], node=["Skill"])
    assert len([s for s in b_after if isinstance(s, Skill)]) == 2


@pytest.mark.asyncio
async def test_private_skill_scope():
    """Resolver-time private flag — Architectural Decision 5.

    App A and App B each register two skills, one private. An agent bound to
    App A may resolve A's private skill but not B's. An agent bound to App B
    may resolve B's private skill but not A's. Both agents see both non-private
    skills.
    """
    workspace_id = "ws_priv"
    app_a = await _make_app("App A — priv", workspace_id=workspace_id)
    app_b = await _make_app("App B — priv", workspace_id=workspace_id)

    await register_skill(
        app_id=app_a.id,
        workspace_id=workspace_id,
        skill_spec={
            "key": "a_pub",
            "kind": "declarative",
            "prompt_template_ref": "p/a_pub.md",
        },
    )
    await register_skill(
        app_id=app_a.id,
        workspace_id=workspace_id,
        skill_spec={
            "key": "a_priv",
            "kind": "declarative",
            "prompt_template_ref": "p/a_priv.md",
            "private": True,
        },
    )
    await register_skill(
        app_id=app_b.id,
        workspace_id=workspace_id,
        skill_spec={
            "key": "b_pub",
            "kind": "declarative",
            "prompt_template_ref": "p/b_pub.md",
        },
    )
    await register_skill(
        app_id=app_b.id,
        workspace_id=workspace_id,
        skill_spec={
            "key": "b_priv",
            "kind": "declarative",
            "prompt_template_ref": "p/b_priv.md",
            "private": True,
        },
    )

    now = utc_now_iso()
    agent_a = await AgentConfig.create(
        user_id="user_a",
        scope="personal",
        app_id=app_a.id,
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )
    agent_b = await AgentConfig.create(
        user_id="user_b",
        scope="personal",
        app_id=app_b.id,
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )

    a_view = await get_callable_skills(agent_a.id, workspace_id)
    a_keys = {s.key for s in a_view}
    assert "a_pub" in a_keys
    assert "a_priv" in a_keys  # same-App private — allowed
    assert "b_pub" in a_keys
    assert "b_priv" not in a_keys  # cross-App private — DENIED

    b_view = await get_callable_skills(agent_b.id, workspace_id)
    b_keys = {s.key for s in b_view}
    assert "a_pub" in b_keys
    assert "a_priv" not in b_keys  # cross-App private — DENIED
    assert "b_pub" in b_keys
    assert "b_priv" in b_keys  # same-App private — allowed


@pytest.mark.asyncio
async def test_get_callable_skills_agent_without_app_id_sees_only_public():
    """Agent with no app_id (legacy / non-App-bundled) sees only public skills."""
    workspace_id = "ws_legacy"
    app = await _make_app("App Legacy", workspace_id=workspace_id)
    await register_skill(
        app_id=app.id,
        workspace_id=workspace_id,
        skill_spec={
            "key": "pub_x",
            "kind": "declarative",
            "prompt_template_ref": "p/pub.md",
        },
    )
    await register_skill(
        app_id=app.id,
        workspace_id=workspace_id,
        skill_spec={
            "key": "priv_x",
            "kind": "declarative",
            "prompt_template_ref": "p/priv.md",
            "private": True,
        },
    )

    now = utc_now_iso()
    legacy_agent = await AgentConfig.create(
        user_id="legacy_user",
        scope="personal",
        # app_id deliberately unset — legacy AgentConfig.
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )
    view = await get_callable_skills(legacy_agent.id, workspace_id)
    keys = {s.key for s in view}
    assert "pub_x" in keys
    assert "priv_x" not in keys


@pytest.mark.asyncio
async def test_get_callable_skills_private_gate_holds_for_workspace_origin_app_scoped():
    """The private gate in get_callable_skills was only ever exercised for
    origin='bundle' skills. This proves it also holds for an origin='workspace'
    skill that's app-scoped via the new create_workspace_skill(app_id=...) path
    — since get_callable_skills walks App CONTAINS Skill regardless of origin.
    """
    from app.agentive.services.agent_skills import create_workspace_skill
    from app.models.nodes import Workspace

    # create_workspace_skill validates the workspace exists (unlike
    # register_skill, which the other tests in this file exercise), so this
    # test needs a real Workspace node rather than a bare string id.
    ws = await Workspace.create(name="WS Mixed Origin", kind="personal")
    workspace_id = ws.id
    app_a = await _make_app("Mixed App A", workspace_id=workspace_id)
    app_b = await _make_app("Mixed App B", workspace_id=workspace_id)

    await create_workspace_skill(
        workspace_id=workspace_id,
        user_id="user_a",
        key="a_scoped_priv",
        name="A Scoped Private",
        description="Private to App A.",
        body_override="# SOP",
        tools_required=[],
        app_id=app_a.id,
    )

    now = utc_now_iso()
    agent_a = await AgentConfig.create(
        user_id="user_a",
        scope="personal",
        app_id=app_a.id,
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )
    agent_b = await AgentConfig.create(
        user_id="user_b",
        scope="personal",
        app_id=app_b.id,
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )

    a_view = await get_callable_skills(agent_a.id, workspace_id)
    assert "a_scoped_priv" in {s.key for s in a_view}

    b_view = await get_callable_skills(agent_b.id, workspace_id)
    assert "a_scoped_priv" not in {s.key for s in b_view}


@pytest.mark.asyncio
async def test_external_apis_captured_not_enforced():
    """Open Question 5 deferred — external_apis persisted, no runtime gate."""
    app = await _make_app("App ExternalAPIs")
    spec = {
        "key": "external_caller",
        "kind": "custom",
        "trust_tier": "trusted",  # ADR-002 Phase 1
        "handler_ref": "app.plugins.external.run",
        "external_apis": ["api.example.com", "hooks.slack.com"],
    }
    skill = await register_skill(
        app_id=app.id, workspace_id=app.workspace_id, skill_spec=spec
    )
    assert skill.external_apis == ["api.example.com", "hooks.slack.com"]


@pytest.mark.asyncio
async def test_get_skill_by_key_round_trip():
    app = await _make_app("App K")
    spec = {
        "key": "lookup_me",
        "kind": "declarative",
        "prompt_template_ref": "p/lookup.md",
    }
    skill = await register_skill(
        app_id=app.id, workspace_id=app.workspace_id, skill_spec=spec
    )
    found = await get_skill_by_key(app_id=app.id, key="lookup_me")
    assert found is not None
    assert found.id == skill.id
    missing = await get_skill_by_key(app_id=app.id, key="nope")
    assert missing is None


# ---------------------------------------------------------------------------
# Compile-time public-catalog regression smoke (mirror of 10-03 coverage)
# ---------------------------------------------------------------------------


def test_custom_skill_public_catalog_compile_rejected():
    """Mirror of the 10-03 test — confirms the compile-time gate still trips.

    Plan 10-04 adds the merge-time mirror; the compile-time check (already
    shipped in 10-03) MUST continue to reject the same manifest shape.
    """
    from app.services.operational_model_runtime import compile_canonical_manifest

    raw = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {"slug": "t", "name": "T", "version": "1.0.0"},
        "app": {
            "tracks": [],
            "skills": [
                {
                    "key": "rejected_custom",
                    "kind": "custom",
                    "handler_ref": "x.y",
                }
            ],
        },
    }
    with pytest.raises(OperationalModelValidationError):
        compile_canonical_manifest(manifest=raw, is_public_catalog=True)


# ---------------------------------------------------------------------------
# agent_tools.resolve_callable_units integration smoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_tools_resolve_callable_units_merges_skills():
    """resolve_callable_units returns BOTH the live MCP catalogue keys AND
    App-bundled skills filtered by the resolver-time private gate."""
    from app.agentive.services.agent_tools import resolve_callable_units

    workspace_id = "ws_merge"
    app = await _make_app("App Merge", workspace_id=workspace_id)
    await register_skill(
        app_id=app.id,
        workspace_id=workspace_id,
        skill_spec={
            "key": "merge_pub",
            "kind": "declarative",
            "prompt_template_ref": "p/merge.md",
        },
    )

    now = utc_now_iso()
    agent = await AgentConfig.create(
        user_id="u_merge",
        scope="personal",
        app_id=app.id,
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )
    result = await resolve_callable_units(agent.id, workspace_id)
    assert "mcp_tools" in result and isinstance(result["mcp_tools"], list)
    assert any(t.startswith("integral_") for t in result["mcp_tools"])
    assert "app_bundled_skills" in result
    skill_keys = {s["key"] for s in result["app_bundled_skills"]}
    assert "merge_pub" in skill_keys


@pytest.mark.asyncio
async def test_custom_skill_requires_trusted_tier():
    """ADR-002 Phase 1: an untrusted bundle may not ship executable code.

    ``kind=custom`` carries a ``handler_ref`` that the runtime would import and
    execute, so it is refused below ``trust_tier=trusted``. Guards the gate
    itself, which previously had no test.
    """
    from app.exceptions import SkillRegistrationError

    app = await _make_app("App UntrustedCustom")
    spec = {
        "key": "untrusted_code",
        "kind": "custom",
        "trust_tier": "untrusted",
        "handler_ref": "app.plugins.evil.run",
    }
    with pytest.raises(SkillRegistrationError) as ei:
        await register_skill(
            app_id=app.id, workspace_id=app.workspace_id, skill_spec=spec
        )
    assert "trust_tier" in str(ei.value)


@pytest.mark.asyncio
async def test_custom_skill_defaults_to_untrusted_and_is_refused():
    """Omitting trust_tier must not be a way past the gate."""
    from app.exceptions import SkillRegistrationError

    app = await _make_app("App DefaultTier")
    spec = {
        "key": "default_tier_code",
        "kind": "custom",
        "handler_ref": "app.plugins.other.run",
    }
    with pytest.raises(SkillRegistrationError):
        await register_skill(
            app_id=app.id, workspace_id=app.workspace_id, skill_spec=spec
        )

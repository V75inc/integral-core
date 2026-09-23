"""App-bundled Agent registration tests — Phase 10 Plan 10-04 (APP-AGENTS-01).

Covers:
- register_app_agent persists AgentConfig.app_id + persona + skills bind.
- Unknown skill reference rejected with AgentRegistrationError.
- Schedule registration: scheduled_runs[].status = "scheduled" when the
  native scheduler is available; "manual" when unavailable (graceful
  degradation per app_bundles_v1.md §6.4 — install does NOT fail).
- staging defaults to "required" per §6.5.
- unregister_app_agents_for_app removes only the targeted App's agents.
- Merge-time custom-skill public-catalog rejection (Architectural Decision 6).
- Custom-skill merge accepted under trusted-partner publisher_tier.
"""

from __future__ import annotations

import pytest

from app.agentive.nodes import AgentConfig
from app.agentive.services import uplink_registry as uplink_module
from app.agentive.services.skill_registry import register_skill
from app.agentive.services.uplink_registry import (
    register_app_agent,
    unregister_app_agents_for_app,
)
from app.exceptions import (
    AgentRegistrationError,
    CustomSkillPublicCatalogRejectedError,
)
from app.models.nodes import App
from app.services.operational_model_merge import (
    merge_library_manifest_into_operational_model,
)
from app.utils.time import utc_now_iso

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_app(name: str, workspace_id: str = "ws_agents") -> App:
    now = utc_now_iso()
    return await App.create(
        name=name,
        name_fold=name.casefold(),
        workspace_id=workspace_id,
        created_at=now,
        updated_at=now,
    )


class _FakeLibraryCP:
    """Minimal in-memory OperationalModel shim — matches the merge protocol."""

    def __init__(self, manifest, cp_id="lib_cp_test"):
        self.manifest = manifest
        self.id = cp_id
        self.updated_at = ""
        self.library_package = True

    async def save(self):  # pragma: no cover — never called on library
        return None


class _FakeTargetCP:
    def __init__(self, manifest, cp_id="target_cp_test"):
        self.manifest = manifest
        self.id = cp_id
        self.updated_at = ""
        self.library_package = False

    async def save(self):
        return None


# ---------------------------------------------------------------------------
# register_app_agent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_registered_with_persona():
    app = await _make_app("App AgentA")
    # First register a skill so the agent can reference it.
    await register_skill(
        app_id=app.id,
        workspace_id=app.workspace_id,
        skill_spec={
            "key": "summarize",
            "kind": "declarative",
            "prompt_template_ref": "prompts/sum.md",
        },
    )
    cfg = await register_app_agent(
        app_id=app.id,
        workspace_id=app.workspace_id,
        agent_spec={
            "key": "the_assistant",
            "name": "The Assistant",
            "description": "Bundled assistant.",
            "persona": "You are a helpful assistant.",
            "scope": "app",
            "skills": ["summarize"],
            "capabilities": [],
        },
    )
    assert cfg is not None
    assert isinstance(cfg, AgentConfig)
    assert cfg.app_id == app.id
    assert cfg.workspace_id == app.workspace_id
    assert cfg.persona == "You are a helpful assistant."
    # Skills + raw scope round-trip via preferences.
    prefs = cfg.preferences or {}
    assert prefs.get("agent_key") == "the_assistant"
    assert prefs.get("app_scope") == "app"
    assert "summarize" in (prefs.get("skills") or [])


@pytest.mark.asyncio
async def test_agent_with_unknown_skill_reference_rejected():
    app = await _make_app("App AgentB")
    with pytest.raises(AgentRegistrationError) as excinfo:
        await register_app_agent(
            app_id=app.id,
            workspace_id=app.workspace_id,
            agent_spec={
                "key": "bad_agent",
                "persona": "x",
                "skills": ["nonexistent_skill"],
            },
        )
    details = getattr(excinfo.value, "details", {}) or {}
    assert details.get("unknown_skill_key") == "nonexistent_skill"


@pytest.mark.asyncio
async def test_schedule_registered_when_scheduler_available(monkeypatch):
    """When scheduler is up, schedule entries persist with status='scheduled'."""
    monkeypatch.setattr(uplink_module, "_scheduler_available", lambda: True)
    # A bundle schedule materializes as a RoutineTask, which must belong to a
    # principal — so the App needs a resolvable owner. Without one the
    # registration correctly degrades to status="manual" rather than creating
    # an ownerless routine.
    from app.models.nodes import User

    now = utc_now_iso()
    # No ``user_id``: _resolve_installing_principal prefers User.user_id (an
    # AuthUser id) and falls back to the node id, which is what resolves here.
    owner = await User.create(
        display_name="Schedule Owner",
        created_at=now,
        updated_at=now,
    )
    app = await _make_app("App AgentSched1")
    app.owner_user_id = owner.id
    await app.save()
    cfg = await register_app_agent(
        app_id=app.id,
        workspace_id=app.workspace_id,
        agent_spec={
            "key": "scheduled_agent",
            "persona": "p",
            "default_schedules": [
                {"cron": "0 9 * * *", "description": "Daily briefing"},
            ],
        },
    )
    assert len(cfg.scheduled_runs) == 1
    assert cfg.scheduled_runs[0]["status"] == "scheduled"
    assert cfg.scheduled_runs[0]["cron"] == "0 9 * * *"


@pytest.mark.asyncio
async def test_schedule_degradation_when_scheduler_unavailable(monkeypatch, caplog):
    """Scheduler down → status='manual', warning logged, registration succeeds."""
    monkeypatch.setattr(uplink_module, "_scheduler_available", lambda: False)
    app = await _make_app("App AgentSched2")
    with caplog.at_level("WARNING"):
        cfg = await register_app_agent(
            app_id=app.id,
            workspace_id=app.workspace_id,
            agent_spec={
                "key": "manual_agent",
                "persona": "p",
                "default_schedules": [
                    {"cron": "*/30 * * * *", "description": "Every 30m"},
                ],
            },
        )
    assert cfg is not None  # install did NOT fail
    assert len(cfg.scheduled_runs) == 1
    assert cfg.scheduled_runs[0]["status"] == "manual"
    # Warning was emitted.
    matching = [r for r in caplog.records if "scheduler unavailable" in r.getMessage()]
    assert matching, "expected degradation warning in log"


@pytest.mark.asyncio
async def test_staging_defaults_to_required():
    """Per app_bundles_v1.md §6.5: v1 default staging mode is 'required'."""
    app = await _make_app("App AgentStaging")
    cfg = await register_app_agent(
        app_id=app.id,
        workspace_id=app.workspace_id,
        agent_spec={"key": "stg", "persona": "p"},
    )
    assert cfg.staging == "required"


@pytest.mark.asyncio
async def test_staging_override_honored():
    app = await _make_app("App AgentStagingOpt")
    cfg = await register_app_agent(
        app_id=app.id,
        workspace_id=app.workspace_id,
        agent_spec={"key": "stg2", "persona": "p", "staging": "optional"},
    )
    assert cfg.staging == "optional"


@pytest.mark.asyncio
async def test_scope_workspace_maps_to_org_facing():
    """scope: 'workspace' → AgentConfig.scope = 'org_facing'."""
    app = await _make_app("App AgentScopeWS")
    cfg = await register_app_agent(
        app_id=app.id,
        workspace_id=app.workspace_id,
        agent_spec={"key": "ws_agent", "persona": "p", "scope": "workspace"},
    )
    assert cfg.scope == "org_facing"
    assert (cfg.preferences or {}).get("app_scope") == "workspace"


@pytest.mark.asyncio
async def test_scope_app_maps_to_personal():
    """scope: 'app' (default) → AgentConfig.scope = 'personal'."""
    app = await _make_app("App AgentScopeApp")
    cfg = await register_app_agent(
        app_id=app.id,
        workspace_id=app.workspace_id,
        agent_spec={"key": "app_agent", "persona": "p", "scope": "app"},
    )
    assert cfg.scope == "personal"
    assert (cfg.preferences or {}).get("app_scope") == "app"


@pytest.mark.asyncio
async def test_unregister_app_agents_removes_all():
    app_a = await _make_app("App MultiA")
    app_b = await _make_app("App MultiB")
    await register_app_agent(
        app_id=app_a.id,
        workspace_id=app_a.workspace_id,
        agent_spec={"key": "a1", "persona": "p"},
    )
    await register_app_agent(
        app_id=app_a.id,
        workspace_id=app_a.workspace_id,
        agent_spec={"key": "a2", "persona": "p"},
    )
    await register_app_agent(
        app_id=app_b.id,
        workspace_id=app_b.workspace_id,
        agent_spec={"key": "b1", "persona": "p"},
    )

    count = await unregister_app_agents_for_app(app_a.id)
    assert count == 2
    remaining_a = await AgentConfig.find({"app_id": app_a.id})
    assert len(remaining_a) == 0
    remaining_b = await AgentConfig.find({"app_id": app_b.id})
    assert len(remaining_b) == 1


# ---------------------------------------------------------------------------
# Merge-time custom-skill public-catalog rejection (Architectural Decision 6 mirror)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_skill_in_public_catalog_merge_rejected():
    """Library carrying publisher_tier='public_catalog' + kind:custom skill
    is rejected by the merge as well as the compile path."""
    lib_manifest = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {
            "slug": "t",
            "name": "T",
            "version": "1.0.0",
            "publisher_tier": "public_catalog",
        },
        "app": {
            "tracks": [],
            "skills": [
                {
                    "key": "evil_custom",
                    "kind": "custom",
                    "handler_ref": "x.y.z",
                }
            ],
        },
    }
    target_manifest = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "app": {"tracks": []},
    }
    library_cp = _FakeLibraryCP(lib_manifest)
    target_cp = _FakeTargetCP(target_manifest)
    with pytest.raises(CustomSkillPublicCatalogRejectedError) as excinfo:
        await merge_library_manifest_into_operational_model(
            library_cp,
            target_cp,
            track=None,
            for_space=True,
            _skip_dependencies=True,
        )
    details = getattr(excinfo.value, "details", {}) or {}
    assert "evil_custom" in (details.get("violating_skill_keys") or [])
    assert details.get("publisher_tier") == "public_catalog"


@pytest.mark.asyncio
async def test_custom_skill_in_trusted_partner_merge_accepted():
    """Same manifest under trusted-partner tier MUST merge cleanly."""
    lib_manifest = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {
            "slug": "t",
            "name": "T",
            "version": "1.0.0",
            "publisher_tier": "trusted_partner",
        },
        "app": {
            "tracks": [],
            "skills": [
                {
                    "key": "legit_custom",
                    "kind": "custom",
                    "handler_ref": "x.y.z",
                }
            ],
        },
    }
    target_manifest = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "app": {"tracks": []},
    }
    library_cp = _FakeLibraryCP(lib_manifest)
    target_cp = _FakeTargetCP(target_manifest)
    # Should not raise.
    await merge_library_manifest_into_operational_model(
        library_cp,
        target_cp,
        track=None,
        for_space=True,
        _skip_dependencies=True,
    )
    # The merged manifest carries the skill through.
    merged_skills = (target_cp.manifest.get("app") or {}).get("skills") or []
    assert any(s.get("key") == "legit_custom" for s in merged_skills)


@pytest.mark.asyncio
async def test_no_publisher_tier_merge_accepted():
    """Library with NO publisher_tier (back-compat) must NOT be rejected."""
    lib_manifest = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {"slug": "t", "name": "T", "version": "1.0.0"},
        "app": {
            "tracks": [],
            "skills": [
                {
                    "key": "untiered_custom",
                    "kind": "custom",
                    "handler_ref": "x.y.z",
                }
            ],
        },
    }
    target_manifest = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "app": {"tracks": []},
    }
    library_cp = _FakeLibraryCP(lib_manifest)
    target_cp = _FakeTargetCP(target_manifest)
    await merge_library_manifest_into_operational_model(
        library_cp,
        target_cp,
        track=None,
        for_space=True,
        _skip_dependencies=True,
    )
    merged_skills = (target_cp.manifest.get("app") or {}).get("skills") or []
    assert any(s.get("key") == "untiered_custom" for s in merged_skills)

"""Content Factory — end-to-end install / lifecycle / cascade integration test.

Phase 10 Plan 10-07 (CONTENT-FACTORY-SEED-01).

This file is the Phase 10 closure stress test. Installing the Content Factory
App via Plan 10-05's lifecycle service exercises every v2 manifest feature
end-to-end:

  - Plan 10-03 compile pipeline (v2 schema + all sections)
  - Plan 10-04 skill + agent registration
  - Plan 10-05 atomic install transaction + settings_schema pause + seeds +
    single-emission D-05
  - Plan 10-06 cross-track relation field surface (within-App, but using
    the same compile path as cross-App)
  - I-APP-01..05 invariants (manifest v2 only, atomicity, cross-App perms,
    requires_apps, same-workspace scope)

Any latent bug in Plans 10-03 through 10-06 surfaces here.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.exceptions import (
    AppLifecycleStateError,
    ContentProfileValidationError,
)
from app.models.edges import CONTAINS, REFERENCES
from app.models.nodes import App, ContentProfile, Entry, Track, Workspace
from app.services.app_lifecycle import (
    _plant_seeds,
    finalize_install,
    install_app,
    uninstall_app,
)
from app.services.change_event_logger import get_change_event_logger
from app.services.content_profile_loader import load_library_profiles
from app.services.content_profile_runtime import (
    compile_canonical_manifest,
)
from app.utils.time import utc_now_iso
from tests.fixtures.workspaces import make_org_workspace

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _content_factory_spec():
    for spec in load_library_profiles():
        if spec.slug == "content-factory":
            return spec
    raise RuntimeError("content-factory bundle missing from library catalog")


def _content_factory_manifest() -> Dict[str, Any]:
    return dict(_content_factory_spec().manifest)


def _content_factory_version() -> str:
    return _content_factory_spec().version


async def _make_workspace(name: str = "ws-content-factory") -> Workspace:
    return await make_org_workspace(name)


async def _make_library_cp() -> ContentProfile:
    """Create a library-package ContentProfile carrying the Content Factory manifest."""
    spec = _content_factory_spec()
    manifest = _content_factory_manifest()
    now = utc_now_iso()
    return await ContentProfile.create(
        name=manifest["package"]["name"],
        scope="app",
        manifest=manifest,
        library_package=True,
        version=spec.version,
        created_at=now,
        updated_at=now,
    )


async def _track_by_key(app_node: App, key: str) -> Track:
    """Find the Track materialized for a given manifest track key."""
    tracks = await app_node.nodes(edge=[CONTAINS], node=["Track"])
    for tr in tracks:
        if getattr(tr, "template_id", "") == key:
            return tr
        if tr.title_fold == key.casefold():
            return tr
    raise AssertionError(
        f"Track {key!r} not found under App {app_node.id} (tracks: "
        f"{[(t.title, getattr(t, 'template_id', None)) for t in tracks]})"
    )


async def _count_change_events(*, scope: str, action: str) -> int:
    """Count ChangeEvents at the given scope whose event_code matches action."""
    rows = await get_change_event_logger().find_all(scope=scope)
    return sum(1 for r in rows if getattr(r, "event_code", "") == action)


# ---------------------------------------------------------------------------
# Manifest compile + library row
# ---------------------------------------------------------------------------


def test_manifest_compiles_with_all_v2_sections():
    """Defensive sanity — Content Factory manifest compiles + carries every v2 section."""
    manifest = _content_factory_manifest()
    canonical = compile_canonical_manifest(manifest=manifest)
    app_block = canonical["app"]
    assert len(app_block["tracks"]) == 4
    track_keys = {t["key"] for t in app_block["tracks"]}
    assert track_keys == {
        "source_material",
        "content_pipeline",
        "agent_runs",
        "performance",
    }
    assert len(app_block["relations"]) == 3
    assert len(app_block["skills"]) == 2
    assert {s["key"] for s in app_block["skills"]} == {
        "carousel_drafter",
        "performance_reviewer",
    }
    assert len(app_block["agents"]) == 1
    assert app_block["agents"][0]["key"] == "drafter"
    assert app_block["agents"][0]["skills"] == ["carousel_drafter"]
    assert app_block["agents"][0]["scope"] == "app"
    assert app_block["agents"][0]["staging"] == "required"
    assert set(app_block["settings_schema"]["properties"].keys()) == {
        "publish_cadence",
        "target_platforms",
        "brand_voice_source_ids",
    }
    assert app_block["settings_schema"]["required"] == [
        "publish_cadence",
        "target_platforms",
    ]
    assert len(app_block["seeds"]) == 1
    assert len(app_block["seeds"][0]["entries"]) == 2


# ---------------------------------------------------------------------------
# End-to-end install — pauses at awaiting_settings, finalized via token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_install_awaiting_settings_then_finalize():
    """Install Content Factory → pause at awaiting_settings → finalize → active."""
    ws = await _make_workspace()
    lib = await _make_library_cp()

    # Step 1: install — manifest declares settings_schema, so we pause.
    first = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert first["status"] == "awaiting_settings"
    assert first["install_token"]
    assert "publish_cadence" in first["settings_schema"]["properties"]

    # Step 2: finalize with valid settings.
    second = await finalize_install(
        app_id=first["app_id"],
        install_token=first["install_token"],
        settings={
            "publish_cadence": "weekly",
            "target_platforms": ["instagram"],
            "brand_voice_source_ids": [],
        },
        actor_id="u_1",
    )
    assert second["status"] == "active"

    # Verify the materialized graph.
    app = await App.get(first["app_id"])
    assert app is not None
    assert app.lifecycle_state == "active"
    assert app.installed_at
    assert app.version == _content_factory_version()
    assert app.installed_from_library_id == lib.id
    assert app.settings["publish_cadence"] == "weekly"
    assert app.settings["target_platforms"] == ["instagram"]
    assert app.settings["brand_voice_source_ids"] == []

    # 4 tracks materialized.
    tracks = await app.nodes(edge=[CONTAINS], node=["Track"])
    track_keys = {getattr(t, "template_id", None) for t in tracks}
    assert track_keys == {
        "source_material",
        "content_pipeline",
        "agent_runs",
        "performance",
    }

    # 2 skills registered.
    from app.models.nodes import Skill

    skills = await Skill.find({"app_id": app.id})
    assert {s.key for s in skills} == {"carousel_drafter", "performance_reviewer"}
    # Skills carry the prompt_template_ref from the manifest.
    by_key = {s.key: s for s in skills}
    assert by_key["carousel_drafter"].prompt_template_ref == (
        "skills/carousel_drafter/SKILL.md"
    )
    assert by_key["performance_reviewer"].prompt_template_ref == (
        "skills/performance_reviewer/SKILL.md"
    )

    # 1 AgentConfig registered.
    from app.agentive.nodes import AgentConfig

    agents = await AgentConfig.find({"app_id": app.id})
    assert len(agents) == 1
    drafter = agents[0]
    assert drafter.preferences.get("agent_key") == "drafter"
    assert drafter.preferences.get("skills") == ["carousel_drafter"]
    # Native scheduler unavailable in test mode → degrades to manual per
    # Plan 10-04 §6.4. The schedule is still present (verifies registration
    # round-trips the manifest's default_schedules).
    assert len(drafter.scheduled_runs) == 1
    assert drafter.scheduled_runs[0]["cron"] == "0 9 * * 1"
    assert drafter.scheduled_runs[0]["status"] in ("scheduled", "manual")

    # 2 seeds planted on source_material track.
    source_track = await _track_by_key(app, "source_material")
    seeded = await Entry.find({"track_id": source_track.id})
    assert len(seeded) == 2
    titles = {e.title for e in seeded}

    # Note: title sanitization may normalize em-dash (—) to hyphen-minus (-).
    # Match on a normalized form so the test is robust to either convention.
    def _norm(s: str) -> str:
        return s.replace("—", "-").replace("–", "-")

    assert {_norm(t) for t in titles} == {
        _norm("Brand Voice — Starter Template"),
        "Welcome to your Content Factory",
    }
    # Idempotency markers set on custom_fields.
    for e in seeded:
        sid = (e.custom_fields or {}).get("__seed_id", "")
        assert sid.startswith(f"seed:{app.id}:source_material:")

    # app.installed ChangeEvent emitted EXACTLY ONCE (D-05).
    n_installed = await _count_change_events(
        scope=f"app:{app.id}", action="app.installed"
    )
    assert (
        n_installed == 1
    ), f"expected exactly 1 app.installed ChangeEvent, got {n_installed}"


@pytest.mark.asyncio
async def test_finalize_with_empty_settings_applies_schema_defaults():
    """June 29 QA #1 — the ``seed`` command finalizes an awaiting-settings app
    with ``settings={}``. Content Factory's required settings ALL declare
    defaults (publish_cadence=weekly, target_platforms=[instagram]), so the
    install MUST succeed by materializing those defaults instead of failing
    'Settings failed schema validation: publish_cadence is a required property'.
    """
    ws = await _make_workspace("ws-cf-empty-settings")
    lib = await _make_library_cp()

    first = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert first["status"] == "awaiting_settings"

    # Finalize with an EMPTY dict — exactly what seed_data's
    # _install_library_bundle sends (settings or {}).
    second = await finalize_install(
        app_id=first["app_id"],
        install_token=first["install_token"],
        settings={},
        actor_id="u_1",
    )
    assert second["status"] == "active", second

    app = await App.get(first["app_id"])
    assert app is not None
    assert app.lifecycle_state == "active"
    # Schema defaults were materialized (not left blank / rejected).
    assert app.settings["publish_cadence"] == "weekly"
    assert app.settings["target_platforms"] == ["instagram"]
    assert app.settings["brand_voice_source_ids"] == []


# ---------------------------------------------------------------------------
# Install with pre-supplied settings — no pause (single-shot active)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_with_pre_supplied_settings_skips_pause():
    """Caller pre-supplies settings → install transitions directly to active."""
    ws = await _make_workspace()
    lib = await _make_library_cp()
    result = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
        settings={
            "publish_cadence": "monthly",
            "target_platforms": ["linkedin", "twitter"],
        },
    )
    assert result["status"] == "active"
    app = await App.get(result["app_id"])
    assert app.settings["publish_cadence"] == "monthly"
    assert app.settings["target_platforms"] == ["linkedin", "twitter"]


# ---------------------------------------------------------------------------
# Install with INVALID settings — 422 (jsonschema rejection)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_with_invalid_settings_rejected():
    """publish_cadence must be in the enum; quarterly is not — install rejected."""
    ws = await _make_workspace()
    lib = await _make_library_cp()
    with pytest.raises(ContentProfileValidationError):
        await install_app(
            workspace_id=ws.id,
            library_cp_id=lib.id,
            actor_id="u_1",
            settings={
                "publish_cadence": "quarterly",
                "target_platforms": ["instagram"],
            },
        )


@pytest.mark.asyncio
async def test_install_omitting_required_setting_backfills_default():
    """target_platforms is required but declares a default — omitting it now
    backfills the default (June 29 QA #1) instead of failing validation. A
    required key with NO default still rejects (see the unit test on
    apply_schema_defaults / validate_settings_against_schema)."""
    ws = await _make_workspace()
    lib = await _make_library_cp()
    first = await install_app(workspace_id=ws.id, library_cp_id=lib.id, actor_id="u_1")
    assert first["status"] == "awaiting_settings"
    second = await finalize_install(
        app_id=first["app_id"],
        install_token=first["install_token"],
        settings={"publish_cadence": "biweekly"},  # target_platforms omitted
        actor_id="u_1",
    )
    assert second["status"] == "active"
    app = await App.get(first["app_id"])
    assert app is not None
    # Caller-supplied value preserved; omitted required key filled from default.
    assert app.settings["publish_cadence"] == "biweekly"
    assert app.settings["target_platforms"] == ["instagram"]


# ---------------------------------------------------------------------------
# Seeds — idempotent on re-plant (Plan 10-05 _seed_deterministic_id)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seeds_idempotent_on_replant():
    """Calling _plant_seeds twice on the same App plants zero new entries."""
    ws = await _make_workspace()
    lib = await _make_library_cp()
    result = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
        settings={
            "publish_cadence": "weekly",
            "target_platforms": ["instagram"],
        },
    )
    app = await App.get(result["app_id"])
    canonical = compile_canonical_manifest(manifest=_content_factory_manifest())
    # Re-invoke plant_seeds — should plant zero new entries.
    n = await _plant_seeds(app, canonical, "u_1")
    assert n == 0


# ---------------------------------------------------------------------------
# Write a content_piece referencing the seeded brand_voice source — verify
# the relation field stores the source_material id correctly. Within-App
# relation; not cross-App.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_content_piece_referencing_seeded_brand_voice():
    """Author a content_piece with source_materials pointing at the seeded brand voice."""
    ws = await _make_workspace()
    lib = await _make_library_cp()
    result = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
        settings={
            "publish_cadence": "weekly",
            "target_platforms": ["instagram"],
        },
    )
    app = await App.get(result["app_id"])

    # Find the seeded brand_voice entry (title sanitization may
    # normalize em-dash to hyphen-minus).
    source_track = await _track_by_key(app, "source_material")
    seeded = await Entry.find({"track_id": source_track.id})
    brand_voice = next(
        e for e in seeded if "Brand Voice" in e.title and "Starter" in e.title
    )

    # Write a content_piece referencing it.
    pipeline_track = await _track_by_key(app, "content_pipeline")
    now = utc_now_iso()
    piece = await Entry.create(
        title="Welcome carousel draft",
        body="Slide 1\n\n---\n\nSlide 2",
        tags=[],
        custom_fields={
            "format": "carousel",
            "platform": "instagram",
            "status": "draft",
            "source_materials": [brand_voice.id],
            "agent_run": None,
        },
        track_id=pipeline_track.id,
        author_id="u_1",
        status="active",
        created_at=now,
        updated_at=now,
    )
    await pipeline_track.connect(piece, edge=CONTAINS, added_at=now)

    # Sanity — the relation field is materialized on the entry.
    fresh = await Entry.get(piece.id)
    assert brand_voice.id in (fresh.custom_fields or {}).get("source_materials", [])


# ---------------------------------------------------------------------------
# Archive — soft uninstall (force=False, archive=True)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_preserves_tracks_and_emits_app_uninstalled():
    """Archive flips lifecycle_state to uninstalled; tracks + entries persist."""
    ws = await _make_workspace()
    lib = await _make_library_cp()
    result = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
        settings={
            "publish_cadence": "weekly",
            "target_platforms": ["instagram"],
        },
    )
    app_id = result["app_id"]
    # Snapshot the seed entries so we can assert preservation.
    app = await App.get(app_id)
    source_track = await _track_by_key(app, "source_material")
    seeded_pre = await Entry.find({"track_id": source_track.id})
    assert len(seeded_pre) == 2

    out = await uninstall_app(app_id=app_id, actor_id="u_1")
    assert out["status"] == "uninstalled"
    assert out["archived"] is True

    # App row persists with lifecycle_state="uninstalled".
    app_post = await App.get(app_id)
    assert app_post is not None
    assert app_post.lifecycle_state == "uninstalled"

    # Tracks + entries preserved (archive, not hard delete).
    tracks_post = await app_post.nodes(edge=[CONTAINS], node=["Track"])
    assert len(tracks_post) == 4
    source_track_post = await _track_by_key(app_post, "source_material")
    seeded_post = await Entry.find({"track_id": source_track_post.id})
    assert len(seeded_post) == 2

    # app.uninstalled ChangeEvent emitted EXACTLY ONCE (D-05).
    n_uninstalled = await _count_change_events(
        scope=f"app:{app_id}", action="app.uninstalled"
    )
    assert n_uninstalled == 1
    n_force = await _count_change_events(
        scope=f"app:{app_id}", action="app.force_uninstalled"
    )
    assert n_force == 0  # archive path emits uninstalled, not force_uninstalled


# ---------------------------------------------------------------------------
# Force-uninstall — hard cascade (force=True)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_uninstall_emits_force_action():
    """Force uninstall returns force_uninstalled + emits app.force_uninstalled."""
    ws = await _make_workspace()
    lib = await _make_library_cp()
    result = await install_app(
        workspace_id=ws.id,
        library_cp_id=lib.id,
        actor_id="u_1",
        settings={
            "publish_cadence": "weekly",
            "target_platforms": ["instagram"],
        },
    )
    app_id = result["app_id"]
    out = await uninstall_app(app_id=app_id, actor_id="u_1", force=True)
    assert out["status"] == "force_uninstalled"

    # app.force_uninstalled ChangeEvent emitted EXACTLY ONCE.
    n_force = await _count_change_events(
        scope=f"app:{app_id}", action="app.force_uninstalled"
    )
    assert n_force == 1
    n_uninstalled = await _count_change_events(
        scope=f"app:{app_id}", action="app.uninstalled"
    )
    assert n_uninstalled == 0  # force path emits force_uninstalled, not uninstalled


# ---------------------------------------------------------------------------
# I-CHA invariant — strict superset preserved across the Content Factory test
# ---------------------------------------------------------------------------


def test_i_cha_lifecycle_actions_in_both_literals():
    """ChangeEventAction + PolicyAction both declare app lifecycle actions.

    Content Factory exercises app.installed / app.uninstalled /
    app.force_uninstalled. Plan 10-05 added these to BOTH PolicyAction
    AND ChangeEventAction Literals in lockstep (I-CHA invariant — lockstep
    members for actions that are both policy-evaluated AND audit-logged).

    Note: the strict-superset relation does NOT hold for ALL ChangeEventAction
    members — ``approval.expired`` is an audit-only signal that is never
    policy-evaluated (caller never evaluates(action='approval.expired')).
    The invariant is narrower: app lifecycle actions appear in both Literals.
    """
    from typing import get_args

    from app.schemas.audit import ChangeEventAction
    from app.schemas.policy import PolicyAction

    ce_actions = set(get_args(ChangeEventAction))
    pol_actions = set(get_args(PolicyAction))

    lifecycle_actions = {"app.installed", "app.uninstalled", "app.force_uninstalled"}
    assert lifecycle_actions.issubset(
        ce_actions
    ), f"missing in ChangeEventAction: {lifecycle_actions - ce_actions}"
    assert lifecycle_actions.issubset(
        pol_actions
    ), f"missing in PolicyAction: {lifecycle_actions - pol_actions}"

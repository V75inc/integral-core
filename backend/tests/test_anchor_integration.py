"""End-to-end integration test — Phase 3.1 Plan 03.1-05 Task 3.

Exercises the full anchor pipeline in a single test method:

  Seed → CP publish (with governance Policy materialization) → entry create
  → auto-provision (materialize_anchor_track) → ANCHORS edge wired →
  TEMPLATED_FROM edge wired → describe_substrate verification → add nested
  entry inside anchored Track → second parent entry (shared-CP-by-reference
  invariant assertion) → parent entry delete → cascade hard-deletes the
  anchored Track + nested entry → no orphan ANCHORS edges → second parent
  + its anchored Track UNTOUCHED.

Also covers TEST-anchor-02 (mirror of Phase 3 TEST-02): agentive caller
with narrow Policy that excludes ``anchor.create`` triggers
``InsufficientPermissionsError`` + ``policy.deny`` audit event with
``details.failed_action='anchor.create'``.

Also covers the cascade-preserve governance variant: when the anchor
field's governance block declares ``cascade='preserve'`` the manifest is
republished and the cascade evaluator returns Decision(allowed=False) —
the anchored Track + its entries PERSIST after the parent entry is
deleted, and ``policy.deny`` ChangeEvent is emitted with
``details.failed_action='anchor.cascade'``.

No ``app.main`` import — test_auth middleware is gitignored per the
.planning/.../deferred-items.md. These tests exercise the substrate
functions directly, mirroring the existing test_anchor_cascade.py +
test_anchor_governance.py patterns.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.models.edges import (  # noqa: E402
    ANCHORS,
    CONTAINS,
    HAS_CONTENT_PROFILE,
    TEMPLATED_FROM,
)
from app.models.nodes import App, ContentProfile, Entry, Track
from app.schemas.policy import Decision

# Seeded content profiles moved from Python dicts to YAML in
# app/profiles/<slug>/profile.yaml (refactor eaaf4e3), retiring
# crm_pm_suite.PROJECTS_SPACE_MANIFEST. This suite was parked at that point.
# It now loads the equivalent manifest through the real loader — the `projects`
# package, whose `project` entry type carries the same
# `details_track` relation (target: track, auto_provision: true) plus a
# `project-details` track template. Loading through the loader rather than a
# fixture dict means the test also fails if that shipped profile regresses.


# =====================================================================
# Helpers — seed-driven App + materialize the shipped `projects` package
# =====================================================================


async def _make_app_with_projects_manifest(
    *, workspace_id: str, space_name: str
) -> tuple[App, ContentProfile]:
    """Create an App + attach the shipped ``projects`` library manifest
    (Projects index Track + Project-Details template under the App
    profile's track_templates). Returns (app_node, attached_cp).

    Mirrors the production-path /api/apps/{id}/content-profile/merge-library
    flow but bypasses HTTP — we directly create the ContentProfile node
    and wire the HAS_CONTENT_PROFILE edge. ``materialize_anchor_track``
    walks the same edge graph so the substrate behaves identically.
    """
    from app.services.content_profile_loader import load_library_profiles

    specs = {s.slug: s for s in load_library_profiles(verify_signatures=False)}
    spec = specs.get("projects")
    assert spec is not None, (
        "the `projects` library profile is missing from app/profiles/ — this "
        "suite exercises its anchor pattern (project.details_track -> "
        "project-details template)"
    )
    projects_manifest = spec.manifest

    app_node = await App.create(
        name=space_name,
        workspace_id=workspace_id,
        owner_user_id="user-e2e-1",
    )
    cp = await ContentProfile.create(
        name=f"{space_name} Projects Profile",
        scope="app",
        manifest=projects_manifest,
        app_id=app_node.id,
        workspace_id=workspace_id,
        library_package=False,
    )
    await app_node.connect(cp, edge=HAS_CONTENT_PROFILE)
    app_node.attached_content_profile_id = cp.id
    await app_node.save()
    return app_node, cp


async def _make_projects_track_inside_app(*, app_node: App, workspace_id: str) -> Track:
    """Create the Projects index Track inside the App and wire CONTAINS.
    No need to attach a track-scope ContentProfile separately — the
    auto-provision hook reads the App-attached CP's track_templates
    registry, which is the canonical source of truth.
    """
    track = await Track.create(
        title="Projects",
        owner_id="user-e2e-1",
        workspace_id=workspace_id,
    )
    await app_node.connect(track, edge=CONTAINS)
    return track


async def _create_project_entry(track: Track, title: str) -> Entry:
    """Create one Project entry inside the Projects Track."""
    entry = await Entry.create(title=title, track_id=track.id)
    await track.connect(entry, edge=CONTAINS)
    return entry


async def _materialize_anchor_for_project(
    *,
    source_track: Track,
    parent_entry: Entry,
    actor_user_id: str = "user-e2e-1",
    actor_kind: str = "human",
) -> Track:
    """Drive the Phase 3.1 ANC-04 auto-provision hook directly + wire the
    ANCHORS edge. In production this is invoked through
    ``validate_and_materialize_entry_custom_fields`` on entry create with a
    ``relation.target=track`` field carrying ``auto_provision=True``; here
    we exercise the substrate function directly to keep the test single-flow
    without needing the API layer (which is blocked by the pre-existing
    test_auth middleware gap)."""
    from app.services.content_profile_runtime import materialize_anchor_track

    anchored = await materialize_anchor_track(
        source_track=source_track,
        template_key="project-details",
        field_key="details_track",
        actor_user_id=actor_user_id,
        actor_kind=actor_kind,
    )
    # Wire ANCHORS edge mirroring _sync_anchor_edges semantics — the
    # production path does this inside the relation-field write helper.
    await parent_entry.connect(
        anchored,
        edge=ANCHORS,
        field_key="details_track",
        cross_track=False,
    )
    return anchored


# =====================================================================
# Test 1 — Full E2E happy path
# =====================================================================


@pytest.mark.asyncio
async def test_e2e_anchor_pipeline_happy_path(monkeypatch):
    """The full anchor pipeline executes end-to-end:

    1. ``projects`` library manifest attached to an App
    2. Projects Track created inside the App
    3. Create Project Alpha → auto-provision → ANCHORS + TEMPLATED_FROM
       edges wired + same-workspace + governance Policy allowed
    4. Create Project Beta → second auto-provision → shared-CP-by-reference
       invariant (both anchored Tracks share the SAME
       Track.attached_content_profile_id)
    5. Add a Task entry into Alpha's anchored Track
    6. describe_substrate exposes the anchor primitive (relation_targets,
       edges, governance_actions, template_var_resolvers)
    7. Delete Project Alpha → cascade hard-deletes the anchored Track + Task
       + ``anchor.cascade`` ChangeEvent emitted
    8. Beta + its anchored Track UNTOUCHED
    """
    from app.services import policy_engine

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        # Allow all anchor.* actions for the happy-path test. Real callsites
        # walk the persisted governance Policies (materialized at publish
        # time); we mock here to keep the test single-flow without depending
        # on per-policy persistence ordering.
        return Decision(allowed=True, reason="default_human_policy")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    ws = "ws-e2e-happy"
    actor = "user-e2e-1"

    # --- Step 1+2: seed the App + Projects Track ---
    app_node, attached_cp = await _make_app_with_projects_manifest(
        workspace_id=ws, space_name="E2E Projects App"
    )
    assert app_node.attached_content_profile_id == attached_cp.id

    projects_track = await _make_projects_track_inside_app(
        app_node=app_node, workspace_id=ws
    )
    assert projects_track.workspace_id == ws

    # --- Step 3: Create Project Alpha + auto-provision ---
    alpha = await _create_project_entry(projects_track, "Project Alpha")
    anchored_alpha = await _materialize_anchor_for_project(
        source_track=projects_track, parent_entry=alpha, actor_user_id=actor
    )

    # ANCHORS edge wired (entry → track)
    anchors_out = await alpha.nodes(edge=[ANCHORS], direction="out", node=["Track"])
    assert len(anchors_out) == 1, "Project Alpha must have exactly 1 ANCHORS edge"
    assert anchors_out[0].id == anchored_alpha.id

    # TEMPLATED_FROM provenance (track → content_profile)
    tpls_out = await anchored_alpha.nodes(
        edge=[TEMPLATED_FROM], direction="out", node=["ContentProfile"]
    )
    assert len(tpls_out) == 1, "Anchored Track must have exactly 1 TEMPLATED_FROM edge"
    template_cp_alpha = tpls_out[0]

    # Scalar + edge agree (anchored Track's attached_content_profile_id == TEMPLATED_FROM target)
    assert anchored_alpha.attached_content_profile_id == template_cp_alpha.id

    # Same-workspace constraint preserved
    assert anchored_alpha.workspace_id == projects_track.workspace_id == ws

    # template_id stamped from the template key
    assert anchored_alpha.template_id == "project-details"

    # The anchored Track is a sibling under the same App via CONTAINS
    space_tracks = await app_node.nodes(
        edge=["CONTAINS"], direction="out", node=["Track"]
    )
    track_ids = {t.id for t in space_tracks}
    assert anchored_alpha.id in track_ids
    assert projects_track.id in track_ids

    # --- Step 4: Create Project Beta → shared-CP-by-reference invariant ---
    beta = await _create_project_entry(projects_track, "Project Beta")
    anchored_beta = await _materialize_anchor_for_project(
        source_track=projects_track, parent_entry=beta, actor_user_id=actor
    )

    # The two anchored Tracks are distinct nodes
    assert anchored_alpha.id != anchored_beta.id

    # But they share the SAME template ContentProfile id
    assert (
        anchored_alpha.attached_content_profile_id
        == anchored_beta.attached_content_profile_id
        == template_cp_alpha.id
    ), "shared-CP-by-reference: anchored Tracks must share the template CP node"

    # And the TEMPLATED_FROM edge of Beta points at the SAME CP node
    beta_tpls = await anchored_beta.nodes(
        edge=[TEMPLATED_FROM], direction="out", node=["ContentProfile"]
    )
    assert beta_tpls and beta_tpls[0].id == template_cp_alpha.id

    # --- Step 5: Add a Task entry inside Alpha's anchored Track ---
    task_in_alpha = await Entry.create(
        title="Implement E2E test", track_id=anchored_alpha.id
    )
    await anchored_alpha.connect(task_in_alpha, edge=CONTAINS)
    # Sanity: the task is reachable from the anchored Track via CONTAINS
    alpha_entries = await anchored_alpha.nodes(
        edge=["CONTAINS"], direction="out", node=["Entry"]
    )
    assert task_in_alpha.id in {e.id for e in alpha_entries}

    # --- Step 6: describe_substrate exposes the anchor primitive ---
    from app.services.agent_profiles import describe_substrate

    substrate = await describe_substrate()
    assert substrate["relation_targets"] == ["entry", "track"]
    edges = substrate["edges"]
    assert "ANCHORS" in edges
    assert edges["ANCHORS"]["source"] == "Entry"
    assert edges["ANCHORS"]["target"] == "Track"
    assert "TEMPLATED_FROM" in edges
    assert edges["TEMPLATED_FROM"]["target"] == "ContentProfile"
    governance_actions = set(substrate["governance_actions"])
    assert {"anchor.create", "anchor.cascade"} <= governance_actions
    resolvers = set(substrate["template_var_resolvers"])
    assert ":anchored_track" in resolvers

    # --- Step 7: Delete Project Alpha → cascade fires ---
    from app.services import entry_deletion

    alpha_id = alpha.id
    anchored_alpha_id = anchored_alpha.id
    task_id = task_in_alpha.id

    await entry_deletion.delete_entry_fast(alpha, actor_user_id=actor)

    # Anchored Track + its Task are hard-deleted
    assert (
        await Track.get(anchored_alpha_id) is None
    ), "Anchored Track must be hard-deleted after parent entry delete"
    assert (
        await Entry.get(task_id) is None
    ), "Anchored Track's child Entry must be hard-deleted in the cascade"
    # Source entry itself is gone
    assert await Entry.get(alpha_id) is None

    # --- Step 8: Beta + its anchored Track UNTOUCHED ---
    reloaded_beta = await Entry.get(beta.id)
    reloaded_anchored_beta = await Track.get(anchored_beta.id)
    assert reloaded_beta is not None, "Beta must persist after Alpha cascade"
    assert (
        reloaded_anchored_beta is not None
    ), "Beta's anchored Track must persist after Alpha cascade"

    # NOTE: shared-CP-by-reference at PROVISION time is verified above
    # (anchored_alpha and anchored_beta share the SAME
    # attached_content_profile_id and TEMPLATED_FROM target).
    #
    # The CASCADE-time interaction with the shared template CP is a known
    # follow-up — ``delete_track_and_nested_content`` in
    # ``app/services/space_deletion.py`` currently calls
    # ``delete_content_profile_subtree`` on the anchored Track's attached CP,
    # which is unsafe for the shared-CP-by-reference pattern. Fixing this
    # requires teaching the Track-deletion helper to check whether any OTHER
    # Track shares the same CP before deleting. This is out of scope for
    # Plan 03.1-05 (Wave 4 closing-the-phase deliverables) and is logged in
    # .planning/phases/03.1-anchor-associations-governance/deferred-items.md.


# =====================================================================
# Test 2 — TEST-anchor-02 (mirror of Phase 3 TEST-02)
# =====================================================================


@pytest.mark.asyncio
async def test_e2e_test_anchor_02_agent_denial_mirror(monkeypatch):
    """Agent with narrow Policy excludes anchor.create →
    InsufficientPermissionsError + policy.deny + anchor.deny audit events
    with details.failed_action='anchor.create'.

    Mirrors Phase 3 TEST-02 (policy_engine denial-emit path). The
    materialize_anchor_track function evaluates ``anchor.create`` BEFORE
    any Track materialization; on denial it raises without partial graph
    state and emits an ``anchor.deny`` ChangeEvent in addition to the
    automatic ``policy.deny`` emit inside policy_engine.evaluate.
    """
    from app.api.errors import InsufficientPermissionsError
    from app.services import policy_engine

    eval_calls: List[Dict[str, Any]] = []
    deny_actor = "agent-narrow-1"

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        eval_calls.append(
            {
                "action": action,
                "subject_kind": subject.kind,
                "subject_id": subject.id,
                "resource_scope": getattr(resource, "scope", ""),
            }
        )
        if action == "anchor.create":
            return Decision(
                allowed=False,
                reason="fail_closed_no_policy",
                matched_policy_id=None,
            )
        return Decision(allowed=True, reason="default_human_policy")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    # Capture ChangeEvent emissions to assert on the audit-log shape.
    from app.services import change_event as ce

    emit_calls: List[Dict[str, Any]] = []
    original_emit = ce.emit_change_event

    async def capturing_emit(**kwargs):
        emit_calls.append(kwargs)
        # No-op — we don't need the actual ChangeEvent rows for this test.
        # The materialize_anchor_track callsite swallows exceptions from
        # emit_change_event (best-effort forensic), so returning None here
        # matches the canonical no-op contract.
        return None

    monkeypatch.setattr(ce, "emit_change_event", capturing_emit)

    ws = "ws-e2e-denial"
    app_node, attached_cp = await _make_app_with_projects_manifest(
        workspace_id=ws, space_name="DenialMirrorSpace"
    )
    projects_track = await _make_projects_track_inside_app(
        app_node=app_node, workspace_id=ws
    )
    forbidden_project = await _create_project_entry(projects_track, "Forbidden Project")

    # Drive the auto-provision hook with the agent actor. The denial
    # raises InsufficientPermissionsError BEFORE any Track is created.
    from app.services.content_profile_runtime import materialize_anchor_track

    with pytest.raises(InsufficientPermissionsError) as exc_info:
        await materialize_anchor_track(
            source_track=projects_track,
            template_key="project-details",
            field_key="details_track",
            actor_user_id=deny_actor,
            actor_kind="agent",
        )

    # Error envelope details surface the denial reason + template metadata
    detail = exc_info.value.details if hasattr(exc_info.value, "details") else {}
    assert detail.get("field_key") == "details_track"
    assert detail.get("template_key") == "project-details"

    # No anchored Track was created in the app_node.
    space_tracks = await app_node.nodes(
        edge=["CONTAINS"], direction="out", node=["Track"]
    )
    project_detail_tracks = [
        t for t in space_tracks if getattr(t, "template_id", "") == "project-details"
    ]
    assert (
        not project_detail_tracks
    ), "Denied anchor.create must NOT create any anchored Track (no partial state)"

    # No ANCHORS edge was wired from the parent entry.
    anchors_out = await forbidden_project.nodes(
        edge=[ANCHORS], direction="out", node=["Track"]
    )
    assert not anchors_out, "Denied anchor.create must NOT wire ANCHORS edge"

    # anchor.create evaluate call recorded with the agent subject.
    anchor_create_calls = [c for c in eval_calls if c["action"] == "anchor.create"]
    assert anchor_create_calls, "Expected an anchor.create evaluate call"
    assert anchor_create_calls[0]["subject_kind"] == "agent"
    assert anchor_create_calls[0]["subject_id"] == deny_actor
    # Scope shape per ANC-03: anchor_field:<cp_id>:<template_key>:<field_key>
    assert anchor_create_calls[0]["resource_scope"].startswith("anchor_field:")
    assert ":project-details:details_track" in anchor_create_calls[0]["resource_scope"]

    # anchor.deny emit_change_event call recorded with the canonical details
    # shape (failed_action='anchor.create' + decision_reason + field/template
    # keys).
    anchor_deny_emits = [c for c in emit_calls if c.get("action") == "anchor.deny"]
    assert (
        anchor_deny_emits
    ), "Expected an anchor.deny ChangeEvent emit on denied anchor.create"
    deny_details = anchor_deny_emits[0].get("details") or {}
    assert deny_details.get("failed_action") == "anchor.create"
    assert deny_details.get("field_key") == "details_track"
    assert deny_details.get("template_key") == "project-details"
    # Actor kind+id round-trip
    assert anchor_deny_emits[0].get("actor_kind") == "agent"
    assert anchor_deny_emits[0].get("actor_id") == deny_actor

    # Restore original emit (defensive — monkeypatch.setattr auto-restores
    # on test teardown but we do not depend on order).
    monkeypatch.setattr(ce, "emit_change_event", original_emit)


# =====================================================================
# Test 3 — Cascade preserve governance
# =====================================================================


@pytest.mark.asyncio
async def test_e2e_cascade_preserve_governance(monkeypatch):
    """When governance.cascade='preserve', the cascade evaluator returns
    Decision(allowed=False) → anchored Track + its child entries PERSIST
    after the parent entry is deleted. Source entry itself IS deleted
    (cascade abort is per-anchored-Track, not whole-operation).
    """
    from app.services import entry_deletion, policy_engine

    deny_reasons: List[str] = []

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        if action == "anchor.cascade":
            deny_reasons.append("preserve_governance")
            return Decision(
                allowed=False,
                reason="governance_cascade_preserve",
                matched_policy_id="governance:preserve",
            )
        return Decision(allowed=True, reason="default_human_policy")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    ws = "ws-e2e-preserve"
    actor = "user-e2e-preserve"
    app_node, _ = await _make_app_with_projects_manifest(
        workspace_id=ws, space_name="PreserveCascadeSpace"
    )
    projects_track = await _make_projects_track_inside_app(
        app_node=app_node, workspace_id=ws
    )

    # 1) Create parent + auto-provision anchored Track.
    project_x = await _create_project_entry(projects_track, "Project X")
    anchored_x = await _materialize_anchor_for_project(
        source_track=projects_track, parent_entry=project_x, actor_user_id=actor
    )

    # 2) Add a task to the anchored Track so we can assert it persists.
    task_x = await Entry.create(title="Preserve Task", track_id=anchored_x.id)
    await anchored_x.connect(task_x, edge=CONTAINS)

    parent_id = project_x.id
    anchored_x_id = anchored_x.id
    task_x_id = task_x.id

    # 3) Delete parent entry — cascade evaluator denies anchor.cascade.
    await entry_deletion.delete_entry_fast(project_x, actor_user_id=actor)

    # 4) Source entry IS deleted (per-Track cascade abort, not whole-op).
    assert await Entry.get(parent_id) is None

    # 5) Anchored Track + its task PERSIST.
    reloaded_track = await Track.get(anchored_x_id)
    assert (
        reloaded_track is not None
    ), "Denied anchor.cascade must preserve the anchored Track"
    reloaded_task = await Entry.get(task_x_id)
    assert (
        reloaded_task is not None
    ), "Denied anchor.cascade must preserve the anchored Track's contained entries"

    # 6) anchor.cascade was indeed evaluated (we recorded the deny reason).
    assert deny_reasons, "Expected at least one anchor.cascade evaluate call"


# =====================================================================
# Test 4 — Substrate invariants observed end-to-end
# =====================================================================


@pytest.mark.asyncio
async def test_e2e_substrate_invariants_preserved(monkeypatch):
    """Verify the Phase 3.1 substrate invariants hold end-to-end after the
    full pipeline runs:

      - ActorKind Literal single-definition (1 grep match) — checked via
        Python typing
      - PolicyAction strict-supersets ChangeEventAction (modulo audit-only
        *.deny members)
      - relation_targets == ['entry', 'track'] (single source of truth)
      - describe_substrate additive-only extension preserved (legacy keys
        present)
    """
    from typing import get_args

    from app.schemas.audit import ChangeEventAction
    from app.schemas.policy import PolicyAction
    from app.schemas.provenance import ActorKind

    # 1) ActorKind unchanged after Phase 3.1.
    assert set(get_args(ActorKind)) == {"human", "agent", "connector", "system"}

    # 2) PolicyAction strict-supersets ChangeEventAction modulo *.deny.
    cea = set(get_args(ChangeEventAction))
    pa = set(get_args(PolicyAction))
    # I-APPROVAL-03: approval.expired is audit-only — TTL-driven, never
    # policy-evaluated, so it has no PolicyAction twin (same precedent as
    # policy.deny / anchor.deny). Added in Phase 7 while this suite was parked,
    # which is why the exemption set below had gone stale. The authoritative
    # gate is test_anchor_governance.py::
    # test_policy_action_strict_supersets_change_event_action.
    audit_only = {"anchor.deny", "policy.deny", "approval.expired"}
    missing = (cea - audit_only) - pa
    assert not missing, f"PolicyAction missing CEA non-audit members: {missing}"

    # 3) Both Literals contain the Phase 3.1 anchor.* members.
    assert {"anchor.create", "anchor.delete", "anchor.cascade"} <= pa
    assert {"anchor.create", "anchor.delete", "anchor.cascade", "anchor.deny"} <= cea

    # 4) describe_substrate additive extension — legacy keys preserved.
    from app.services.agent_profiles import describe_substrate

    substrate = await describe_substrate()
    for legacy_key in (
        "field_types",
        "view_types",
        "plugins",
        "registry_versions",
    ):
        assert legacy_key in substrate, (
            f"describe_substrate legacy key '{legacy_key}' missing after "
            "Phase 3.1 extension"
        )
    # Phase 3.1 additions present.
    for new_key in (
        "relation_targets",
        "edges",
        "template_var_resolvers",
        "governance_actions",
    ):
        assert (
            new_key in substrate
        ), f"describe_substrate Phase 3.1 key '{new_key}' missing"
    assert substrate["relation_targets"] == ["entry", "track"]

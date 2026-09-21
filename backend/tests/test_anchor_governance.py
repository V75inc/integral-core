"""ANC-03 governance tests — Phase 3.1 Plan 03.1-03.

Section 1 (Task 1): PolicyAction + ChangeEventAction Literal extensions,
manifest governance validator, policy_engine.evaluate gate on
materialize_anchor_track.

Section 2 (Task 2): governance Policy persistence at operational-model publish
time (materialize_governance_policies_for_operational_model in policy_registry).

No ``app.main`` import — test_auth middleware is gitignored per
deferred-items.md; the substrate functions are tested directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, get_args

import pytest

from app.exceptions import BadRequestError
from app.schemas.audit import ChangeEventAction
from app.schemas.policy import Decision, PolicyAction
from app.services.operational_model_runtime import compile_canonical_manifest

# ===== Section 1 — Literal extensions =====


def test_policy_action_includes_anchor_actions():
    """PolicyAction Literal extended additively with anchor.* members."""
    members = set(get_args(PolicyAction))
    expected = {"anchor.create", "anchor.delete", "anchor.cascade"}
    missing = expected - members
    assert not missing, f"PolicyAction missing anchor.* members: {missing}"


def test_change_event_action_includes_anchor_actions():
    """ChangeEventAction Literal extended additively with anchor.* + anchor.deny."""
    members = set(get_args(ChangeEventAction))
    expected = {"anchor.create", "anchor.delete", "anchor.cascade", "anchor.deny"}
    missing = expected - members
    assert not missing, f"ChangeEventAction missing anchor.* members: {missing}"


def test_policy_action_strict_supersets_change_event_action_after_extension():
    """Phase 3 Plan 03-05 invariant preserved.

    Every ChangeEventAction member appears in PolicyAction. anchor.deny is
    listed in BOTH literals (mirroring the policy.deny precedent) so the
    existing test_policy_action_strict_supersets_change_event_action in
    test_policy_engine.py continues to pass unchanged.
    """
    cea = set(get_args(ChangeEventAction))
    pa = set(get_args(PolicyAction))
    # I-APPROVAL-03: approval.expired is audit-only (TTL-driven, never policy-
    # evaluated). It lives in CEA for audit-trail completeness but must NOT
    # appear in PolicyAction — same treatment as policy.deny / anchor.deny
    # for the strict-superset check here.
    audit_only = {"policy.deny", "anchor.deny", "approval.expired"}
    missing = (cea - audit_only) - pa
    assert not missing, f"PolicyAction missing ChangeEventAction members: {missing}"


def test_anchor_deny_is_in_both_literals():
    """anchor.deny is listed in PolicyAction AND ChangeEventAction.

    Mirrors the policy.deny precedent — denial-emit fires from
    policy_engine.evaluate so the action must be a valid ChangeEventAction
    Literal; listing it in PolicyAction too preserves the strict-superset
    invariant. Callers MUST NOT call evaluate(action='anchor.deny') —
    anchor.deny is engine-emitted only.
    """
    pa = set(get_args(PolicyAction))
    cea = set(get_args(ChangeEventAction))
    assert "anchor.deny" in pa
    assert "anchor.deny" in cea


def test_policy_action_single_literal_gate():
    """grep gate: PolicyAction Literal defined exactly once."""
    repo_root = Path(__file__).resolve().parents[1]
    cmd = (
        f'grep -rE "^PolicyAction\\s*=\\s*Literal" {repo_root}/app/ '
        f'--include="*.py" | grep -v __pycache__'
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    lines = [ln for ln in result.stdout.strip().split("\n") if ln]
    assert (
        len(lines) == 1
    ), f"PolicyAction Literal must be defined exactly once, got: {lines}"


def test_change_event_action_single_literal_gate():
    """grep gate: ChangeEventAction Literal defined exactly once."""
    repo_root = Path(__file__).resolve().parents[1]
    cmd = (
        f'grep -rE "^ChangeEventAction\\s*=\\s*Literal" {repo_root}/app/ '
        f'--include="*.py" | grep -v __pycache__'
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    lines = [ln for ln in result.stdout.strip().split("\n") if ln]
    assert (
        len(lines) == 1
    ), f"ChangeEventAction Literal must be defined exactly once, got: {lines}"


def test_actor_kind_unchanged_after_phase_31():
    """ActorKind Literal UNCHANGED — governance uses subject_kind='system'.

    Per CONTEXT decisions Governance subject (ANC-03): the locked design
    reuses the existing 'system' ActorKind member. Adding an 'association'
    member would break Phase 2 D-10 invariant.
    """
    from app.schemas.provenance import ActorKind

    members = set(get_args(ActorKind))
    assert members == {
        "human",
        "agent",
        "connector",
        "system",
    }, f"ActorKind drifted: {members}"


# ===== Section 1 — Manifest governance validator =====


def _manifest_with_anchor_field(
    *,
    governance: Dict[str, Any] | None = None,
    many: bool = False,
) -> Dict[str, Any]:
    rel: Dict[str, Any] = {
        "target": "track",
        "target_track_template": "details",
        "auto_provision": True,
        "many": many,
    }
    if governance is not None:
        rel["governance"] = governance
    return {
        "operational_model_schema_version": 2,
        "scope": "track",
        "package": {"slug": "test", "name": "Test", "version": "1.0.0"},
        "track": {
            "entry_types": [
                {
                    "key": "project",
                    "name": "Project",
                    "fields": [
                        {
                            "key": "details",
                            "type": "relation",
                            "relation": rel,
                        }
                    ],
                }
            ],
            "taxonomy": {"tag_groups": []},
            "views": [],
            "defaults": {},
        },
    }


def _gov_from_compiled(compiled: Dict[str, Any]) -> Dict[str, Any]:
    """Walk the compiled track-scope manifest to the anchor field's governance."""
    return compiled["track"]["entry_types"][0]["fields"][0]["relation"]["governance"]


def test_relation_governance_block_round_trip():
    """Explicit governance block round-trips through compile_canonical_manifest."""
    manifest = _manifest_with_anchor_field(
        governance={
            "cardinality": "many",
            "cascade": "hard",
            "acl_inheritance": "inherit",
        }
    )
    compiled = compile_canonical_manifest(manifest=manifest)
    gov = _gov_from_compiled(compiled)
    assert gov["cardinality"] == "many"
    assert gov["cascade"] == "hard"
    assert gov["acl_inheritance"] == "inherit"


def test_relation_governance_defaults_when_block_absent_many_false():
    """Defaults applied when block absent (many=False → cardinality='one')."""
    manifest = _manifest_with_anchor_field(governance=None, many=False)
    compiled = compile_canonical_manifest(manifest=manifest)
    gov = _gov_from_compiled(compiled)
    assert gov["cardinality"] == "one"
    assert gov["cascade"] == "hard"
    assert gov["acl_inheritance"] == "inherit"


def test_relation_governance_defaults_when_block_absent_many_true():
    """Defaults applied when block absent (many=True → cardinality='many')."""
    manifest = _manifest_with_anchor_field(governance=None, many=True)
    compiled = compile_canonical_manifest(manifest=manifest)
    gov = _gov_from_compiled(compiled)
    assert gov["cardinality"] == "many"


def test_relation_governance_rejects_bogus_cascade():
    """governance.cascade='soft' raises BadRequestError."""
    manifest = _manifest_with_anchor_field(governance={"cascade": "soft"})
    with pytest.raises(BadRequestError) as ei:
        compile_canonical_manifest(manifest=manifest)
    assert "cascade" in str(ei.value)


def test_relation_governance_rejects_bogus_cardinality():
    """governance.cardinality='zero' raises BadRequestError."""
    manifest = _manifest_with_anchor_field(governance={"cardinality": "zero"})
    with pytest.raises(BadRequestError) as ei:
        compile_canonical_manifest(manifest=manifest)
    assert "cardinality" in str(ei.value)


def test_relation_governance_rejects_bogus_acl_inheritance():
    """governance.acl_inheritance='inherited' raises BadRequestError."""
    manifest = _manifest_with_anchor_field(governance={"acl_inheritance": "inherited"})
    with pytest.raises(BadRequestError) as ei:
        compile_canonical_manifest(manifest=manifest)
    assert "acl_inheritance" in str(ei.value)


def test_relation_governance_rejects_non_dict():
    """governance must be an object, not a string/list."""
    manifest = {
        "operational_model_schema_version": 2,
        "scope": "track",
        "package": {"slug": "test", "name": "Test", "version": "1.0.0"},
        "track": {
            "entry_types": [
                {
                    "key": "project",
                    "name": "Project",
                    "fields": [
                        {
                            "key": "details",
                            "type": "relation",
                            "relation": {
                                "target": "track",
                                "target_track_template": "x",
                                "governance": "hard",  # bogus — must be dict
                            },
                        }
                    ],
                }
            ],
            "taxonomy": {"tag_groups": []},
            "views": [],
            "defaults": {},
        },
    }
    with pytest.raises(BadRequestError) as ei:
        compile_canonical_manifest(manifest=manifest)
    assert "governance" in str(ei.value)


# ===== Section 1 — materialize_anchor_track policy gate =====
#
# These tests build a real anchor scaffold using the same fixture pattern
# from test_materialize_anchor_track.py. We monkeypatch policy_engine.evaluate
# at the policy_engine module level (the import in materialize_anchor_track
# uses ``from app.services import policy_engine`` then ``policy_engine.evaluate``
# so the patch hits the live symbol).


async def _build_space_with_template(
    *,
    workspace_id: str,
    space_name: str,
    template_key: str,
    template_name: str = "Detail Track",
):
    from app.models.edges import HAS_OPERATIONAL_MODEL
    from app.models.nodes import App, OperationalModel

    app_node = await App.create(
        name=space_name,
        workspace_id=workspace_id,
        owner_user_id="user-gov-1",
    )
    cp_manifest = {
        "operational_model_schema_version": 2,
        "scope": "app",
        "package": {"slug": "t", "name": "T", "version": "1.0.0"},
        "app": {
            "tracks": [],
            "track_templates": [
                {
                    "key": template_key,
                    "name": template_name,
                    "entry_types": [
                        {
                            "key": "note",
                            "name": "Note",
                            "fields": [{"key": "title", "type": "text"}],
                        }
                    ],
                    "views": [],
                    "taxonomy": {"tag_groups": []},
                    "defaults": {},
                }
            ],
            "relations": [],
            "defaults": {},
        },
    }
    cp = await OperationalModel.create(
        name=f"{space_name} Profile",
        scope="app",
        manifest=cp_manifest,
        app_id=app_node.id,
        workspace_id=workspace_id,
        library_package=False,
    )
    await app_node.connect(cp, edge=HAS_OPERATIONAL_MODEL)
    app_node.attached_operational_model_id = cp.id
    await app_node.save()
    return app_node, cp


async def _track_inside(app_node, *, title: str, workspace_id: str):
    from app.models.edges import CONTAINS
    from app.models.nodes import Track

    track = await Track.create(
        title=title, owner_id="user-gov-1", workspace_id=workspace_id
    )
    await app_node.connect(track, edge=CONTAINS)
    return track


@pytest.mark.asyncio
async def test_materialize_anchor_track_calls_policy_engine_evaluate(monkeypatch):
    """policy_engine.evaluate is called with action='anchor.create'."""
    from app.services import operational_model_runtime as cpr
    from app.services import policy_engine

    eval_calls: List[Dict[str, Any]] = []
    real_evaluate = policy_engine.evaluate

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        eval_calls.append({"subject": subject, "action": action, "resource": resource})
        return Decision(allowed=True, reason="default_human_policy")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    ws = "ws-gov-call-1"
    app_node, _ = await _build_space_with_template(
        workspace_id=ws,
        space_name="GovCallSpace",
        template_key="details",
    )
    src = await _track_inside(app_node, title="Src GovCall", workspace_id=ws)

    anchor = await cpr.materialize_anchor_track(
        source_track=src,
        template_key="details",
        field_key="details",
        actor_user_id="user-gov-call",
    )

    anchor_create_calls = [c for c in eval_calls if c["action"] == "anchor.create"]
    assert anchor_create_calls, "Expected at least one anchor.create evaluate call"
    # Verify scope shape matches the locked format anchor_field:<cp>:<et>:<field>
    scope = anchor_create_calls[0]["resource"].scope
    assert scope.startswith("anchor_field:"), f"unexpected scope: {scope}"
    assert ":details:details" in scope
    assert anchor is not None
    # Restore for cleanliness (monkeypatch handles teardown automatically).
    _ = real_evaluate


@pytest.mark.asyncio
async def test_materialize_anchor_track_denial_raises_403(monkeypatch):
    """Decision(allowed=False) → InsufficientPermissionsError; no Track persists."""
    from app.api.errors import InsufficientPermissionsError
    from app.models.nodes import Track
    from app.services import operational_model_runtime as cpr
    from app.services import policy_engine

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        if action == "anchor.create":
            return Decision(
                allowed=False,
                reason="fail_closed_no_policy",
                matched_policy_id=None,
            )
        # System-bypass for other actions (so emit_change_event for anchor.deny
        # doesn't trigger its own denial-emit recursion).
        return Decision(allowed=True, reason="system_subject")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    ws = "ws-gov-deny-1"
    app_node, _ = await _build_space_with_template(
        workspace_id=ws,
        space_name="GovDenySpace",
        template_key="details",
    )
    src = await _track_inside(app_node, title="Src Deny", workspace_id=ws)

    tracks_before = await Track.find({"workspace_id": ws})
    count_before = len(list(tracks_before))

    with pytest.raises(InsufficientPermissionsError) as ei:
        await cpr.materialize_anchor_track(
            source_track=src,
            template_key="details",
            field_key="details",
            actor_user_id="agent-gov-deny",
            actor_kind="agent",
        )

    msg = str(ei.value)
    assert "Anchor governance" in msg or "anchor.create" in msg

    # No new Track persisted.
    tracks_after = await Track.find({"workspace_id": ws})
    assert len(list(tracks_after)) == count_before


@pytest.mark.asyncio
async def test_materialize_anchor_track_emit_action_is_anchor_create(monkeypatch):
    """On allow, emit_change_event is called with action='anchor.create' (NOT 'track.create')."""
    from app.services import change_event as ce
    from app.services import operational_model_runtime as cpr
    from app.services import policy_engine

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        return Decision(allowed=True, reason="default_human_policy")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    emit_calls: List[Dict[str, Any]] = []
    real_emit = ce.emit_change_event

    async def mock_emit(*, action, **kwargs):
        emit_calls.append({"action": action, **kwargs})
        return None

    monkeypatch.setattr(ce, "emit_change_event", mock_emit)

    ws = "ws-gov-emit-switch"
    app_node, _ = await _build_space_with_template(
        workspace_id=ws,
        space_name="EmitSwitch",
        template_key="d",
    )
    src = await _track_inside(app_node, title="Src Emit", workspace_id=ws)

    await cpr.materialize_anchor_track(
        source_track=src,
        template_key="d",
        field_key="f",
        actor_user_id="user-emit-switch",
    )

    anchor_create_emits = [c for c in emit_calls if c["action"] == "anchor.create"]
    track_create_emits = [c for c in emit_calls if c["action"] == "track.create"]
    assert (
        anchor_create_emits
    ), "Expected anchor.create emit after Plan 03.1-03 switch from track.create"
    assert (
        not track_create_emits
    ), "Plan 03.1-02 placeholder track.create emit MUST be switched off"
    _ = real_emit


@pytest.mark.asyncio
async def test_materialize_anchor_track_denial_emits_anchor_deny(monkeypatch):
    """On deny, anchor.deny ChangeEvent is emitted (parallel forensic record).

    Phase 3 Plan 03-05's denial-emit path inside policy_engine.evaluate fires
    a ``policy.deny`` event independently; this test verifies the additional
    ``anchor.deny`` event mirrors that for anchor-specific forensic clarity.
    """
    from app.api.errors import InsufficientPermissionsError
    from app.services import change_event as ce
    from app.services import operational_model_runtime as cpr
    from app.services import policy_engine

    async def mock_evaluate(*, subject, action, resource, _internal_actor=None):
        if action == "anchor.create":
            return Decision(
                allowed=False,
                reason="fail_closed_no_policy",
            )
        return Decision(allowed=True, reason="system_subject")

    monkeypatch.setattr(policy_engine, "evaluate", mock_evaluate)

    emit_calls: List[Dict[str, Any]] = []

    async def mock_emit(*, action, **kwargs):
        emit_calls.append({"action": action, **kwargs})

    monkeypatch.setattr(ce, "emit_change_event", mock_emit)

    ws = "ws-anchor-deny-emit"
    app_node, _ = await _build_space_with_template(
        workspace_id=ws,
        space_name="AnchorDenyEmit",
        template_key="d",
    )
    src = await _track_inside(app_node, title="Src AD", workspace_id=ws)

    with pytest.raises(InsufficientPermissionsError):
        await cpr.materialize_anchor_track(
            source_track=src,
            template_key="d",
            field_key="f",
            actor_user_id="agent-ad",
            actor_kind="agent",
        )

    deny_emits = [c for c in emit_calls if c["action"] == "anchor.deny"]
    assert deny_emits, "Expected at least one anchor.deny ChangeEvent"
    assert deny_emits[0]["details"]["failed_action"] == "anchor.create"


# ===== Section 2 — Governance Policy persistence at publish time =====
#
# Tests for Task 2 — materialize_governance_policies_for_operational_model.


@pytest.mark.asyncio
async def test_materialize_governance_policies_creates_one_per_anchor_field():
    """Publishing a manifest with 2 anchor fields → 2 governance Policy rows."""
    from app.models.nodes import OperationalModel, Policy
    from app.services.policy_registry import (
        materialize_governance_policies_for_operational_model,
    )

    cp = await OperationalModel.create(
        name="GovPolicyCP-1",
        scope="track",
        manifest={"package": {"slug": "g", "name": "G", "version": "1.0.0"}},
        library_package=False,
    )

    compiled_manifest = {
        "package": {"slug": "g", "name": "G", "version": "1.0.0"},
        "entry_types": [
            {
                "key": "project",
                "name": "Project",
                "fields": [
                    {
                        "key": "details",
                        "type": "relation",
                        "relation": {
                            "target": "track",
                            "target_track_template": "details",
                            "governance": {
                                "cardinality": "many",
                                "cascade": "hard",
                                "acl_inheritance": "inherit",
                            },
                        },
                    }
                ],
            },
            {
                "key": "contract",
                "name": "Contract",
                "fields": [
                    {
                        "key": "addenda",
                        "type": "relation",
                        "relation": {
                            "target": "track",
                            "target_track_template": "addenda",
                            "governance": {
                                "cardinality": "many",
                                "cascade": "hard",
                                "acl_inheritance": "inherit",
                            },
                        },
                    }
                ],
            },
        ],
    }

    policies = await materialize_governance_policies_for_operational_model(
        cp.id, compiled_manifest
    )
    assert len(policies) == 2, f"expected 2 governance policies, got {len(policies)}"
    for p in policies:
        assert p.subject_kind == "system"
        assert p.subject_id.startswith("governance:")
        assert p.scope.startswith(f"anchor_field:{cp.id}:")
        assert "anchor.create" in p.actions
        assert "anchor.cascade" in p.actions  # cascade='hard' → includes

    # Sanity: round-trip the rows by id.
    fetched = [await Policy.get(p.id) for p in policies]
    assert all(f is not None for f in fetched)


@pytest.mark.asyncio
async def test_governance_policy_actions_reflect_cascade_preserve():
    """cascade='preserve' → Policy.actions excludes anchor.cascade."""
    from app.models.nodes import OperationalModel
    from app.services.policy_registry import (
        materialize_governance_policies_for_operational_model,
    )

    cp = await OperationalModel.create(
        name="GovPreserveCP",
        scope="track",
        manifest={"package": {"slug": "g", "name": "G", "version": "1.0.0"}},
        library_package=False,
    )

    compiled_manifest = {
        "package": {"slug": "g", "name": "G", "version": "1.0.0"},
        "entry_types": [
            {
                "key": "project",
                "name": "Project",
                "fields": [
                    {
                        "key": "details",
                        "type": "relation",
                        "relation": {
                            "target": "track",
                            "target_track_template": "details",
                            "governance": {
                                "cardinality": "many",
                                "cascade": "preserve",
                                "acl_inheritance": "inherit",
                            },
                        },
                    }
                ],
            }
        ],
    }

    policies = await materialize_governance_policies_for_operational_model(
        cp.id, compiled_manifest
    )
    assert len(policies) == 1
    assert "anchor.create" in policies[0].actions
    assert "anchor.cascade" not in policies[0].actions  # preserve → exclude


@pytest.mark.asyncio
async def test_governance_policy_republish_replaces_stale_rows():
    """Calling the materializer twice replaces (not duplicates) governance Policies."""
    from app.models.nodes import OperationalModel, Policy
    from app.services.policy_registry import (
        materialize_governance_policies_for_operational_model,
    )

    cp = await OperationalModel.create(
        name="GovRepublishCP",
        scope="track",
        manifest={"package": {"slug": "g", "name": "G", "version": "1.0.0"}},
        library_package=False,
    )

    compiled_manifest = {
        "package": {"slug": "g", "name": "G", "version": "1.0.0"},
        "entry_types": [
            {
                "key": "project",
                "name": "Project",
                "fields": [
                    {
                        "key": "details",
                        "type": "relation",
                        "relation": {
                            "target": "track",
                            "target_track_template": "details",
                            "governance": {
                                "cardinality": "many",
                                "cascade": "hard",
                                "acl_inheritance": "inherit",
                            },
                        },
                    }
                ],
            }
        ],
    }

    p1 = await materialize_governance_policies_for_operational_model(
        cp.id, compiled_manifest
    )
    p2 = await materialize_governance_policies_for_operational_model(
        cp.id, compiled_manifest
    )

    assert len(p1) == 1
    assert len(p2) == 1
    # Fresh rows on the second call — stale ones deleted.
    assert p1[0].id != p2[0].id

    # The first batch should be gone.
    stale = await Policy.get(p1[0].id)
    assert stale is None


@pytest.mark.asyncio
async def test_governance_policy_skips_non_track_relation_fields():
    """Relation fields with target='entry' do NOT produce governance Policies."""
    from app.models.nodes import OperationalModel
    from app.services.policy_registry import (
        materialize_governance_policies_for_operational_model,
    )

    cp = await OperationalModel.create(
        name="GovEntryRelCP",
        scope="track",
        manifest={"package": {"slug": "g", "name": "G", "version": "1.0.0"}},
        library_package=False,
    )

    compiled_manifest = {
        "package": {"slug": "g", "name": "G", "version": "1.0.0"},
        "entry_types": [
            {
                "key": "project",
                "name": "Project",
                "fields": [
                    {
                        "key": "lead_contact",
                        "type": "relation",
                        "relation": {
                            "target": "entry",  # NOT track → no governance
                            "target_entry_types": ["contact"],
                        },
                    },
                    {"key": "title", "type": "text"},  # non-relation → no governance
                ],
            }
        ],
    }

    policies = await materialize_governance_policies_for_operational_model(
        cp.id, compiled_manifest
    )
    assert policies == []


@pytest.mark.asyncio
async def test_system_subject_bypass_preserved_after_phase_31():
    """RESEARCH Pitfall 2 regression: system subject bypasses evaluate.

    Plan 03.1-03's governance Policies use subject_kind='system'; the engine
    must still short-circuit on Subject(kind='system') without consulting
    HAS_POLICY edges, or governance writes themselves trigger infinite
    recursion via the denial-emit path.
    """
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate

    decision = await evaluate(
        subject=Subject(kind="system", id="anchor_governance_test"),
        action="anchor.create",
        resource=Resource(
            kind="track",
            id="",
            scope="anchor_field:cp-x:et-y:f-z",
        ),
    )
    assert decision.allowed is True
    assert decision.reason == "system_subject"

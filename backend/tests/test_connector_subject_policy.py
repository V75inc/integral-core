"""Plan 05-01 Task 2 — Additive substrate fields + Literal extensions +
Subject(kind='connector') round-trip + per-connector Policy materialization.

Covers behaviors 1-13 locked in 05-01-PLAN.md Task 2.

Tests intentionally use only schema/service/model imports (NOT the
authenticated_client fixture) to remain robust against the pre-existing
``app.main`` import path bug (Plan 03-01 deferred-item).
"""

from __future__ import annotations

from typing import get_args

import pytest

# ---------------------------------------------------------------------------
# Behaviors 1-4: additive Node fields with legacy-safe defaults
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_idempotency_key_default_empty():
    """Entry.idempotency_key defaults to '' — legacy entries unchanged."""
    from app.models.nodes import Entry

    e = Entry(track_id="t-1")
    assert e.idempotency_key == ""


@pytest.mark.asyncio
async def test_entry_migration_status_default_complete():
    """Entry.migration_status defaults to 'complete' — legacy entries appear migrated."""
    from app.models.nodes import Entry

    e = Entry(track_id="t-1")
    assert e.migration_status == "complete"
    assert e.migration_error is None


@pytest.mark.asyncio
async def test_content_profile_migration_status_default_complete():
    """ContentProfile.migration_status defaults to 'complete'."""
    from app.models.nodes import ContentProfile

    cp = ContentProfile(name="test-cp")
    assert cp.migration_status == "complete"


@pytest.mark.asyncio
async def test_connector_additive_fields_defaults():
    """Connector.sync_interval_seconds defaults to 300; last_synced_at to None."""
    from app.agentive.nodes import Connector

    c = Connector(owner="u-1")
    assert c.sync_interval_seconds == 300
    assert c.last_synced_at is None
    assert c.subclass_slug == ""
    assert c.workspace_id == ""
    assert c.health_status == "unknown"
    assert c.last_error is None
    assert c.last_health_at is None


# ---------------------------------------------------------------------------
# Behavior 5: IsConnectedTo edge — Connector → Track with mapping YAML
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_connected_to_edge_connector_to_track():
    """IS_CONNECTED_TO edge wires Connector → Track and carries the mapping YAML."""
    from app.agentive.nodes import Connector
    from app.models.edges import IS_CONNECTED_TO, IsConnectedTo
    from app.models.nodes import Track

    # Alias sanity
    assert IS_CONNECTED_TO is IsConnectedTo

    connector = await Connector.create(kind="jvagent", owner="u-1")
    track = await Track.create(title="bound-track", workspace_id="w-1")

    yaml_spec = "entry_type: github_issue\nmapping:\n  title: title\n"
    await connector.connect(track, edge=IsConnectedTo, mapping_profile_yaml=yaml_spec)

    bound = await connector.nodes(
        edge=["IS_CONNECTED_TO"], direction="out", node=["Track"]
    )
    assert bound is not None
    bound_list = list(bound)
    assert len(bound_list) == 1
    assert bound_list[0].id == track.id


# ---------------------------------------------------------------------------
# Behaviors 6-7: PolicyAction +8 / ChangeEventAction +4 (Literal membership)
# ---------------------------------------------------------------------------


def test_policy_action_extended_with_8_new_members():
    """PolicyAction contains the 8 new members from locked decision #5."""
    from app.schemas.policy import PolicyAction

    members = set(get_args(PolicyAction))
    expected_new = {
        "connector.sync",
        "connector.sync.start",
        "connector.sync.complete",
        "connector.sync.failed",
        "migration.publish",
        "migration.force_publish",
        "migration.run",
        "conflict.resolve",
    }
    assert expected_new.issubset(
        members
    ), f"Missing PolicyAction members: {expected_new - members}"


def test_change_event_action_extended_with_4_new_members():
    """ChangeEventAction contains the 4 new members from locked decision #5."""
    from app.schemas.audit import ChangeEventAction

    members = set(get_args(ChangeEventAction))
    expected_new = {
        "connector.sync.start",
        "connector.sync.complete",
        "connector.sync.failed",
        "migration.run",
    }
    assert expected_new.issubset(
        members
    ), f"Missing ChangeEventAction members: {expected_new - members}"


# ---------------------------------------------------------------------------
# Behavior 8: single-Literal grep gates — UNCHANGED (still exactly 1 each)
# ---------------------------------------------------------------------------


def test_single_literal_grep_gates_unchanged():
    """The AST grep gates from INVARIANTS.md L26-30 remain at exactly 1 match."""
    import re
    from pathlib import Path

    backend_app = Path(__file__).resolve().parent.parent / "app"

    for literal_name in ("PolicyAction", "ChangeEventAction", "ActorKind"):
        # Lines starting (no leading whitespace) with `<Name> = Literal`.
        # Python ``re`` keeps the gate portable across GNU grep builds that
        # do not treat ``\\s`` as whitespace.
        rx = re.compile(rf"^{literal_name}\s*=\s*Literal")
        matches: list[str] = []
        for py in backend_app.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if rx.search(line):
                    matches.append(f"{py.as_posix()}:{i}:{line}")
        assert len(matches) == 1, (
            f"{literal_name} single-Literal gate broken: {len(matches)} matches:\n"
            + "\n".join(matches)
        )


# ---------------------------------------------------------------------------
# Behavior 9: PolicyAction strict-supersets ChangeEventAction (INVARIANTS L43-63)
# ---------------------------------------------------------------------------


def test_policy_action_strict_supersets_change_event_action():
    """Every ChangeEventAction member (modulo denial-only) appears in PolicyAction."""
    from app.schemas.audit import ChangeEventAction
    from app.schemas.policy import PolicyAction

    cea = set(get_args(ChangeEventAction))
    pa = set(get_args(PolicyAction))
    # ``anchor.deny`` + ``policy.deny`` are denial-emit-only and intentionally
    # in BOTH lists per Plan 03-05 / Plan 03.1-03 idiom — included in subset
    # check by design (they're in PolicyAction too).
    # ``approval.expired`` is I-APPROVAL-03 audit-only (TTL-driven, never
    # policy-evaluated) — must NOT appear in PolicyAction by design.
    audit_only = {"approval.expired"}
    missing = (cea - audit_only) - pa
    assert (
        not missing
    ), f"PolicyAction MUST strict-superset ChangeEventAction; missing: {missing}"


# ---------------------------------------------------------------------------
# Behavior 10: Subject(kind='connector') constructs
# ---------------------------------------------------------------------------


def test_subject_connector_kind_constructs():
    """Subject(kind='connector', id='c-1') — no ValidationError."""
    from app.schemas.policy import Subject

    s = Subject(kind="connector", id="c-1")
    assert s.kind == "connector"
    assert s.id == "c-1"


# ---------------------------------------------------------------------------
# Behavior 11: evaluate fail-closed for connector with no policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_fail_closed_no_policy_for_connector():
    """policy_engine.evaluate returns fail_closed_no_policy for unattached connector."""
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate

    decision = await evaluate(
        subject=Subject(kind="connector", id="c-no-policy"),
        action="connector.sync",
        resource=Resource(
            kind="connector",
            id="c-no-policy",
            scope="connector:c-no-policy",
        ),
    )
    assert decision.allowed is False
    assert decision.reason == "fail_closed_no_policy"


# ---------------------------------------------------------------------------
# Behavior 12: materialize_policies_for_connector writes Policy + HAS_POLICY
# ---------------------------------------------------------------------------


async def _owner_user_id(display_name: str) -> str:
    """Persist a real User node and return its id.

    ``create_connector`` now REFUSES to persist a Connector whose owner does
    not resolve to a User node (I-GRAPH-01): the Owns edge could never be
    wired, and the old behaviour was to log a warning and return a detached
    node. These tests used to pass a synthetic ``"u-policy-1"`` string, which
    silently exercised exactly that broken path.
    """
    from app.models.nodes import User

    user = await User.create(display_name=display_name)
    return user.id


@pytest.mark.asyncio
async def test_materialize_policies_for_connector_creates_policy_and_edge():
    """create_connector (extended) materializes a per-connector Policy.

    Verifies I-CON-04: every Connector created via connector_registry_node
    gets a per-connector Policy attached at create time so policy_engine
    evaluates allowed=True for connector.sync.
    """
    from app.agentive.services.connector_registry_node import create_connector
    from app.schemas.policy import Resource, Subject
    from app.services.policy_engine import evaluate

    owner_id = await _owner_user_id("policy-owner")
    connector = await create_connector(kind="jvagent", owner=owner_id)

    # Verify the HAS_POLICY edge wired
    attached_policies = await connector.nodes(
        edge=["HAS_POLICY"], direction="out", node=["Policy"]
    )
    policies = list(attached_policies) if attached_policies else []
    assert len(policies) >= 1, "Expected at least one HAS_POLICY-attached Policy"

    # Verify the Policy includes the 3 baseline actions (connector.sync,
    # entry.create, entry.update)
    matching = [
        p
        for p in policies
        if set(["connector.sync", "entry.create", "entry.update"]).issubset(
            set(getattr(p, "actions", []) or [])
        )
    ]
    assert matching, (
        "Expected a Policy with baseline actions "
        "['connector.sync','entry.create','entry.update']; "
        f"found: {[getattr(p, 'actions', None) for p in policies]}"
    )

    # And evaluate(connector.sync) is now allowed
    decision = await evaluate(
        subject=Subject(kind="connector", id=connector.id),
        action="connector.sync",
        resource=Resource(
            kind="connector", id=connector.id, scope=f"connector:{connector.id}"
        ),
    )
    assert decision.allowed is True
    assert decision.reason.startswith(
        "policy_match"
    ), f"Expected policy_match reason; got: {decision.reason}"


# ---------------------------------------------------------------------------
# Behavior 13: existing connector.create Phase 1 smoke continues to pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_connector_still_returns_persisted_node():
    """Back-compat — create_connector still returns the persisted Connector.

    The Phase 1 contract (Owns edge, scalar field round-trip) is preserved;
    only the per-connector Policy materialization is added.
    """
    from app.agentive.nodes import Connector
    from app.agentive.services.connector_registry_node import create_connector

    owner_id = await _owner_user_id("backcompat-owner")
    c = await create_connector(
        kind="jvagent",
        owner=owner_id,
        auth_state={"token": "abc"},
        capabilities=["filing"],
    )
    fetched = await Connector.get(c.id)
    assert fetched is not None
    assert fetched.owner == owner_id
    assert fetched.kind == "jvagent"
    assert fetched.auth_state == {"token": "abc"}
    assert fetched.capabilities == ["filing"]
    # Additive fields default-applied
    assert fetched.sync_interval_seconds == 300
    assert fetched.last_synced_at is None

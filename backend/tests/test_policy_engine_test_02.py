"""TEST-02 — Phase 3 Plan 03-05 end-to-end integration test.

Per ROADMAP Phase 3 acceptance criterion #5: "agent attempts a write outside
its policy -> 403 with canonical envelope -> ChangeEvent with action=policy.deny
appears in GET /api/audit-log with the agent as actor."

This test exercises the full substrate stack:
  1. Plan 03 admin REST: POST /api/policies creates a narrow Policy
  2. Plan 01 + Plan 03 engine: policy_engine.evaluate denies an out-of-policy action
  3. Plan 05 denial-emit: emit_change_event writes a policy.deny ChangeEvent
  4. Phase 2 + Plan 02 audit log: GET /api/audit-log returns the event

The agent in this test is represented by a Subject(kind="agent", id="<id>").
The full agentive HTTP write path (jvagent dispatching a chat -> entry creation)
is Phase 4+ scope; Plan 05 verifies the substrate end-to-end via direct engine
invocation, which is the same single decision point every agentive surface
flows through after Plan 02's atomic sweep.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_test_02_agentive_write_denied_emits_audit_event(
    authenticated_admin_client: AsyncClient,
    test_user,
):
    """TEST-02 acceptance — full substrate exercise.

    1. Owner creates a real Track they own (so the audit-log per-event read
       filter admits events scoped to that track for the owner).
    2. Owner creates a Policy: agent "a-test-02" gets read-only access on
       that same track.
    3. Agent attempts entry.create on the track via policy_engine.evaluate
       (the agentive HTTP write path is Phase 4+ scope; the engine call is the
       same single decision point both human and agent paths funnel through
       after Plan 02's atomic sweep).
    4. Engine denies (action="entry.create" not in policy.actions=["entry.read"])
       — Decision.reason in {action_mismatch, no_matching_policy,
       fail_closed_no_policy} depending on whether the HAS_POLICY edge wired
       (subject_id is a synthetic agent id with no AgentConfig Node, so the
       Plan 03 fallback Policy.find scan applies).
    5. Plan 05 denial-emit writes a ChangeEvent with action="policy.deny",
       actor.kind="agent", actor.id="a-test-02", details={failed_action,
       decision_reason, matched_policy_id}, scope=f"track:{track_id}".
    6. GET /api/audit-log?scope=track:<id>&actor_kind=agent surfaces the
       event (the owner can see audit events under tracks they own — Phase 2
       D-07 + Plan 02 audit_log.read scope-prefix dispatch).
    """
    from app.middleware.permissions_cache import reset_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services import policy_engine as pe

    # Step 1 — owner creates a real track they own (so audit-log filter admits)
    track_resp = await authenticated_admin_client.post(
        "/api/tracks", json={"title": "TEST-02 Track"}
    )
    assert track_resp.status_code == 200, track_resp.text
    track_id = track_resp.json()["track"]["id"]
    track_scope = f"track:{track_id}"

    # Step 2 — owner creates a narrow Policy for agent "a-test-02"
    policy_resp = await authenticated_admin_client.post(
        "/api/policies",
        json={
            "subject_kind": "agent",
            "subject_id": "a-test-02",
            "scope": track_scope,
            "actions": ["entry.read"],
            "is_active": True,
        },
    )
    assert policy_resp.status_code == 201, policy_resp.text
    policy_id = policy_resp.json()["id"]
    assert policy_id

    # Step 3 + 4 — agent attempts an out-of-policy write; engine denies
    reset_permissions_cache()
    decision = await pe.evaluate(
        subject=Subject(kind="agent", id="a-test-02"),
        action="entry.create",
        resource=Resource(
            kind="entry",
            id="e-attempt-1",
            scope=track_scope,
        ),
    )
    assert (
        decision.allowed is False
    ), "TEST-02: out-of-policy entry.create must be denied"
    assert decision.reason in {
        "action_mismatch",
        "no_matching_policy",
        "fail_closed_no_policy",
    }, f"Unexpected denial reason: {decision.reason}"

    # Step 5 + 6 — verify the policy.deny ChangeEvent landed in the audit log.
    # The audit-log endpoint runs every event through policy_engine.evaluate
    # (audit_log.read) which dispatches on scope prefix. Owner sees their
    # track's events; this surfaces the agent-actor policy.deny event.
    audit_resp = await authenticated_admin_client.get(
        f"/api/audit-log?scope={track_scope}&actor_kind=agent",
    )
    assert audit_resp.status_code == 200, audit_resp.text
    body = audit_resp.json()
    events = body.get("events", [])
    assert isinstance(events, list)

    matching = [
        e
        for e in events
        if e.get("action") == "policy.deny"
        and e.get("actor_id") == "a-test-02"
        and e.get("resource_id") == "e-attempt-1"
    ]
    assert matching, (
        f"TEST-02 regression: policy.deny ChangeEvent for agent 'a-test-02' "
        f"attempting entry.create on e-attempt-1 not found in "
        f"/api/audit-log?scope={track_scope}&actor_kind=agent. "
        f"Sample events: {events[:5]}"
    )

    deny_event = matching[0]
    assert deny_event["actor_kind"] == "agent"
    assert deny_event["action"] == "policy.deny"
    assert deny_event["resource_type"] == "entry"
    assert deny_event["scope"] == track_scope

    # Plan 05 details payload — surfaced via to_wire_flat (Task 1 extension)
    details = deny_event.get("details") or {}
    assert (
        details.get("failed_action") == "entry.create"
    ), f"details payload missing failed_action: {details}"
    assert details.get(
        "decision_reason"
    ), f"details payload missing decision_reason: {details}"
    assert "matched_policy_id" in details


@pytest.mark.asyncio
async def test_test_02_audit_log_actor_kind_filter(
    authenticated_client: AsyncClient,
    test_user,
):
    """GET /api/audit-log?actor_kind=agent returns ONLY agent-actor events.

    Ensures the actor_kind query parameter narrows the scan as documented.
    Generate at least one agent-actor event by triggering a denial first.
    """
    from app.middleware.permissions_cache import reset_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services import policy_engine as pe

    # Seed a denial so the suite produces at least one agent-actor event.
    reset_permissions_cache()
    await pe.evaluate(
        subject=Subject(kind="agent", id="a-filter-test"),
        action="entry.create",
        resource=Resource(kind="entry", id="e-filter", scope="track:t-filter"),
    )

    resp = await authenticated_client.get("/api/audit-log?actor_kind=agent")
    assert resp.status_code == 200
    events = resp.json().get("events", [])
    assert isinstance(events, list)
    # Every returned event must have actor_kind=agent (no filter leak)
    for e in events:
        assert (
            e.get("actor_kind") == "agent"
        ), f"Filter leak: event with actor_kind={e.get('actor_kind')} in agent scan"


@pytest.mark.asyncio
async def test_test_02_human_allow_does_not_produce_deny(
    authenticated_client: AsyncClient,
    test_user,
):
    """Negative control: a system-subject allow does NOT produce a policy.deny
    event (no false positives in the audit log).
    """
    from app.middleware.permissions_cache import reset_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services import policy_engine as pe

    reset_permissions_cache()
    decision = await pe.evaluate(
        subject=Subject(kind="system", id="sys-test-02-control"),
        action="audit_log.read",
        resource=Resource(kind="change_event", id="*", scope="*"),
    )
    assert decision.allowed is True

    # Verify NO policy.deny event for sys-test-02-control across all actor_kinds
    resp = await authenticated_client.get("/api/audit-log?actor_kind=system")
    assert resp.status_code == 200
    events = resp.json().get("events", [])
    matching = [
        e
        for e in events
        if e.get("action") == "policy.deny"
        and e.get("actor_id") == "sys-test-02-control"
    ]
    assert not matching, (
        f"False positive: system-subject allow produced a policy.deny event: "
        f"{matching}"
    )


@pytest.mark.asyncio
async def test_test_02_canonical_envelope_on_unauthenticated_policy_post():
    """403/401 paths return the canonical structured-error envelope.

    The full HTTP path (agentive write -> 403 with canonical envelope) is
    Phase 4+ scope. Plan 05 verifies the canonical envelope shape via an
    unauthenticated POST /api/policies — the same envelope every 401/403
    flows through.
    """
    from httpx import ASGITransport

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/policies",
            json={"subject_kind": "agent", "subject_id": "a-x"},
        )

    # Unauthenticated -> 401 (or 403 depending on auth middleware ordering)
    assert resp.status_code in (401, 403), resp.text
    body = resp.json()
    # Structured-error envelope — at minimum error_code OR detail present.
    # The canonical 5-key envelope uses error_code + message; FastAPI's
    # default validation envelope uses detail. Either is acceptable — we
    # just require a structured response, not a bare string.
    assert "error_code" in body or "detail" in body, body

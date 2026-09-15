"""Policy denial audit-emit + recursion-guard tests — Plan 03-05.

Section A (Task 1): ChangeEventAction Literal extension + emit_change_event
details kwarg + ChangeEventEnvelope.details round-trip.
Section B (Task 2): policy_engine.evaluate denial-emit path + recursion guard +
structured logging.
"""

from __future__ import annotations

import inspect
import logging
import subprocess
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from app.schemas.audit import Actor, ChangeEventAction, ChangeEventResponse
from app.schemas.policy import PolicyAction
from app.schemas.provenance import ActorKind
from app.services.change_event import emit_change_event
from app.services.change_event_logger import (
    envelope_from_dblog,
    get_change_event_logger,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ===== Section A — Literal extension + emit details kwarg =====


def test_change_event_action_includes_policy_actions():
    """D-10 strict-superset: policy.* actions are members of ChangeEventAction."""
    members = set(get_args(ChangeEventAction))
    expected = {"policy.create", "policy.update", "policy.delete", "policy.deny"}
    missing = expected - members
    assert not missing, f"ChangeEventAction missing policy.* members: {missing}"


def test_change_event_action_strict_superset_after_extension():
    """D-09 invariant: extension does not remove members."""
    members = set(get_args(ChangeEventAction))
    prior_required = {
        "entry.create",
        "entry.update",
        "entry.delete",
        "track.create",
        "track.update",
        "track.delete",
        "space.create",
        "space.update",
        "space.delete",
        "agent_config.register",
        "agent_config.heartbeat",
        "connector.create",
        # Sharing primitives added between plan authoring and Plan 03 execution.
        "track.collaborator_add",
        "track.share_link.mint",
        "entry.invitation_create",
        # Workspace primitives.
        "workspace.member_remove",
    }
    missing = prior_required - members
    assert not missing, f"ChangeEventAction prior members removed: {missing}"


def test_change_event_action_single_literal_definition():
    """Phase 2 D-09 invariant: ChangeEventAction Literal defined exactly once."""
    cmd = (
        'grep -rE "^ChangeEventAction\\s*=\\s*Literal" '
        f'{REPO_ROOT}/backend/app/ --include="*.py"'
    )
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    lines = [
        ln for ln in out.stdout.strip().split("\n") if ln and "__pycache__" not in ln
    ]
    assert (
        len(lines) == 1
    ), f"ChangeEventAction Literal must be defined exactly once; found: {lines}"


def test_policy_action_strict_supersets_change_event_action():
    """Plan 01 invariant preserved: PolicyAction superset of ChangeEventAction.

    Phase 7 Plan 07-04 whitelist update: ``approval.expired`` is
    audit-only — system-emitted by the TTL reclaim loop. It has NO
    PolicyAction twin (callers never evaluate(action='approval.expired')).
    Follows the existing audit-only exemptions precedent (``policy.deny``
    is in BOTH literals via the strict-superset idiom — this exemption is
    different: a CEA-only member that intentionally has no PolicyAction
    twin). See I-APPROVAL-03 in docs/INVARIANTS.md.
    """
    cea = set(get_args(ChangeEventAction))
    pa = set(get_args(PolicyAction))
    # Audit-only ChangeEventAction members — system-emitted, never evaluated
    # as a policy gate. Each is documented inline in app/schemas/audit.py
    # at the member definition site.
    audit_only_cea_exempt = {
        "approval.expired",  # Phase 7 Plan 07-04 — TTL reclaim loop
    }
    missing = cea - pa - audit_only_cea_exempt
    assert not missing, f"PolicyAction missing ChangeEventAction members: {missing}"


def test_actor_kind_single_literal_definition():
    """D-10 invariant: ActorKind Literal defined exactly once across backend/app/."""
    cmd = (
        'grep -rE "^ActorKind\\s*=\\s*Literal" '
        f'{REPO_ROOT}/backend/app/ --include="*.py"'
    )
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    lines = [
        ln for ln in out.stdout.strip().split("\n") if ln and "__pycache__" not in ln
    ]
    assert (
        len(lines) == 1
    ), f"ActorKind Literal must be defined exactly once; found: {lines}"


def test_change_event_response_accepts_policy_deny():
    """ChangeEventResponse Literal validation accepts policy.deny."""
    resp = ChangeEventResponse(
        id="ev-1",
        ts="2026-05-07T12:00:00+00:00",  # type: ignore[arg-type]
        actor=Actor(kind="agent", id="a-1"),
        action="policy.deny",
        resource_type="entry",
        resource_id="e-1",
        scope="track:t-1",
        details={
            "failed_action": "entry.create",
            "decision_reason": "fail_closed_no_policy",
        },
    )
    assert resp.action == "policy.deny"
    assert resp.details == {
        "failed_action": "entry.create",
        "decision_reason": "fail_closed_no_policy",
    }


def test_change_event_response_rejects_unknown_action():
    """Pydantic Literal enforcement on ChangeEventResponse.action."""
    with pytest.raises(ValidationError):
        ChangeEventResponse(
            id="ev-x",
            ts="2026-05-07T12:00:00+00:00",  # type: ignore[arg-type]
            actor=Actor(kind="agent", id="a-1"),
            action="not.in.literal",  # type: ignore[arg-type]
            resource_type="entry",
            resource_id="e-1",
            scope="track:t-1",
        )


def test_emit_change_event_signature_has_details_and_internal_actor():
    """D-10 + Plan 05 contract: emit_change_event exposes additive details and
    reserved _internal_actor kwargs; both are KEYWORD_ONLY and default to None.
    """
    sig = inspect.signature(emit_change_event)
    assert "details" in sig.parameters, "details kwarg missing on emit_change_event"
    assert (
        "_internal_actor" in sig.parameters
    ), "_internal_actor kwarg missing on emit_change_event (D-10)"
    assert sig.parameters["details"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["details"].default is None
    assert sig.parameters["_internal_actor"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["_internal_actor"].default is None


@pytest.mark.asyncio
async def test_emit_change_event_accepts_details_kwarg():
    """Plan 05 additive: emit_change_event accepts details kwarg; persists to
    DBLog.log_data["details"]; round-trips through envelope_from_dblog.
    """
    envelope = await emit_change_event(
        actor_kind="agent",
        actor_id="a-emit-1",
        action="policy.deny",
        resource_type="entry",
        resource_id="e-emit-1",
        before=None,
        after=None,
        scope="track:t-1",
        details={
            "failed_action": "entry.create",
            "decision_reason": "fail_closed_no_policy",
            "matched_policy_id": None,
        },
    )
    assert envelope.id
    row = await get_change_event_logger().get(envelope.id)
    assert row is not None
    reloaded = envelope_from_dblog(row)
    assert reloaded.details == {
        "failed_action": "entry.create",
        "decision_reason": "fail_closed_no_policy",
        "matched_policy_id": None,
    }
    assert reloaded.action == "policy.deny"


@pytest.mark.asyncio
async def test_emit_change_event_without_details_phase_2_backcompat():
    """Phase 2 callers omit the details kwarg; backwards compatible."""
    envelope = await emit_change_event(
        actor_kind="human",
        actor_id="u-emit-1",
        action="entry.create",
        resource_type="Entry",
        resource_id="e-bc-1",
        before=None,
        after={"id": "e-bc-1", "title": "Test"},
        scope="track:t-1",
        # NO details kwarg — Phase 2 callers
    )
    row = await get_change_event_logger().get(envelope.id)
    assert row is not None
    reloaded = envelope_from_dblog(row)
    assert reloaded.details is None


@pytest.mark.asyncio
async def test_emit_change_event_envelope_to_wire_flat_includes_details():
    """to_wire_flat (used by /api/audit-log) surfaces the details slot."""
    envelope = await emit_change_event(
        actor_kind="agent",
        actor_id="a-wire-1",
        action="policy.deny",
        resource_type="entry",
        resource_id="e-wire-1",
        before=None,
        after=None,
        scope="track:t-wire",
        details={"failed_action": "entry.update", "decision_reason": "scope_mismatch"},
    )
    flat = envelope.to_wire_flat()
    assert "details" in flat
    assert flat["details"] == {
        "failed_action": "entry.update",
        "decision_reason": "scope_mismatch",
    }


@pytest.mark.asyncio
async def test_emit_change_event_internal_actor_not_persisted():
    """D-10 invariant: _internal_actor kwarg is in-process control flow ONLY.

    Even when callers pass _internal_actor, it MUST NOT leak into the persisted
    DBLog row's log_data. Verifies the explicit no-op acknowledgment in
    emit_change_event holds.
    """
    from app.schemas.policy import Subject

    envelope = await emit_change_event(
        actor_kind="agent",
        actor_id="a-ia-persist-test",
        action="policy.deny",
        resource_type="entry",
        resource_id="e-ia-persist",
        before=None,
        after=None,
        scope="track:t-1",
        details={
            "failed_action": "entry.create",
            "decision_reason": "fail_closed_no_policy",
        },
        _internal_actor=Subject(kind="system", id="policy_engine"),
    )
    row = await get_change_event_logger().get(envelope.id)
    assert row is not None
    log_data = getattr(row, "log_data", {}) or {}
    # The kwarg name MUST NOT appear in the persisted log_data
    assert "_internal_actor" not in log_data
    # And no other key carrying a Subject-shaped value
    for v in log_data.values():
        if (
            isinstance(v, dict)
            and v.get("kind") == "system"
            and v.get("id") == "policy_engine"
        ):
            raise AssertionError(
                f"_internal_actor leaked into persisted log_data: {log_data}"
            )


def test_actor_kind_literal_unchanged():
    """D-10: ActorKind Literal members are stable across Phase 3."""
    assert set(get_args(ActorKind)) == {"human", "agent", "connector", "system"}


def test_phase_2_d_05_grep_gate_still_passes():
    """Plan 05 adds new emit call sites in policy_engine.py (a service module,
    not a handler). The Phase 2 D-05 grep gate scans handlers only — running it
    here proves the new emit site does NOT regress that invariant.
    """
    result = subprocess.run(
        ["pytest", "tests/test_change_event_no_bypass.py", "-v"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "backend"),
    )
    # The suite has a pre-existing baseline failure unrelated to Plan 05
    # (test_no_bypass_paths_in_core_api — ~50 handlers missing emit; logged in
    # deferred-items.md by Plans 03-01..03). We assert that the failure list
    # does NOT contain anything under app/services/policy_engine.py (the only
    # file Plan 05 modifies in the scan scope is a SERVICE module, not a
    # handler — so it should not appear in the scan output at all).
    combined = result.stdout + result.stderr
    assert (
        "app/services/policy_engine.py" not in combined
    ), f"Plan 05 introduced a handler-shaped violation:\n{combined}"
    # Plan 05 must not introduce NEW handler-side violations; the
    # test_allow_list_files_exist (second test in the file) MUST still pass.
    assert (
        "test_allow_list_files_exist PASSED" in combined
        or "1 passed" in combined
        or "2 passed" in combined
    ), f"test_allow_list_files_exist regressed:\n{combined}"


# ===== Section B — Denial emit path + recursion guard + structured logging =====


async def _find_deny_events(actor_id: str, action_filter: str = "policy.deny"):
    """Test helper: find policy.deny DBLog rows for a given actor_id."""
    ce_logger = get_change_event_logger()
    rows = await ce_logger.find_all(actor_kind=None)
    matching = []
    for r in rows:
        env = envelope_from_dblog(r)
        if env.action == action_filter and env.actor_id == actor_id:
            matching.append(env)
    return matching


@pytest.mark.asyncio
async def test_engine_denial_emits_policy_deny_event():
    """Every Decision(allowed=False) emits a policy.deny ChangeEvent via the
    single emission helper.
    """
    from app.middleware.permissions_cache import reset_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services import policy_engine as pe

    reset_permissions_cache()
    decision = await pe.evaluate(
        subject=Subject(kind="agent", id="a-deny-1"),
        action="entry.create",
        resource=Resource(kind="entry", id="e-deny-1", scope="track:t-deny-1"),
    )
    assert decision.allowed is False

    matching = await _find_deny_events("a-deny-1")
    assert matching, "policy.deny ChangeEvent for the denied evaluation did not land"


@pytest.mark.asyncio
async def test_engine_denial_event_payload_shape():
    """Denial event carries the canonical actor / resource / details fields
    per CONTEXT specifics.
    """
    from app.middleware.permissions_cache import reset_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services import policy_engine as pe

    reset_permissions_cache()
    await pe.evaluate(
        subject=Subject(kind="agent", id="a-shape-1"),
        action="entry.update",
        resource=Resource(kind="entry", id="e-shape-1", scope="track:t-shape"),
    )

    matching = await _find_deny_events("a-shape-1")
    assert matching
    ev = matching[0]

    assert ev.actor_kind == "agent"
    assert ev.actor_id == "a-shape-1"
    assert ev.action == "policy.deny"
    assert ev.resource_type == "entry"
    assert ev.resource_id == "e-shape-1"
    assert ev.scope == "track:t-shape"
    assert ev.before is None
    assert ev.after is None
    assert ev.details is not None
    assert ev.details["failed_action"] == "entry.update"
    assert ev.details["decision_reason"]  # non-empty string
    # matched_policy_id is None when fail-closed-no-policy
    assert "matched_policy_id" in ev.details


@pytest.mark.asyncio
async def test_engine_allow_does_not_emit():
    """Allow decisions do NOT emit a ChangeEvent (v1 — denials only)."""
    from app.middleware.permissions_cache import reset_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services import policy_engine as pe

    reset_permissions_cache()

    pre = await _find_deny_events("sys-allow-1")
    pre_count = len(pre)

    decision = await pe.evaluate(
        subject=Subject(kind="system", id="sys-allow-1"),
        action="audit_log.read",
        resource=Resource(kind="change_event", id="*", scope="*"),
    )
    assert decision.allowed is True

    post = await _find_deny_events("sys-allow-1")
    assert len(post) == pre_count, "Allow decision must NOT emit a policy.deny event"


@pytest.mark.asyncio
async def test_engine_recursion_guard_kwarg_short_circuits():
    """Plan 01 D-10 contract: _internal_actor=Subject(kind='system') forces
    allow regardless of subject identity (the recursion guard).
    """
    from app.middleware.permissions_cache import reset_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services import policy_engine as pe

    reset_permissions_cache()
    decision = await pe.evaluate(
        subject=Subject(kind="agent", id="a-guarded"),  # would normally fail-closed
        action="anything",
        resource=Resource(kind="change_event", id="*", scope="*"),
        _internal_actor=Subject(kind="system", id="policy_engine"),
    )
    assert decision.allowed is True
    assert decision.reason == "system_subject_internal"


@pytest.mark.asyncio
async def test_engine_no_infinite_recursion_on_denial():
    """Denial path does NOT re-enter evaluate. If the recursion guard or
    call-chain is broken, the test hangs — we cap with asyncio.wait_for at 5s.

    Success: a single denial completes in < 5 seconds and exactly one
    policy.deny event lands for the synthetic actor id.
    """
    import asyncio

    from app.middleware.permissions_cache import reset_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services import policy_engine as pe

    reset_permissions_cache()

    async def run_one_denial():
        await pe.evaluate(
            subject=Subject(kind="agent", id="a-loop-test"),
            action="entry.create",
            resource=Resource(kind="entry", id="e-loop", scope="track:t-loop"),
        )

    await asyncio.wait_for(run_one_denial(), timeout=5.0)

    matching = await _find_deny_events("a-loop-test")
    assert (
        len(matching) == 1
    ), f"Expected exactly 1 policy.deny event for a-loop-test, got {len(matching)}"


@pytest.mark.asyncio
async def test_engine_structured_log_on_denial(caplog):
    """POL-04 observability: every denial emits exactly one structured
    'policy_engine.deny' log record from the app.services.policy_engine logger
    with the canonical extra fields.
    """
    from app.middleware.permissions_cache import reset_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services import policy_engine as pe

    caplog.set_level(logging.INFO, logger="app.services.policy_engine")

    reset_permissions_cache()
    with caplog.at_level(logging.INFO, logger="app.services.policy_engine"):
        await pe.evaluate(
            subject=Subject(kind="agent", id="a-log-1"),
            action="entry.create",
            resource=Resource(kind="entry", id="e-log-1", scope="track:t-1"),
        )

    # Strict logger-name filter — exactly one record from policy_engine module
    # with the canonical message.
    deny_logs = [
        r
        for r in caplog.records
        if r.name == "app.services.policy_engine" and r.message == "policy_engine.deny"
    ]
    assert len(deny_logs) == 1, (
        f"expected exactly 1 'policy_engine.deny' log from policy_engine module, "
        f"got {len(deny_logs)}. Records: "
        f"{[(r.name, r.message) for r in caplog.records[:10]]}"
    )
    rec = deny_logs[0]
    assert getattr(rec, "subject_kind", None) == "agent"
    assert getattr(rec, "subject_id", None) == "a-log-1"
    assert getattr(rec, "action", None) == "entry.create"
    assert getattr(rec, "resource_kind", None) == "entry"
    matched_id = getattr(rec, "matched_policy_id", "<missing>")
    assert matched_id is None or isinstance(matched_id, str)


@pytest.mark.asyncio
async def test_plan_01_recursion_guard_test_still_passes():
    """Plan 01 Test 21 (test_recursion_guard_short_circuits) continues to pass
    after Plan 05.
    """
    result = subprocess.run(
        [
            "pytest",
            "tests/test_policy_engine.py::test_recursion_guard_short_circuits",
            "-v",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "backend"),
    )
    assert result.returncode == 0, (
        f"Plan 01 recursion-guard test failed after Plan 05 changes:\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )


@pytest.mark.asyncio
async def test_denial_emit_passes_internal_actor(monkeypatch):
    """D-10 enforcement: the engine's denial-emit MUST forward
    _internal_actor=Subject(kind='system', id='policy_engine') to
    emit_change_event. Any future change that drops the kwarg fails here.
    """
    captured_kwargs: dict = {}

    async def stub_emit(**kwargs):
        captured_kwargs.update(kwargs)

        class _Sentinel:
            id = "stub-event-id"

        return _Sentinel()

    monkeypatch.setattr("app.services.policy_engine.emit_change_event", stub_emit)

    from app.middleware.permissions_cache import reset_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services import policy_engine as pe

    reset_permissions_cache()
    decision = await pe.evaluate(
        subject=Subject(kind="agent", id="a-internal-actor-test"),
        action="entry.create",
        resource=Resource(kind="entry", id="e-ia-1", scope="track:t-ia"),
    )
    assert decision.allowed is False

    assert (
        "_internal_actor" in captured_kwargs
    ), "D-10 regression — denial-emit did NOT pass _internal_actor to emit_change_event"
    ia = captured_kwargs["_internal_actor"]
    assert ia is not None
    assert ia.kind == "system"
    assert ia.id == "policy_engine"
    # And the deny payload must include details
    assert captured_kwargs.get("action") == "policy.deny"
    assert captured_kwargs.get("details") == {
        "failed_action": "entry.create",
        "decision_reason": "fail_closed_no_policy",
        "matched_policy_id": None,
    }


@pytest.mark.asyncio
async def test_policy_deny_does_not_trigger_ws_broadcast(monkeypatch):
    """T-03-05-E01 broadcast-fan-out safeguard (Plan 05 deviation): policy.deny
    events persist to the DBLog row but do NOT trigger WS broadcast.

    Without this skip, a per-subscriber permission check that returns
    Decision(allowed=False) inside _user_permitted_for_scope would re-enter
    broadcast_change_event with the denial event, producing O(N^2) broadcast
    amplification. The skip preserves audit visibility (GET /api/audit-log
    still surfaces the DBLog row) and breaks the cascade. Verified by
    monkeypatching broadcast_change_event to record invocations.
    """
    broadcast_calls: list = []

    async def stub_broadcast(envelope):
        broadcast_calls.append((envelope.action, envelope.actor_id))

    # Patch the function the emit module imports lazily — emit_change_event
    # does `from app.services.event_subscription_registry import broadcast_change_event`
    # inside its body, so the target lives on that module.
    monkeypatch.setattr(
        "app.services.event_subscription_registry.broadcast_change_event",
        stub_broadcast,
    )

    # Emit a normal entry.create — broadcast MUST be called.
    await emit_change_event(
        actor_kind="human",
        actor_id="u-broadcast-check",
        action="entry.create",
        resource_type="Entry",
        resource_id="e-broadcast-1",
        before=None,
        after={"id": "e-broadcast-1"},
        scope="track:t-broadcast",
    )
    normal_calls = [
        c for c in broadcast_calls if c == ("entry.create", "u-broadcast-check")
    ]
    assert (
        len(normal_calls) == 1
    ), f"normal entry.create event should broadcast: {broadcast_calls}"

    # Emit a policy.deny — broadcast MUST be skipped.
    broadcast_calls.clear()
    await emit_change_event(
        actor_kind="agent",
        actor_id="a-deny-no-broadcast",
        action="policy.deny",
        resource_type="entry",
        resource_id="e-deny-bc",
        before=None,
        after=None,
        scope="track:t-deny-bc",
        details={
            "failed_action": "entry.create",
            "decision_reason": "fail_closed_no_policy",
        },
    )
    deny_calls = [c for c in broadcast_calls if c[0] == "policy.deny"]
    assert deny_calls == [], (
        f"policy.deny events MUST NOT broadcast (T-03-05-E01 cascade safeguard); "
        f"got: {deny_calls}"
    )


@pytest.mark.asyncio
async def test_engine_denial_survives_emit_failure(monkeypatch):
    """T-03-05-R01 mitigation: if the audit-emit raises, the engine STILL
    returns the denial Decision (NEVER flips deny→allow). The exception is
    logged as a warning and swallowed.
    """

    async def boom_emit(**kwargs):
        raise RuntimeError("simulated audit-write failure")

    monkeypatch.setattr("app.services.policy_engine.emit_change_event", boom_emit)

    from app.middleware.permissions_cache import reset_permissions_cache
    from app.schemas.policy import Resource, Subject
    from app.services import policy_engine as pe

    reset_permissions_cache()
    decision = await pe.evaluate(
        subject=Subject(kind="agent", id="a-emit-boom"),
        action="entry.create",
        resource=Resource(kind="entry", id="e-boom", scope="track:t-boom"),
    )
    # Crucially — the denial is still returned. NEVER flipped to allow.
    assert decision.allowed is False
    assert decision.reason  # non-empty reason preserved

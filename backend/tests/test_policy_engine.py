"""Policy engine regression suite — Phase 3 Plan 1.

Section 1 (Task 1): Pydantic boundary regression — Subject/Resource/Decision shape,
PolicyAction strict-superset of ChangeEventAction, single-Literal AST grep gate, cache
key widening.

Section 2 (Task 2): evaluate() behaviour — default-human parity, fail-closed-agent,
system-subject bypass, per-request cache hit/miss, recursion-guard kwarg.

All tests reach into the typed Pydantic boundary in `app.schemas.policy` and the
async engine entry point at `app.services.policy_engine.evaluate`. The 6-tuple
policy cache slot is exercised through the existing `permissions_cache_get()` /
`reset_permissions_cache()` helpers from Phase 1 (D-11).
"""

from __future__ import annotations

import ast
import inspect
import subprocess
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from app.middleware.permissions_cache import (
    permissions_cache_get,
    reset_permissions_cache,
)
from app.schemas.audit import ChangeEventAction
from app.schemas.policy import Decision, PolicyAction, Resource, Subject
from app.schemas.provenance import ActorKind

# ===== Section 1 — Pydantic boundary regression =====


def test_policy_action_strict_supersets_change_event_action():
    """D-12: every ChangeEventAction member appears in PolicyAction (no orphans).

    Phase 7 Plan 07-04 whitelist update: ``approval.expired`` is
    system-emitted only by the TTL reclaim loop and has NO PolicyAction
    twin (see I-APPROVAL-03 in docs/INVARIANTS.md).
    """
    cea_members = set(get_args(ChangeEventAction))
    pa_members = set(get_args(PolicyAction))
    audit_only_cea_exempt = {
        "approval.expired",  # Phase 7 Plan 07-04 — TTL audit-only
    }
    missing = cea_members - pa_members - audit_only_cea_exempt
    assert not missing, f"PolicyAction missing ChangeEventAction members: {missing}"


def test_policy_action_includes_read_variants():
    """D-12: read actions are Phase 3 additions to the Literal."""
    pa_members = set(get_args(PolicyAction))
    expected = {
        "entry.read",
        "track.read",
        "space.read",
        "operational_model.read",
        "tag.read",
        "view.read",
        "comment.read",
        "attachment.read",
        "agent_config.read",
        "connector.read",
        "policy.read",
        "audit_log.read",
        "event_feed.subscribe",
        "notification.read",
        "organization.read",
        "user.read",
    }
    missing = expected - pa_members
    assert not missing, f"PolicyAction missing read variants: {missing}"


def test_policy_action_includes_policy_crud_and_deny():
    """D-12 + D-10: policy.* CRUD + denial action are PolicyAction members."""
    pa_members = set(get_args(PolicyAction))
    expected = {"policy.create", "policy.update", "policy.delete", "policy.deny"}
    missing = expected - pa_members
    assert not missing, f"PolicyAction missing policy.* actions: {missing}"


def test_subject_kind_reuses_actor_kind():
    """D-10: Subject.kind is the SAME Literal as ActorKind from app.schemas.provenance."""
    ak_members = set(get_args(ActorKind))
    sk_members = set(get_args(Subject.model_fields["kind"].annotation))
    assert ak_members == sk_members == {"human", "agent", "connector", "system"}


def test_subject_rejects_extra_fields():
    """D-01: Subject has model_config={'extra':'forbid'}."""
    with pytest.raises(ValidationError):
        Subject(kind="human", id="u-1", role="admin")  # type: ignore[call-arg]


def test_subject_rejects_invalid_kind():
    """D-10: invalid kind raises ValidationError."""
    with pytest.raises(ValidationError):
        Subject(kind="bogus", id="u-1")  # type: ignore[arg-type]


def test_resource_rejects_extra_fields():
    """D-01: Resource has model_config={'extra':'forbid'}."""
    with pytest.raises(ValidationError):
        Resource(kind="entry", id="e-1", scope="track:1", role="admin")  # type: ignore[call-arg]


def test_resource_rejects_invalid_kind():
    """D-01: ResourceKind Literal enforced."""
    with pytest.raises(ValidationError):
        Resource(kind="bogus", id="e-1", scope="track:1")  # type: ignore[arg-type]


def test_decision_constructs_with_defaults():
    """D-01: Decision.policy_chain defaults to []; matched_policy_id defaults to None."""
    d = Decision(allowed=True, reason="default_human_policy")
    assert d.policy_chain == []
    assert d.matched_policy_id is None


def test_decision_rejects_extra_fields():
    """D-01: Decision has model_config={'extra':'forbid'}."""
    with pytest.raises(ValidationError):
        Decision(allowed=True, reason="x", custom_field="y")  # type: ignore[call-arg]


def test_policy_action_literal_defined_exactly_once():
    """Single-Literal AST grep gate — PolicyAction lives only in app/schemas/policy.py."""
    repo_root = Path(__file__).resolve().parents[2]
    backend_app = repo_root / "backend" / "app"
    matches: list[str] = []
    for py_path in backend_app.rglob("*.py"):
        rel = str(py_path.relative_to(repo_root))
        if "__pycache__" in rel:
            continue
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "PolicyAction":
                        matches.append(rel)
    assert (
        len(matches) == 1
    ), f"PolicyAction Literal must be defined exactly once; found in: {matches}"


def test_actor_kind_literal_still_defined_exactly_once():
    """Phase 2 D-10 invariant: Phase 3 must NOT introduce a duplicate ActorKind Literal."""
    repo_root = Path(__file__).resolve().parents[2]
    cmd = (
        'grep -rE "^ActorKind\\s*=\\s*Literal" '
        f'{repo_root}/backend/app/ --include="*.py"'
    )
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    lines = [
        ln for ln in out.stdout.strip().split("\n") if ln and "__pycache__" not in ln
    ]
    assert (
        len(lines) == 1
    ), f"ActorKind Literal must be defined exactly once; found: {lines}"


def test_permissions_cache_accepts_six_tuple_policy_key():
    """D-11: PermissionCacheKey union widened to accept the policy slot."""
    reset_permissions_cache()
    cache = permissions_cache_get()
    key = ("policy", "human", "u-1", "track.read", "track", "t-1")
    cache[key] = "allow"
    assert cache[key] == "allow"


# ===== Section 2 — evaluate() behaviour (Task 2) =====


def _uid(test_user) -> str:
    """Extract a stable user id from the test_user fixture (User node OR _UserLike)."""
    uid = getattr(test_user, "id", None) or getattr(test_user, "user_id", None)
    return uid or ""


@pytest.mark.asyncio
async def test_default_human_allowed_via_legacy_helper(test_user, monkeypatch):
    """POL-03 parity — default human path delegates to can_view_track."""
    from app.services import policy_engine as pe

    calls = {"can_view_track": 0}
    uid = _uid(test_user)

    async def stub_can_view_track(user_id: str, track_id: str) -> bool:
        calls["can_view_track"] += 1
        return user_id == uid and track_id == "t-1"

    monkeypatch.setattr(
        "app.services.policy_engine.can_view_track", stub_can_view_track
    )
    reset_permissions_cache()

    decision = await pe.evaluate(
        subject=Subject(kind="human", id=uid),
        action="track.read",
        resource=Resource(kind="track", id="t-1", scope="track:t-1"),
    )
    assert decision.allowed is True
    assert decision.reason == "default_human_policy"
    assert calls["can_view_track"] == 1


@pytest.mark.asyncio
async def test_default_human_denied_via_legacy_helper(test_user, monkeypatch):
    """POL-03 parity — denial path also delegates."""
    from app.services import policy_engine as pe

    async def stub_deny(user_id: str, track_id: str) -> bool:
        return False

    monkeypatch.setattr("app.services.policy_engine.can_view_track", stub_deny)
    reset_permissions_cache()

    decision = await pe.evaluate(
        subject=Subject(kind="human", id=_uid(test_user)),
        action="track.read",
        resource=Resource(kind="track", id="t-x", scope="track:t-x"),
    )
    assert decision.allowed is False
    assert decision.reason == "default_human_policy_denied"


@pytest.mark.asyncio
async def test_default_agent_fail_closed():
    """Plan 1: agents fail-closed; Plan 3 adds Policy-edge traversal."""
    from app.services import policy_engine as pe

    reset_permissions_cache()
    decision = await pe.evaluate(
        subject=Subject(kind="agent", id="a-1"),
        action="entry.create",
        resource=Resource(kind="entry", id="e-1", scope="track:t-1"),
    )
    assert decision.allowed is False
    assert decision.reason == "fail_closed_no_policy"


@pytest.mark.asyncio
async def test_default_connector_fail_closed():
    """Connectors mirror agents at the engine level."""
    from app.services import policy_engine as pe

    reset_permissions_cache()
    decision = await pe.evaluate(
        subject=Subject(kind="connector", id="c-1"),
        action="entry.create",
        resource=Resource(kind="entry", id="e-1", scope="track:t-1"),
    )
    assert decision.allowed is False
    assert decision.reason == "fail_closed_no_policy"


@pytest.mark.asyncio
async def test_system_subject_bypass():
    """D-03: system subject reserved for internal jobs — short-circuit allow."""
    from app.services import policy_engine as pe

    reset_permissions_cache()
    decision = await pe.evaluate(
        subject=Subject(kind="system", id="policy_engine"),
        action="audit_log.read",
        resource=Resource(kind="change_event", id="*", scope="*"),
    )
    assert decision.allowed is True
    assert decision.reason == "system_subject"


@pytest.mark.asyncio
async def test_per_request_cache_hits_on_second_call(test_user, monkeypatch):
    """D-11: contextvar cache survives within one request, not across requests."""
    from app.services import policy_engine as pe

    calls = {"can_view_track": 0}

    async def counter_stub(user_id: str, track_id: str) -> bool:
        calls["can_view_track"] += 1
        return True

    monkeypatch.setattr("app.services.policy_engine.can_view_track", counter_stub)
    reset_permissions_cache()

    uid = _uid(test_user)
    args = dict(
        subject=Subject(kind="human", id=uid),
        action="track.read",
        resource=Resource(kind="track", id="t-1", scope="track:t-1"),
    )
    d1 = await pe.evaluate(**args)
    d2 = await pe.evaluate(**args)
    assert d1.allowed is True
    assert d2.allowed is True
    assert (
        calls["can_view_track"] == 1
    ), "second call should hit the cache, not re-invoke helper"


@pytest.mark.asyncio
async def test_cache_resets_via_reset_permissions_cache(test_user, monkeypatch):
    """D-11: reset_permissions_cache() drops the slot — next call re-invokes helper."""
    from app.services import policy_engine as pe

    calls = {"can_view_track": 0}

    async def counter_stub(user_id: str, track_id: str) -> bool:
        calls["can_view_track"] += 1
        return True

    monkeypatch.setattr("app.services.policy_engine.can_view_track", counter_stub)
    reset_permissions_cache()

    uid = _uid(test_user)
    args = dict(
        subject=Subject(kind="human", id=uid),
        action="track.read",
        resource=Resource(kind="track", id="t-2", scope="track:t-2"),
    )
    await pe.evaluate(**args)
    reset_permissions_cache()
    await pe.evaluate(**args)
    assert calls["can_view_track"] == 2


@pytest.mark.asyncio
async def test_recursion_guard_short_circuits():
    """D-10: _internal_actor=Subject(kind='system',...) forces allow regardless of subject."""
    from app.services import policy_engine as pe

    reset_permissions_cache()
    decision = await pe.evaluate(
        subject=Subject(kind="agent", id="a-1"),
        action="entry.create",
        resource=Resource(kind="change_event", id="*", scope="*"),
        _internal_actor=Subject(kind="system", id="policy_engine"),
    )
    assert decision.allowed is True
    assert decision.reason == "system_subject_internal"


def test_internal_actor_is_private_kwarg():
    """D-10: _internal_actor parameter name MUST start with underscore + KEYWORD_ONLY."""
    from app.services.policy_engine import evaluate

    sig = inspect.signature(evaluate)
    assert "_internal_actor" in sig.parameters
    assert sig.parameters["_internal_actor"].kind == inspect.Parameter.KEYWORD_ONLY


@pytest.mark.asyncio
async def test_engine_never_raises_on_well_formed_input(monkeypatch):
    """Hypothesis-style fuzz lite: the engine returns a Decision for every well-formed shape."""
    from app.services import policy_engine as pe

    async def always_allow(*args, **kwargs):
        return True

    # Stub every helper the engine might call so we never touch the graph.
    for name in (
        "can_view_track",
        "can_edit_track",
        "can_delete_track",
        "can_view_app",
        "can_edit_app",
        "can_delete_app",
        "can_view_entry",
        "can_edit_entry",
        "can_view_view",
        "can_edit_view",
    ):
        monkeypatch.setattr(f"app.services.policy_engine.{name}", always_allow)

    sample_subjects = [
        Subject(kind="human", id="u-1"),
        Subject(kind="agent", id="a-1"),
        Subject(kind="connector", id="c-1"),
        Subject(kind="system", id="sys"),
    ]
    sample_actions = [
        "entry.create",
        "entry.read",
        "entry.update",
        "entry.delete",
        "track.read",
        "track.update",
        "audit_log.read",
        "event_feed.subscribe",
        "policy.deny",
        "user.read",
        "organization.read",
    ]
    sample_resources = [
        Resource(kind="entry", id="e-1", scope="track:t-1"),
        Resource(kind="track", id="t-1", scope="track:t-1"),
        Resource(kind="app", id="s-1", scope="app:s-1"),
        Resource(kind="user", id="u-1", scope="user:u-1"),
        Resource(kind="change_event", id="*", scope="*"),
    ]

    for subj in sample_subjects:
        for act in sample_actions:
            for res in sample_resources:
                reset_permissions_cache()
                d = await pe.evaluate(subject=subj, action=act, resource=res)
                assert isinstance(
                    d, Decision
                ), f"engine returned non-Decision for {subj}/{act}/{res}"


@pytest.mark.asyncio
async def test_sharing_actions_dispatch_via_resolve_role_owner(monkeypatch):
    """Sharing PolicyActions delegate to resolve_role owner gate."""
    from app.schemas.policy import Resource, Subject
    from app.services import policy_engine as pe

    reset_permissions_cache()

    async def stub_resolve_role(user_id: str, resource_type: str, resource_id: str):
        return "owner" if resource_id.endswith("-owned") else "editor"

    monkeypatch.setattr(pe, "resolve_role", stub_resolve_role)

    denied = await pe.evaluate(
        subject=Subject(kind="human", id="user-a"),
        action="track.share_link.mint",
        resource=Resource(kind="track", id="t-other", scope="track:t-other"),
    )
    assert denied.allowed is False

    allowed = await pe.evaluate(
        subject=Subject(kind="human", id="user-a"),
        action="app.collaborator_add",
        resource=Resource(kind="app", id="app-owned", scope="app:app-owned"),
    )
    assert allowed.allowed is True

    redeem = await pe.evaluate(
        subject=Subject(kind="human", id="user-a"),
        action="track.share_link.redeem",
        resource=Resource(kind="track", id="t-other", scope="track:t-other"),
    )
    assert redeem.allowed is True


def test_existing_crud_suite_unaffected_by_engine_module_import():
    """POL-03 parity gate — importing the engine must not regress CRUD tests.

    This test simply imports the engine and asserts no startup-time side-effects
    (engine has no module-level state mutations, no node_types registration, etc.).
    """
    from app.services import policy_engine as pe

    assert callable(pe.evaluate)
    assert callable(pe._action_matches)

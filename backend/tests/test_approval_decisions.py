from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.agentive.services import approval_decisions as decisions
from app.agentive.services.approval_policy import ApprovalEffectClass, effect_class
from app.agentive.staging import StagingError


def staged(**overrides):
    values = {
        "token": "proposal-1",
        "user_id": "principal-1",
        "workspace_id": "workspace-1",
        "session_id": "conversation-1",
        "kind": "create_entry",
        "summary": "Add the new equipment record",
        "diff_human": "Add serial QA-100 to Equipment",
        "payload": {"track_id": "track-1"},
        "idempotency_key": "idem-1",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "state": "pending",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_effect_policy_is_fail_closed_and_elevates_nested_batch_risk():
    assert effect_class("create_entry") is ApprovalEffectClass.PRIVATE_REVERSIBLE
    assert effect_class("delete_entry") is ApprovalEffectClass.DESTRUCTIVE_SECURITY
    assert effect_class("mcp_tool_call") is ApprovalEffectClass.MATERIAL_EXTERNAL
    assert (
        effect_class("new_unclassified_operation")
        is ApprovalEffectClass.MATERIAL_EXTERNAL
    )
    assert (
        effect_class(
            "batch", {"ops": [{"kind": "create_entry"}, {"kind": "delete_app"}]}
        )
        is ApprovalEffectClass.DESTRUCTIVE_SECURITY
    )


def test_proposal_contract_excludes_executor_payload():
    proposal = decisions.proposal_from_staged(
        staged(payload={"track_id": "track-1", "secret": "private"})
    )

    assert proposal.proposal_id == "proposal-1"
    assert proposal.principal_id == "principal-1"
    assert proposal.workspace_id == "workspace-1"
    assert proposal.conversation_id == "conversation-1"
    assert proposal.target_id == "track-1"
    assert "secret" not in proposal.model_dump()
    with pytest.raises(Exception):
        proposal.summary = "changed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope",
    [
        {"principal_id": "other-principal"},
        {"workspace_id": "other-workspace"},
        {"conversation_id": "other-conversation"},
    ],
)
async def test_scope_mismatch_fails_before_decision(monkeypatch, scope):
    monkeypatch.setattr(decisions, "get_token", lambda _token: _immediate(staged()))
    calls = []

    async def approve(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "consumed": True, "staged_change": {"state": "consumed"}}

    monkeypatch.setattr(decisions, "bless_and_execute", approve)
    args = {
        "principal_id": "principal-1",
        "workspace_id": "workspace-1",
        "conversation_id": "conversation-1",
    }
    args.update(scope)
    with pytest.raises(StagingError):
        await decisions.decide_staged_write(
            **args,
            proposal_id="proposal-1",
            decision="approve",
            source="chat",
        )
    assert calls == []


@pytest.mark.asyncio
async def test_chat_approval_resolves_one_private_pending_write(monkeypatch):
    monkeypatch.setattr(decisions, "get_token", lambda _token: _immediate(staged()))
    calls = []

    async def approve(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "consumed": True, "staged_change": {"state": "consumed"}}

    monkeypatch.setattr(decisions, "bless_and_execute", approve)
    result = await decisions.decide_staged_write(
        principal_id="principal-1",
        proposal_id="proposal-1",
        decision="approve",
        source="chat",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
    )

    assert result["ok"] is True
    assert result["proposal"]["policy_version"] == "integral-staged-write-v1"
    assert calls[0]["token"] == "proposal-1"
    assert calls[0]["user_id"] == "principal-1"
    assert calls[0]["decision_source"] == "chat"


@pytest.mark.asyncio
async def test_destructive_natural_approval_requires_card_confirmation(monkeypatch):
    monkeypatch.setattr(
        decisions,
        "get_token",
        lambda _token: _immediate(
            staged(kind="delete_entry", payload={"entry_id": "entry-1"})
        ),
    )
    calls = []

    async def approve(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "consumed": True, "staged_change": {"state": "consumed"}}

    monkeypatch.setattr(decisions, "bless_and_execute", approve)
    with pytest.raises(StagingError) as caught:
        await decisions.decide_staged_write(
            principal_id="principal-1",
            proposal_id="proposal-1",
            decision="approve",
            source="chat",
            workspace_id="workspace-1",
            conversation_id="conversation-1",
        )
    assert caught.value.code == "strong_confirmation_required"
    assert calls == []


async def _immediate(value):
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["approve all", "yes"])
async def test_text_adapter_never_fans_out_across_pending_proposals(monkeypatch, text):
    from app.agentive.api import staging as staging_api

    pending = [staged(token="proposal-1"), staged(token="proposal-2")]
    monkeypatch.setattr(staging_api, "_resolve_user", lambda _request: "principal-1")
    monkeypatch.setattr(
        staging_api, "get_pending_for_user", lambda _user: _immediate(pending)
    )

    async def decision(**kwargs):
        raise AssertionError("ambiguous text must not resolve a proposal")

    monkeypatch.setattr(decisions, "decide_staged_write", decision)
    result = await staging_api.text_approve_endpoint(
        request=object(), text=text, thread_id=""
    )

    assert result["parsed"] == 0
    assert result["reason"] == "select_one_pending_action"
    assert result["pending_count"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,state,error,allowed",
    [
        ("card", "blessed", {"code": "apply_failed"}, True),
        ("chat", "blessed", {"code": "apply_failed"}, False),
        ("card", "blessed", None, False),
        ("card", "consumed", {"code": "apply_failed"}, False),
    ],
)
async def test_only_failed_card_can_resume_blessed_proposal(
    monkeypatch, source, state, error, allowed
):
    monkeypatch.setattr(
        decisions,
        "get_token",
        lambda _: _immediate(staged(state=state, last_error=error)),
    )
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        return {"consumed": True, "staged_change": {"state": "consumed"}}

    monkeypatch.setattr(decisions, "bless_and_execute", execute)
    args = dict(
        principal_id="principal-1",
        proposal_id="proposal-1",
        decision="approve",
        source=source,
        workspace_id="workspace-1",
        conversation_id="conversation-1",
    )
    if allowed:
        result = await decisions.decide_staged_write(**args)
        assert result["ok"]
        assert calls[0]["token"] == "proposal-1"
    else:
        with pytest.raises(StagingError):
            await decisions.decide_staged_write(**args)
        assert not calls

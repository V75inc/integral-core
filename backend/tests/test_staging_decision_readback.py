"""Closed MCP proposals remain readable without reviving execution authority."""

from datetime import timedelta

import pytest

from app.agentive import staging, staging_store
from app.agentive.api.staging import token_state_endpoint


@pytest.fixture(autouse=True)
def clean_staging():
    staging._reset_for_tests()
    yield
    staging._reset_for_tests()


async def mint():
    return await staging.create_staged_change(
        user_id="u1",
        session_id=None,
        workspace_id="w1",
        kind="update_entry",
        summary="Private customer name",
        diff_human="Private record body",
        diff_machine={"private": "Private record body"},
        payload={"entry_id": "n.Entry.1", "body": "Private record body"},
    )


@pytest.mark.smoke
@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["revoked", "consumed", "expired"])
async def test_closed_state_survives_restart_without_executable_payload(
    monkeypatch, state
):
    sc = await mint()
    if state == "revoked":
        await staging.revoke_token(user_id="u1", token=sc.token)
    elif state == "consumed":
        await staging.bless_token(user_id="u1", token=sc.token)
        await staging.consume_token(user_id="u1", token=sc.token, expected_kind=sc.kind)
    else:
        sc.expires_at = sc.created_at - timedelta(seconds=1)
        await staging.get_token(sc.token)

    staging._tokens.clear()
    assert await staging.get_token(sc.token) is None
    monkeypatch.setattr("app.agentive.api.staging._resolve_user", lambda request: "u1")
    result = await token_state_endpoint(None, sc.token)
    assert result["ok"] is True
    closed = result["staged_change"]
    assert closed["state"] == state
    assert closed["decision_state_only"] is True
    assert closed["diff_machine"] == {}
    assert closed["diff_human"] == ""
    assert "Private" not in str(closed)
    assert "payload" not in closed
    assert not await staging.get_pending_for_user("u1")
    with pytest.raises(staging.StagingError) as exc:
        await staging.bless_token(user_id="u1", token=sc.token)
    assert exc.value.code == "unknown_token"


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_another_principal_cannot_read_the_closed_state(monkeypatch):
    sc = await mint()
    await staging.revoke_token(user_id="u1", token=sc.token)
    staging._tokens.clear()
    monkeypatch.setattr("app.agentive.api.staging._resolve_user", lambda request: "u2")
    result = await token_state_endpoint(None, sc.token)
    assert result == {"ok": False, "error_code": "unknown_token"}


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_expired_readback_is_removed_and_never_guessed_consumed(monkeypatch):
    sc = await mint()
    await staging.revoke_token(user_id="u1", token=sc.token)
    record_id = staging_store._decision_record_id(sc.token)
    record = await staging_store.StagingDecisionRecord.get(record_id)
    assert record is not None
    record.retained_until = (staging_store._now() - timedelta(seconds=1)).isoformat()
    await record.save()
    staging._tokens.clear()
    monkeypatch.setattr("app.agentive.api.staging._resolve_user", lambda request: "u1")
    assert await token_state_endpoint(None, sc.token) == {
        "ok": False,
        "error_code": "unknown_token",
    }
    assert await staging_store.StagingDecisionRecord.get(record_id) is None


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_new_closure_cleans_expired_state_receipts():
    first = await mint()
    await staging.revoke_token(user_id="u1", token=first.token)
    record_id = staging_store._decision_record_id(first.token)
    record = await staging_store.StagingDecisionRecord.get(record_id)
    record.retained_until = (staging_store._now() - timedelta(seconds=1)).isoformat()
    await record.save()
    second = await mint()
    await staging.revoke_token(user_id="u1", token=second.token)
    assert await staging_store.StagingDecisionRecord.get(record_id) is None


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_receipt_storage_failure_does_not_grant_or_guess_authority(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(staging_store.StagingDecisionRecord, "create", fail)
    sc = await mint()
    await staging.revoke_token(user_id="u1", token=sc.token)
    staging._tokens.clear()
    monkeypatch.setattr("app.agentive.api.staging._resolve_user", lambda request: "u1")
    assert await token_state_endpoint(None, sc.token) == {
        "ok": False,
        "error_code": "unknown_token",
    }

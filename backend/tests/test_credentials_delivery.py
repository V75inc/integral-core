"""Server-owned, atomic alternate-mailbox password setup."""

import asyncio
import hashlib
from unittest.mock import AsyncMock
from urllib.parse import unquote

import pytest

from app.models.nodes import User
from app.services import credential_delivery as delivery
from app.services.authentication import get_authentication_service

pytestmark = [pytest.mark.asyncio, pytest.mark.smoke]
CODE = "123456"


async def prepare(user, monkeypatch):
    monkeypatch.setenv("INTEGRAL_CREDENTIAL_ENC_KEY", "ab" * 32)
    monkeypatch.setattr(
        delivery,
        "_generate_otp",
        lambda: (CODE, hashlib.sha256(CODE.encode()).hexdigest()),
    )
    monkeypatch.setattr(delivery, "send_email", AsyncMock(return_value=True))
    auth = await get_authentication_service()._get_user_by_id(user.user_id)
    meta = await delivery.begin_alternate_credentials_delivery(
        user_node=user,
        recipient_email="alternate@example.com",
        account_email=auth.email,
        workspace_id="ws-1",
        workspace_name="Example",
        display_name="Test",
        welcome_message=None,
        actor_user_id="actor-1",
    )
    return meta, auth.password_hash


async def consume(user, meta, code=CODE):
    return await delivery.consume_credentials_delivery_code(
        member_user_id=user.id, intent_id=meta["intent_id"], code=code
    )


async def test_success_is_idempotent_and_never_changes_password(test_user, monkeypatch):
    meta, password_hash = await prepare(test_user, monkeypatch)
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(delivery, "send_email", send)
    assert await consume(test_user, meta) == (True, None, True)
    assert await consume(test_user, meta) == (True, None, True)
    assert send.await_count == 1
    assert "/reset-password?token=" in send.call_args.args[0].text
    assert "Temporary password" not in send.call_args.args[0].text
    auth = await get_authentication_service()._get_user_by_id(test_user.user_id)
    assert auth.password_hash == password_hash
    user = await User.get(test_user.id)
    assert delivery.delivery_status(user)["credentials_emailed"] is True
    assert "setup_token_enc" not in user.preferences["credentials_delivery"]


@pytest.mark.parametrize("failure", [False, RuntimeError("mail unavailable")])
async def test_failed_send_is_retryable_with_identical_link(
    test_user, monkeypatch, failure
):
    meta, password_hash = await prepare(test_user, monkeypatch)
    send = AsyncMock(side_effect=[failure, True])
    monkeypatch.setattr(delivery, "send_email", send)
    assert await consume(test_user, meta) == (True, None, False)
    user = await User.get(test_user.id)
    assert delivery.delivery_status(user)["credentials_emailed"] is False
    assert user.preferences["credentials_delivery"]["setup_token_enc"].startswith("v1:")
    assert await consume(test_user, meta) == (True, None, True)
    assert send.call_args_list[0].args[0].text == send.call_args_list[1].args[0].text
    auth = await get_authentication_service()._get_user_by_id(test_user.user_id)
    assert auth.password_hash == password_hash


async def test_concurrent_verification_claims_one_delivery(test_user, monkeypatch):
    meta, _ = await prepare(test_user, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()

    async def send(message):
        started.set()
        await release.wait()
        return True

    mock = AsyncMock(side_effect=send)
    monkeypatch.setattr(delivery, "send_email", mock)
    first = asyncio.create_task(consume(test_user, meta))
    try:
        await asyncio.wait_for(started.wait(), 5)
        assert await consume(test_user, meta) == (True, None, False)
    finally:
        release.set()
    assert await first == (True, None, True)
    assert mock.await_count == 1


async def test_failed_worker_lease_reuses_staged_token(test_user, monkeypatch):
    meta, _ = await prepare(test_user, monkeypatch)
    send = AsyncMock(return_value=False)
    monkeypatch.setattr(delivery, "send_email", send)
    await consume(test_user, meta)
    user = await User.get(test_user.id)
    slot = user.preferences["credentials_delivery"]
    slot.update(state="sending", lease_until="2000-01-01T00:00:00+00:00")
    await user.save()
    send.return_value = True
    assert await consume(test_user, meta) == (True, None, True)
    assert send.call_args_list[0].args[0].text == send.call_args_list[1].args[0].text


async def test_wrong_codes_have_shared_attempt_limit(test_user, monkeypatch):
    meta, _ = await prepare(test_user, monkeypatch)
    results = await asyncio.gather(
        *(consume(test_user, meta, "000000") for _ in range(7))
    )
    assert sum(code == delivery.ERR_ATTEMPTS_EXCEEDED for _, code, _ in results) == 1
    assert await consume(test_user, meta) == (False, delivery.ERR_NOT_PENDING, False)


async def test_superseding_reset_cannot_be_revived_by_delivery_retry(
    test_user, monkeypatch
):
    meta, _ = await prepare(test_user, monkeypatch)
    monkeypatch.setattr(delivery, "send_email", AsyncMock(return_value=False))
    await consume(test_user, meta)
    user = await User.get(test_user.id)
    user.preferences["reset_token"]["hash"] = "new-reset-hash"
    await user.save()
    assert await consume(test_user, meta) == (False, delivery.ERR_NOT_PENDING, False)


async def test_profile_reads_and_writes_protect_delivery_state(
    test_user, authenticated_client, monkeypatch
):
    meta, _ = await prepare(test_user, monkeypatch)
    original = (await User.get(test_user.id)).preferences["credentials_delivery"]
    for url in ["/api/auth/me", f"/api/users/{test_user.id}"]:
        response = await authenticated_client.get(url)
        assert response.status_code == 200, response.text
        assert original["hash"] not in response.text
        assert meta["intent_id"] not in response.text
    for url in ["/api/auth/update-profile", f"/api/users/{test_user.id}"]:
        response = await authenticated_client.put(
            url,
            json={
                "preferences": {
                    "credentials_delivery": {"hash": "forged"},
                    "credentials_delivery_released": {"at": "fake"},
                    "theme": "dark",
                }
            },
        )
        assert response.status_code == 200, response.text
        user = await User.get(test_user.id)
        assert user.preferences["credentials_delivery"] == original
        assert "credentials_delivery_released" not in user.preferences
        assert original["hash"] not in response.text


async def test_setup_link_uses_existing_single_use_password_reset(
    test_user, monkeypatch
):
    import re

    from app.services.password_reset import consume_reset_token

    meta, _ = await prepare(test_user, monkeypatch)
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(delivery, "send_email", send)
    assert await consume(test_user, meta) == (True, None, True)
    token = unquote(
        re.search(r"reset-password\?token=([^\s]+)", send.call_args.args[0].text).group(
            1
        )
    )
    ok, _ = await consume_reset_token(token, "New-Password-For-Test-138!")
    assert ok
    ok, _ = await consume_reset_token(token, "Another-Password-Test-138!")
    assert not ok


async def test_event_projection_scrubs_delivery_slots():
    from app.services.event_access import scrub_event_credentials

    assert scrub_event_credentials(
        {
            "context": {
                "preferences": {
                    "credentials_delivery": {"hash": "hidden"},
                    "credentials_delivery_released": {"at": "hidden"},
                    "theme": "dark",
                }
            }
        }
    ) == {"context": {"preferences": {"theme": "dark"}}}


@pytest.mark.postgres
async def test_independent_workers_share_one_delivery_claim(
    test_user, monkeypatch, tmp_path
):
    """Separate processes must fence on shared storage, not an asyncio lock."""
    import os
    import sys
    from pathlib import Path

    meta, _ = await prepare(test_user, monkeypatch)
    trace = tmp_path / "dispatches.txt"
    worker = r"""
import asyncio, json, os, sys
from jvspatial.db.factory import create_database
from jvspatial.db import get_database_manager
from jvspatial.core.context import GraphContext, scoped_default_context_async
from app.services import credential_delivery as service
async def main():
    db = create_database(db_type="postgres", dsn=os.environ["JVSPATIAL_POSTGRES_DSN"])
    get_database_manager().set_prime_database(db)
    async def send(message):
        with open(sys.argv[3], "a") as f: f.write("send\n")
        await asyncio.sleep(2)
        return True
    service.send_email = send
    try:
        async with scoped_default_context_async(GraphContext(database=db)):
            result = await service.consume_credentials_delivery_code(member_user_id=sys.argv[1], intent_id=sys.argv[2], code="123456")
            print("RESULT:"+json.dumps(result))
    finally:
        await db.close()
asyncio.run(main())
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    processes = await asyncio.gather(
        *(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                worker,
                test_user.id,
                meta["intent_id"],
                str(trace),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            for _ in range(3)
        )
    )
    outcomes = await asyncio.gather(*(p.communicate() for p in processes))
    for process, (out, err) in zip(processes, outcomes):
        assert process.returncode == 0, err.decode()
        assert b"RESULT:[true, null," in out, out.decode()
    assert trace.read_text().splitlines() == ["send"]


async def test_challenge_failure_can_retry_fixed_recipient_without_resetting_attempts(
    test_user, monkeypatch
):
    meta, _ = await prepare(test_user, monkeypatch)
    user = await User.get(test_user.id)
    slot = user.preferences["credentials_delivery"]
    original_hash = slot["hash"]
    original_expiry = slot["expires_at"]
    slot["challenge_last_attempt"] = "2000-01-01T00:00:00+00:00"
    slot["attempts"] = 2
    await user.save()
    send = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(delivery, "send_email", send)
    kwargs = {"member_user_id": user.id, "intent_id": meta["intent_id"]}
    assert not await delivery.resend_credentials_delivery_code(**kwargs)
    assert not await delivery.resend_credentials_delivery_code(**kwargs)  # throttle
    user = await User.get(user.id)
    user.preferences["credentials_delivery"][
        "challenge_last_attempt"
    ] = "2000-01-01T00:00:00+00:00"
    await user.save()
    assert await delivery.resend_credentials_delivery_code(**kwargs)
    assert not await delivery.resend_credentials_delivery_code(**kwargs)  # cap
    after = (await User.get(user.id)).preferences["credentials_delivery"]
    assert after["hash"] == original_hash
    assert after["expires_at"] == original_expiry
    assert after["attempts"] == 2
    assert send.call_args_list[0].args[0].text == send.call_args_list[1].args[0].text
    assert send.call_args.args[0].to == "alternate@example.com"


async def test_delivery_invalidated_after_password_change(test_user, monkeypatch):
    meta, _ = await prepare(test_user, monkeypatch)
    auth = await get_authentication_service()._get_user_by_id(test_user.user_id)
    auth.password_hash = "new-password-hash"
    await auth.save()
    assert await consume(test_user, meta) == (False, delivery.ERR_NOT_PENDING, False)


async def test_untrusted_legacy_slot_requires_reissue(test_user, monkeypatch):
    meta, _ = await prepare(test_user, monkeypatch)
    user = await User.get(test_user.id)
    user.preferences["credentials_delivery"].pop("version")
    await user.save()
    assert await consume(test_user, meta) == (False, delivery.ERR_NOT_PENDING, False)


@pytest.mark.parametrize(
    "source",
    ["password_reset", "email_verification", "credentials_delivery_verification"],
)
async def test_auth_mail_never_leaks_to_workspace_hooks(monkeypatch, source):
    from types import SimpleNamespace

    from app.services.email_service import EmailMessage, _dispatch_workspace_email_hooks

    hook = AsyncMock()
    monkeypatch.setattr(
        "app.services.hooks.email_sent_runtime.run_email_sent_hooks", hook
    )
    message = EmailMessage(
        to="mail@example.com",
        subject="Authentication",
        text="secret",
        html="secret",
        workspace_id="workspace",
        actor_user_id="actor",
        source_kind=source,
    )
    await _dispatch_workspace_email_hooks(
        message,
        "console",
        SimpleNamespace(from_email="sender@example.com", from_name="Platform"),
        SimpleNamespace(success=True, error=None, provider_message_id=None),
        None,
        reply_to=None,
    )
    hook.assert_not_called()

"""Lifecycle guarantees for deferred steering resume baselines."""

import uuid

import pytest

from app.agentive.work_models import WorkItem
from app.services.chat_turn_submissions import submit_chat_turn
from tests.contract.test_chat_turn_submission_postgres import (
    _request,
    _submission_context,
)


@pytest.fixture
def postgres_graph_context(postgres_raw_db):
    """Bind this test to its isolated PostgreSQL graph."""
    from jvspatial.core.context import (
        GraphContext,
        _default_context_var,
        set_default_context,
    )

    token = set_default_context(GraphContext(database=postgres_raw_db))
    try:
        yield
    finally:
        _default_context_var.reset(token)


@pytest.mark.contract
@pytest.mark.postgres
@pytest.mark.asyncio
async def test_steering_resume_baseline_rotation_and_retention(
    postgres_graph_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retire old keys without losing active baselines or retaining terminal ones."""
    from datetime import datetime, timezone

    from jvspatial.core.context import get_default_context

    from app.agentive.harness.key_rotation import rewrap_harness_session_records
    from app.agentive.harness.turn_input import (
        purge_expired_turn_input_capsules,
        purge_turn_input_capsules_for_thread,
        thread_scope_key,
    )
    from app.models.harness_records import HarnessSteeringResumeBaseline
    from app.services.credential_crypto import (
        decrypt_secret_from_storage,
        encrypt_secret_for_storage,
    )

    keys = {"current": b"p" * 32, "previous": None}
    monkeypatch.setattr(
        "app.services.credential_crypto._resolve_key", lambda: (keys["current"], None)
    )
    monkeypatch.setattr(
        "app.services.credential_crypto._previous_key", lambda: keys["previous"]
    )
    thread, owner_id, workspace_id = await _submission_context()
    receipt = await submit_chat_turn(_request(thread, owner_id, workspace_id))
    scope_key = thread_scope_key(
        principal_id=owner_id, workspace_id=workspace_id, thread_id=thread.id
    )
    baseline_id = "o.HarnessSteeringResumeBaseline." + uuid.uuid4().hex
    plaintext = '{"correction":"private direction"}'
    await HarnessSteeringResumeBaseline.create_if_absent(
        id=baseline_id,
        scope_key=scope_key,
        work_item_id=receipt.work_item_id,
        approval_id="approval",
        expires_at="2000-01-01T00:00:00+00:00",
        payload_ciphertext=encrypt_secret_for_storage(plaintext, aad=baseline_id),
    )
    keys.update(current=b"q" * 32, previous=b"p" * 32)
    report = await rewrap_harness_session_records(scope_key=scope_key)
    assert report["HarnessSteeringResumeBaseline"] == 1
    keys["previous"] = None
    row = await get_default_context().database.get("object", baseline_id)
    assert (
        decrypt_secret_from_storage(
            row["context"]["payload_ciphertext"], aad=baseline_id
        )
        == plaintext
    )
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    await purge_expired_turn_input_capsules(retention_days=90, now=now)
    assert await get_default_context().database.get("object", baseline_id) is not None
    work_item = await WorkItem.get(f"o.WorkItem.{receipt.work_item_id}")
    assert work_item is not None
    work_item.status = "succeeded"
    await work_item.save()
    await purge_expired_turn_input_capsules(retention_days=90, now=now)
    assert await get_default_context().database.get("object", baseline_id) is None

    other_id = "o.HarnessSteeringResumeBaseline." + uuid.uuid4().hex
    await HarnessSteeringResumeBaseline.create_if_absent(
        id=other_id,
        scope_key=scope_key,
        work_item_id=receipt.work_item_id,
        approval_id="other-approval",
        expires_at="2099-01-01T00:00:00+00:00",
        payload_ciphertext=encrypt_secret_for_storage(plaintext, aad=other_id),
    )
    await purge_turn_input_capsules_for_thread(
        principal_id=owner_id, workspace_id=workspace_id, thread_id=thread.id
    )
    assert await get_default_context().database.get("object", other_id) is None

"""Exact selected-route model evidence in the real disposable PostgreSQL store.

No real provider call, credential or invoice is used by this storage contract.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from jvspatial.core.context import (
    GraphContext,
    get_default_context,
    set_default_context,
)

from app.agentive.harness.contracts import (
    HarnessExecutionScope,
    PhysicalModelRequest,
    ResolvedModelRoute,
)
from app.agentive.harness.model_observations import (
    _record_id,
    _scope_key,
    load_bound_model_request_observation,
    persist_model_request_observation,
)
from app.agentive.services.work_outbox import _active_database
from tests.fixtures.encryption import encrypted_model_storage

pytestmark = [pytest.mark.contract, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.mark.parametrize("source", ["workspace_byok", "platform", "local"])
async def test_model_receipt_is_encrypted_deduplicated_and_read_from_fresh_context(
    source,
):
    identity = uuid4().hex
    scope = HarnessExecutionScope(
        tenant_id=f"receipt-ws-{identity}",
        principal_id=f"receipt-user-{identity}",
        workspace_id=f"receipt-ws-{identity}",
        thread_id=f"receipt-thread-{identity}",
        session_id=f"receipt-session-{identity}",
        run_id=f"receipt-run-{identity}",
        permission_revision="synthetic-permissions-1",
        capability_version="synthetic-tools-1",
    )
    route = ResolvedModelRoute(
        provider="ollama_chat" if source == "local" else "anthropic",
        model="synthetic-model",
        credential_source=source,
        credential_ref=f"synthetic-credential-{identity}",
        api_key="synthetic-test-key" if source == "workspace_byok" else None,
    )
    now = datetime.now(timezone.utc)
    intent = PhysicalModelRequest(
        request_id=f"receipt-request-{identity}",
        scope=scope,
        provider=route.provider,
        model=route.model,
        credential_source=route.credential_source,
        credential_ref=route.credential_ref,
        attempt=1,
        dispatched_at=now,
        observed_at=now,
        outcome="dispatch_intent",
    )
    terminal = intent.model_copy(
        update={"outcome": "responded", "observed_at": datetime.now(timezone.utc)}
    )
    await asyncio.gather(*(persist_model_request_observation(intent) for _ in range(3)))
    await asyncio.gather(
        *(persist_model_request_observation(terminal) for _ in range(3))
    )
    db = _active_database()
    for outcome in ("dispatch_intent", "responded"):
        document = await db.get(
            "object", _record_id(_scope_key(scope), intent.request_id, outcome)
        )
        assert document is not None
        assert document["context"]["payload_ciphertext"].startswith("v1:")
        assert "synthetic-test-key" not in str(document)
        assert route.credential_ref not in document["context"]["payload_ciphertext"]

    original = get_default_context()
    set_default_context(GraphContext(database=original.database))
    try:
        restored = await load_bound_model_request_observation(
            scope=scope,
            request_id=intent.request_id,
            outcome="responded",
            expected_route=route,
        )
        assert restored == terminal
        assert restored.usage is None
        with pytest.raises(RuntimeError, match="route mismatch"):
            await load_bound_model_request_observation(
                scope=scope,
                request_id=intent.request_id,
                outcome="responded",
                expected_route=route.model_copy(
                    update={"credential_ref": "other-credential"}
                ),
            )
        with pytest.raises(RuntimeError, match="already bound to different evidence"):
            await persist_model_request_observation(
                terminal.model_copy(update={"credential_ref": "other-credential"})
            )
        assert (
            await load_bound_model_request_observation(
                scope=scope,
                request_id=intent.request_id,
                outcome="responded",
                expected_route=route,
            )
            == terminal
        )
    finally:
        set_default_context(original)

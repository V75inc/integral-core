"""WS /api/events tests (EVT-01) — Plan 02-03 Task 2.

starlette.testclient.TestClient supports WS via websocket_connect(). The Phase 1
agentive WS has no test in repo — Phase 2 introduces this pattern.

Fixtures jwt_for_test_user, jwt_for_second_user, second_user, second_user_client
are defined in backend/tests/conftest.py (Sub-task 2d).

Auth tests use TestClient sync API. End-to-end tests use the in-process app
together with a mocked subscription registry to keep the broadcast flow
testable without spinning a separate event loop just for the WS.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest


def test_ws_rejects_missing_token():
    """No ?token= → close 4001 (D-13).

    starlette's TestClient surfaces server-initiated close codes via a
    WebSocketDisconnect raised on the next receive_*. The connection is
    accepted (await websocket.accept()) BEFORE the auth check runs, so the
    handshake itself succeeds — the close arrives on the first receive.
    """
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.main import app

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/events") as ws:
            ws.receive_text()
    assert exc_info.value.code == 4001


def test_ws_rejects_invalid_token():
    """Bogus JWT → close 4001 (D-13)."""
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.main import app

    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/events?token=not-a-real-jwt") as ws:
            ws.receive_text()
    assert exc_info.value.code == 4001


def test_ws_accepts_valid_token_and_responds_to_ping(jwt_for_test_user):
    """Valid JWT → connection accepted; ping/pong echo works."""
    from starlette.testclient import TestClient

    from app.main import app

    if not jwt_for_test_user:
        pytest.skip("auth_token fixture unavailable — cannot mint JWT")

    client = TestClient(app)
    with client.websocket_connect(f"/api/events?token={jwt_for_test_user}") as ws:
        ws.send_text(json.dumps({"type": "ping"}))
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "pong"


def test_ws_registers_and_unregisters_on_disconnect(jwt_for_test_user):
    """Connection registers in _subscriptions and unregisters on close."""
    from starlette.testclient import TestClient

    from app.main import app
    from app.services import event_subscription_registry as esr

    if not jwt_for_test_user:
        pytest.skip("auth_token fixture unavailable — cannot mint JWT")

    # Snapshot pre-state so we don't depend on absolute counts.
    initial_keys = set(esr._subscriptions.keys())
    client = TestClient(app)
    with client.websocket_connect(f"/api/events?token={jwt_for_test_user}") as ws:
        ws.send_text(json.dumps({"type": "ping"}))
        ws.receive_text()  # ensure register_subscription has run
        assert (
            set(esr._subscriptions.keys()) - initial_keys
        ), "subscription registry did not gain a new user_id key"

    # After context exit, the registry must drop the key we added.
    assert set(esr._subscriptions.keys()) == initial_keys


def test_ws_path_is_core_not_agentive_gated():
    """/api/events lives outside the AGENTIVE_ENABLED block (D-13)."""
    from app.main import app

    # Use the OpenAPI schema rather than iterating ``app.routes``: fastapi 0.136+
    # wraps included routers in ``_IncludedRouter`` (no ``.path``), so direct
    # iteration is version-fragile. The schema is the canonical registered view.
    paths = set(app.openapi().get("paths", {}).keys())
    assert "/api/events" in paths, (
        "WS /api/events not registered; " f"present paths: {sorted(paths)}"
    )


@pytest.mark.asyncio
async def test_emit_change_event_reaches_registered_subscriber(monkeypatch):
    """End-to-end: emit_change_event resolves the registry import and broadcasts.

    Uses an in-memory mock WebSocket registered through register_subscription,
    monkeypatches the can_view_track helper so the permission filter passes,
    then triggers emit_change_event directly. Asserts the mock received the
    JSON-serialized broadcast payload.
    """
    from app.schemas.policy import Decision as _Decision
    from app.services import event_subscription_registry as esr
    from app.services.change_event import emit_change_event

    async def always_allow(*args, **kwargs):
        return _Decision(allowed=True, reason="test_bypass")

    # Plan 03-02 migrated the per-message filter to policy_engine.evaluate.
    monkeypatch.setattr(
        "app.services.event_subscription_registry.policy_evaluate", always_allow
    )

    ws = AsyncMock()
    initial_keys = set(esr._subscriptions.keys())
    await esr.register_subscription("user-A", ws)
    try:
        await emit_change_event(
            actor_kind="human",
            actor_id="actor-1",
            action="entry.create",
            resource_type="Entry",
            resource_id="e-emit-1",
            before=None,
            after={"id": "e-emit-1"},
            scope="track:T-emit",
        )
        assert ws.send_text.await_count >= 1
        sent_text = ws.send_text.await_args.args[0]
        msg = json.loads(sent_text)
        assert msg["type"] == "change_event"
        assert msg["payload"]["action"] == "entry.create"
        assert msg["payload"]["scope"] == "track:T-emit"
    finally:
        await esr.unregister_subscription("user-A", ws)
        # Restore original keyset
        for k in list(esr._subscriptions.keys()):
            if k not in initial_keys:
                del esr._subscriptions[k]


@pytest.mark.asyncio
async def test_cross_tenant_emit_does_not_reach_unpermitted_subscriber(monkeypatch):
    """User A subscribed; emit on a track A cannot view → A receives nothing.

    Mirrors the WS-level cross-tenant invariant without standing up two TestClient
    WS connections. The registry-level filter is the canonical D-07 enforcement.
    """
    from app.schemas.policy import Decision as _Decision
    from app.services import event_subscription_registry as esr
    from app.services.change_event import emit_change_event

    async def fake_deny(*args, **kwargs):
        return _Decision(allowed=False, reason="test_deny")  # A cannot view ANY track

    # Plan 03-02 migrated the per-message filter to policy_engine.evaluate.
    monkeypatch.setattr(
        "app.services.event_subscription_registry.policy_evaluate",
        fake_deny,
    )

    ws = AsyncMock()
    initial_keys = set(esr._subscriptions.keys())
    await esr.register_subscription("user-A", ws)
    try:
        await emit_change_event(
            actor_kind="human",
            actor_id="actor-other",
            action="entry.create",
            resource_type="Entry",
            resource_id="e-private",
            before=None,
            after={"id": "e-private"},
            scope="track:T-other",
        )
        assert ws.send_text.await_count == 0
    finally:
        await esr.unregister_subscription("user-A", ws)
        for k in list(esr._subscriptions.keys()):
            if k not in initial_keys:
                del esr._subscriptions[k]


@pytest.mark.asyncio
async def test_revocation_mid_session_stops_subsequent_broadcasts(monkeypatch):
    """D-07 invariant exercised through the emit path (not just registry unit)."""
    from app.schemas.policy import Decision as _Decision
    from app.services import event_subscription_registry as esr
    from app.services.change_event import emit_change_event

    state = {"granted": True}

    async def fake_evaluate(*args, **kwargs):
        return _Decision(
            allowed=state["granted"],
            reason="test_granted" if state["granted"] else "test_revoked",
        )

    # Plan 03-02 migrated the per-message filter to policy_engine.evaluate.
    monkeypatch.setattr(
        "app.services.event_subscription_registry.policy_evaluate",
        fake_evaluate,
    )

    ws = AsyncMock()
    initial_keys = set(esr._subscriptions.keys())
    await esr.register_subscription("user-A", ws)
    try:
        await emit_change_event(
            actor_kind="human",
            actor_id="actor-1",
            action="entry.create",
            resource_type="Entry",
            resource_id="e-1",
            before=None,
            after={"id": "e-1"},
            scope="track:T-rev",
        )
        first_count = ws.send_text.await_count
        assert first_count >= 1

        state["granted"] = False  # revoke

        await emit_change_event(
            actor_kind="human",
            actor_id="actor-1",
            action="entry.update",
            resource_type="Entry",
            resource_id="e-1",
            before={"id": "e-1"},
            after={"id": "e-1", "title": "x"},
            scope="track:T-rev",
        )
        # Count must NOT increment after revocation.
        assert ws.send_text.await_count == first_count
    finally:
        await esr.unregister_subscription("user-A", ws)
        for k in list(esr._subscriptions.keys()):
            if k not in initial_keys:
                del esr._subscriptions[k]


def test_w4_fixtures_resolve(
    second_user, jwt_for_test_user, jwt_for_second_user, second_user_client
):
    """Smoke-test that all four W4 fixtures resolve without 'fixture not found'.

    Per Plan 02-03 success criterion: pytest --collect-only on this file AND
    test_audit_log_query.py must produce zero `fixture ... not found` errors.
    """
    # The fixtures may be None in fallback paths (e.g. live-server mode where
    # /api/auth/login isn't reachable) — that's acceptable. The acceptance bar
    # is collection success, not non-None content.
    assert jwt_for_test_user is None or isinstance(jwt_for_test_user, str)
    assert jwt_for_second_user is None or isinstance(jwt_for_second_user, str)
    # second_user may be None if test_user2 fixture failed; that's documented.
    assert (
        second_user is None
        or hasattr(second_user, "id")
        or hasattr(second_user, "user_id")
    )
    # second_user_client is always an AsyncClient (yields client unauthenticated
    # if no JWT was mintable).
    assert second_user_client is not None

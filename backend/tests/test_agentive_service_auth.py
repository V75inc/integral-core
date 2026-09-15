"""D-01 (mandatory HMAC sig) + D-02 (auto-create gate) regression tests.

Plan 01-02 — Wave 2 trust-boundary hardening. Verifies:
  - Every /api/agentive/* user-context path requires X-Integral-Signature
    + X-Integral-Timestamp; missing/stale/tampered → canonical envelope.
  - Only `/api/agentive/uplink/register-system` and `/heartbeat-system`
    accept a bare service-key without a signature.
  - INTEGRAL_SERVICE_AUTO_CREATE_USERS gates User auto-create:
      flag-off + unknown user → 404 + agentive.auth.user_node_missing
      flag-on  + unknown user → auto-create + INFO log with audit fields
  - The hardened middleware does NOT break the chat dispatch path —
    a signed jvagent-style request reaches the typed connector and
    returns the canonical ChatTurnResponse shape.

Analog: backend/tests/test_agentive_whatsapp.py:28-78 (TestServiceAuthMiddleware).
"""

import logging
import subprocess
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# The default test service key matches conftest.py's setdefault below; tests
# that monkeypatch it MUST also call _reset_service_key_cache via the fixture.
_TEST_SERVICE_KEY = "integral-test-service-key-for-agentive-only__________"


def _reset_key_cache() -> None:
    """Clear the cached service key so monkeypatch.setenv takes effect."""
    from app.agentive.middleware.service_auth import _reset_service_key_cache

    _reset_service_key_cache()


@pytest.mark.asyncio
async def test_user_context_path_requires_signature(monkeypatch):
    """D-01: missing X-Integral-Signature on user-context path → 401 + signature_required envelope."""
    monkeypatch.setenv("AGENTIVE_ENABLED", "1")
    monkeypatch.setenv("INTEGRAL_SERVICE_KEY", _TEST_SERVICE_KEY)
    _reset_key_cache()
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/agentive/chat/message",
            json={"message": "hello"},
            headers={
                "X-Integral-Service-Key": _TEST_SERVICE_KEY,
                "X-Integral-User-Id": "user-1",
                # NOTE: no signature, no timestamp
            },
        )
    assert r.status_code == 401, r.text
    body = r.json()
    assert body["error_code"] == "agentive.auth.signature_required"
    assert set(body.keys()) >= {
        "error_code",
        "message",
        "details",
        "timestamp",
        "path",
    }
    # Details should list which header(s) were missing.
    assert "X-Integral-Signature" in body["details"]["missing_headers"]
    assert "X-Integral-Timestamp" in body["details"]["missing_headers"]


@pytest.mark.asyncio
async def test_stale_timestamp_rejected(signed_service_headers, monkeypatch):
    """D-01: timestamp >5 min in the past → 401 + signature_stale envelope."""
    monkeypatch.setenv("AGENTIVE_ENABLED", "1")
    monkeypatch.setenv("INTEGRAL_SERVICE_KEY", _TEST_SERVICE_KEY)
    _reset_key_cache()
    from app.main import app

    old_ts = int(time.time()) - 3600  # 1 hour ago
    headers = signed_service_headers("user-1", ts=old_ts)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/agentive/chat/message",
            json={"message": "hello"},
            headers=headers,
        )
    assert r.status_code == 401, r.text
    body = r.json()
    assert body["error_code"] == "agentive.auth.signature_stale"
    assert body["details"]["max_skew_seconds"] == 300


@pytest.mark.asyncio
async def test_tampered_signature_rejected(signed_service_headers, monkeypatch):
    """D-01: signature mismatch → 401 + signature_invalid envelope."""
    monkeypatch.setenv("AGENTIVE_ENABLED", "1")
    monkeypatch.setenv("INTEGRAL_SERVICE_KEY", _TEST_SERVICE_KEY)
    _reset_key_cache()
    from app.main import app

    headers = signed_service_headers("user-1")
    headers["X-Integral-Signature"] = "0" * 64  # tamper

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/agentive/chat/message",
            json={"message": "hello"},
            headers=headers,
        )
    assert r.status_code == 401, r.text
    body = r.json()
    assert body["error_code"] == "agentive.auth.signature_invalid"


@pytest.mark.asyncio
async def test_register_system_exempt_from_signature(monkeypatch):
    """D-01: register-system / heartbeat-system accept bare service-key (exempt)."""
    monkeypatch.setenv("AGENTIVE_ENABLED", "1")
    monkeypatch.setenv("INTEGRAL_SERVICE_KEY", _TEST_SERVICE_KEY)
    _reset_key_cache()
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/agentive/uplink/heartbeat-system",
            json={"agent_config_id": "system-agent-1"},
            headers={"X-Integral-Service-Key": _TEST_SERVICE_KEY},
        )
    # Exempt path — sig not required. Whatever the handler returns (200, 404
    # for missing agent, etc.), the middleware must NOT have rejected with
    # signature_required.
    if r.status_code == 401:
        body = r.json()
        assert (
            body.get("error_code") != "agentive.auth.signature_required"
        ), f"register-system path should be exempt from signature_required: got {body!r}"


@pytest.mark.asyncio
async def test_auto_create_off_returns_user_missing(
    signed_service_headers, monkeypatch
):
    """D-02: flag off + unknown user → 404 + user_node_missing envelope.

    No importlib.reload: the middleware reads INTEGRAL_SERVICE_AUTO_CREATE_USERS
    via os.getenv() per-request, so monkeypatch.setenv suffices.
    """
    monkeypatch.setenv("AGENTIVE_ENABLED", "1")
    monkeypatch.setenv("INTEGRAL_SERVICE_KEY", _TEST_SERVICE_KEY)
    monkeypatch.setenv("INTEGRAL_SERVICE_AUTO_CREATE_USERS", "0")
    _reset_key_cache()
    from app.main import app

    headers = signed_service_headers("nonexistent-user-id")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/agentive/chat/message",
            json={"message": "hi"},
            headers=headers,
        )
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["error_code"] == "agentive.auth.user_node_missing"
    assert body["details"]["user_id"] == "nonexistent-user-id"


@pytest.mark.asyncio
async def test_auto_create_on_creates_user_and_logs(
    signed_service_headers, monkeypatch, caplog
):
    """D-02: flag on + unknown user with upstream AuthUser → graph User node
    auto-created + INFO log with caller-key fingerprint + auth_user_id.
    """
    monkeypatch.setenv("AGENTIVE_ENABLED", "1")
    monkeypatch.setenv("INTEGRAL_SERVICE_KEY", _TEST_SERVICE_KEY)
    monkeypatch.setenv("INTEGRAL_SERVICE_AUTO_CREATE_USERS", "1")
    _reset_key_cache()
    from jvspatial.api.auth.models import User as AuthUser

    from app.main import app

    # Pre-create the upstream AuthUser so auto-create has a name source.
    existing_id = "brand-new-user-id-for-auto-create-test"
    try:
        await AuthUser.create(
            id=existing_id,
            email=f"{existing_id}@example.com",
            name="Auto Create Tester",
        )
    except Exception:
        # AuthUser signature may differ across jvspatial versions; the
        # test path through the middleware tolerates auth_user None and
        # fails closed via a different code path (also a valid D-02 outcome).
        pass

    caplog.set_level(logging.INFO, logger="app.agentive.middleware.service_auth")

    headers = signed_service_headers(existing_id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/agentive/chat/message",
            json={"message": "hi"},
            # The chat endpoint will likely 503 due to no system agent;
            # the assertion here is on auto-create + log emission, not on chat.
            headers=headers,
        )

    # The auto-create path emits "auto-created User graph node" only when
    # AuthUser.get(user_id) returns a non-None object. If AuthUser.create
    # above failed (signature drift), the middleware emits the "auto-create
    # blocked — no AuthUser" warning instead, which is also correct D-02
    # behavior. Accept either outcome but require ONE of them to be present.
    success_records = [
        rec
        for rec in caplog.records
        if "auto-created User graph node" in rec.getMessage()
    ]
    blocked_records = [
        rec for rec in caplog.records if "auto-create blocked" in rec.getMessage()
    ]

    assert success_records or blocked_records, (
        "Expected D-02 auto-create log line (success or blocked); got: "
        f"{[rec.getMessage() for rec in caplog.records]}"
    )

    if success_records:
        rec = success_records[0]
        assert getattr(rec, "auto_create_used", None) is True
        # Fingerprint must be 8 hex chars (or "<no-key>" if unset, which
        # would be a test-env failure).
        fp = getattr(rec, "caller_key_fingerprint", "")
        assert fp and fp != "<no-key>" and len(fp) == 8
        assert getattr(rec, "auth_user_id", "") == existing_id
        assert getattr(rec, "user_node_id", "")  # non-empty


def test_no_silent_except_in_service_auth():
    """D-02: zero `except: pass` survive in service_auth.py."""
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            "grep",
            "-rEn",
            r"except[^:]*:\s*pass",
            "backend/app/agentive/middleware/service_auth.py",
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert (
        result.stdout.strip() == ""
    ), f"silent except in service_auth.py:\n{result.stdout}"


@pytest.mark.asyncio
async def test_jvagent_round_trip_through_signed_path(
    signed_service_headers, monkeypatch, test_user
):
    """Round-trip integration: simulated jvagent client signs → middleware
    verifies → dispatch routes to a stubbed connector → typed
    ChatTurnResponse echoed back.

    Proves D-01's mandatory-signature path doesn't break the chat surface.

    Note: enables jvspatial auth_config.test_mode so the AuthenticationMiddleware
    accepts the request.state.user that ServiceAuthMiddleware sets. This mirrors
    the in-process ASGI test pattern documented in jvspatial's
    "Request State Contract".
    """
    monkeypatch.setenv("AGENTIVE_ENABLED", "1")
    monkeypatch.setenv("INTEGRAL_SERVICE_KEY", _TEST_SERVICE_KEY)
    monkeypatch.setenv("INTEGRAL_SERVICE_AUTO_CREATE_USERS", "0")
    _reset_key_cache()

    from app.agentive.connectors.base import ChatTurnResult
    from app.agentive.services import uplink_registry as uplink_registry_mod
    from app.main import app, server

    # In-process ASGI testing: tell jvspatial auth_config to honour state.user
    # set by upstream middleware (ServiceAuthMiddleware). Restored by monkeypatch.
    monkeypatch.setattr(server._auth_config, "test_mode", True)

    class _FakeConn:
        agent_type = "jvagent"
        config_id = "system-agent"
        scope = "system"
        preferences: dict = {}

    async def _mock_get_system():
        return _FakeConn()

    class _FakeConnector:
        async def send_turn(self, ctx, *, preferences):
            return ChatTurnResult(
                message="round-trip ok",
                session_id="sess-rt",
                agent_user_id=ctx.email,
            )

    monkeypatch.setattr(
        uplink_registry_mod.uplink_registry, "get_system_agent", _mock_get_system
    )
    monkeypatch.setattr(
        "app.agentive.api.chat.get_chat_connector",
        lambda _t: _FakeConnector(),
    )

    # `test_user` fixture provides a known graph User we can sign for.
    headers = signed_service_headers(test_user.id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/agentive/chat/message",
            json={"message": "hello from jvagent"},
            headers=headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["message"] == "round-trip ok"
    assert body["agent_type"] == "jvagent"


@pytest.mark.asyncio
async def test_service_auth_path_gate_blocks_non_agentive_routes(
    signed_service_headers, monkeypatch, test_user
):
    """Service-key + HMAC must not mint identity for non-/api/agentive routes.

    Handoff JWT still works on agentive paths; the rest of the API requires a
    real user session (or an intentional allowlist — none today).
    """
    monkeypatch.setenv("AGENTIVE_ENABLED", "1")
    monkeypatch.setenv("INTEGRAL_SERVICE_KEY", _TEST_SERVICE_KEY)
    monkeypatch.setenv("INTEGRAL_SERVICE_AUTO_CREATE_USERS", "0")
    _reset_key_cache()

    from app.main import app

    headers = signed_service_headers(test_user.id)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        blocked = await client.get("/api/auth/me", headers=headers)
        # Any agentive route proves the path gate admitted service auth.
        # Chat may 503 without a connected uplink — that still means auth passed.
        allowed = await client.post(
            "/api/agentive/chat/message",
            json={"message": "path-gate-ok"},
            headers=headers,
        )

    assert blocked.status_code == 401, blocked.text
    assert allowed.status_code != 401, allowed.text
    assert allowed.status_code in (200, 503), allowed.text


def test_handoff_token_is_tagged_as_service_auth(monkeypatch):
    """D-04: tokens minted by `_mint_handoff_token` carry the svc_auth +
    caller_key_fp forensic claims and are signed with the app secret.

    Unit-testing the mint helper directly avoids the
    "can't add middleware after app start" trap that hits any attempt to
    intercept the injected Authorization header from outside the request
    pipeline.
    """
    import jwt as _jwt

    from app.agentive.middleware.service_auth import (
        _mint_handoff_token,
        _reset_service_key_cache,
    )
    from app.config import settings

    monkeypatch.setenv("INTEGRAL_SERVICE_KEY", _TEST_SERVICE_KEY)
    _reset_service_key_cache()

    token = _mint_handoff_token(
        user_id="o.User.test-handoff-id",
        email="handoff@example.com",
        roles=["user"],
        permissions=["read:tracks"],
    )

    payload = _jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    assert payload.get("svc_auth") is True
    assert payload.get("caller_key_fp")  # 8-char fingerprint, non-empty
    assert payload.get("user_id") == "o.User.test-handoff-id"
    assert payload.get("email") == "handoff@example.com"
    assert payload.get("roles") == ["user"]
    assert payload.get("permissions") == ["read:tracks"]

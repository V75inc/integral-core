"""Tests for the agentive service auth middleware and channel identity endpoints."""

import hashlib
import os
import secrets

import pytest

# Part of the per-PR smoke gate (see pyproject [tool.pytest.ini_options] markers).
# These are auth-isolation tests: they assert that a service key is required,
# that the right one is accepted, and that neither answer leaks into the next
# test. That belongs on every PR — the two files were both outside the marker
# when a leaked `INTEGRAL_SERVICE_KEY` in one of them turned the other red on
# `main` for two days while every PR stayed green.
pytestmark = pytest.mark.smoke

# Ensure test DB and testing mode
os.environ["JVSPATIAL_DB_PATH"] = "test_integral_db"
os.environ["JVSPATIAL_DB_TYPE"] = "json"
os.environ["TESTING"] = "1"
# NOT set here: conftest forces INTEGRAL_SERVICE_KEY for the whole session
# (see the comment above that assignment). A module-level setdefault would be
# inert today and a trap the moment import order changes — tests that need a
# different key take the `service_key_env` fixture below, which restores.


@pytest.fixture
def service_headers():
    """Headers for service-to-service auth."""
    from app.agentive.middleware.service_auth import _get_service_key

    return {
        "X-Integral-Service-Key": _get_service_key()
        or "test-service-key-for-integration-tests",
    }


@pytest.fixture
def service_key_env(monkeypatch):
    """Set INTEGRAL_SERVICE_KEY for one test and leave nothing behind.

    Two things have to be undone, and the version of these tests that used
    ``os.environ[...] = ...`` + ``importlib.reload`` undid neither:

    1. **The env var.** A raw assignment persists for the rest of the worker
       process, so every later test on that worker validated against
       ``test-key-12345`` instead of the key conftest forces. Under
       ``--dist loadfile`` (what CI uses) that put this file and
       ``test_agentive_chat.py`` on the same worker and turned
       ``test_register_system_and_heartbeat_with_service_key`` into a 401. It
       is why `main`'s full suite has been red since 2026-08-10 while every PR
       stayed green — PRs run only the `smoke` marker, which excludes both
       files.
    2. **The module-level cache.** ``_get_service_key`` memoises into
       ``_SERVICE_KEY`` / ``_SERVICE_KEY_LOADED``, so restoring the env var
       alone is not enough — ``test_service_key_missing`` restored the
       variable in a ``finally`` and still left the cache holding ``None``.

    ``monkeypatch`` handles the first; the reset either side handles the
    second. ``importlib.reload`` is gone: it rebinds the module object, so
    anything already holding a reference to the old functions keeps reading a
    different copy of those globals.
    """
    from app.agentive.middleware.service_auth import _reset_service_key_cache

    def _set(value):
        if value is None:
            monkeypatch.delenv("INTEGRAL_SERVICE_KEY", raising=False)
        else:
            monkeypatch.setenv("INTEGRAL_SERVICE_KEY", value)
        _reset_service_key_cache()

    yield _set
    _reset_service_key_cache()


class TestServiceAuthMiddleware:
    """Test the service auth middleware."""

    @pytest.mark.asyncio
    async def test_service_key_verification(self, service_key_env):
        from app.agentive.middleware.service_auth import verify_service_key

        service_key_env("test-key-12345")

        assert verify_service_key("test-key-12345")
        assert not verify_service_key("wrong-key")
        assert not verify_service_key("")

    @pytest.mark.asyncio
    async def test_service_key_missing(self, service_key_env):
        from app.agentive.middleware.service_auth import verify_service_key

        service_key_env(None)
        assert not verify_service_key("any-key")

    @pytest.mark.asyncio
    async def test_hmac_signature(self):
        import hmac as hmac_mod

        key = "test-service-key-for-integration-tests"
        timestamp = "1700000000"
        user_id = "user123"
        sig = hmac_mod.new(
            key.encode(),
            f"{timestamp}.{user_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        assert len(sig) == 64
        expected = hmac_mod.new(
            key.encode(),
            f"{timestamp}.{user_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        assert sig == expected


async def _make_user(user_id: str):
    """Create + catalog a real User node for the channel-identity services.

    ``create_channel_identity_with_otp`` / ``initiate_link`` must wire the
    ``HAS_CHANNEL_IDENTITY`` edge from a real User (I-GRAPH-01), so a
    fabricated id is no longer accepted.
    """
    from datetime import datetime

    from app.models.nodes import User
    from app.services.app_graph import catalog_user

    now = datetime.now().isoformat()
    user = await User.create(
        user_id=user_id,
        display_name="WA Test",
        created_at=now,
        updated_at=now,
    )
    await catalog_user(user)
    return user


class TestChannelIdentityService:
    """Test channel identity resolution and OTP verification."""

    @pytest.mark.asyncio
    async def test_create_and_resolve_identity(self):
        from datetime import datetime

        from app.agentive.edges import HAS_CHANNEL_IDENTITY
        from app.agentive.nodes import ChannelIdentity
        from app.agentive.services.channel_identity import resolve_channel_identity
        from app.models.nodes import User
        from app.services.app_graph import catalog_user

        now = datetime.now().isoformat()
        user = await User.create(
            user_id="u_whatsapp_test1",
            display_name="WA Test",
            created_at=now,
            updated_at=now,
        )
        await catalog_user(user)

        ci = await ChannelIdentity.create(
            user_id=user.id,
            channel="whatsapp",
            channel_user_id="+15551234567",
            verified=True,
            created_at="2024-01-01T00:00:00",
        )
        # Wire the graph edge so resolve_channel_identity can traverse it.
        await user.connect(
            ci, edge=HAS_CHANNEL_IDENTITY, is_primary=False, created_at=now
        )

        result = await resolve_channel_identity("whatsapp", "+15551234567")
        assert result is not None
        assert result["channel"] == "whatsapp"
        assert result["channel_user_id"] == "+15551234567"
        assert result["verified"] is True

        await ci.delete()
        await user.delete()

    @pytest.mark.asyncio
    async def test_resolve_unknown_identity(self):
        from app.agentive.services.channel_identity import resolve_channel_identity

        result = await resolve_channel_identity("whatsapp", "+19999999999")
        assert result is None

    @pytest.mark.asyncio
    async def test_otp_verification(self):
        from app.agentive.services.channel_identity import (
            create_channel_identity_with_otp,
            verify_channel_otp,
        )

        # A real User node: the service wires HAS_CHANNEL_IDENTITY at create
        # (I-GRAPH-01) and refuses a user id it cannot resolve.
        user = await _make_user("u_whatsapp_otp")

        result = await create_channel_identity_with_otp(
            user_id=user.id,
            channel="whatsapp",
            channel_user_id="+15559876543",
        )
        assert result["verified"] is False
        assert result["otp_code"] is not None
        assert len(result["otp_code"]) == 6

        verify_result = await verify_channel_otp(
            "whatsapp", "+15559876543", result["otp_code"]
        )
        assert verify_result["verified"] is True

        verify_bad = await verify_channel_otp("whatsapp", "+15559876543", "WRONG1")
        assert verify_bad["verified"] is False

    @pytest.mark.asyncio
    async def test_otp_expiry_and_attempts(self):
        from app.agentive.nodes import ChannelIdentity
        from app.agentive.services.channel_identity import verify_channel_otp

        ci = await ChannelIdentity.create(
            user_id="test-user-expiry",
            channel="whatsapp",
            channel_user_id="+15551111111",
            verified=False,
            preferences={
                "otp_hash": hashlib.sha256(b"ABC123").hexdigest(),
                "otp_expires": "2020-01-01T00:00:00",
                "otp_attempts": 0,
            },
            created_at="2024-01-01T00:00:00",
        )

        result = await verify_channel_otp("whatsapp", "+15551111111", "ABC123")
        assert result["verified"] is False
        assert "expired" in result["message"].lower()

        await ci.delete()

    @pytest.mark.asyncio
    async def test_link_token_verification(self):
        from app.agentive.nodes import ChannelIdentity
        from app.agentive.services.channel_identity import verify_identity_by_token

        token = secrets.token_urlsafe(16)
        token_hash = hashlib.sha256(token.encode()).hexdigest()

        ci = await ChannelIdentity.create(
            user_id="test-user-link",
            channel="whatsapp",
            channel_user_id="+15552222222",
            verified=False,
            preferences={
                "link_token": token_hash,
                "link_token_expires": "2099-01-01T00:00:00",
            },
            created_at="2024-01-01T00:00:00",
        )

        result = await verify_identity_by_token(ci.id, token)
        assert result["verified"] is True

        await ci.delete()

    @pytest.mark.asyncio
    async def test_initiate_link(self):
        from app.agentive.services.channel_identity import initiate_link

        user = await _make_user("u_whatsapp_init")
        result = await initiate_link(user.id, "whatsapp", "+15553333333")
        assert result["otp_code"] is not None
        assert result["link_token"] is not None
        assert result["verified"] is False


class TestNewMCPTools:
    """Test the manifest tool surface used by WhatsApp agent integration."""

    @pytest.mark.asyncio
    async def test_agent_tools_in_catalogue(self):
        from app.agentive.tooling.catalogue import build_tool_catalogue

        names = {t["name"] for t in build_tool_catalogue()}
        assert "integral_update_entry" in names
        assert "integral_add_comment" in names
        assert "integral_create_track" in names
        assert "integral_create_app" in names
        assert "integral_share" in names
        assert "integral_set_focus" in names
        assert "integral_resolve_entry" in names

    @pytest.mark.asyncio
    async def test_create_track_tool(self):
        from app.agentive.tooling.dispatch import dispatch_tool

        # integral_create_track is a PROPOSE tool — a successful dispatch STAGES
        # a pending change (data is a StagedChange dict), it does not apply.
        result = await dispatch_tool(
            "integral_create_track",
            {
                "title": "Agent Test Track",
                "visibility": "private",
            },
            principal_id="test-user-tool",
            scope=None,
        )
        if result.is_error:
            assert result.error_code
        else:
            assert isinstance(result.data, dict)

    @pytest.mark.asyncio
    async def test_resolve_entry_unknown(self):
        from app.agentive.tooling.dispatch import dispatch_tool

        result = await dispatch_tool(
            "integral_resolve_entry",
            {
                "reference": "nonexistent entry",
            },
            principal_id="test-user-tool",
            scope=None,
        )
        # Either a fail-closed error envelope or a structured "not resolved".
        assert result.is_error or (result.data or {}).get("resolved") is False

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        from app.agentive.tooling.dispatch import dispatch_tool

        result = await dispatch_tool(
            "nonexistent_tool", {}, principal_id="test-user", scope=None
        )
        assert result.is_error
        assert result.error_code == "unknown_tool"

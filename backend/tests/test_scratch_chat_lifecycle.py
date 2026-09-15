"""Regression tests for the chat first-message scratch-provision hook (04-04-3).

Covers:

- First chat dispatch triggers ``provision_scratch_track`` with the
  authenticated user's id.
- Repeated chat dispatch hits the per-process cache — the underlying
  ``_find_existing_scratch_track`` DB walk is NOT re-entered on the
  second turn.
- Provision failure does NOT break the chat turn — the connector
  dispatches and the endpoint still returns 200; a warning is logged.
- ``docs/INVARIANTS.md`` carries the five Phase 4 scratch invariants
  (I-SCRATCH-01..05).

The hook lives at ``app/agentive/api/chat.py::post_agentive_chat_message``
immediately after the email-validation block and BEFORE the
``uplink_registry.get_system_agent()`` call (CONTEXT lock #11).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import agent_scratch as agent_scratch_module


@pytest.fixture(autouse=True)
def _reset_scratch_cache():
    agent_scratch_module._SCRATCH_TRACK_ID_CACHE.clear()
    yield
    agent_scratch_module._SCRATCH_TRACK_ID_CACHE.clear()


@pytest.fixture
def _mock_system_agent(monkeypatch):
    """Mock the uplink registry + chat connector so the chat turn returns 200."""
    from app.agentive.services import uplink_registry as uplink_registry_mod

    class _McpConfig:
        agent_type = "mcp"
        preferences = {"mcp_endpoint": "http://stub.example.com"}

    async def _mock_get_system():
        return _McpConfig()

    monkeypatch.setattr(
        uplink_registry_mod.uplink_registry,
        "get_system_agent",
        _mock_get_system,
    )
    return _McpConfig


@pytest.mark.asyncio
async def test_first_chat_dispatch_calls_provision_scratch_track(
    authenticated_client, test_user, _mock_system_agent
):
    """First chat turn → provision_scratch_track called with the principal id."""
    if test_user is None:
        pytest.skip("no test_user node available")

    real_provision = agent_scratch_module.provision_scratch_track
    calls = []

    async def _spy(*, user_id):  # type: ignore[no-untyped-def]
        calls.append(user_id)
        return await real_provision(user_id=user_id)

    with patch.object(
        agent_scratch_module, "provision_scratch_track", side_effect=_spy
    ):
        r = await authenticated_client.post(
            "/api/agentive/chat/message",
            json={"message": "hello"},
        )
    assert r.status_code == 200, r.text
    assert len(calls) == 1, f"expected exactly one provision call, got {calls}"
    assert calls[0]  # non-empty user_id


@pytest.mark.asyncio
async def test_repeated_chat_dispatch_hits_provision_cache(
    authenticated_client, test_user, _mock_system_agent
):
    """Second chat turn re-enters provision_scratch_track but DOES NOT re-walk the DB.

    The first chat turn warms the cache via the in-endpoint hook (the
    user_id passed to ``provision_scratch_track`` is whatever the
    authenticated principal resolves to — AuthUser id in this test
    harness). The second turn then must observe the cached entry and
    skip the DB lookup.
    """
    if test_user is None:
        pytest.skip("no test_user node available")

    # First turn — warm the cache. ``_find_existing_scratch_track`` may
    # fire once (DB miss → create) on this turn.
    r1 = await authenticated_client.post(
        "/api/agentive/chat/message",
        json={"message": "first"},
    )
    assert r1.status_code == 200, r1.text
    # Cache MUST have populated at least one entry after the first turn.
    assert (
        len(agent_scratch_module._SCRATCH_TRACK_ID_CACHE) >= 1
    ), agent_scratch_module._SCRATCH_TRACK_ID_CACHE

    # Second turn — DB walk MUST NOT happen.
    with patch.object(
        agent_scratch_module,
        "_find_existing_scratch_track",
        side_effect=AssertionError(
            "scratch cache should have short-circuited the DB walk"
        ),
    ) as guarded:
        r2 = await authenticated_client.post(
            "/api/agentive/chat/message",
            json={"message": "second"},
        )
    assert r2.status_code == 200, r2.text
    guarded.assert_not_called()


@pytest.mark.asyncio
async def test_provision_failure_does_not_break_chat_turn(
    authenticated_client, test_user, _mock_system_agent
):
    """Hook swallows + logs provision exceptions — chat turn still returns 200."""
    if test_user is None:
        pytest.skip("no test_user node available")

    async def _boom(*, user_id):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated provisioning failure")

    with patch.object(
        agent_scratch_module, "provision_scratch_track", side_effect=_boom
    ):
        r = await authenticated_client.post(
            "/api/agentive/chat/message",
            json={"message": "hello"},
        )
    # The chat turn returns 200 even though provisioning blew up.
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# INVARIANTS.md scratch section
# ---------------------------------------------------------------------------


def _invariants_path() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "INVARIANTS.md"


def test_invariants_md_contains_phase_4_scratch_section():
    """All five I-SCRATCH-0N invariants are present in docs/INVARIANTS.md."""
    text = _invariants_path().read_text(encoding="utf-8")
    for key in (
        "I-SCRATCH-01",
        "I-SCRATCH-02",
        "I-SCRATCH-03",
        "I-SCRATCH-04",
        "I-SCRATCH-05",
    ):
        assert key in text, f"missing {key} in INVARIANTS.md"


def test_invariants_md_phase_4_invariant_anchors():
    """Anchor strings called out in the plan are present (substrate audit hook)."""
    text = _invariants_path().read_text(encoding="utf-8")
    for anchor in (
        "Scratch-Track-In-Personal-Workspace",
        "Scratch-Track-Kind-Discriminator",
        "Promote-Preserves-Derived-From",
        "Source-Archive-Via-Status-Not-Delete",
    ):
        assert anchor in text, f"missing anchor '{anchor}' in INVARIANTS.md"


# ---------------------------------------------------------------------------
# Substrate single-Literal grep gates
# ---------------------------------------------------------------------------


def _grep_literal_count(prefix: str) -> int:
    """Count ``^<prefix>\\s*=\\s*Literal`` under backend/app via Python ``re``.

    Portable across GNU grep builds that do not treat ``\\s`` as whitespace.
    """
    import re

    backend_app = Path(__file__).resolve().parents[2] / "backend" / "app"
    rx = re.compile(rf"^{prefix}\s*=\s*Literal")
    count = 0
    for py in backend_app.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        for line in py.read_text(encoding="utf-8").splitlines():
            if rx.search(line):
                count += 1
    return count


def test_single_literal_invariants_preserved_after_plan_04_04():
    assert _grep_literal_count("PolicyAction") == 1
    assert _grep_literal_count("ChangeEventAction") == 1
    assert _grep_literal_count("ActorKind") == 1

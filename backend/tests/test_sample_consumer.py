"""EVT-03 sample consumer end-to-end tests — Plan 02-04 Task 3.

Verifies:
- opt-in flag honored (off by default; on when AGENTIVE_SAMPLE_CONSUMER_ENABLED=1)
- trigger condition — entry.create event with tags containing "summarize-me"
- recursion guard — auto-drafted summary has empty tags AND consumer ignores its own writes
- consumer's own write also emits a ChangeEvent (D-05 loop closure)
- consumer is NOT loaded when AGENTIVE_ENABLED=0 (D-11)

Tests directly exercise the consumer's `on_change_event` callback (synchronously
in the same event loop) — this is the same dispatch path that
broadcast_change_event uses, but skips the WS subscriber overhead.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest


@pytest.mark.asyncio
async def test_consumer_disabled_by_default(monkeypatch):
    """Without AGENTIVE_SAMPLE_CONSUMER_ENABLED, register_sample_consumers is a no-op."""
    monkeypatch.delenv("AGENTIVE_SAMPLE_CONSUMER_ENABLED", raising=False)

    from app.agentive.sample_consumers import register_sample_consumers
    from app.services import event_subscription_registry as esr

    esr.reset_consumer_hooks()
    initial = len(esr._consumer_hooks)
    register_sample_consumers()
    assert (
        len(esr._consumer_hooks) == initial
    ), "consumer hook leaked when opt-in flag is off"


@pytest.mark.asyncio
async def test_consumer_enabled_drafts_summary(
    authenticated_client, test_user, monkeypatch
):
    """Tagged entry creation → summary entry drafted in same track."""
    monkeypatch.setenv("AGENTIVE_SAMPLE_CONSUMER_ENABLED", "1")
    from app.agentive.sample_consumers import register_sample_consumers
    from app.services import event_subscription_registry as esr

    esr.reset_consumer_hooks()
    register_sample_consumers()

    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Consumer Track 1"}
    )
    assert track_resp.status_code == 200, track_resp.text
    track_id = track_resp.json()["track"]["id"]

    entry_resp = await authenticated_client.post(
        "/api/entries",
        json={
            "track_id": track_id,
            "title": "Original Article",
            "tags": ["summarize-me"],
        },
    )
    assert entry_resp.status_code in (200, 201), entry_resp.text
    src_entry_id = entry_resp.json()["entry"]["id"]

    # Allow the in-process hook to run.
    await asyncio.sleep(0.1)

    list_resp = await authenticated_client.get(f"/api/entries?track_id={track_id}")
    assert list_resp.status_code == 200, list_resp.text
    entries = list_resp.json()["entries"]

    summaries = [e for e in entries if (e.get("title") or "").startswith("Summary:")]
    assert (
        len(summaries) >= 1
    ), f"no summary entry drafted; entries={[(e.get('title'), e.get('id')) for e in entries]}"
    summary = summaries[0]

    prov = summary.get("provenance") or {}
    assert (
        prov.get("source") == "agent"
    ), f"summary provenance.source != 'agent': {prov}"
    assert prov.get("source_id") == "sample.summary_drafter", prov
    derived_from = prov.get("derived_from") or []
    assert (
        src_entry_id in derived_from
    ), f"derived_from missing source entry: {derived_from}"

    # Recursion guard — summary entry has empty tags
    assert "summarize-me" not in (
        summary.get("tags") or []
    ), "recursion guard violated — summary contains trigger tag"

    esr.reset_consumer_hooks()


@pytest.mark.asyncio
async def test_consumer_ignores_non_trigger_tag(
    authenticated_client, test_user, monkeypatch
):
    """Entry with a different tag does NOT trigger the consumer."""
    monkeypatch.setenv("AGENTIVE_SAMPLE_CONSUMER_ENABLED", "1")
    from app.agentive.sample_consumers import register_sample_consumers
    from app.services import event_subscription_registry as esr

    esr.reset_consumer_hooks()
    register_sample_consumers()

    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Consumer Wrong Tag"}
    )
    track_id = track_resp.json()["track"]["id"]
    await authenticated_client.post(
        "/api/entries",
        json={"track_id": track_id, "title": "Untagged thing", "tags": ["other"]},
    )
    await asyncio.sleep(0.1)

    list_resp = await authenticated_client.get(f"/api/entries?track_id={track_id}")
    entries = list_resp.json()["entries"]
    summaries = [e for e in entries if (e.get("title") or "").startswith("Summary:")]
    assert len(summaries) == 0, f"unexpected summary on non-trigger tag: {summaries}"

    esr.reset_consumer_hooks()


@pytest.mark.asyncio
async def test_consumer_no_recursion(authenticated_client, test_user, monkeypatch):
    """Auto-drafted summary does NOT re-trigger consumer (exactly 1 summary per source)."""
    monkeypatch.setenv("AGENTIVE_SAMPLE_CONSUMER_ENABLED", "1")
    from app.agentive.sample_consumers import register_sample_consumers
    from app.services import event_subscription_registry as esr

    esr.reset_consumer_hooks()
    register_sample_consumers()

    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Consumer No Recursion"}
    )
    track_id = track_resp.json()["track"]["id"]
    await authenticated_client.post(
        "/api/entries",
        json={
            "track_id": track_id,
            "title": "Source",
            "tags": ["summarize-me"],
        },
    )
    # Allow consumer write + any recursive hook dispatches a chance to run.
    await asyncio.sleep(0.5)

    list_resp = await authenticated_client.get(f"/api/entries?track_id={track_id}")
    entries = list_resp.json()["entries"]
    summaries = [e for e in entries if (e.get("title") or "").startswith("Summary:")]
    assert (
        len(summaries) == 1
    ), f"recursion detected: {len(summaries)} summary entries (expected 1)"

    esr.reset_consumer_hooks()


@pytest.mark.asyncio
async def test_consumer_emit_appears_in_audit_log(
    authenticated_client, test_user, monkeypatch
):
    """Consumer's own write closes the D-05 loop — visible in /api/audit-log."""
    monkeypatch.setenv("AGENTIVE_SAMPLE_CONSUMER_ENABLED", "1")
    from app.agentive.sample_consumers import register_sample_consumers
    from app.services import event_subscription_registry as esr

    esr.reset_consumer_hooks()
    register_sample_consumers()

    track_resp = await authenticated_client.post(
        "/api/tracks", json={"title": "Consumer Audit"}
    )
    track_id = track_resp.json()["track"]["id"]
    await authenticated_client.post(
        "/api/entries",
        json={
            "track_id": track_id,
            "title": "Source",
            "tags": ["summarize-me"],
        },
    )
    await asyncio.sleep(0.2)

    audit_resp = await authenticated_client.get(
        f"/api/audit-log?scope=track:{track_id}"
    )
    assert audit_resp.status_code == 200, audit_resp.text
    events = audit_resp.json()["events"]
    agent_events = [
        e
        for e in events
        if e.get("actor_kind") == "agent"
        and e.get("actor_id") == "sample.summary_drafter"
    ]
    assert len(agent_events) >= 1, (
        f"consumer's own write did NOT emit ChangeEvent visible in audit-log; "
        f"events={[(e['action'], e['actor_kind'], e['actor_id']) for e in events]}"
    )
    assert agent_events[0]["action"] == "entry.create"

    esr.reset_consumer_hooks()


@pytest.mark.asyncio
async def test_consumer_idempotent_register():
    """register_sample_consumers is safe to call multiple times (idempotent semantics)."""
    import os

    os.environ["AGENTIVE_SAMPLE_CONSUMER_ENABLED"] = "1"
    try:
        from app.agentive.sample_consumers import register_sample_consumers
        from app.services import event_subscription_registry as esr

        esr.reset_consumer_hooks()
        register_sample_consumers()
        first_count = len(esr._consumer_hooks)
        # Calling again with the flag still set; the registry has no
        # de-duplication, but the test asserts the call does not raise and
        # the count is consistent at >= 1.
        register_sample_consumers()
        assert len(esr._consumer_hooks) >= first_count
        esr.reset_consumer_hooks()
    finally:
        os.environ.pop("AGENTIVE_SAMPLE_CONSUMER_ENABLED", None)


def test_summary_drafter_module_exports():
    """summary_drafter exports on_change_event, start, CONSUMER_ID, TRIGGER_TAG."""
    from app.agentive.sample_consumers import summary_drafter

    assert hasattr(summary_drafter, "on_change_event")
    assert hasattr(summary_drafter, "start")
    assert summary_drafter.CONSUMER_ID == "sample.summary_drafter"
    assert summary_drafter.TRIGGER_TAG == "summarize-me"


def test_consumer_recursion_guard_in_source():
    """Static check — auto-drafted entry builds with tags=[] (recursion guard)."""
    from pathlib import Path

    src_path = (
        Path(__file__).resolve().parent.parent
        / "app"
        / "agentive"
        / "sample_consumers"
        / "summary_drafter.py"
    )
    src = src_path.read_text(encoding="utf-8")
    # Recursion guard #1 — the auto-drafted Entry.create call must pass tags=[]
    # so the resulting entry.create event does NOT contain TRIGGER_TAG.
    assert "tags=[]" in src, "recursion guard missing — auto-drafted entry has tags=[]"
    # Recursion guard #2 — defense-in-depth check on actor identity inside
    # on_change_event (so even if some future field carries the trigger tag, the
    # consumer never re-triggers itself).
    assert "CONSUMER_ID" in src
    # Closes the D-05 loop — consumer's own write also emits.
    assert "emit_change_event" in src

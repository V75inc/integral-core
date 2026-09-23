"""Batch staging primitive — group N proposes into one StagedChange.

Covers the staging-level lifecycle (open/append/commit/cancel) and the bless
executor that replays each sub-op through the per-kind dispatch.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.agentive import staging
from app.agentive.staging import (
    StagingError,
    append_to_batch,
    cancel_batch,
    claim_open_batch_auto_continuation,
    commit_batch,
    consume_token,
    format_open_batch_marker,
    is_batch_open,
    open_batch,
    peek_open_batch,
    release_open_batch_auto_continuation,
)


@pytest.fixture(autouse=True)
def _clean_staging():
    """Reset the in-memory staging store around each test."""
    staging._reset_for_tests()
    yield
    staging._reset_for_tests()


@pytest.mark.asyncio
async def test_open_append_commit_groups_ops():
    """Two appended ops commit into a single kind='batch' StagedChange."""
    uid, sid = "u1", "s1"
    assert is_batch_open(uid, sid) is False
    await open_batch(user_id=uid, session_id=sid, label="Set up CRM")
    assert is_batch_open(uid, sid) is True

    await append_to_batch(
        user_id=uid,
        session_id=sid,
        op={
            "kind": "create_track",
            "summary": "Create Contacts track",
            "diff_human": "…",
            "diff_machine": {},
            "payload": {"title": "Contacts"},
        },
    )
    n = await append_to_batch(
        user_id=uid,
        session_id=sid,
        op={
            "kind": "create_track",
            "summary": "Create Deals track",
            "diff_human": "…",
            "diff_machine": {},
            "payload": {"title": "Deals"},
        },
    )
    assert n == 2

    sc = await commit_batch(user_id=uid, session_id=sid)
    assert sc is not None
    assert sc.kind == "batch"
    assert sc.payload["operations"][0]["payload"]["title"] == "Contacts"
    assert len(sc.payload["operations"]) == 2
    # Combined diff lists each step.
    assert "Create Contacts track" in sc.diff_human
    assert "Create Deals track" in sc.diff_human
    # Title already carries the batch summary — body must not repeat it.
    assert not (sc.summary and sc.diff_human.startswith(sc.summary))
    # Batch is cleared after commit.
    assert is_batch_open(uid, sid) is False


@pytest.mark.asyncio
async def test_commit_empty_batch_returns_none():
    """Committing an open-but-empty batch mints nothing and clears it."""
    await open_batch(user_id="u1", session_id="s1")
    sc = await commit_batch(user_id="u1", session_id="s1")
    assert sc is None
    assert is_batch_open("u1", "s1") is False


@pytest.mark.asyncio
async def test_append_without_open_raises():
    """Appending with no open batch fails closed."""
    with pytest.raises(StagingError):
        await append_to_batch(
            user_id="u1", session_id="s1", op={"kind": "create_track", "payload": {}}
        )


@pytest.mark.asyncio
async def test_cancel_batch():
    """Cancel discards the open batch; second cancel reports nothing existed."""
    await open_batch(user_id="u1", session_id="s1")
    assert await cancel_batch(user_id="u1", session_id="s1") is True
    assert await cancel_batch(user_id="u1", session_id="s1") is False


@pytest.mark.asyncio
async def test_open_is_session_scoped():
    """A batch open for one session does not leak into another."""
    await open_batch(user_id="u1", session_id="s1")
    assert is_batch_open("u1", "s1") is True
    assert is_batch_open("u1", "s2") is False
    assert is_batch_open("u2", "s1") is False


@pytest.mark.asyncio
async def test_open_batch_marker_exposes_staged_refs_for_recovery():
    """Continuation prompts must not ask for ids before the batch commits."""
    await open_batch(user_id="u1", session_id="s-recover", label="Rental app")
    await append_to_batch(
        user_id="u1",
        session_id="s-recover",
        op={
            "kind": "create_app",
            "summary": "Create rental app",
            "diff_human": "…",
            "diff_machine": {},
            "payload": {"name": "Car Rental Management"},
        },
    )
    await append_to_batch(
        user_id="u1",
        session_id="s-recover",
        op={
            "kind": "create_app_track",
            "summary": "Create Cars track",
            "diff_human": "…",
            "diff_machine": {},
            "payload": {"title": "Cars", "app_id": "{{app.id}}"},
        },
    )

    snapshot = peek_open_batch("u1", "s-recover")
    assert snapshot is not None
    assert snapshot["track_refs"] == [{"name": "Cars", "ref": "{{track.id:Cars}}"}]

    marker = format_open_batch_marker(snapshot)
    assert 'app_id="{{app.id}}"' in marker
    assert "Cars={{track.id:Cars}}" in marker
    assert "Do not list persisted apps or tracks" in marker
    assert "integral_" not in marker


@pytest.mark.asyncio
async def test_open_batch_continuation_claim_is_bounded_and_single_flight():
    """Duplicate terminal callbacks cannot start competing recovery turns."""
    await open_batch(user_id="u1", session_id="s-recover", label="Rental app")
    await append_to_batch(
        user_id="u1",
        session_id="s-recover",
        op={
            "kind": "create_app",
            "summary": "Create rental app",
            "diff_human": "…",
            "diff_machine": {},
            "payload": {"name": "Car Rental Management"},
        },
    )

    first = await claim_open_batch_auto_continuation(
        user_id="u1", session_id="s-recover", max_attempts=2
    )
    assert first is not None
    assert first["auto_continuation_attempts"] == 1
    assert (
        await claim_open_batch_auto_continuation(
            user_id="u1", session_id="s-recover", max_attempts=2
        )
        is None
    )

    await release_open_batch_auto_continuation(user_id="u1", session_id="s-recover")
    second = await claim_open_batch_auto_continuation(
        user_id="u1", session_id="s-recover", max_attempts=2
    )
    assert second is not None
    assert second["auto_continuation_attempts"] == 2

    await release_open_batch_auto_continuation(user_id="u1", session_id="s-recover")
    assert (
        await claim_open_batch_auto_continuation(
            user_id="u1", session_id="s-recover", max_attempts=2
        )
        is None
    )


@pytest.mark.asyncio
async def test_batch_executor_replays_each_op(monkeypatch):
    """On bless, the batch executor dispatches every sub-op in order."""
    from app.agentive import staging_executors

    calls: List[Dict[str, Any]] = []

    async def _fake_dispatch(*, user_id: str, kind: str, payload: Dict[str, Any]):
        calls.append({"kind": kind, "payload": payload})
        return {"ok": True, "kind": kind}

    monkeypatch.setattr(staging_executors, "dispatch", _fake_dispatch)

    result = await staging_executors._x_batch(
        "u1",
        {
            "operations": [
                {"kind": "create_track", "payload": {"title": "A"}},
                {"kind": "create_entry", "payload": {"track_id": "t", "title": "B"}},
            ]
        },
    )
    assert result["batched"] is True
    assert result["completed"] == 2 and result["total"] == 2
    assert [c["kind"] for c in calls] == ["create_track", "create_entry"]


@pytest.mark.asyncio
async def test_batch_resolves_intra_batch_refs(monkeypatch):
    """A later op's {{app.id}} resolves to the id the earlier op created."""
    from app.agentive import staging_executors

    seen: List[Dict[str, Any]] = []

    async def _fake_dispatch(*, user_id: str, kind: str, payload: Dict[str, Any]):
        seen.append({"kind": kind, "payload": payload})
        if kind == "create_app":
            return {"app": {"id": "n.App.REAL123"}}
        return {"track": {"id": "n.Track.T1"}}

    monkeypatch.setattr(staging_executors, "dispatch", _fake_dispatch)

    result = await staging_executors._x_batch(
        "u1",
        {
            "operations": [
                {"kind": "create_app", "payload": {"name": "QA CRM"}},
                {
                    "kind": "create_track",
                    "payload": {"title": "Contacts", "app_id": "{{app.id}}"},
                },
            ]
        },
    )
    assert result["batched"] is True
    # The track op's placeholder was replaced with the real created app id.
    assert seen[1]["payload"]["app_id"] == "n.App.REAL123"


def test_resolve_refs_leaves_unknown_token():
    """An unresolved token is left verbatim (op then fails its own validation)."""
    from app.agentive import staging_executors

    out = staging_executors._resolve_batch_refs(
        {"app_id": "{{app.id}}", "title": "x"}, {}
    )
    assert out["app_id"] == "{{app.id}}"


def test_extract_created_shapes():
    """Created-entity extraction returns (type, id, name) for wrapped + bare."""
    from app.agentive import staging_executors as sx

    assert sx._extract_created({"app": {"id": "A", "name": "CRM"}}) == (
        "app",
        "A",
        "CRM",
    )
    assert sx._extract_created({"track": {"id": "T", "title": "Authors"}}) == (
        "track",
        "T",
        "Authors",
    )
    assert sx._extract_created({"entry": {"id": "E"}}) == ("entry", "E", None)
    assert sx._extract_created({"tag": {"id": "G"}}) == ("tag", "G", None)
    assert sx._extract_created({"id": "X"}) == (None, "X", None)
    assert sx._extract_created({"error": True}) == (None, None, None)


def test_named_batch_tokens_are_order_independent():
    """A named ``{{track.id:Authors}}`` resolves to the track created with that
    title even when a later track is the most-recent — the positional
    ``{{track.id}}`` alone would mis-target. Regression for the multi-track
    scaffold bug (views/entries landing on the wrong track)."""
    from app.agentive.staging_executors import _resolve_batch_refs

    ctx = {
        "app.id": "A1",
        "track.id": "T_books",  # positional = last-created (Books)
        "track.id:Authors": "T_auth",
        "track.id:Books": "T_books",
        "entry.id:Jane Austen": "E_jane",
    }
    assert _resolve_batch_refs("{{track.id:Authors}}", ctx) == "T_auth"
    assert _resolve_batch_refs("{{ track.id:Books }}", ctx) == "T_books"
    assert _resolve_batch_refs("{{entry.id:Jane Austen}}", ctx) == "E_jane"
    # Positional still works (last-created) and app ref unchanged.
    assert _resolve_batch_refs("{{track.id}}", ctx) == "T_books"
    assert _resolve_batch_refs("{{app.id}}", ctx) == "A1"
    # Unknown named token is left verbatim so the op fails its own validation.
    assert _resolve_batch_refs("{{track.id:Missing}}", ctx) == "{{track.id:Missing}}"


@pytest.mark.asyncio
async def test_batch_resolves_tag_ref(monkeypatch):
    """create_tag → add_entry_tag with {{tag.id}} resolves to the new tag id."""
    from app.agentive import staging_executors

    seen: List[Dict[str, Any]] = []

    async def _fake_dispatch(*, user_id: str, kind: str, payload: Dict[str, Any]):
        seen.append({"kind": kind, "payload": payload})
        if kind == "create_tag":
            return {"tag": {"id": "n.Tag.QA1"}}
        return {"ok": True}

    monkeypatch.setattr(staging_executors, "dispatch", _fake_dispatch)
    await staging_executors._x_batch(
        "u1",
        {
            "operations": [
                {"kind": "create_tag", "payload": {"name": "qa-reviewed"}},
                {
                    "kind": "add_entry_tag",
                    "payload": {"entry_id": "e1", "tag_id": "{{tag.id}}"},
                },
            ]
        },
    )
    assert seen[1]["payload"]["tag_id"] == "n.Tag.QA1"


@pytest.mark.asyncio
async def test_batch_executor_fail_stops(monkeypatch):
    """A failing sub-op stops the batch and reports the partial progress."""
    from app.agentive import staging_executors

    async def _fake_dispatch(*, user_id: str, kind: str, payload: Dict[str, Any]):
        if kind == "create_entry":
            return {"error": True, "message": "denied"}
        return {"ok": True}

    monkeypatch.setattr(staging_executors, "dispatch", _fake_dispatch)

    result = await staging_executors._x_batch(
        "u1",
        {
            "operations": [
                {"kind": "create_track", "payload": {}},
                {"kind": "create_entry", "payload": {}},
                {"kind": "create_track", "payload": {}},
            ]
        },
    )
    assert result["error"] is True
    assert result["error_code"] == "batch_partial_failure"
    assert result["completed"] == 1 and result["total"] == 3


@pytest.mark.asyncio
async def test_committed_batch_token_consumes_as_batch_kind():
    """The minted batch token consumes under expected_kind='batch'."""
    await open_batch(user_id="u1", session_id="s1", label="wf")
    await append_to_batch(
        user_id="u1",
        session_id="s1",
        op={
            "kind": "create_track",
            "summary": "t",
            "diff_human": "t",
            "diff_machine": {},
            "payload": {"title": "T"},
        },
    )
    sc = await commit_batch(user_id="u1", session_id="s1")
    assert sc is not None
    # Bless then consume — payload round-trips with the op list intact.
    await staging.bless_token(user_id="u1", token=sc.token)
    payload = await consume_token(user_id="u1", token=sc.token, expected_kind="batch")
    assert payload["operations"][0]["payload"]["title"] == "T"


# ---------------------------------------------------------------------------
# Transcript terminal-state rewrite (revoked/consumed reload fix)
# ---------------------------------------------------------------------------


def test_coerce_tool_result_dict_and_jsonstring():
    """Result coercion handles dict, JSON-string, and non-envelope inputs."""
    from app.agentive import staging as st

    d, was_str = st._coerce_tool_result({"a": 1})
    assert d == {"a": 1} and was_str is False

    d, was_str = st._coerce_tool_result('{"a": 1}')
    assert d == {"a": 1} and was_str is True

    d, was_str = st._coerce_tool_result("not json")
    assert d is None and was_str is True

    d, was_str = st._coerce_tool_result(42)
    assert d is None and was_str is False


def test_rewrite_staged_state_matches_token_dict_result():
    """A dict staged-change result for the token is rewritten in place."""
    from app.agentive import staging as st

    parts = [
        {"type": "text", "text": "hi"},
        {
            "type": "tool-call",
            "toolCallId": "c1",
            "result": {"_kind": "staged_change", "token": "TK", "state": "pending"},
        },
    ]
    out, changed = st._rewrite_staged_state_in_parts(parts, "TK", "revoked")
    assert changed is True
    assert out[1]["result"]["state"] == "revoked"
    assert out[0] == {"type": "text", "text": "hi"}  # untouched
    # original not mutated (pure)
    assert parts[1]["result"]["state"] == "pending"


def test_rewrite_staged_state_jsonstring_result_roundtrips():
    """A JSON-string result is parsed, rewritten, and re-serialized as a string."""
    import json as _json

    from app.agentive import staging as st

    parts = [
        {
            "type": "tool-call",
            "result": _json.dumps(
                {"_kind": "staged_change", "token": "TK", "state": "pending"}
            ),
        }
    ]
    out, changed = st._rewrite_staged_state_in_parts(parts, "TK", "consumed")
    assert changed is True
    assert isinstance(out[0]["result"], str)
    assert _json.loads(out[0]["result"])["state"] == "consumed"


def test_rewrite_staged_state_no_match_leaves_parts():
    """Non-matching token / non-staged parts are left unchanged."""
    from app.agentive import staging as st

    parts = [
        {
            "type": "tool-call",
            "result": {"_kind": "staged_change", "token": "OTHER", "state": "pending"},
        },
        {"type": "tool-call", "result": {"_kind": "other", "token": "TK"}},
    ]
    out, changed = st._rewrite_staged_state_in_parts(parts, "TK", "revoked")
    assert changed is False
    assert out == parts


def test_rewrite_staged_envelope_persists_consumed_nav():
    """consumed_nav is written onto the staged-change tool-call envelope."""
    from app.agentive import staging as st

    consumed_nav = {
        "trackId": "n.Track.aaaaaaaaaaaaaaaaaaaaaaaa",
        "created": [
            {
                "kind": "track",
                "id": "n.Track.aaaaaaaaaaaaaaaaaaaaaaaa",
                "title": "Sprint board",
            }
        ],
    }
    parts = [
        {
            "type": "tool-call",
            "result": {
                "_kind": "staged_change",
                "token": "TK",
                "state": "consumed",
            },
        }
    ]
    out, changed = st._rewrite_staged_envelope_in_parts(
        parts, "TK", consumed_nav=consumed_nav
    )
    assert changed is True
    assert out[0]["result"]["consumed_nav"] == consumed_nav


@pytest.mark.asyncio
async def test_open_batch_reenter_keeps_ops():
    """A second begin_batch must not wipe staged creates (empty commit bug)."""
    from app.agentive.staging import (
        _reset_for_tests,
        append_to_batch,
        commit_batch,
        open_batch,
    )

    _reset_for_tests()
    await open_batch(user_id="u1", session_id="s-reenter", label="first")
    await append_to_batch(
        user_id="u1",
        session_id="s-reenter",
        op={
            "kind": "create_track",
            "summary": "App",
            "diff_human": "create app",
            "diff_machine": {},
            "payload": {"name": "App"},
        },
    )
    await open_batch(user_id="u1", session_id="s-reenter", label="second")
    sc = await commit_batch(user_id="u1", session_id="s-reenter", summary="build")
    assert sc is not None
    ops = (sc.diff_machine or {}).get("operations") or []
    assert len(ops) == 1
    assert ops[0]["kind"] == "create_track"

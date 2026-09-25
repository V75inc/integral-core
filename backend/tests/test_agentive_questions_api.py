"""Agentive clarifying-question HTTP surface — JVSpatialAPIException conventions.

Forbidden ownership must be 403 (not HTTP 200 ``{"ok": false}``). Stale
question ids must be 422. Schemas live under ``app.schemas.agentive.questions``.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest
import yaml
from httpx import AsyncClient

from app.models.nodes import ChatThread
from app.services import chat_threads
from app.services import prompt_queue as pq

OPTIONS = [
    {"label": "Rebuild", "description": "Start fresh"},
    {"label": "Extend", "description": "Add fields"},
]


def _principal_id(test_user) -> str:
    """JWT subject / ChatThread.user_id is the auth principal, not the User node id."""
    return getattr(test_user, "user_id", None) or test_user.id


def _qid(thread: ChatThread) -> str:
    for item in pq.get_queue(thread)["items"]:
        if item.get("kind") == "question" and item.get("status") == "pending":
            return str(item.get("id") or "")
    return ""


def _has_pending_q(thread: ChatThread) -> bool:
    return bool(_qid(thread))


async def _owned_thread_with_question(user_id: str, session: str) -> ChatThread:
    thread = await ChatThread.create(user_id=user_id, provider_session_id=session)
    asked = await chat_threads.record_pending_question(
        user_id=user_id,
        session_id=session,
        question="Rebuild or extend?",
        options=OPTIONS,
    )
    assert asked.get("question_id"), asked
    refreshed = await ChatThread.get(thread.id)
    assert refreshed is not None
    return refreshed


@pytest.mark.asyncio
async def test_pending_question_forbidden_is_403_not_ok_false(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
):
    thread = await _owned_thread_with_question(
        _principal_id(test_user), "sess-q-forbidden-pend"
    )

    ok = await authenticated_client.get(
        "/api/agentive/questions/pending",
        params={"thread_id": thread.id},
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["ok"] is True
    assert body["pending"] is not None

    forbidden = await second_user_client.get(
        "/api/agentive/questions/pending",
        params={"thread_id": thread.id},
    )
    assert forbidden.status_code == 403, forbidden.text
    payload = forbidden.json()
    assert payload.get("ok") is not False or "error_code" in payload
    assert payload.get("error_code") == "insufficient_permissions"


@pytest.mark.asyncio
async def test_answer_question_forbidden_is_403(
    authenticated_client: AsyncClient,
    second_user_client: AsyncClient,
    test_user,
):
    thread = await _owned_thread_with_question(
        _principal_id(test_user), "sess-q-forbidden-ans"
    )
    qid = _qid(thread)

    forbidden = await second_user_client.post(
        "/api/agentive/questions/answer",
        json={
            "thread_id": thread.id,
            "question_id": qid,
            "choices": ["Extend"],
        },
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json().get("error_code") == "insufficient_permissions"
    still = await ChatThread.get(thread.id)
    assert still is not None
    assert _has_pending_q(still)


@pytest.mark.asyncio
async def test_answer_stale_question_is_422(
    authenticated_client: AsyncClient,
    test_user,
):
    thread = await _owned_thread_with_question(_principal_id(test_user), "sess-q-stale")

    stale = await authenticated_client.post(
        "/api/agentive/questions/answer",
        json={
            "thread_id": thread.id,
            "question_id": "not-the-live-one",
            "choices": ["Extend"],
        },
    )
    assert stale.status_code == 422, stale.text
    details = stale.json().get("details") or {}
    assert details.get("error_code") == "stale_question"
    still = await ChatThread.get(thread.id)
    assert still is not None
    assert _has_pending_q(still)


@pytest.mark.asyncio
async def test_answer_question_happy_path(
    authenticated_client: AsyncClient,
    test_user,
):
    thread = await _owned_thread_with_question(_principal_id(test_user), "sess-q-ok")
    qid = _qid(thread)

    answered = await authenticated_client.post(
        "/api/agentive/questions/answer",
        json={
            "thread_id": thread.id,
            "question_id": qid,
            "choices": ["Extend"],
        },
    )
    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert body["ok"] is True
    assert body["cleared"] is True
    assert body["choices"] == ["Extend"]
    cleared = await ChatThread.get(thread.id)
    assert cleared is not None
    assert not _has_pending_q(cleared)


def test_mcp_tool_name_overrides_canonical_home():
    """Single definition site is agentive.tooling.name_overrides (grep gate)."""
    app_root = Path(__file__).resolve().parents[1] / "app"
    hits = []
    for path in app_root.rglob("*.py"):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"^MCP_TOOL_NAME_OVERRIDES\s*[:=]", line):
                hits.append(f"{path}:{i}")
    assert len(hits) == 1, hits
    assert "name_overrides.py" in hits[0]


def test_tool_manifest_summary_matches_tool_counts():
    manifest_path = (
        Path(__file__).resolve().parents[1] / "app" / "agentive" / "tool_manifest.yaml"
    )
    data = yaml.safe_load(manifest_path.read_text())
    statuses: Counter = Counter()
    ops: Counter = Counter()
    prios: Counter = Counter()
    for domain in (data.get("domains") or {}).values():
        for tool in domain.get("tools") or []:
            statuses[tool.get("status")] += 1
            ops[tool.get("op_class")] += 1
            prios[tool.get("priority")] += 1
    summary = data["summary"]
    assert summary["total_tools"] == sum(statuses.values())
    assert summary["existing_tools"] == statuses["existing"]
    assert summary["gap_tools_proposed"] == statuses["gap"]
    assert summary["by_op_class"] == dict(ops)
    assert summary["by_priority"] == dict(prios)
    assert summary["domains"] == len(data["domains"])

"""Old staged design cards cannot approve or overwrite a current outline."""

import pytest

from app.agentive.staging_executors import dispatch
from app.services import chat_threads


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"session_id": "earlier-session"},
        {
            "session_id": "n.ChatThread.current",
            "summary": "Unreviewed replacement",
            "proposal": "Different design",
            "idempotency_key": "claimed-approval",
        },
    ],
)
async def test_legacy_design_card_refuses_without_resolving_or_mutating_thread(
    monkeypatch, payload
):
    """Persisted compatibility cards are refused before any thread can be read."""

    async def forbidden_lookup(*args, **kwargs):
        pytest.fail("An obsolete design card resolved a live conversation")

    monkeypatch.setattr(chat_threads, "get_thread_by_session", forbidden_lookup)
    result = await dispatch(
        user_id="earlier-principal", kind="design_proposal", payload=payload
    )
    assert result["error"] is True
    assert result["error_code"] == "design_approval_reconciliation_required"
    assert not result.get("approved")
    assert not result.get("needs_agent_build")
    assert "Unreviewed replacement" not in str(result)

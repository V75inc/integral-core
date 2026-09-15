"""Tests for proactive message → ChatThread bridge."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_persist_proactive_message_on_thread_returns_none_when_missing() -> None:
    from app.services.chat_proactive_bridge import persist_proactive_message_on_thread

    with patch(
        "app.services.chat_proactive_bridge.find_thread_by_provider_session",
        new=AsyncMock(return_value=None),
    ):
        result = await persist_proactive_message_on_thread(
            user_id="u1",
            session_id="sess-1",
            content="Hello",
        )
    assert result is None


@pytest.mark.asyncio
async def test_persist_proactive_message_on_thread_appends_and_notifies() -> None:
    from app.services.chat_proactive_bridge import persist_proactive_message_on_thread

    thread = type("T", (), {"id": "thread-1"})()
    message = type("M", (), {"id": "msg-1"})()

    with (
        patch(
            "app.services.chat_proactive_bridge.find_thread_by_provider_session",
            new=AsyncMock(return_value=thread),
        ),
        patch(
            "app.services.chat_proactive_bridge.chat_store.append_message",
            new=AsyncMock(return_value=message),
        ) as append_mock,
        patch(
            "app.services.chat_proactive_bridge.notify_thread_message",
            new=AsyncMock(),
        ) as notify_mock,
    ):
        result = await persist_proactive_message_on_thread(
            user_id="u1",
            session_id="sess-1",
            content="Heads up",
        )

    assert result == "thread-1"
    append_mock.assert_awaited_once()
    notify_mock.assert_awaited_once()

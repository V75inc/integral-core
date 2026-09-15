"""Session rebind when an embedded jvagent provider_session_id is orphaned."""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from app.providers import jvagent_embed


class _FakeEmbed:
    def __init__(self, passes: List[List[Dict[str, Any]]]) -> None:
        self._passes = list(passes)
        self.calls: List[Optional[str]] = []

    def interact_stream(self, **kwargs: Any) -> AsyncIterator[Dict[str, Any]]:
        self.calls.append(kwargs.get("session_id"))
        envelopes = self._passes.pop(0) if self._passes else []

        async def _gen() -> AsyncIterator[Dict[str, Any]]:
            for env in envelopes:
                yield env

        return _gen()


def _install_fake_embed(monkeypatch: pytest.MonkeyPatch, fake: _FakeEmbed) -> None:
    import sys
    import types

    mod = types.ModuleType("jvagent")
    embed_mod = types.ModuleType("jvagent.embed")
    embed_mod.interact_stream = fake.interact_stream  # type: ignore[attr-defined]
    embed_mod.cancel_interact = MagicMock(return_value=False)  # type: ignore[attr-defined]
    mod.embed = embed_mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "jvagent", mod)
    monkeypatch.setitem(sys.modules, "jvagent.embed", embed_mod)

    async def _passthrough(envelope: Dict[str, Any], _state: Any):
        if envelope.get("type") == "final":
            yield {"type": "text-delta", "delta": envelope.get("text") or ""}
            yield {"type": "message-finish"}

    monkeypatch.setattr(jvagent_embed, "translate_envelope", _passthrough)
    monkeypatch.setattr(jvagent_embed, "fresh_translator_state", lambda **_: {})
    monkeypatch.setattr(jvagent_embed, "_emit_tool_progress", lambda *_a, **_k: [])


@pytest.mark.asyncio
async def test_session_rebind_on_interaction_not_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeEmbed(
        [
            [
                {
                    "type": "error",
                    "code": "interaction_not_created",
                    "message": "Interaction was not created [session_resolution_error]",
                    "details": {"bootstrap_error": "session_resolution_error"},
                }
            ],
            [
                {
                    "type": "start",
                    "session_id": "fresh-session",
                    "interaction_id": "i1",
                    "user_id": "u1",
                },
                {"type": "final", "text": "hello"},
            ],
        ]
    )
    _install_fake_embed(monkeypatch, fake)

    events: List[Dict[str, Any]] = []
    async for ev in jvagent_embed.stream_jvagent_embed_turn(
        agent_id="agent-1",
        user_id="user-1",
        text="hi",
        session_id="stale-session",
        channel="integral",
    ):
        events.append(ev)

    assert fake.calls == ["stale-session", None]
    assert any(e.get("clear_provider_session") for e in events)
    assert any(e.get("provider_session_id") == "fresh-session" for e in events)
    assert not any(e.get("type") == "error" for e in events)


@pytest.mark.asyncio
async def test_no_rebind_without_prior_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeEmbed(
        [
            [
                {
                    "type": "error",
                    "code": "interaction_not_created",
                    "message": "Interaction was not created",
                }
            ],
        ]
    )
    _install_fake_embed(monkeypatch, fake)

    events: List[Dict[str, Any]] = []
    async for ev in jvagent_embed.stream_jvagent_embed_turn(
        agent_id="agent-1",
        user_id="user-1",
        text="hi",
        session_id=None,
        channel="integral",
    ):
        events.append(ev)

    assert fake.calls == [None]
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["code"] == "interaction_not_created"
    # Technical message scrubbed for the browser.
    assert "Interaction was not created" not in events[0]["message"]

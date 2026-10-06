"""The chat host delegates cancellation to the selected harness adapter."""

from types import SimpleNamespace

from app.services.chat_providers.base import register_provider_cancel_hook


class _TurnHandle:
    def __init__(self) -> None:
        self.cancel_hook = None

    def register_cancel_hook(self, hook) -> None:
        self.cancel_hook = hook

    def cancel(self) -> None:
        assert self.cancel_hook is not None
        self.cancel_hook()


def test_native_provider_cancel_does_not_import_jvagent() -> None:
    """A native harness receives cancellation through its own adapter method."""
    called = []
    turn = _TurnHandle()
    provider = SimpleNamespace(
        id="pydantic-ai",
        cancel_turn=lambda *, thread_id: called.append(thread_id),
    )

    register_provider_cancel_hook(turn, thread_id="thread-1", provider=provider)
    turn.cancel()

    assert called == ["thread-1"]


def test_pydantic_provider_cancel_hook_is_synchronous() -> None:
    """TurnRegistry.cancel is sync; native token cancellation must be too."""
    from app.services.chat_providers.pydantic_ai_provider import PydanticAIProvider

    called = []

    class Token:
        def cancel(self):
            called.append("cancelled")

    provider = PydanticAIProvider()
    provider._active_tokens["thread-3"] = Token()
    turn = _TurnHandle()
    register_provider_cancel_hook(turn, thread_id="thread-3", provider=provider)

    turn.cancel()

    assert called == ["cancelled"]


def test_legacy_jvagent_provider_keeps_cancel_fallback(monkeypatch) -> None:
    """Legacy provider doubles still cancel embedded jvagent interactions."""
    from app.providers import jvagent_embed

    called = []
    monkeypatch.setattr(
        jvagent_embed, "cancel_interact", lambda *, thread_id: called.append(thread_id)
    )
    turn = _TurnHandle()

    register_provider_cancel_hook(
        turn, thread_id="thread-2", provider=SimpleNamespace(id="jvagent")
    )
    turn.cancel()

    assert called == ["thread-2"]

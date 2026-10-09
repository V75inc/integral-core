"""Offline provider double for provider-neutral HTTP/transcript contracts."""

from app.services.chat_providers.base import ChatBackendProvider


class StandaloneTestProvider(ChatBackendProvider):
    id = "test-provider"
    label = "Test"
    capabilities = {
        "reasoning": True,
        "tools": True,
        "attachments": True,
        "vision": True,
        "voice": False,
    }

    def is_available(self):
        return True

    async def list_agents(self):
        return [{"id": "aiva", "name": "Test", "description": "Offline fixture"}]

    async def stream_turn(self, ctx):
        if False:
            yield {}

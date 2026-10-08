"""Remote MCP protocol failures cannot become successful receipt payloads."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.agentive.connectors import mcp_client

pytestmark = pytest.mark.smoke


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "structured,texts",
    [({"secret": "never echo"}, []), (None, ["never echo"]), (None, [])],
)
async def test_remote_error_precedes_every_payload_projection(
    monkeypatch, structured, texts
):
    class Session:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                isError=True,
                structuredContent=structured,
                content=[SimpleNamespace(text=text) for text in texts],
            )

    @asynccontextmanager
    async def session(auth):
        yield Session()

    monkeypatch.setattr(mcp_client, "open_mcp_session", session)
    with pytest.raises(
        RuntimeError, match="Remote MCP tool reported an error"
    ) as error:
        await mcp_client.call_remote_tool({}, "fetch", {"url": "https://example.com/"})
    assert "never echo" not in str(error.value)


@pytest.mark.asyncio
async def test_successful_structured_payload_is_preserved(monkeypatch):
    class Session:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                isError=False, structuredContent={"rows": ["source"]}, content=[]
            )

    @asynccontextmanager
    async def session(auth):
        yield Session()

    monkeypatch.setattr(mcp_client, "open_mcp_session", session)
    assert await mcp_client.call_remote_tool({}, "fetch") == {"rows": ["source"]}

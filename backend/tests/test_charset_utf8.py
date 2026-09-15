"""Charset is declared on JSON responses so clients decode UTF-8 correctly."""

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_json_response_includes_utf8_charset() -> None:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    ct = r.headers.get("content-type") or ""
    assert "charset=utf-8" in ct.lower()

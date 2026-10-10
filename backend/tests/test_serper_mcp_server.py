from __future__ import annotations

import httpx
import pytest

from app.connectors import serper_mcp_server


def test_search_web_normalizes_and_caps_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "connector-test-key")
    calls = {}

    def fake_post(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "organic": [
                    {
                        "title": f"Result {i}",
                        "link": f"https://example.test/{i}",
                        "snippet": "Evidence",
                    }
                    for i in range(12)
                ]
            },
        )

    monkeypatch.setattr(serper_mcp_server.httpx, "post", fake_post)
    result = serper_mcp_server._search("  startup grants  ", gl="GY", num=99)

    assert calls["url"] == serper_mcp_server.SERPER_ENDPOINT
    assert calls["kwargs"]["headers"]["X-API-KEY"] == "connector-test-key"
    assert calls["kwargs"]["json"] == {
        "q": "startup grants",
        "gl": "gy",
        "hl": "en",
        "num": 10,
    }
    assert result["result_count"] == 10
    assert result["results"][0] == {
        "title": "Result 0",
        "url": "https://example.test/0",
        "snippet": "Evidence",
    }
    assert "connector-test-key" not in repr(result)
    assert result["source_kind"] == "search_snippets_not_fetched_pages"
    assert result["retrieved_at"]


def test_search_web_requires_key_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="not configured"):
        serper_mcp_server._search("startup grants")
    monkeypatch.setenv("SERPER_API_KEY", "connector-test-key")
    with pytest.raises(ValueError, match="query must not be empty"):
        serper_mcp_server._search(" ")

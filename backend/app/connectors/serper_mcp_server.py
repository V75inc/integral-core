"""Small stdio MCP adapter for Serper's Google Search API.

The connector process receives its credential from the encrypted Connector
auth state or the server environment. It never logs or returns the key.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

SERPER_ENDPOINT = "https://google.serper.dev/search"
MAX_RESULTS = 10
TIMEOUT_SECONDS = 20.0

mcp = FastMCP("Integral Serper Web Search")


def _search(
    query: str, *, gl: str = "us", hl: str = "en", num: int = 5
) -> dict[str, Any]:
    key = (os.getenv("SERPER_API_KEY") or "").strip()
    if not key:
        raise ValueError(
            "Serper is not configured. Add a Serper API key to this Connector "
            "or configure SERPER_API_KEY for the Integral service."
        )
    normalized_query = (query or "").strip()
    if not normalized_query:
        raise ValueError("query must not be empty")
    result_count = max(1, min(int(num), MAX_RESULTS))
    response = httpx.post(
        SERPER_ENDPOINT,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={
            "q": normalized_query,
            "gl": (gl or "us")[:2].lower(),
            "hl": (hl or "en")[:2].lower(),
            "num": result_count,
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    organic = payload.get("organic") if isinstance(payload, dict) else []
    results = []
    for item in organic or []:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        title = str(item.get("title") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        if link and title:
            results.append({"title": title, "url": link, "snippet": snippet})
        if len(results) >= result_count:
            break
    return {
        "query": normalized_query,
        "results": results,
        "result_count": len(results),
        "source": "Serper",
    }


@mcp.tool()
def search_web(
    query: str, gl: str = "us", hl: str = "en", num: int = 5
) -> dict[str, Any]:
    """Search the public web. Returns titles, URLs, and snippets for sources."""
    return _search(query, gl=gl, hl=hl, num=num)


def main() -> None:
    """Run the Serper MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

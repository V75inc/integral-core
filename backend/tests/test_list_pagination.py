"""Agent list auto-pagination helpers."""

from __future__ import annotations

import pytest

from app.agentive.tooling.list_pagination import (
    agent_list_kwargs,
    expand_agent_list_pages,
)


def test_agent_list_kwargs_sets_max_limit_when_unpaged():
    kw = agent_list_kwargs(
        "integral_list_tracks",
        {},
        {"app_id": "n.WorkspaceApp.x"},
    )
    assert kw["limit"] == 100
    assert kw["app_id"] == "n.WorkspaceApp.x"


def test_agent_list_kwargs_respects_explicit_limit():
    kw = agent_list_kwargs(
        "integral_list_tracks",
        {"limit": 5},
        {},
    )
    assert "limit" not in kw


@pytest.mark.asyncio
async def test_expand_agent_list_pages_merges_follow_on_pages():
    pages = {
        None: {
            "tracks": [{"id": "t1"}, {"id": "t2"}],
            "has_more": True,
            "next_cursor": "c2",
            "total": 3,
        },
        "c2": {
            "tracks": [{"id": "t3"}],
            "has_more": False,
            "next_cursor": None,
            "total": 3,
        },
    }

    async def fetch(extra):
        return pages[extra.get("cursor")]

    out = await expand_agent_list_pages(
        "integral_list_tracks",
        safe_args={},
        first_page=pages[None],
        fetch_page=fetch,
    )
    assert [t["id"] for t in out["tracks"]] == ["t1", "t2", "t3"]
    assert out["has_more"] is False
    assert out["next_cursor"] is None
    assert out["total"] == 3


@pytest.mark.asyncio
async def test_expand_skipped_when_cursor_in_args():
    first = {"tracks": [{"id": "t1"}], "has_more": True, "next_cursor": "x"}

    async def fetch(_extra):
        raise AssertionError("should not fetch more pages")

    out = await expand_agent_list_pages(
        "integral_list_tracks",
        safe_args={"cursor": "x"},
        first_page=first,
        fetch_page=fetch,
    )
    assert out is first

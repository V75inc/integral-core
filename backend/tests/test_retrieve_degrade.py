"""Retrieve endpoint degrade + graph text-search helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.retrieve import _entry_matches_query, _is_vector_store_unavailable


class _FakeEntry:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_entry_matches_query_title_substring():
    entry = _FakeEntry(title="Alpha Project", body="", type="task")
    assert _entry_matches_query(entry, "alpha") is True
    assert _entry_matches_query(entry, "beta") is False


def test_entry_matches_query_empty_query_matches_all():
    entry = _FakeEntry(title="x", body="", type="")
    assert _entry_matches_query(entry, "   ") is True


def test_entry_matches_query_natural_language_keyword_overlap():
    """An NL query keyword-matches an entry whose title/body holds the terms.

    Pre-keyword-tokenizer this matched NOTHING — the whole NL string was
    substring-checked against title/body and never appeared verbatim.
    """
    entry = _FakeEntry(
        title="Acme Inc company wiki", body="Notes about the customer", type="page"
    )
    # "what is the" are stopwords; "acme"/"company" survive → ANY hits.
    assert _entry_matches_query(entry, "What is the Acme Inc company?") is True
    # No surviving term hits this entry.
    assert _entry_matches_query(entry, "globex partnership") is False


def test_entry_matches_query_boolean_or_blob():
    """An 'A OR B OR C' blob splits into terms and matches on ANY of them."""
    entry = _FakeEntry(title="Globex roadmap", body="", type="doc")
    assert _entry_matches_query(entry, "Acme OR Globex OR Initech") is True
    assert _entry_matches_query(entry, "Acme OR Initech") is False


def test_entry_matches_query_body_and_type_terms():
    entry = _FakeEntry(title="Untitled", body="quarterly budget review", type="report")
    assert _entry_matches_query(entry, "budget") is True
    assert _entry_matches_query(entry, "report") is True  # matches the type token


@pytest.mark.asyncio
async def test_retrieve_graph_ranks_by_keyword_overlap(monkeypatch):
    """Graph mode orders matched entries by keyword-overlap count DESC.

    Two entries match the multi-term query; the one hitting BOTH terms must
    rank above the one hitting only one, regardless of traversal order.
    """
    from app.api import retrieve as retrieve_mod
    from app.schemas.policy import Subject

    # e_one hits only "budget"; e_both hits "budget" + "marketing".
    e_one = _FakeEntry(id="n.Entry.one", title="Budget notes", body="", track_id="t1")
    e_both = _FakeEntry(
        id="n.Entry.both",
        title="Marketing budget plan",
        body="",
        track_id="t1",
    )

    async def fake_fetch_page(
        user_id,
        *,
        track_id=None,
        workspace_id=None,
        q=None,
        limit=None,
        include_total=False,
    ):
        # Traversal order puts the weaker match FIRST to prove the re-rank.
        return [e_one, e_both], None

    # _retrieve_graph now sources entries via the paginated listing helper
    # (perf) rather than permissions.get_user_accessible_entries.
    monkeypatch.setattr(
        "app.services.entry_listing.fetch_accessible_entries_page", fake_fetch_page
    )

    payload = SimpleNamespace(query="marketing budget", scope=None, filters=None)
    subject = Subject(kind="human", id="u1")
    results, dropped, examined = await retrieve_mod._retrieve_graph(
        subject, payload, k=10, top_n=10
    )
    ids = [r.entry_id for r in results]
    assert ids == ["n.Entry.both", "n.Entry.one"]
    assert examined == 2
    assert dropped == 0


def test_is_vector_store_unavailable_detects_search_not_enabled():
    try:
        from pymongo.errors import OperationFailure
    except ImportError:
        pytest.skip("pymongo not installed")
    exc = OperationFailure(
        "Using $search and $vectorSearch aggregation stages requires additional configuration.",
        code=31082,
    )
    assert _is_vector_store_unavailable(exc) is True
    assert _is_vector_store_unavailable(ValueError("other")) is False

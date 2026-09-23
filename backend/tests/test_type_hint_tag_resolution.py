"""Plan 07-01 — MCP-04 type_hint tag-resolution coverage.

Asserts that the MCP-04 keyword resolver consulted by ``integral_create_track``
+ ``integral_create_app`` (and by ``GET /api/operational-models?type_hint=...``
via the ``resolve_type_hint`` helper) matches against ``manifest.package.tags``
in addition to ``cp.name``. Pre-Plan-07-01 the resolver consulted ``cp.name``
+ ``package.name`` + ``package.description`` only, so ``type_hint='bug'``
silently fell through (no token match against ``Bug Tracking`` /
``bug-tracking``).

Four cases:
  (a) ``type_hint='bug'``        -> resolves to Bug Tracking via tag.
  (b) ``type_hint='zettelkasten'`` -> resolves to Personal Knowledge
        Base via tag (no name/description match exists pre-Plan-07-01).
  (c) ``type_hint='bug tracking'`` -> resolves to Bug Tracking via name
        match (no regression).
  (d) ``type_hint='nonexistent-domain-xyz'`` -> returns no match.

The resolver is exercised through ``resolve_type_hint`` (the canonical
keyword-resolution helper that backs ``GET /api/operational-models?type_hint=...``,
``integral_list_models``, ``integral_create_track``, ``integral_create_app``).
See ``docs/INVARIANTS.md`` I-LIB-01 / I-LIB-02 for the tag-semantics contract.
"""

from __future__ import annotations

import pytest

from app.api.operational_models import resolve_type_hint

pytestmark = pytest.mark.library


@pytest.mark.asyncio
async def test_bug_hint_resolves_via_tag(library_catalog_seeded) -> None:
    """``type_hint='bug'`` matches Bug Tracking.

    Empirically resolved pre-Plan-07-01 too (the tokenizer splits
    ``bug-tracking`` -> ``{bug, tracking}`` so ``bug`` already matched
    via the package slug). Plan 07-01's ``bug`` tag REINFORCES the
    match (tag haystack now also contains 'bug') and protects against
    future slug renames silently breaking the resolution. Regression
    sentinel — this case MUST continue to pass.
    """
    matches = await resolve_type_hint("bug")
    assert matches, "type_hint='bug' produced zero matches"
    top = matches[0]
    assert (
        top["name"] == "Bug Tracking"
    ), f"Expected Bug Tracking as top match, got {top}"


@pytest.mark.asyncio
async def test_zettelkasten_hint_resolves_via_tag(library_catalog_seeded) -> None:
    """``type_hint='zettelkasten'`` matches Personal Knowledge Base.

    Pre-Plan-07-01 zettelkasten was NOT in package.name, description, or
    cp.name — so the resolver missed entirely. Plan 07-01 adds it as a
    tag and the haystack now contains it.
    """
    matches = await resolve_type_hint("zettelkasten")
    assert matches, "type_hint='zettelkasten' produced zero matches"
    top = matches[0]
    assert (
        top["name"] == "Personal Knowledge Base"
    ), f"Expected Personal Knowledge Base as top match, got {top}"


@pytest.mark.asyncio
async def test_existing_name_match_no_regression(library_catalog_seeded) -> None:
    """``type_hint='bug tracking'`` still resolves via name match.

    Tokenization yields {'bug', 'tracking'} which intersects the
    haystack {'bug-tracking' -> 'bug', 'tracking', ...}. Score == 1.0.
    """
    matches = await resolve_type_hint("bug tracking")
    assert matches, "type_hint='bug tracking' produced zero matches"
    top = matches[0]
    assert (
        top["name"] == "Bug Tracking"
    ), f"Expected Bug Tracking as top match (name path), got {top}"
    assert top["score"] >= 1.0, f"Expected full-token match score, got {top['score']}"


@pytest.mark.asyncio
async def test_unknown_hint_returns_none() -> None:
    """``type_hint='nonexistent-domain-xyz'`` returns no matches (no false positive)."""
    matches = await resolve_type_hint("nonexistent-domain-xyz")
    assert matches == [], f"Expected empty matches for nonsense hint, got {matches}"

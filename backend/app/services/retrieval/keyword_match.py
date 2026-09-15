"""Shared keyword normalizer for mode-adaptive (degraded) retrieval.

When the deployment has no live vector store (the ``null`` driver is
active), ``mode=semantic`` / ``mode=hybrid`` degrade to ``mode=graph`` and
the agent's text queries can no longer be answered by meaning. The two
text paths that previously matched a LITERAL full-query substring —

* ``app/api/retrieve.py::_entry_matches_query`` (graph mode), and
* ``app/services/agent_insights.py::query_entries`` —

would then match NOTHING for a natural-language or ``"A OR B OR C"`` query.
This module supplies a tiny, dependency-free tokenizer + term-overlap
matcher both paths share so a multi-word / NL / boolean query degrades to
keyword recall instead of an empty result set.

Pure + unit-testable: no I/O, no graph access, no imports from
``app.services`` / ``app.models``.
"""

from __future__ import annotations

import re
from typing import List

# Small, hand-curated stopword set. Keeps recall sensible for NL queries
# ("what is the budget of a project") and boolean blobs ("Acme OR Globex")
# without pulling in an NLP dependency. Intentionally minimal — over-
# aggressive stopword removal hurts precision more than it helps.
_STOPWORDS = frozenset(
    {
        # question words / determiners / prepositions (NL noise)
        "or",
        "and",
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "is",
        "are",
        "do",
        "does",
        "what",
        "where",
        "who",
        "how",
        "about",
        # extra boolean noise
        "not",
        "but",
        "with",
        "for",
        "on",
        "at",
        "by",
    }
)

# Minimum significant-token length. Drops single-char noise ("x", "1") and
# 1-char operators that survived the split.
_MIN_TOKEN_LEN = 2

# Split on any run of non-alphanumeric characters.
_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def tokenize(query: str) -> List[str]:
    """Lowercase, split on non-alphanumeric, drop stopwords + short tokens.

    Returns a deduped list preserving first-seen order. An empty query, a
    whitespace-only query, or a query consisting solely of stopwords /
    short tokens returns ``[]`` — callers treat ``[]`` as "no text filter"
    (match all), preserving the legacy "empty query → all entries"
    behaviour.
    """
    if not query:
        return []
    seen: set[str] = set()
    out: List[str] = []
    for raw in _SPLIT_RE.split(query.lower()):
        if not raw or len(raw) < _MIN_TOKEN_LEN or raw in _STOPWORDS:
            continue
        if raw in seen:
            continue
        seen.add(raw)
        out.append(raw)
    return out


def keyword_overlap(text: str, terms: List[str]) -> int:
    """Count how many ``terms`` appear as case-insensitive substrings of ``text``.

    Used both to test membership (``> 0``) and to RANK matched entries by
    how many query terms they hit (more matching terms → higher relevance
    in the degraded graph fallback).
    """
    if not terms or not text:
        return 0
    haystack = text.lower()
    return sum(1 for term in terms if term in haystack)


def matches_keywords(text: str, terms: List[str], *, require: str = "any") -> bool:
    """Return whether ``text`` matches the keyword ``terms``.

    * ``require="any"`` (default) → at least one term present (recall-first).
    * ``require="all"`` → every term present.

    Empty ``terms`` → ``True`` (no text filter; match all), preserving the
    legacy "empty query matches everything" contract.
    """
    if not terms:
        return True
    haystack = text.lower()
    if require == "all":
        return all(term in haystack for term in terms)
    return any(term in haystack for term in terms)


__all__ = ["tokenize", "keyword_overlap", "matches_keywords"]

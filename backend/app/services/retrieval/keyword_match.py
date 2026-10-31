"""Shared lexical term matching for compact entry queries.

This dependency-free matcher is a keyword fallback, not semantic retrieval.
The storage prefilter narrows candidates before payload hydration; canonical
text matching and current graph admission still determine the returned rows.
Ranked lexical, semantic and hybrid retrieval remain a separate service.
"""

from __future__ import annotations

import re
import sys
from functools import lru_cache
from threading import Lock
from typing import Any, List

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


_ALPHABET_LOCK = Lock()


@lru_cache(maxsize=1)
def _lowercase_candidate_alphabet() -> dict[str, str] | None:
    """Character classes containing every source of an ASCII lowercase unit.

    Do not depend on a database locale's case folding. Python's lowercase
    matcher also recognizes, for example, the Kelvin sign. Extra matches from
    expansions such as dotted I are harmless: the canonical matcher runs after
    admission. If a future Unicode version expands one character into multiple
    ASCII units, disable this optimization rather than risk a false negative.
    """
    alphabet: dict[str, set[str]] = {
        unit: set() for unit in "abcdefghijklmnopqrstuvwxyz0123456789"
    }
    for point in range(sys.maxunicode + 1):
        char = chr(point)
        lowered = char.lower()
        units = [unit for unit in lowered if unit in alphabet]
        if not lowered or len(units) > 1:
            return None
        if units:
            alphabet[units[0]].add(char)
    return {
        unit: "[" + re.escape("".join(sorted(chars))) + "]"
        for unit, chars in alphabet.items()
    }


def keyword_candidate_query(query: str) -> dict[str, Any]:
    """A bounded storage prefilter, never an authority or final text evaluator.

    Significant terms contain only ASCII alphanumerics and cannot cross the
    space separating title and body. These literal character-class alternatives
    are a superset of the shared lowercase substring matcher on either field.
    Oversized predicates and unsupported Unicode mappings retain the complete
    canonical scan instead of silently dropping terms.
    """
    terms = tokenize(query)
    if not terms or len(terms) > 16 or any(len(term) > 128 for term in terms):
        return {}
    # A cold lookup is pure CPU work; async callers offload it. The lock
    # coalesces concurrent cold requests before entering the bounded cache.
    with _ALPHABET_LOCK:
        alphabet = _lowercase_candidate_alphabet()
    if alphabet is None:
        return {}
    pattern = (
        "(" + "|".join("".join(alphabet[unit] for unit in term) for term in terms) + ")"
    )
    return {
        "$or": [
            {field: {"$regex": pattern}} for field in ("context.title", "context.body")
        ]
    }


__all__ = ["tokenize", "keyword_overlap", "matches_keywords", "keyword_candidate_query"]

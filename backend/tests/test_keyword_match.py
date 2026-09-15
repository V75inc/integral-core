"""Unit tests for the shared keyword normalizer (mode-adaptive retrieval).

``app/services/retrieval/keyword_match.py`` underpins the degraded
(keyword/graph) retrieval path: when semantic search is OFF, both the
graph-mode handler (``app/api/retrieve.py``) and the agent insight query
(``app/services/agent_insights.py::query_entries``) tokenize the query and
match by term overlap instead of literal full-string substring. These
tests pin the tokenizer + matcher contract so that behaviour stays pure
and dependency-free.
"""

from __future__ import annotations

from app.services.retrieval.keyword_match import (
    keyword_overlap,
    matches_keywords,
    tokenize,
)

# ---------------------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------------------


def test_tokenize_lowercases_and_splits_on_non_alphanumeric():
    assert tokenize("Acme Inc, company!") == ["acme", "inc", "company"]


def test_tokenize_drops_stopwords():
    # "what", "is", "the", "of", "a" are all stopwords -> only "budget" survives.
    assert tokenize("What is the budget of a project") == ["budget", "project"]


def test_tokenize_drops_boolean_noise_or_and():
    # The classic "A OR B OR C" blob — boolean operators are dropped.
    assert tokenize("Acme OR Globex AND Initech") == ["acme", "globex", "initech"]


def test_tokenize_drops_short_tokens():
    # Tokens shorter than 2 chars are dropped ("x", "1").
    assert tokenize("x ab 1 cd") == ["ab", "cd"]


def test_tokenize_dedupes_preserving_order():
    assert tokenize("acme acme globex acme") == ["acme", "globex"]


def test_tokenize_empty_string_returns_empty():
    assert tokenize("") == []


def test_tokenize_whitespace_only_returns_empty():
    assert tokenize("   \t  \n ") == []


def test_tokenize_only_stopwords_returns_empty():
    # "what is the" are all stopwords -> no significant terms.
    assert tokenize("what is the") == []


def test_tokenize_only_short_and_boolean_returns_empty():
    assert tokenize("a or x") == []


# ---------------------------------------------------------------------------
# keyword_overlap
# ---------------------------------------------------------------------------


def test_keyword_overlap_counts_present_terms():
    text = "The Acme Inc company wiki page"
    assert keyword_overlap(text, ["acme", "company", "missing"]) == 2


def test_keyword_overlap_is_case_insensitive():
    assert keyword_overlap("ACME GLOBEX", ["acme", "globex"]) == 2


def test_keyword_overlap_substring_match():
    # "comp" is a substring of "company".
    assert keyword_overlap("our company budget", ["comp"]) == 1


def test_keyword_overlap_no_terms_is_zero():
    assert keyword_overlap("anything", []) == 0


def test_keyword_overlap_empty_text_is_zero():
    assert keyword_overlap("", ["acme"]) == 0


# ---------------------------------------------------------------------------
# matches_keywords
# ---------------------------------------------------------------------------


def test_matches_keywords_any_default_one_term_present():
    assert matches_keywords("acme wiki page", ["acme", "globex"]) is True


def test_matches_keywords_any_no_terms_present():
    assert matches_keywords("nothing here", ["acme", "globex"]) is False


def test_matches_keywords_all_requires_every_term():
    assert (
        matches_keywords("acme globex page", ["acme", "globex"], require="all") is True
    )
    assert (
        matches_keywords("acme page only", ["acme", "globex"], require="all") is False
    )


def test_matches_keywords_empty_terms_matches_all():
    # No significant terms -> "no text filter" -> match everything.
    assert matches_keywords("anything at all", []) is True
    assert matches_keywords("", []) is True

"""Shared lexical matcher and conservative storage-prefilter regressions."""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize(
    "query,text",
    [
        ("selective match", "A SELECTIVE match"),
        ("kelvin", "\u212aelvin"),
        ("hi", "h\u0130"),
        ("file 42", "FILE 42"),
        ("budget OR invoice", "An invoice"),
        ("niño", "NIÑO"),
    ],
)
def test_storage_candidate_never_drops_a_canonical_keyword_hit(query, text):
    import re

    from app.services.retrieval.keyword_match import keyword_candidate_query

    assert matches_keywords(text, tokenize(query))
    predicate = keyword_candidate_query(query)
    pattern = predicate["$or"][0]["context.title"]["$regex"]
    assert re.search(pattern, text)


def test_candidate_is_only_a_prefilter_and_never_embeds_user_regex():
    import re

    from app.services.retrieval.keyword_match import keyword_candidate_query

    pattern = keyword_candidate_query("id")["$or"][0]["context.title"]["$regex"]
    assert re.search(pattern, "\u0130d")
    assert not matches_keywords("\u0130d", tokenize("id"))
    pattern = keyword_candidate_query("literal.*[evil]")["$or"][0]["context.title"][
        "$regex"
    ]
    assert not re.search(pattern, "something else")


@pytest.mark.parametrize(
    "query", ["", "AND OR the", "x", "x" * 129, " ".join(f"term{i}" for i in range(17))]
)
def test_unbounded_or_empty_text_prefilters_fall_back_without_dropping_terms(query):
    from app.services.retrieval.keyword_match import keyword_candidate_query

    assert keyword_candidate_query(query) == {}

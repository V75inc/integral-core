"""Diverse evidence preserves candidates and material source wording."""

from decimal import Decimal

import pytest

from app.services.retrieval.passage_ranking import diverse_passage_ranks

pytestmark = pytest.mark.smoke


def passage(identity, content, score, field="title"):
    return dict(
        entry_id=identity,
        content=content,
        score=Decimal(str(score)),
        field_path=field,
        start_offset=0,
        end_offset=len(content),
        source_hash="hash-" + identity,
    )


def test_repeated_titles_cannot_fill_the_evidence_window():
    rows = [
        passage(f"copy-{i}", f"Delivery planning — East office #{i}", 1 / (61 + i))
        for i in range(20)
    ]
    relevant = passage("relevant", "Emergency response procedure", 1 / 85)
    inputs = [*rows, relevant]
    ranked = diverse_passage_ranks(inputs)
    assert ranked[0]["entry_id"] == "copy-0"
    assert "relevant" in [row["entry_id"] for row in ranked[:5]]
    assert {row["entry_id"] for row in ranked} == {row["entry_id"] for row in inputs}
    scores = [float(row["score"]) for row in ranked]
    assert scores == sorted(scores, reverse=True)
    assert rows[1]["score"] == Decimal(str(1 / 62))
    assert ranked == diverse_passage_ranks(inputs)


def test_same_title_with_different_body_evidence_keeps_both_bodies():
    rows = [
        passage("one", "A shared record title", 0.1),
        passage("two", "A shared record title", 0.09),
        passage("one", "Approval is required above 5000.", 0.08, "body"),
        passage("two", "Approval is not required above 6000.", 0.07, "body"),
    ]
    bodies = [row for row in diverse_passage_ranks(rows) if row["field_path"] == "body"]
    assert bodies == rows[2:]


def test_stale_or_denied_duplicates_still_have_refill_candidates():
    rows = [
        passage(identity, "Repeated passage.", score, "body")
        for identity, score in [
            ("stale", 0.1),
            ("denied", 0.09),
            ("current-readable", 0.08),
        ]
    ]
    ranked = diverse_passage_ranks(rows)
    assert len(ranked) == len(rows)
    admitted = [row for row in ranked if row["entry_id"] == "current-readable"]
    assert admitted[0]["source_hash"] == "hash-current-readable"
    assert admitted[0]["end_offset"] == len("Repeated passage.")


def test_numeric_and_negated_body_changes_are_not_duplicates():
    texts = [
        "The valve must remain open.",
        "The valve must not remain open.",
        "Payment is due in 15 days.",
        "Payment is due in 30 days.",
        "Alice reports to Bob.",
        "Bob reports to Alice.",
    ]
    rows = [passage(str(i), body, 1 / (i + 1), "body") for i, body in enumerate(texts)]
    assert diverse_passage_ranks(rows) == rows


def test_whitespace_duplicates_share_only_their_own_field():
    rows = [
        passage("one", "same  passage", 0.1, "body"),
        passage("two", "same\npassage", 0.09, "body"),
        passage("three", "same passage", 0.08, "custom_fields.summary"),
    ]
    ranked = {row["entry_id"]: row for row in diverse_passage_ranks(rows)}
    assert ranked["two"]["score"] == 0.045
    assert ranked["three"]["score"] == Decimal("0.08")


def test_empty_and_unicode_titles_preserve_candidates():
    rows = [
        passage("one", "Équipe opérations — région 1", 0.1),
        passage("two", "ÉQUIPE OPÉRATIONS — région 2", 0.09),
        passage("three", "", 0.08),
    ]
    ranked = {row["entry_id"]: row for row in diverse_passage_ranks(rows)}
    assert len(ranked) == 3
    assert ranked["two"]["score"] == 0.045
    assert diverse_passage_ranks([]) == []


def test_substantive_paragraph_duplicates_ignore_unrelated_footer_variants():
    substantive = (
        "Inspect the backup unit oil and coolant every Monday before starting work."
    )
    rows = [
        passage("one", substantive + "\n\nReference A.", 0.1, "body"),
        passage("two", substantive + "\n\nReference B.", 0.09, "body"),
        passage(
            "three",
            substantive.replace("every Monday", "every Friday") + "\n\nReference C.",
            0.08,
            "body",
        ),
    ]
    ranked = {row["entry_id"]: row for row in diverse_passage_ranks(rows)}
    assert ranked["two"]["score"] == 0.045
    assert ranked["three"]["score"] == Decimal("0.08")


def test_substantive_evidence_precedes_vector_only_title_resemblance():
    label = {
        **passage("label", "Emergency dispatch notes", 0.1),
        "vector_match": True,
        "lexical_match": False,
    }
    body = passage("answer", "Meet beside the northern gate.", 0.08, "body")
    ranked = diverse_passage_ranks(
        [label, body], query="Where should visitors assemble?"
    )
    assert ranked[0]["entry_id"] == "answer"
    assert label["score"] == Decimal("0.1")


def test_exact_title_and_identifier_are_not_discounted_or_hidden_by_a_duplicate():
    rows = [
        {**passage("other", "Refund policy", 0.1), "vector_match": True},
        {**passage("exact", "Refund policy #42", 0.09), "vector_match": True},
    ]
    ranked = diverse_passage_ranks(rows, query="Refund policy 42")
    assert ranked[0]["entry_id"] == "exact"
    assert ranked[0]["score"] == Decimal("0.09")
    assert diverse_passage_ranks(rows, query="Refund policy")[0]["score"] == Decimal(
        "0.1"
    )


def test_lexical_title_match_retains_its_evidence_weight():
    row = {
        **passage("label", "Dispatch notes", 0.1),
        "vector_match": True,
        "lexical_match": True,
    }
    assert diverse_passage_ranks([row], query="Where should visitors assemble?") == [
        row
    ]


def test_prefix_filter_matches_exhaustive_similarity_groups():
    """The fast candidate filter must retain every exhaustive Jaccard match."""
    import random

    rng = random.Random(812)
    rows = [
        passage(
            f"{i:04}",
            " ".join(
                sorted(rng.sample([f"word{j}" for j in range(20)], rng.randint(2, 12)))
            ),
            1 / (61 + i),
        )
        for i in range(200)
    ]
    groups = []
    expected = []
    for row in rows:
        terms = set(row["content"].split())
        matches = [
            (len(terms & group[0]) / len(terms | group[0]), index)
            for index, group in enumerate(groups)
        ]
        similarity, index = (
            max(matches, key=lambda item: (item[0], -item[1])) if matches else (0, None)
        )
        item = dict(row)
        if similarity >= 0.6:
            representative, count = groups[index]
            item["score"] = float(item["score"]) / (count + 1)
            groups[index] = (representative, count + 1)
        else:
            groups.append((terms, 1))
        expected.append(item)
    expected.sort(
        key=lambda row: (-float(row["score"]), row["entry_id"], row["start_offset"])
    )
    assert diverse_passage_ranks(rows) == expected

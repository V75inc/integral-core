"""Bounded passage diversity after independent lexical/vector rank fusion.

This is a presentation rank, never an admission or completeness decision.
No candidates are dropped: repeated wording receives a decreasing rank so
one repeated template cannot occupy the entire evidence window. Canonical
source, hash, offset and live audience checks still run in search_service.
"""

from __future__ import annotations

import re
from typing import Any

_WORDS = re.compile(r"[^\W_]+", re.UNICODE)


def diverse_passage_ranks(
    candidates: list[dict[str, Any]], *, query: str = ""
) -> list[dict[str, Any]]:
    """Discount near-duplicate passages without another model or index rebuild.

    Compare unique Unicode words in titles. Standalone title numbers are often
    record counters. Body/custom-field redundancy uses exact paragraphs, not
    word-set similarity: changed quantities, negation and word order matter.
    The first/highest-ranked representative keeps its score. Later members
    receive 1/(1 + preceding members) of their fused score. Preserve every
    candidate for ordinary validation/refill, including exact duplicates that
    happen to have different permissions or source revisions.
    """
    groups: list[tuple[frozenset[str], int]] = []
    postings: dict[str, list[int]] = {}
    exact_counts: dict[tuple[str, str], int] = {}
    query_terms = frozenset(_WORDS.findall(query.casefold()))
    ranked = []
    calibrated = [dict(candidate) for candidate in candidates]
    for row in calibrated:
        if row.get("field_path") != "title":
            continue
        title_terms = frozenset(
            _WORDS.findall(str(row.get("content") or "").casefold())
        )
        # A label's semantic resemblance is weaker evidence for a prose
        # question than a matching substantive passage. Exact label/identifier
        # requests and lexical title matches retain their original rank.
        if (
            query_terms
            and row.get("vector_match")
            and not row.get("lexical_match")
            and not query_terms.issubset(title_terms)
        ):
            row["score"] = float(row["score"]) * 0.5
    calibrated.sort(
        key=lambda row: (-float(row["score"]), row["entry_id"], row["start_offset"])
    )
    for candidate in calibrated:
        row = dict(candidate)
        field = str(row.get("field_path") or "")
        if field != "title":
            raw = str(row.get("content") or "")
            text = " ".join(raw.split())
            # Short headings are not evidence of repeated substantive content.
            # A whole exact duplicate is still redundant even if it is short.
            signatures = {text} if text else set()
            signatures.update(
                " ".join(paragraph.split())
                for paragraph in re.split(r"\n\s*\n", raw)
                if len(_WORDS.findall(paragraph)) >= 8
            )
            count = max(
                (exact_counts.get((field, signature), 0) for signature in signatures),
                default=0,
            )
            for signature in signatures:
                key = (field, signature)
                exact_counts[key] = exact_counts.get(key, 0) + 1
            if count:
                row["score"] = float(row["score"]) / (count + 1)
            ranked.append(row)
            continue
        title_terms = frozenset(
            _WORDS.findall(str(row.get("content") or "").casefold())
        )
        terms = frozenset(word for word in title_terms if not word.isdecimal())
        # Exact Jaccard prefix filter: a 0.6 match shares at least ceil(0.6*n)
        # words, hence must touch ANY prefix of n-ceil(0.6*n)+1 words. Choose
        # the rarest words to avoid quadratic comparisons on generic headings.
        prefix_size = len(terms) - (3 * len(terms) + 4) // 5 + 1
        prefix = sorted(terms, key=lambda word: (len(postings.get(word, ())), word))[
            :prefix_size
        ]
        possible = {index for word in prefix for index in postings.get(word, ())}
        match = None
        best = 0.0
        for index in possible:
            representative, _ = groups[index]
            shared = len(terms & representative)
            union = len(terms) + len(representative) - shared
            similarity = shared / union if union else 0.0
            if similarity >= 0.6 and (
                match is None
                or similarity > best
                or (similarity == best and index < match)
            ):
                match, best = index, similarity
        if match is None:
            if terms:
                index = len(groups)
                groups.append((terms, 1))
                for word in terms:
                    postings.setdefault(word, []).append(index)
        else:
            representative, count = groups[match]
            row["score"] = float(row["score"]) / (count + 1)
            groups[match] = (representative, count + 1)
        ranked.append(row)
    return sorted(
        ranked,
        key=lambda row: (-float(row["score"]), row["entry_id"], row["start_offset"]),
    )

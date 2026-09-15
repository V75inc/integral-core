"""Text matching utilities for fuzzy name resolution.

Public module — replaces private ``_casefold_match`` from smart_filing.
"""


def casefold_match(query: str, candidate_fold: str) -> float:
    """Return a 0-1 similarity score between a query and a casefolded candidate.

    Scoring: exact=1.0, prefix=0.8, substring=0.6, word-prefix=0.5.
    """
    q = query.strip().casefold()
    if not q or not candidate_fold:
        return 0.0
    if q == candidate_fold:
        return 1.0
    if candidate_fold.startswith(q):
        return 0.8
    if q in candidate_fold:
        return 0.6
    # Check if query matches the start of any word in candidate
    words = candidate_fold.split()
    for word in words:
        if word.startswith(q):
            return 0.5
    return 0.0

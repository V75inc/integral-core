"""RET-04 — Reciprocal Rank Fusion helper for hybrid retrieval.

Plan 04-02's hybrid mode combines a graph-traversal ranking with the
semantic (vector) ranking via RRF (RESEARCH §Q4 — chosen over
weighted-sum-with-tie-breaker because it is parameter-free and robust
across score scales).

Extension point — drivers that want per-channel weighting can wrap this
helper or replace it; the import path
``from app.services.retrieval.rank_fusion import reciprocal_rank_fusion``
is locked for Plan 04-02's consumer.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Tuple[str, float]]],
    k: int = 60,
    top_n: int = 20,
) -> List[Tuple[str, float]]:
    """Fuse multiple ranked lists into one via Reciprocal Rank Fusion.

    Each input ranking is a sequence of ``(id, score)`` tuples sorted
    descending by score. The score itself is IGNORED — RRF uses rank
    position only — but kept on the input shape so callers can pass the
    raw ``EmbeddingStore.search`` output without re-shaping.

    Returns a list of ``(id, rrf_score)`` tuples sorted descending by
    ``rrf_score``, capped at ``top_n``. The ``k=60`` default matches the
    OpenSearch / Elastic reference implementation and is the value
    locked by Plan 04-02.
    """

    rrf_scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            item_id = item[0]
            rrf_scores[item_id] = rrf_scores.get(item_id, 0.0) + 1.0 / (k + rank)
    fused = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return fused[: max(0, int(top_n))]


__all__ = ["reciprocal_rank_fusion"]

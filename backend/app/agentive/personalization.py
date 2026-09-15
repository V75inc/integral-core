"""Silent personalization layer for the embedded agent.

Records patterns the user accepts (today: filing patterns) and applies
them as soft defaults on future similar requests. Per the user-set
preference ('silent adapt'), defaults are surfaced **only as inputs to
the staged change** — the user still approves every write, but the
agent's first proposal is shaped by what they've accepted before.

Scope today: filing-pattern learning. When a user approves a
``file_content`` staged change, we remember the (signals → track_id,
entry_type, tags) mapping. When a later ``prepare_file_content`` runs
on similar text, the learned pattern wins over (or augments) the
agent-supplied ``track_hint`` / ``type_hint`` params.

Storage is in-memory and per-process. A jvspatial-backed persistence
is the natural next step (Phase 4b) — the public API here is shaped to
make that swap mechanical.

Pattern matching is intentionally simple: 4+ char lowercase token
sets compared via Jaccard similarity. Sufficient for the pattern
*"this kind of content goes to that track"*; anything semantically
deeper would benefit from embeddings (deferred).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Bound per-user acceptance history so very active users don't grow
# the in-memory store unboundedly. Most recent N acceptances dominate.
_MAX_HISTORY_PER_USER = 100

# Minimum Jaccard similarity over signal sets to count as a match.
# Tuned conservatively — false positives are worse than misses
# because they propose the wrong default.
_MATCH_THRESHOLD = 0.30


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[a-z]{4,}")


def _signals_for(text: str) -> Set[str]:
    """Extract normalised tokens that capture the gist of ``text``.

    Lowercase, 4+ chars, deduped, for similarity matching.

    Stop-list intentionally absent — most stop words are <4 chars and
    fall out naturally; common 4+ char fillers (`this`, `that`, `with`,
    etc.) tend to appear in *both* haystack and needle so they wash
    out of Jaccard scoring.
    """
    return set(_TOKEN_RE.findall((text or "").lower()))


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------


@dataclass
class FilingAcceptance:
    """One historical accepted filing.

    The unit personalization learns from.
    """

    signals: Set[str]
    track_id: str
    entry_type_name: Optional[str]
    tags: List[str] = field(default_factory=list)
    accepted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_lock = asyncio.Lock()
_history: Dict[str, Deque[FilingAcceptance]] = {}


# ---------------------------------------------------------------------------
# Public API — record + lookup + diagnostic
# ---------------------------------------------------------------------------


async def record_filing_acceptance(
    *,
    user_id: str,
    text: str,
    track_id: str,
    entry_type_name: Optional[str] = None,
    tags: Optional[List[str]] = None,
) -> None:
    """Record that ``user_id`` approved filing ``text`` to ``track_id``.

    Called by the filing skill's execute path after a successful entry
    create, with the given entry type / tags.
    """
    if not user_id or not text or not track_id:
        return
    signals = _signals_for(text)
    if not signals:
        return
    acceptance = FilingAcceptance(
        signals=signals,
        track_id=track_id,
        entry_type_name=entry_type_name,
        tags=list(tags or []),
    )
    async with _lock:
        bucket = _history.setdefault(user_id, deque(maxlen=_MAX_HISTORY_PER_USER))
        bucket.append(acceptance)
    logger.info(
        "personalization.recorded user=%s track=%s type=%s signals=%d",
        user_id,
        track_id,
        entry_type_name,
        len(signals),
    )


async def get_filing_defaults(
    *,
    user_id: str,
    text: str,
) -> Optional[Dict[str, Any]]:
    """Return soft filing defaults derived from past acceptances.

    ``None`` when no past acceptance is similar enough.

    The returned dict has the shape::

        {
            "track_id": str,
            "entry_type_name": str | None,
            "tags": [str, ...],
            "confidence": float,        # the Jaccard score of the match
            "matched_count": int,       # how many past acceptances voted
            "explanation": str,         # human-readable, for "why?" queries
        }

    The caller (stage_file_content) blends this into filing params as
    ``track_id`` / ``type_hint``, so the staged change
    naturally reflects the learned pattern. The user still approves
    or rejects — silent adapt, never silent commit.
    """
    if not user_id or not text:
        return None

    needle = _signals_for(text)
    if not needle:
        return None

    async with _lock:
        bucket = list(_history.get(user_id, ()))

    if not bucket:
        return None

    from app.services.agent_scope import (
        accessible_tracks_for_scope,
        active_workspace_id,
    )

    if active_workspace_id():
        scoped_ids = {t.id for t in await accessible_tracks_for_scope(user_id)}
        bucket = [acc for acc in bucket if acc.track_id in scoped_ids]
        if not bucket:
            return None

    # Score every acceptance against the needle, keep matches above
    # threshold, then group by track_id and pick the highest aggregate
    # score — this lets multiple weaker matches outvote a single
    # strongest one if they all agree on the destination.
    scored: List[tuple[FilingAcceptance, float]] = []
    for acc in bucket:
        score = _jaccard(needle, acc.signals)
        if score >= _MATCH_THRESHOLD:
            scored.append((acc, score))

    if not scored:
        return None

    # Aggregate by track_id (sum of scores).
    by_track: Dict[str, Dict[str, Any]] = {}
    for acc, score in scored:
        bucket_key = acc.track_id
        slot = by_track.setdefault(
            bucket_key,
            {
                "track_id": acc.track_id,
                "score_sum": 0.0,
                "votes": 0,
                "entry_type_votes": {},
                "tag_counts": {},
                "best_score": 0.0,
            },
        )
        slot["score_sum"] += score
        slot["votes"] += 1
        slot["best_score"] = max(slot["best_score"], score)
        if acc.entry_type_name:
            slot["entry_type_votes"][acc.entry_type_name] = (
                slot["entry_type_votes"].get(acc.entry_type_name, 0) + 1
            )
        for tag in acc.tags:
            slot["tag_counts"][tag] = slot["tag_counts"].get(tag, 0) + 1

    winner = max(by_track.values(), key=lambda s: s["score_sum"])

    # Pick the most-voted entry type for the winning track.
    type_votes = winner["entry_type_votes"]
    best_type = max(type_votes, key=type_votes.get) if type_votes else None
    # Tags: include any tag voted by majority of matching acceptances.
    half = max(1, winner["votes"] // 2)
    suggested_tags = [
        tag for tag, count in winner["tag_counts"].items() if count >= half
    ]

    return {
        "track_id": winner["track_id"],
        "entry_type_name": best_type,
        "tags": suggested_tags,
        "confidence": round(winner["best_score"], 3),
        "matched_count": winner["votes"],
        "explanation": (
            f"Matched {winner['votes']} prior accepted filing"
            f"{'s' if winner['votes'] != 1 else ''} with "
            f"similarity {round(winner['best_score'], 2)}; routing to the "
            f"track that received those filings."
        ),
    }


async def history_for(user_id: str) -> List[Dict[str, Any]]:
    """Diagnostic view of what's been recorded for the user.

    Used by the agent if the user asks "what have you learned about my
    filing?" — the data is intentionally accessible because silent
    adapt does not mean opaque.
    """
    async with _lock:
        bucket = list(_history.get(user_id, ()))
    return [
        {
            "accepted_at": a.accepted_at.isoformat(),
            "track_id": a.track_id,
            "entry_type_name": a.entry_type_name,
            "tags": list(a.tags),
            "signal_count": len(a.signals),
        }
        for a in bucket
    ]


def _reset_for_tests() -> None:
    _history.clear()

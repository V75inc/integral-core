"""Parse approval / rejection intent from a user's free-text reply.

The companion to ``staging_renderers.py``: where the renderer turns a
``StagedChange`` into a text prompt for the user, this module parses
the user's text response back into a structured intent that the
caller can dispatch via ``bless_token`` / ``revoke_token``.

Used by channel adapters (SMS / WhatsApp / voice / etc) that receive
user replies as plain text. The assistant-ui web surface does NOT use
this — it has explicit Approve / Reject buttons.

What it does
------------

Given:
  * A short user reply ("yes", "approve 2", "reject all", "ok always",
    "no I changed my mind").
  * A list of currently-pending ``StagedChange`` tokens for the user
    (typically loaded via ``staging.get_pending_for_user``).

Returns:
  * A list of ``ApprovalIntent`` entries — each one names a single
    token + the verb to apply (``bless`` / ``revoke``) + the autonomy
    hint for bless calls. Caller iterates and dispatches.
  * Or ``None`` if the message doesn't parse as an approval intent at
    all (most user messages aren't approvals — caller should forward
    those to the agent unchanged).

Parser design
-------------

Deliberately conservative. False positives are worse than false
negatives: if we mis-parse a non-approval message as "approve all",
the user has staged commits they did not authorize. If we miss an
approval, the worst case is the user re-states their intent.

Recognised patterns (case-insensitive, whitespace-tolerant):

  * Bare verb: "yes" / "approve" / "ok" / "no" / "reject" / "cancel".
    Resolves against the single most-recent pending token. Ambiguous
    when multiple are pending; resolves to "first by created_at"
    only when the user explicitly asks for "all" or a numbered
    ordinal.
  * Verb + ordinal: "approve 2" / "reject the first" / "no, 3".
    Resolves to the indexed pending token (1-based).
  * Verb + "all": "approve all" / "reject all". Resolves to every
    pending token of compatible kind.
  * Verb + autonomy: "always approve" / "approve and remember" /
    "auto-allow" → bless with ``autonomy=session``.

Anything not matching these patterns returns ``None``. The caller
should forward the message to the agent as a normal turn.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable, List, Literal, Optional

from app.agentive.staging import StagedChange

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


ApprovalVerb = Literal["bless", "revoke"]
AutonomyMode = Literal["single", "session"]


@dataclass(frozen=True)
class ApprovalIntent:
    """One parsed approval action against one specific token.

    ``token`` is the StagedChange token to act on. ``verb`` is the
    staging primitive verb (``bless`` for approve, ``revoke`` for
    reject). ``autonomy`` is only meaningful for bless calls; ignored
    on revoke. Caller passes this to ``staging.bless_token`` /
    ``staging.revoke_token``.

    ``confidence`` is a coarse ``"high" | "medium" | "low"`` band
    so adapters can decide whether to confirm with the user before
    dispatching. ``high`` means we matched an explicit verb + a clear
    target reference (ordinal or "all"). ``medium`` is bare-verb
    against the single-pending case. ``low`` is currently unused;
    reserved for future fuzzier matches.
    """

    token: str
    verb: ApprovalVerb
    autonomy: AutonomyMode  # ``single`` if not specified
    confidence: Literal["high", "medium", "low"]
    # The literal pending-list index (1-based) we resolved against,
    # for trace logging. ``None`` for "all" resolutions.
    ordinal: Optional[int]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


# Single tokens that unambiguously map to ``bless``.
_BLESS_TOKENS = {
    "yes",
    "approve",
    "approved",
    "ok",
    "okay",
    "confirm",
    "confirmed",
    "go",
    "go ahead",
    "do it",
    "proceed",
    "accept",
    "y",
    "ack",
    "sure",
}

# Single tokens that unambiguously map to ``revoke``.
_REVOKE_TOKENS = {
    "no",
    "reject",
    "rejected",
    "cancel",
    "cancelled",
    "stop",
    "denied",
    "decline",
    "declined",
    "don't",
    "do not",
    "abort",
    "n",
    "skip",
}

# Phrases that escalate a bless to ``autonomy=session``.
_AUTONOMY_PHRASES = {
    "always",
    "auto",
    "auto allow",
    "auto-allow",
    "auto approve",
    "auto-approve",
    "always approve",
    "always allow",
    "remember",
    "from now on",
    "every time",
}

# Words that indicate "act on every pending item" rather than a
# specific ordinal. Combined with a verb ("approve all", "reject
# all", "all yes").
_ALL_WORDS = {"all", "every", "everything", "both"}

# Numeric-word ordinals (for "approve the first" / "reject second").
_ORDINAL_WORDS = {
    "first": 1,
    "1st": 1,
    "one": 1,
    "second": 2,
    "2nd": 2,
    "two": 2,
    "third": 3,
    "3rd": 3,
    "three": 3,
    "fourth": 4,
    "4th": 4,
    "four": 4,
    "fifth": 5,
    "5th": 5,
    "five": 5,
    "sixth": 6,
    "6th": 6,
    "six": 6,
    "seventh": 7,
    "7th": 7,
    "seven": 7,
    "eighth": 8,
    "8th": 8,
    "eight": 8,
    "ninth": 9,
    "9th": 9,
    "nine": 9,
    "tenth": 10,
    "10th": 10,
    "ten": 10,
}


_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
_DIGIT_RE = re.compile(r"\b(\d+)\b")


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    t = (text or "").lower()
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def _extract_ordinal(text: str) -> Optional[int]:
    """Return 1-based ordinal if the text references one, else None.

    Recognises Arabic numerals (``"2"`` → 2) and English ordinal
    words (``"second"`` → 2). Returns the FIRST hit — ambiguous
    multi-ordinal messages ("approve 2 and 3") are left to the
    caller's downstream parse OR fall through to None.
    """
    # Numeric first — cheaper and dominant in real SMS replies.
    m = _DIGIT_RE.search(text)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 99:
                return n
        except ValueError:
            pass
    for word, value in _ORDINAL_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", text):
            return value
    return None


def _has_any(text: str, vocab: Iterable[str]) -> bool:
    """Return True if ``text`` contains any phrase from ``vocab``.

    Word-boundary match. Phrases may contain spaces.
    """
    for phrase in vocab:
        if " " in phrase:
            if phrase in text:
                return True
        else:
            if re.search(rf"\b{re.escape(phrase)}\b", text):
                return True
    return False


def _detect_verb(text: str) -> Optional[ApprovalVerb]:
    """Detect bless / revoke from text.

    Conservative: requires a clear vocabulary hit, not just "ok" inside
    a longer sentence that may carry contrary signal.

    Returns None when:
      * Neither vocabulary hits.
      * Both vocabularies hit (ambiguous — "approve no wait reject").
    """
    is_bless = _has_any(text, _BLESS_TOKENS)
    is_revoke = _has_any(text, _REVOKE_TOKENS)
    if is_bless and is_revoke:
        return None
    if is_bless:
        return "bless"
    if is_revoke:
        return "revoke"
    return None


# ---------------------------------------------------------------------------
# Public parser
# ---------------------------------------------------------------------------


def parse_approval_intent(
    text: str,
    *,
    pending: List[StagedChange],
) -> Optional[List[ApprovalIntent]]:
    """Parse ``text`` against the user's ``pending`` token list.

    Args:
        text: The user's raw reply.
        pending: All non-terminal staged changes for the user, ordered
            most-recent-first (the order ``get_pending_for_user``
            returns them in). Empty list yields ``None`` — there's
            nothing to act on.

    Returns:
        A list of ``ApprovalIntent`` entries to dispatch, or ``None``
        when the text doesn't parse as an approval intent.

        * Single bare verb + single pending token → 1-entry list.
        * Verb + ordinal → 1-entry list keyed to that ordinal
          (skipped silently if out of range — adapter should ask
          the user to clarify).
        * Verb + "all" → N-entry list, one per pending token.
        * No verb match → None.
        * Verb match but no pending tokens → None (no-op; adapter
          should explain to the user there's nothing to act on).

    The caller is responsible for actually invoking the staging
    primitive's ``bless_token`` / ``revoke_token`` per intent. This
    function does not touch any state.
    """
    if not text or not text.strip():
        return None
    if not pending:
        return None

    norm = _normalise(text)
    if not norm:
        return None

    verb = _detect_verb(norm)
    if verb is None:
        return None

    autonomy: AutonomyMode = "single"
    if verb == "bless" and _has_any(norm, _AUTONOMY_PHRASES):
        autonomy = "session"

    targets_all = _has_any(norm, _ALL_WORDS)
    ordinal = _extract_ordinal(norm)

    intents: List[ApprovalIntent] = []
    if targets_all:
        intents = [
            ApprovalIntent(
                token=sc.token,
                verb=verb,
                autonomy=autonomy,
                confidence="high",
                ordinal=None,
            )
            for sc in pending
        ]
    elif ordinal is not None:
        # Ordinals are 1-based and reference the pending list as
        # surfaced to the user via ``render_pending_list``. That list
        # is sorted most-recent-first, so ordinal=1 is the newest
        # card.
        if 1 <= ordinal <= len(pending):
            sc = pending[ordinal - 1]
            intents = [
                ApprovalIntent(
                    token=sc.token,
                    verb=verb,
                    autonomy=autonomy,
                    confidence="high",
                    ordinal=ordinal,
                )
            ]
        else:
            logger.info(
                "approval_intent: ordinal %d out of range (%d pending)",
                ordinal,
                len(pending),
            )
            return None
    else:
        # Bare verb. Only safe to apply if there's exactly one pending
        # token — otherwise the user must disambiguate.
        if len(pending) == 1:
            sc = pending[0]
            intents = [
                ApprovalIntent(
                    token=sc.token,
                    verb=verb,
                    autonomy=autonomy,
                    confidence="medium",
                    ordinal=1,
                )
            ]
        else:
            logger.info(
                "approval_intent: bare verb %r against %d pending — "
                "ambiguous, caller should ask user to disambiguate",
                verb,
                len(pending),
            )
            return None

    return intents or None


def looks_like_approval(text: str) -> bool:
    """Cheap pre-filter for whether ``text`` looks like approval intent.

    Used by chat adapters to decide whether to bother loading the
    pending-token list before parsing.

    No state lookup, no DB hit — purely string matching.
    """
    norm = _normalise(text)
    if not norm:
        return False
    return _has_any(norm, _BLESS_TOKENS) or _has_any(norm, _REVOKE_TOKENS)


def looks_like_bless(text: str) -> bool:
    """Return true only for a positive, unambiguous approval cue.

    Chat orchestration uses this narrower signal when it wants to nudge a
    model into acting on a previously discussed plan.  A broader approval
    pre-filter also recognises rejection language, which is correct for a
    token parser but unsafe for a write-oriented nudge: ``do not build`` must
    never be transformed into a "user confirmed" instruction.
    """
    norm = _normalise(text)
    if not norm:
        return False
    return _has_any(norm, _BLESS_TOKENS) and not _has_any(norm, _REVOKE_TOKENS)

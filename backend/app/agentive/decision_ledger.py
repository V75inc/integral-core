"""The decision ledger — ADR-007.

Every approval card a person acts on is a decision about their own work:
what they let the agent do, what they refused, what they left to expire.
That is the highest-signal record of how somebody decides that this system
produces, and until now it was discarded — ``staging_store`` keeps only
``pending`` and ``blessed`` and drops terminal rows on transition, which is
right for a durability mirror and wrong for a ledger.

So the decision is recorded here, at the moment of transition, before the
row goes.

Shape of the record
-------------------

A ``Decision`` is a record of an EVENT, not a claim about the person. It
carries no belief fields, is never superseded, and lives in its own
``decisions`` track rather than beside ``Commitment`` — which is a belief,
stages like one, and would have been dragged into the staging exemption if
the two shared a track (ADR-007, gate B ruling).

The ledger is written unstaged, because recording that somebody approved
something cannot itself require approval.

Failure policy
--------------

Nothing here may fail a bless. Every path is best-effort and logged: a
person who approves a card gets their change whether or not the ledger
write succeeds. That is also why this module never raises — the caller
sites in ``staging`` are inside the approval flow.

Substrate boundary
------------------

Names no bundle. The target is resolved through
``services.personal_context``, which owns the mapping from a person to
their App and its tracks, and the write goes through the same agent
dispatch seam every other write uses — so permissions, change events and
the unstaged gate all apply unchanged.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:  # avoid an import cycle — staging imports this module
    from app.agentive.staging import StagedChange

logger = logging.getLogger(__name__)

#: Manifest key of the track the ledger writes into.
_LEDGER_TRACK_KEY = "decisions"

#: Terminal staging state -> ``Decision.outcome``.
#:
#: ``edited_then_blessed`` is declared in the manifest and deliberately
#: absent here: there is no edit-before-bless path in the codebase today
#: (``bless_token`` takes ``(user_id, token, autonomy)`` and flips state;
#: nothing accepts a modified payload). The enum member is reserved rather
#: than removed — an unused select member costs nothing, and removing it
#: later is a manifest edit while adding it back is a migration. Whoever
#: builds card editing maps to it here.
_OUTCOME_BY_STATE: Dict[str, str] = {
    "consumed": "blessed",
    "revoked": "rejected",
    "expired": "expired",
}


def outcome_for_state(state: str) -> Optional[str]:
    """Return the ledger outcome for a terminal staging state, or None."""
    return _OUTCOME_BY_STATE.get(str(state or "").strip())


async def record_decision(sc: "StagedChange") -> Optional[str]:
    """Write one ``Decision`` for a staged change that just went terminal.

    Returns the new entry id (or ``""`` when the write succeeded without
    surfacing one), and ``None`` when nothing was written — which is the
    common case: most people will not have the App, and a person who does
    not is not an error.

    Never raises.
    """
    try:
        return await _record(sc)
    except Exception:  # noqa: BLE001
        logger.exception(
            "decision ledger: write failed for token %s; approval unaffected",
            getattr(sc, "token", ""),
        )
        return None


async def _record(sc: "StagedChange") -> Optional[str]:
    outcome = outcome_for_state(getattr(sc, "state", ""))
    if outcome is None:
        return None

    user_id = str(getattr(sc, "user_id", "") or "")
    if not user_id:
        return None

    from app.services.permissions import get_user_node
    from app.services.personal_context import resolve_personal_context

    user = await get_user_node(user_id)
    if user is None:
        return None

    target = await resolve_personal_context(user_id=user.id)
    if target is None:
        # No App, or attention switched off. Note that `attention_enabled`
        # gating the ledger is deliberate: somebody who has told the App to
        # stop paying attention has not carved out an exception for their
        # approvals.
        return None

    track = (target.get("tracks") or {}).get(_LEDGER_TRACK_KEY)
    if track is None:
        return None

    from app.agentive.tooling import dispatch_tool

    summary = str(getattr(sc, "summary", "") or "").strip() or "A staged change"
    # `diff_human` is what the person was actually SHOWN. The machine payload
    # is deliberately not recorded: it carries entry bodies, field values and
    # node ids, and copying those into a long-lived, human-browsable ledger is
    # a data-retention decision rather than an implementation detail (ADR-007).
    diff = str(getattr(sc, "diff_human", "") or "").strip()

    result = await dispatch_tool(
        "integral_create_entry",
        {
            "track_id": track.id,
            "title": summary[:200],
            "body": diff,
            "fields": {
                "proposal_kind": str(getattr(sc, "kind", "") or ""),
                "outcome": outcome,
                "diff": diff,
                "token_ref": str(getattr(sc, "token", "") or ""),
                "decided_at": _decided_at(sc),
            },
        },
        principal_id=user_id,
        scope=str(target.get("workspace_id") or ""),
    )
    if result.is_error:
        logger.warning(
            "decision ledger: write rejected (%s): %s",
            result.error_code,
            result.message,
        )
        return None

    data = result.data or {}
    if data.get("staged") is not False:
        # A card asking a person to approve the record of their own approval.
        # If this fires, the unstaged declaration is not reaching the attached
        # manifest — the same failure the merge path once caused for hooks.
        logger.error(
            "decision ledger: the ledger entry STAGED instead of landing "
            "(track=%s) — the unstaged declaration is not reaching the "
            "attached manifest",
            track.id,
        )
    written: Any = data.get("result")
    if isinstance(written, dict):
        for key in ("id", "entry_id"):
            if written.get(key):
                return str(written[key])
    return ""


def _decided_at(sc: "StagedChange") -> str:
    """ISO timestamp for the transition, best-effort."""
    from datetime import datetime, timezone

    for attr in ("resolved_at", "updated_at", "created_at"):
        value = getattr(sc, attr, None)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, datetime):
            return value.isoformat()
    return datetime.now(timezone.utc).isoformat()

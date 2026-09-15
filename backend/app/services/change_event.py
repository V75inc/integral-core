"""Single emission path for ChangeEvent (D-05).

Every mutation handler in app/api/*.py and app/agentive/api/*.py calls
``emit_change_event`` AFTER the resource write succeeds and BEFORE the HTTP
response is returned. Per D-06: row persist is sync (durable before 2xx); WS
broadcast rides on async best-effort (recoverable via EVT-02 polling on cursor).

Storage shape: ChangeEvents are persisted as ``DBLog`` rows with
``log_level="CHANGE_EVENT"`` in jvspatial's secondary logging database (see
``app/services/change_event_logger.py``). This mirrors the INTERACTION /
AUDIT pattern from ``jvspatial.logging`` — one canonical log table, log_level
discriminator. The prime application graph stays free of audit rows, and the
Actor / resource graph edges that used to live on ChangeEvent are dropped
(metadata is denormalized inside every row).

Kill switch: ``settings.CHANGE_EVENT_ENABLED=False`` makes ``emit_change_event``
construct an in-memory envelope without persisting and without broadcasting.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, Optional, Set

from app.schemas.audit import ChangeEventAction
from app.schemas.provenance import ActorKind
from app.services.change_event_logger import (
    ChangeEventEnvelope,
    get_change_event_logger,
    is_change_event_enabled,
)
from app.utils.time import utc_now_iso

if TYPE_CHECKING:
    # Type-only import — avoids a runtime circular dependency between
    # change_event.py and policy.py (the Subject type lives in schemas/policy.py,
    # but change_event must remain importable by every mutation handler without
    # forcing the policy schema module to load first).
    from app.schemas.policy import Subject

logger = logging.getLogger(__name__)

# Phase 3 Plan 03-05 + Phase 3.1 Plan 03.1-03 — high-fan-out actions where
# the per-subscriber permission check inside the WS broadcast helper would
# either (a) re-enter policy_engine.evaluate on each subscriber and produce
# O(N) denial-emit fan-out (the policy.deny case — every per-subscriber
# Decision(allowed=False) would emit another policy.deny ChangeEvent which
# would itself fan out), or (b) generate N broadcast frames for what is
# semantically a forensic record (anchor.cascade — one Track delete per
# anchored field). Both cases are persisted in full to the DBLog row and
# remain visible via GET /api/audit-log; only the WS broadcast is skipped.
#
# CRITICAL — add a new action here ONLY when both: (1) the action is
# emitted as part of a multi-element loop or per-subscriber check, AND
# (2) subscribers do not need real-time visibility (the audit log poll
# suffices). Skipping a normal mutation action would silently break the
# real-time event feed.
BROADCAST_SKIP_ACTIONS: Set[str] = {
    "policy.deny",  # Phase 3 Plan 03-05 — denial-emit O(N^2) safeguard
    "anchor.cascade",  # Phase 3.1 Plan 03.1-03 — cascade fan-out safeguard
    # Phase 7 Plan 07-04 — TTL maintenance log, not a subscriber event.
    # The approval review UI polls the GET /api/approvals endpoint; expiry
    # visibility is driven by the row's status field on subsequent reads,
    # not via real-time WS push.
    "approval.expired",
}


async def emit_change_event(
    *,
    actor_kind: ActorKind,
    actor_id: str,
    actor_capability: Optional[str] = None,
    action: ChangeEventAction,
    resource_type: str,
    resource_id: str,
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
    scope: str,
    # Phase 3 Plan 03-05 — additive parameters, backwards-compatible defaults.
    details: Optional[Dict[str, Any]] = None,
    # D-10 recursion guard. Consumed only by policy_engine.evaluate's
    # denial-emit pass — the kwarg exists here so the engine has a single,
    # type-checked surface to thread system-actor authorization through any
    # future code path that itself calls policy_engine.evaluate. NEVER
    # persisted to the DBLog row — in-process control flow only.
    _internal_actor: Optional["Subject"] = None,
) -> ChangeEventEnvelope:
    """Persist a ChangeEvent (as a DBLog row) and return its envelope.

    Caller invariant: call AFTER the resource mutation succeeds, BEFORE the
    HTTP response is returned. The CI grep gate
    (``tests/test_change_event_no_bypass.py``) enforces this for every mutation
    handler.

    When ``CHANGE_EVENT_ENABLED`` is False, returns an envelope that was never
    persisted and never broadcast — treat the return value as fire-and-forget.

    Phase 3 Plan 03-05:
      - ``details`` carries structured metadata (e.g. policy-deny event payload
        ``{failed_action, decision_reason, matched_policy_id}``). Persisted on
        DBLog.log_data["details"]; surfaced via ``to_wire``/``to_wire_flat``.
        Not subject to Phase 2 D-04 TTL reclaim (only ``before``/``after`` are
        nulled). Phase 2 callers omit this kwarg.
      - ``_internal_actor`` is consumed only by ``policy_engine.evaluate``'s
        denial-emit recursion guard. Any future call inside
        ``emit_change_event``'s implementation that itself calls
        ``policy_engine.evaluate`` MUST pass ``_internal_actor`` through; the
        engine short-circuits on ``Subject(kind="system", ...)`` returning
        ``Decision(allowed=True, reason="system_subject_internal")`` (Plan 01
        Test 21). The kwarg is NOT persisted to the DBLog row.
    """
    # _internal_actor is intentionally not used inside this function today.
    # The current emit path does not call policy_engine.evaluate. The kwarg is
    # declared here so any FUTURE code path inside emit_change_event (or any
    # helper it transitively invokes) that introduces a call to
    # policy_engine.evaluate has a single, type-checked surface to forward the
    # recursion guard through. Plan 01 Test 21 verifies the engine short-circuits.
    _ = _internal_actor  # explicit no-op acknowledgment

    # Enrich human actors with display_name so consumers (activity feed) can
    # render "Eldon commented on …" without a follow-up /users lookup. Stored
    # on the persisted DBLog row under ``details.actor_display_name``. Lookup
    # is best-effort and silent — failure simply leaves the field unset.
    enriched_details = dict(details) if details else {}
    from app.services.mutation_provenance import provenance_details

    for key, value in provenance_details().items():
        if key not in enriched_details and value is not None:
            enriched_details[key] = value
    if (
        actor_kind == "human"
        and actor_id
        and "actor_display_name" not in enriched_details
    ):
        try:
            from app.services.permissions import get_user_node

            actor_user = await get_user_node(actor_id)
            if actor_user is not None:
                display_name = getattr(actor_user, "display_name", "") or ""
                if display_name:
                    enriched_details["actor_display_name"] = display_name
        except Exception:  # noqa: BLE001 — enrichment is best-effort, never raise
            pass

    envelope = ChangeEventEnvelope(
        ts=utc_now_iso(),
        actor_kind=actor_kind,
        actor_id=actor_id or "",
        actor_capability=actor_capability,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id or "",
        scope=scope,
        before=before,
        after=after,
        details=enriched_details or None,
    )

    if not is_change_event_enabled():
        return envelope

    ce_logger = get_change_event_logger()
    await ce_logger.persist(envelope)

    # Async WS broadcast — best-effort. Failure does NOT roll back the row.
    # Phase 3 Plan 03-05 — policy.deny events are forensic audit records, NOT
    # subscriber-relevant change feeds. Skipping the WS broadcast for these
    # events (a) preserves the persisted audit trail in the DBLog row, (b)
    # prevents a denial-emit cascade where every per-subscriber permission
    # check that returns Decision(allowed=False) inside _user_permitted_for_scope
    # would otherwise re-enter broadcast_change_event with the denial event,
    # producing O(N^2) broadcast amplification. See T-03-05-E01 mitigation —
    # the _internal_actor recursion guard handles a future case where the
    # subscriber check itself recurses; this skip is the broadcast-fan-out
    # safeguard. Audit visibility is unaffected (GET /api/audit-log still
    # surfaces the DBLog row).
    if action not in BROADCAST_SKIP_ACTIONS:
        try:
            from app.services.event_subscription_registry import (  # type: ignore[import-not-found]
                broadcast_change_event,
            )

            await broadcast_change_event(envelope)
        except ImportError:
            pass
        except Exception as e:
            logger.warning("emit_change_event: WS broadcast failed: %s", e)

    # Full Sweep R4 — best-effort in-process wake for routines / proactive.
    try:
        from app.services.event_wake import notify_substrate_change

        await notify_substrate_change(
            action=str(action),
            resource_type=resource_type,
            resource_id=resource_id or "",
            actor_id=actor_id or None,
            workspace_id=None,
            extra={"scope": scope},
        )
    except Exception as e:
        logger.debug("emit_change_event: event_wake failed: %s", e)

    return envelope

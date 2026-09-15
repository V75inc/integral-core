"""WhatsApp channel adapter (Phase 9 Plan 09-03b, NOTIF-02 / A3).

Replaces the 09-03a placeholder adapter with the real Meta Cloud API
client wiring. The registry slot ``CHANNEL_REGISTRY['whatsapp']`` points
at this adapter; no other call site changes (single-class swap per
09-03a's channel-adapter-registry pattern).

A3 — welcome-template-then-freeform decision lives here. The adapter
inspects ``Notification.metadata.dispatched_to[]`` for a prior
``{channel:"whatsapp", status:"sent"}`` entry:

- No prior success → first send → ``kind="template"`` with the welcome
  template registered as ``settings.WHATSAPP_WELCOME_TEMPLATE_NAME``.
  This opens Meta's 24-hour customer-service window.
- Prior success exists → freeform send (``kind="text"``) within the
  24h window. Body falls back across ``payload['snippet']`` →
  ``payload['summary']`` → ``f"Integral: {kind}"``.

Threat model (see PLAN.md <threat_model>):
- T-09-03b-S01 (Spoofing): welcome fires only from ``whatsapp_verify_otp``
  after OTP-verified ChannelIdentity; this adapter re-validates E.164.
- T-09-03b-V01 (Validation): E.164 regex ``^\\+[1-9][0-9]{7,14}$`` rejects
  any non-conforming phone with status="failed" reason="invalid_phone".
- T-09-03b-V02 (Missing creds): ``send_whatsapp_message`` short-circuits
  without an HTTP call when creds are absent; adapter surfaces
  status="failed" reason="cloud_api_error" (no silent send).
- T-09-03b-R01 (Repudiation): the router stamps each ChannelDispatchResult
  with ts + external_ref (Meta wamid) into ``dispatched_to[]``.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from app.config import settings
from app.services.notification_channels.types import ChannelDispatchResult
from app.services.whatsapp_service import send_whatsapp_message

# E.164: leading +, 1-9 first digit (no +0...), 7-14 more digits.
# Matches Meta's accepted phone-number format for the ``to`` field.
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


class WhatsappChannel:
    """WhatsApp channel — opt-in gated; first send uses welcome template (A3).

    Skips and failures are surfaced via :class:`ChannelDispatchResult`
    instead of raising — the router (single-entry dispatch funnel) needs
    a structured envelope so it can append to
    ``Notification.metadata.dispatched_to[]`` regardless of outcome.
    """

    name = "whatsapp"

    async def dispatch(
        self,
        user: Any,
        kind: str,
        payload: Dict[str, Any],
        notification: Any,
        actor_id: str,
        actor_kind: str,
    ) -> ChannelDispatchResult:
        # Master outbound switch — escape hatch for dev/test environments.
        if not settings.NOTIFICATION_OUTBOUND_ENABLED:
            return ChannelDispatchResult(
                channel="whatsapp",
                status="skipped",
                reason="outbound_disabled",
            )

        # Resolve opt-in state from the user's notification_preferences dict.
        # The Pydantic boundary in NotificationPreferences enforces shape at
        # the router; here we read the stored dict defensively (the user's
        # prefs may be None if the router skipped validation, e.g. callers
        # passing through an explicit ``channels=`` override).
        prefs: Dict[str, Any] = user.notification_preferences or {}
        wa: Dict[str, Any] = prefs.get("whatsapp") or {}
        opted_in_at = wa.get("opted_in_at")
        phone = wa.get("phone_e164")

        if not opted_in_at:
            return ChannelDispatchResult(
                channel="whatsapp",
                status="skipped",
                reason="not_opted_in",
            )

        # T-09-03b-V01 — E.164 enforcement BEFORE any HTTPX call. Reject
        # missing / malformed phones with a stable reason code so operators
        # can grep dispatched_to[] for misconfigurations.
        if not phone or not _E164.match(phone):
            return ChannelDispatchResult(
                channel="whatsapp",
                status="failed",
                reason="invalid_phone",
            )

        # A3 decision: welcome-template on first send, freeform thereafter.
        # The dispatched_to[] ledger drives the choice — a prior
        # ``{channel:"whatsapp", status:"sent"}`` entry means Meta's 24h
        # customer-service window is open (or was; freeform sends outside
        # the window will fail at Meta's side with a 470/error, which this
        # adapter surfaces as status="failed" reason="cloud_api_error" —
        # the operator handles re-opening the window via a fresh template).
        prior: List[Dict[str, Any]] = []
        if getattr(notification, "metadata", None):
            prior = notification.metadata.get("dispatched_to") or []
        has_prior_whatsapp = any(
            d.get("channel") == "whatsapp" and d.get("status") == "sent" for d in prior
        )

        if not has_prior_whatsapp:
            # First send — pre-approved template opens the 24h window.
            template_params = [
                {"type": "text", "text": payload.get("actor_name", "Integral")}
            ]
            ok, ref = await send_whatsapp_message(
                phone,
                kind="template",
                template_params=template_params,
            )
        else:
            # Within the 24h window — freeform text body. Heuristic mirrors
            # router._render_summary's payload-key precedence so a mention
            # notification's "snippet" or an agent_pending_write's "summary"
            # land verbatim.
            body = (
                payload.get("snippet") or payload.get("summary") or f"Integral: {kind}"
            )
            ok, ref = await send_whatsapp_message(
                phone,
                kind="text",
                text=body,
            )

        if ok:
            return ChannelDispatchResult(
                channel="whatsapp",
                status="sent",
                external_ref=ref,
            )
        return ChannelDispatchResult(
            channel="whatsapp",
            status="failed",
            reason="cloud_api_error",
        )

"""Channel adapter result envelope (Phase 9 Plan 09-03a, NOTIF-02).

Every notification channel adapter (InAppChannel, EmailChannel, WhatsAppChannel)
returns a :class:`ChannelDispatchResult` from its ``dispatch`` coroutine. The
router (``app.services.notification_router``) consumes these envelopes,
stamps a ``ts``, and appends each one to ``Notification.metadata.dispatched_to[]``
— the per-Notification ledger that drives idempotency on retry (A1 — no new
ChangeEventAction Literal members are needed; channel-level dispatch is
metadata on the existing Notification entity).
"""

from typing import Literal, Optional

from pydantic import BaseModel


class ChannelDispatchResult(BaseModel):
    """Outcome of a single channel adapter ``dispatch`` call.

    Fields:

    - ``channel`` — the channel name (``"in_app" | "email" | "whatsapp"``).
    - ``status`` — ``"sent"`` on success, ``"skipped"`` when the adapter
      intentionally did not send (no recipient, opt-out, idempotency,
      stub-not-implemented), ``"failed"`` on hard failure (template error,
      transport returned False).
    - ``reason`` — free-form short string explaining a non-``sent`` outcome
      (e.g. ``"no_recipient"``, ``"already_dispatched"``,
      ``"adapter_landing_in_09-03b"``, ``"template_error:<exc>"``).
    - ``external_ref`` — provider-side message id (e.g. Resend's mail id,
      Meta's WhatsApp message id) when available; used for repudiation
      defense (T-09-03a-R01) and operator diagnostics.
    - ``ts`` — stamped by the router after the adapter returns; channel
      adapters leave this field None.
    """

    channel: str
    status: Literal["sent", "skipped", "failed"]
    reason: Optional[str] = None
    external_ref: Optional[str] = None
    ts: Optional[str] = None

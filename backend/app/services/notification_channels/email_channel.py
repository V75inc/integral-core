"""Email channel adapter (Phase 9 Plan 09-03a, NOTIF-02).

Renders ``<kind>.txt.j2`` + ``<kind>.html.j2`` via Jinja2 and dispatches
via the existing ``app.services.email_service.send_email`` transport
(reuses the Phase 1 console / Resend provider switch — adapter is
provider-agnostic).

XSS gate (T-09-03a-T01): the Jinja2 ``Environment`` is built with
``autoescape=select_autoescape(['html','j2'])`` so HTML templates escape
user-supplied payload variables. The corresponding ``.txt.j2`` templates
are plain text and carry no XSS surface.

Recipient resolution (T-09-03a-I01): the recipient address is read from
``user.preferences['email']`` server-side. Callers cannot override the
recipient via the payload dict — there is no code path that lets a
caller-supplied value flow into the email's ``to`` field.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.services.email_service import EmailMessage, send_email
from app.services.notification_channels.types import ChannelDispatchResult

# Resolve the template directory relative to this file. The notification
# templates live under ``backend/app/templates/notifications/``; this file
# lives at ``backend/app/services/notification_channels/email_channel.py``,
# so two ``parent`` hops reach ``backend/app/``.
_TEMPLATE_DIR = (
    Path(__file__).resolve().parent.parent.parent / "templates" / "notifications"
)
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    # autoescape gates XSS (T-09-03a-T01). HTML templates escape user-supplied
    # variables (e.g. an ``actor_name`` containing ``<script>`` renders as
    # ``&lt;script&gt;``). Plain-text templates are not HTML-rendered, no XSS
    # surface — but ``select_autoescape`` only enables autoescape for
    # extensions matching the supplied list, so .txt.j2 still renders verbatim.
    autoescape=select_autoescape(["html", "j2"]),
)


class EmailChannel:
    """Email channel — Jinja-rendered + ``email_service.send_email`` transport.

    Returns:

    - ``status="sent"`` when ``send_email`` returns True.
    - ``status="failed"`` when ``send_email`` returns False (provider rejected
      the message) or when the Jinja templates fail to render
      (``reason="template_error:<exc>"``).
    - ``status="skipped"`` ``reason="no_recipient"`` when
      ``user.preferences['email']`` is unset — surfaces account-state
      misconfiguration without blocking the router's overall dispatch.
    """

    name = "email"

    async def dispatch(
        self, user, kind, payload, notification, actor_id, actor_kind
    ) -> ChannelDispatchResult:
        # Render both formats. A template-render error is a hard failure
        # (kind selected a template that doesn't exist, or a payload variable
        # the template needs is missing). Surface as status="failed" with
        # the exception message in ``reason`` for operator diagnostics.
        try:
            txt = _env.get_template(f"{kind}.txt.j2").render(**payload)
            html = _env.get_template(f"{kind}.html.j2").render(**payload)
        except Exception as exc:  # noqa: BLE001 — capture template errors verbatim
            return ChannelDispatchResult(
                channel="email",
                status="failed",
                reason=f"template_error:{exc}",
            )

        # Recipient is server-side state (T-09-03a-I01 — never caller-supplied).
        recipient = (
            user.preferences.get("email")
            if getattr(user, "preferences", None)
            else None
        )
        if not recipient:
            return ChannelDispatchResult(
                channel="email",
                status="skipped",
                reason="no_recipient",
            )

        # Subject defaults to a kind-derived label when the payload omits one
        # (most templates don't surface a subject; the kind acts as a stable
        # bucket for the user's inbox).
        subject = payload.get("subject") or f"Integral: {kind}"

        ok = await send_email(
            EmailMessage(
                to=recipient,
                subject=subject,
                text=txt,
                html=html,
            )
        )
        return ChannelDispatchResult(
            channel="email",
            status="sent" if ok else "failed",
        )

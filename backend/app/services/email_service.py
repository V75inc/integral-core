"""Transactional email service.

Provides a small async API for sending transactional email with three providers:

- ``console`` (default, dev) — logs the email body to stdout/structured logs
  so you can copy a reset link from the terminal during local dev.
- ``resend`` — calls the Resend HTTP API
  (https://resend.com/docs/api-reference/emails/send-email).
- ``sendgrid`` — calls the SendGrid v3 Mail Send API
  (https://docs.sendgrid.com/api-reference/mail-send/mail-send).

Selecting the provider via ``settings.EMAIL_PROVIDER`` keeps the call sites
ignorant of how delivery happens. Failures are logged but do NOT raise to the
caller — we never want a flaky mail provider to break a critical flow like
password reset (the user can request another email).

A small templating helper assembles password-reset emails so the format is
consistent between providers.
"""

from __future__ import annotations

import contextlib
import html as html_lib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.services.workspace_email_delivery import SendContext

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    """Outbound email payload."""

    to: str
    subject: str
    html: str
    text: str
    reply_to: Optional[str] = None
    workspace_id: Optional[str] = None
    source_kind: Optional[str] = None
    source_id: Optional[str] = None
    actor_user_id: Optional[str] = None


@dataclass
class _SendOutcome:
    success: bool
    error: Optional[str] = None
    provider_message_id: Optional[str] = None


async def send_email(message: EmailMessage) -> bool:
    """Dispatch the message via the configured provider. Never raises.

    Returns True on apparent success, False if the provider rejected the
    message or raised. The caller should not surface this distinction to the
    end user (avoid leaking provider state) — it's logged for operators.
    """
    from app.middleware.agentive_scope import get_actor_id, get_scope_key
    from app.services.workspace_email_delivery import (
        resolve_send_context,
        touch_last_used,
    )

    ctx = await resolve_send_context(
        workspace_id=message.workspace_id or get_scope_key(),
        source_kind=message.source_kind,
    )
    provider = ctx.provider
    outcome = _SendOutcome(success=False, error="unknown provider outcome")
    try:
        if provider == "sendgrid":
            outcome = await _send_via_sendgrid(message, ctx)
        elif provider == "resend":
            outcome = await _send_via_resend(message, ctx)
        else:
            outcome = _SendOutcome(success=_send_via_console(message, ctx))
    except Exception as exc:
        logger.exception(
            "email_service: provider=%s failed to send to=%s subject=%r",
            provider,
            message.to,
            message.subject,
        )
        outcome = _SendOutcome(success=False, error=str(exc)[:500])

    platform_mail_log_id = await _persist_mail_attempt(message, provider, ctx, outcome)
    await _dispatch_workspace_email_hooks(
        message, provider, ctx, outcome, platform_mail_log_id
    )
    ws_touch = str(message.workspace_id or get_scope_key() or "").strip()
    if outcome.success and ctx.delivery_source == "workspace" and ws_touch:
        with contextlib.suppress(Exception):
            await touch_last_used(ws_touch)
    return outcome.success


async def _persist_mail_attempt(
    message: EmailMessage,
    provider: str,
    ctx: "SendContext",
    outcome: _SendOutcome,
) -> Optional[str]:
    from app.services.mail_log import record_mail_attempt

    try:
        return await record_mail_attempt(
            to=message.to,
            from_email=ctx.from_email,
            from_name=ctx.from_name,
            reply_to=message.reply_to,
            subject=message.subject,
            text=message.text,
            html=message.html,
            provider=provider,
            status="sent" if outcome.success else "failed",
            error=outcome.error,
            provider_message_id=outcome.provider_message_id,
        )
    except Exception:
        logger.exception(
            "email_service: platform mail_log persist failed to=%s subject=%r",
            message.to,
            message.subject,
        )
        return None


async def _dispatch_workspace_email_hooks(
    message: EmailMessage,
    provider: str,
    ctx: "SendContext",
    outcome: _SendOutcome,
    platform_mail_log_id: Optional[str],
) -> None:
    from app.middleware.agentive_scope import get_actor_id, get_scope_key
    from app.services.hooks.email_sent_runtime import run_email_sent_hooks
    from app.utils.time import utc_now_iso

    workspace_id = str(message.workspace_id or get_scope_key() or "").strip()
    if not workspace_id:
        return
    actor_id = str(message.actor_user_id or get_actor_id() or "").strip()
    payload = {
        "workspace_id": workspace_id,
        "to": message.to,
        "from_email": ctx.from_email,
        "from_name": ctx.from_name,
        "reply_to": message.reply_to,
        "subject": message.subject,
        "text": message.text,
        "html": message.html,
        "provider": provider,
        "status": "sent" if outcome.success else "failed",
        "error": outcome.error,
        "provider_message_id": outcome.provider_message_id,
        "platform_mail_log_id": platform_mail_log_id,
        "source_kind": message.source_kind,
        "source_id": message.source_id,
        "delivery_source": ctx.delivery_source,
        "sent_at": utc_now_iso(),
    }
    try:
        await run_email_sent_hooks(
            workspace_id=workspace_id,
            actor_id=actor_id,
            payload=payload,
        )
    except Exception:
        logger.exception(
            "email_service: workspace email hooks failed ws=%s to=%s",
            workspace_id,
            message.to,
        )


def _send_via_console(message: EmailMessage, ctx: "SendContext") -> bool:
    """Log the email at INFO so operators / devs can read it.

    Used in development and tests. The output deliberately includes the full
    text body so a developer running the backend can copy/paste the reset
    link from the terminal without setting up a mail provider.
    """
    logger.info(
        "email_service[console] → to=%s from=%s<%s> subject=%r\n--- TEXT ---\n%s\n--- END ---",
        message.to,
        ctx.from_name,
        ctx.from_email,
        message.subject,
        message.text,
    )
    return True


async def _send_via_sendgrid(message: EmailMessage, ctx: "SendContext") -> _SendOutcome:
    """Call SendGrid's v3 Mail Send API."""
    api_key = ctx.api_key or settings.SENDGRID_API_KEY
    if not api_key:
        logger.error("email_service[sendgrid]: SENDGRID API key unset")
        return _SendOutcome(success=False, error="SENDGRID API key unset")

    payload: dict = {
        "personalizations": [{"to": [{"email": message.to}]}],
        "from": {"email": ctx.from_email, "name": ctx.from_name},
        "subject": message.subject,
        "content": [
            {"type": "text/plain", "value": message.text},
            {"type": "text/html", "value": message.html},
        ],
    }
    if message.reply_to:
        payload["reply_to"] = {"email": message.reply_to}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if resp.status_code >= 400:
        logger.error(
            "email_service[sendgrid]: send failed status=%s body=%s",
            resp.status_code,
            resp.text[:500],
        )
        return _SendOutcome(
            success=False,
            error=f"sendgrid HTTP {resp.status_code}",
        )

    logger.info(
        "email_service[sendgrid] sent → to=%s subject=%r",
        message.to,
        message.subject,
    )
    return _SendOutcome(success=True)


async def _send_via_resend(message: EmailMessage, ctx: "SendContext") -> _SendOutcome:
    """Call Resend's API."""
    api_key = ctx.api_key or settings.RESEND_API_KEY
    if not api_key:
        logger.error("email_service[resend]: RESEND API key unset")
        return _SendOutcome(success=False, error="RESEND API key unset")

    payload = {
        "from": f"{ctx.from_name} <{ctx.from_email}>",
        "to": [message.to],
        "subject": message.subject,
        "html": message.html,
        "text": message.text,
    }
    if message.reply_to:
        payload["reply_to"] = message.reply_to

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if resp.status_code >= 400:
        logger.error(
            "email_service[resend]: send failed status=%s body=%s",
            resp.status_code,
            resp.text[:500],
        )
        return _SendOutcome(
            success=False,
            error=f"resend HTTP {resp.status_code}",
        )

    msg_id = None
    try:
        msg_id = (resp.json() or {}).get("id")
    except Exception:
        pass
    logger.info(
        "email_service[resend] sent → to=%s subject=%r id=%s",
        message.to,
        message.subject,
        msg_id or "?",
    )
    return _SendOutcome(success=True, provider_message_id=msg_id)


# ─────────────────────────────────────────────────────────────────────────────
# Templating
# ─────────────────────────────────────────────────────────────────────────────


def render_password_reset_email(
    *,
    recipient_email: str,
    recipient_name: Optional[str],
    reset_url: str,
    expires_minutes: int,
) -> EmailMessage:
    """Compose the password-reset email body.

    Kept intentionally plain — system-font HTML, no images, single CTA.
    The same content is rendered as both HTML and text so plain-text clients
    receive an identical message.
    """
    # SECURITY: every value coming from the database (display_name,
    # email) is HTML-escaped before interpolation; the reset URL host
    # path is fixed by APP_BASE_URL but the token segment is escaped via
    # urllib.parse.quote at the link-building site (password_reset.py).
    # We additionally escape the URL here as a defence-in-depth — html
    # escaping does not corrupt a properly-encoded URL.
    safe_name = html_lib.escape(recipient_name) if recipient_name else None
    safe_email = html_lib.escape(recipient_email)
    safe_url_attr = html_lib.escape(reset_url, quote=True)
    safe_url_text = html_lib.escape(reset_url)
    greeting_text = f"Hi {recipient_name}," if recipient_name else "Hi,"
    greeting_html = f"Hi {safe_name}," if safe_name else "Hi,"
    subject = "Reset your Integral password"

    text = (
        f"{greeting_text}\n\n"
        f"Someone (hopefully you) requested a password reset for your Integral account "
        f"({recipient_email}).\n\n"
        f"Open this link to choose a new password — it expires in {expires_minutes} "
        f"minutes and works only once:\n\n"
        f"{reset_url}\n\n"
        f"If you didn't request this, you can safely ignore this email — your password "
        f"won't change.\n\n"
        f"— Integral\n"
    )

    html = f"""<!doctype html>
<html>
  <body style="margin:0;padding:24px;font-family:-apple-system,Segoe UI,Inter,sans-serif;background:#fafafa;color:#0b0b0c;line-height:1.55;">
    <div style="max-width:480px;margin:0 auto;background:#ffffff;border:1px solid #ececef;border-radius:14px;padding:32px;">
      <h1 style="font-size:20px;font-weight:600;letter-spacing:-0.018em;margin:0 0 16px 0;">Reset your password</h1>
      <p style="margin:0 0 16px 0;color:#4a4a52;font-size:14px;">
        {greeting_html} someone (hopefully you) requested a password reset for your Integral account
        (<strong>{safe_email}</strong>).
      </p>
      <p style="margin:0 0 24px 0;color:#4a4a52;font-size:14px;">
        Tap the button to choose a new password. The link expires in {expires_minutes} minutes
        and works only once.
      </p>
      <p style="margin:0 0 24px 0;">
        <a href="{safe_url_attr}" style="display:inline-block;background:#ff5a1f;color:#ffffff;padding:10px 18px;border-radius:8px;font-weight:600;text-decoration:none;font-size:14px;">
          Reset password
        </a>
      </p>
      <p style="margin:0 0 8px 0;color:#8a8a93;font-size:12px;">Or copy and paste this URL:</p>
      <p style="margin:0 0 24px 0;color:#4a4a52;font-size:12px;word-break:break-all;">
        <a href="{safe_url_attr}" style="color:#4a4a52;">{safe_url_text}</a>
      </p>
      <p style="margin:0;color:#8a8a93;font-size:12px;">
        If you didn't request this, you can safely ignore this email — your password won't change.
      </p>
    </div>
  </body>
</html>"""

    return EmailMessage(to=recipient_email, subject=subject, html=html, text=text)


def render_verification_email(
    *,
    recipient_email: str,
    recipient_name: Optional[str],
    code: str,
    expires_minutes: int,
) -> EmailMessage:
    """Compose the email-verification OTP email."""
    safe_name = html_lib.escape(recipient_name) if recipient_name else None
    safe_email = html_lib.escape(recipient_email)
    safe_code = html_lib.escape(code)
    greeting_text = f"Hi {recipient_name}," if recipient_name else "Hi,"
    greeting_html = f"Hi {safe_name}," if safe_name else "Hi,"
    subject = "Verify your Integral email"

    text = (
        f"{greeting_text}\n\n"
        f"Enter this 6-digit code to verify your Integral account "
        f"({recipient_email}):\n\n"
        f"  {code}\n\n"
        f"The code expires in {expires_minutes} minutes.\n\n"
        f"If you didn't create an Integral account, you can safely ignore "
        f"this email.\n\n"
        f"— Integral\n"
    )

    html = f"""<!doctype html>
<html>
  <body style="margin:0;padding:24px;font-family:-apple-system,Segoe UI,Inter,sans-serif;background:#fafafa;color:#0b0b0c;line-height:1.55;">
    <div style="max-width:480px;margin:0 auto;background:#ffffff;border:1px solid #ececef;border-radius:14px;padding:32px;">
      <h1 style="font-size:20px;font-weight:600;letter-spacing:-0.018em;margin:0 0 16px 0;">Verify your email</h1>
      <p style="margin:0 0 16px 0;color:#4a4a52;font-size:14px;">
        {greeting_html} enter this code to verify your Integral account
        (<strong>{safe_email}</strong>):
      </p>
      <div style="margin:0 0 24px 0;text-align:center;">
        <span style="display:inline-block;font-size:32px;font-weight:700;letter-spacing:0.2em;background:#f3f4f6;border-radius:10px;padding:14px 32px;color:#0b0b0c;font-family:monospace;">
          {safe_code}
        </span>
      </div>
      <p style="margin:0 0 8px 0;color:#8a8a93;font-size:12px;">
        This code expires in {expires_minutes} minutes and can only be used once.
      </p>
      <p style="margin:0;color:#8a8a93;font-size:12px;">
        If you didn't create an Integral account, you can safely ignore this email.
      </p>
    </div>
  </body>
</html>"""

    return EmailMessage(to=recipient_email, subject=subject, html=html, text=text)


def render_invitation_email(
    *,
    recipient_email: str,
    inviter_name: Optional[str],
    organization_name: str,
    role: str,
    acceptance_url: str,
    message: Optional[str] = None,
    expires_days: int = 14,
) -> EmailMessage:
    """Compose the organization-invitation email (Phase 3b)."""
    safe_email = html_lib.escape(recipient_email)
    safe_inviter = html_lib.escape(inviter_name) if inviter_name else None
    safe_org = html_lib.escape(organization_name or "an organization")
    safe_role = html_lib.escape(role or "member")
    safe_url_attr = html_lib.escape(acceptance_url, quote=True)
    safe_url_text = html_lib.escape(acceptance_url)
    safe_msg = html_lib.escape(message) if message else None

    inviter_clause = f"{inviter_name} invited" if inviter_name else "You're invited"
    inviter_html = f"{safe_inviter} invited" if safe_inviter else "You've been invited"
    subject = f"You're invited to join {organization_name} on Integral"

    text_parts = [
        f"{inviter_clause} you to join {organization_name} on Integral as a "
        f"{role}.",
        "",
        f"Open this link to accept — it expires in {expires_days} days:",
        "",
        acceptance_url,
    ]
    if message:
        text_parts.extend(["", f"Note from inviter: {message}"])
    text_parts.extend(
        [
            "",
            "If you don't want to join, ignore this email — no action is taken.",
            "",
            "— Integral",
        ]
    )
    text = "\n".join(text_parts)

    msg_html = ""
    if safe_msg:
        msg_html = (
            f'<p style="margin:0 0 16px 0;color:#4a4a52;font-size:14px;">'
            f"<em>Note from inviter:</em> {safe_msg}</p>"
        )

    html = f"""<!doctype html>
<html>
  <body style="margin:0;padding:24px;font-family:-apple-system,Segoe UI,Inter,sans-serif;background:#fafafa;color:#0b0b0c;line-height:1.55;">
    <div style="max-width:480px;margin:0 auto;background:#ffffff;border:1px solid #ececef;border-radius:14px;padding:32px;">
      <h1 style="font-size:20px;font-weight:600;letter-spacing:-0.018em;margin:0 0 16px 0;">Join {safe_org}</h1>
      <p style="margin:0 0 16px 0;color:#4a4a52;font-size:14px;">
        {inviter_html} you to join <strong>{safe_org}</strong> on Integral as a
        <strong>{safe_role}</strong> ({safe_email}).
      </p>
      {msg_html}
      <p style="margin:0 0 24px 0;color:#4a4a52;font-size:14px;">
        Tap the button to accept. The link expires in {expires_days} days.
      </p>
      <p style="margin:0 0 24px 0;">
        <a href="{safe_url_attr}" style="display:inline-block;background:#ff5a1f;color:#ffffff;padding:10px 18px;border-radius:8px;font-weight:600;text-decoration:none;font-size:14px;">
          Accept invitation
        </a>
      </p>
      <p style="margin:0 0 8px 0;color:#8a8a93;font-size:12px;">Or copy and paste this URL:</p>
      <p style="margin:0 0 24px 0;color:#4a4a52;font-size:12px;word-break:break-all;">
        <a href="{safe_url_attr}" style="color:#4a4a52;">{safe_url_text}</a>
      </p>
      <p style="margin:0;color:#8a8a93;font-size:12px;">
        If you don't want to join, ignore this email — no action is taken.
      </p>
    </div>
  </body>
</html>"""

    return EmailMessage(to=recipient_email, subject=subject, html=html, text=text)

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

import html as html_lib
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Never routed through workspace Email Log delivery (platform credentials only).
PLATFORM_ONLY_SOURCE_KINDS = frozenset(
    {"password_reset", "email_verification"},
)


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
class _SendCredentials:
    provider: str
    from_email: str
    from_name: str
    api_key: Optional[str] = None
    default_reply_to: Optional[str] = None


@dataclass
class _SendOutcome:
    success: bool
    error: Optional[str] = None
    provider_message_id: Optional[str] = None


async def _resolve_send_credentials(message: EmailMessage) -> _SendCredentials:
    """Pick workspace delivery when configured; otherwise platform env."""
    ws_id = str(message.workspace_id or "").strip()
    kind = str(message.source_kind or "").strip()
    if ws_id and kind not in PLATFORM_ONLY_SOURCE_KINDS:
        try:
            from app.services.workspace_email_delivery import resolve_active_delivery

            delivery = await resolve_active_delivery(ws_id)
        except Exception:
            logger.exception(
                "email_service: workspace delivery resolve failed ws=%s", ws_id
            )
            delivery = None
        if delivery is not None:
            return _SendCredentials(
                provider=delivery.provider,
                from_email=delivery.from_email,
                from_name=delivery.from_name,
                api_key=delivery.api_key,
                default_reply_to=delivery.reply_to,
            )

    platform = (settings.EMAIL_PROVIDER or "console").strip().lower()
    return _SendCredentials(
        provider=platform,
        from_email=str(settings.EMAIL_FROM or "").strip(),
        from_name=str(settings.EMAIL_FROM_NAME or "").strip(),
        api_key=None,
    )


def _effective_reply_to(
    message: EmailMessage, creds: _SendCredentials
) -> Optional[str]:
    if message.reply_to:
        return message.reply_to
    return creds.default_reply_to


async def send_email(message: EmailMessage) -> bool:
    """Dispatch the message via the configured provider. Never raises.

    Returns True on apparent success, False if the provider rejected the
    message or raised. The caller should not surface this distinction to the
    end user (avoid leaking provider state) — it's logged for operators.

    When ``workspace_id`` is set and workspace Email Log delivery is enabled,
    sends through that org's provider/from address (except platform-only
    ``source_kind`` values).
    """
    creds = await _resolve_send_credentials(message)
    provider = creds.provider
    outcome = _SendOutcome(success=False, error="unknown provider outcome")
    reply_to = _effective_reply_to(message, creds)
    try:
        if provider == "sendgrid":
            outcome = await _send_via_sendgrid(message, creds, reply_to=reply_to)
        elif provider == "resend":
            outcome = await _send_via_resend(message, creds, reply_to=reply_to)
        else:
            outcome = _send_via_console(message, creds)
    except Exception as exc:
        logger.exception(
            "email_service: provider=%s failed to send to=%s subject=%r",
            provider,
            message.to,
            message.subject,
        )
        outcome = _SendOutcome(success=False, error=str(exc)[:500])

    platform_mail_log_id = await _persist_mail_attempt(
        message, provider, creds, outcome
    )
    await _dispatch_workspace_email_hooks(
        message, provider, creds, outcome, platform_mail_log_id, reply_to=reply_to
    )
    return outcome.success


async def _persist_mail_attempt(
    message: EmailMessage,
    provider: str,
    creds: _SendCredentials,
    outcome: _SendOutcome,
) -> Optional[str]:
    try:
        from app.services.mail_log import record_mail_attempt
    except ModuleNotFoundError:
        return None
    try:
        return await record_mail_attempt(
            to=message.to,
            from_email=creds.from_email,
            from_name=creds.from_name,
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
    creds: _SendCredentials,
    outcome: _SendOutcome,
    platform_mail_log_id: Optional[str],
    *,
    reply_to: Optional[str],
) -> None:
    try:
        from app.middleware.agentive_scope import get_actor_id, get_scope_key
        from app.services.hooks.email_sent_runtime import run_email_sent_hooks
        from app.utils.time import utc_now_iso
    except ImportError:
        return

    workspace_id = str(message.workspace_id or get_scope_key() or "").strip()
    if not workspace_id:
        return
    actor_id = str(message.actor_user_id or get_actor_id() or "").strip()
    payload = {
        "workspace_id": workspace_id,
        "to": message.to,
        "from_email": creds.from_email,
        "from_name": creds.from_name,
        "reply_to": reply_to,
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


def _send_via_console(message: EmailMessage, creds: _SendCredentials) -> _SendOutcome:
    """Log the email at INFO so operators / devs can read it.

    Used in development and tests. The output deliberately includes the full
    text body so a developer running the backend can copy/paste the reset
    link from the terminal without setting up a mail provider.
    """
    logger.info(
        "email_service[console] → to=%s from=%s<%s> subject=%r\n--- TEXT ---\n%s\n--- END ---",
        message.to,
        creds.from_name,
        creds.from_email,
        message.subject,
        message.text,
    )
    return _SendOutcome(success=True)


async def _send_via_sendgrid(
    message: EmailMessage,
    creds: _SendCredentials,
    *,
    reply_to: Optional[str],
) -> _SendOutcome:
    """Call SendGrid's v3 Mail Send API."""
    api_key = creds.api_key or settings.SENDGRID_API_KEY
    if not api_key:
        logger.error("email_service[sendgrid]: API key is unset")
        return _SendOutcome(success=False, error="sendgrid API key is unset")

    payload: dict = {
        "personalizations": [{"to": [{"email": message.to}]}],
        "from": {"email": creds.from_email, "name": creds.from_name},
        "subject": message.subject,
        "content": [
            {"type": "text/plain", "value": message.text},
            {"type": "text/html", "value": message.html},
        ],
    }
    if reply_to:
        payload["reply_to"] = {"email": reply_to}

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
            error=f"sendgrid status={resp.status_code} body={resp.text[:500]}",
        )

    logger.info(
        "email_service[sendgrid] sent → to=%s subject=%r",
        message.to,
        message.subject,
    )
    return _SendOutcome(success=True)


async def _send_via_resend(
    message: EmailMessage,
    creds: _SendCredentials,
    *,
    reply_to: Optional[str],
) -> _SendOutcome:
    """Call Resend's API."""
    api_key = creds.api_key or settings.RESEND_API_KEY
    if not api_key:
        logger.error("email_service[resend]: API key is unset")
        return _SendOutcome(success=False, error="resend API key is unset")

    from_line = creds.from_email
    if creds.from_name:
        from_line = f"{creds.from_name} <{creds.from_email}>"

    payload = {
        "from": from_line,
        "to": [message.to],
        "subject": message.subject,
        "html": message.html,
        "text": message.text,
    }
    if reply_to:
        payload["reply_to"] = reply_to

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
            error=f"resend status={resp.status_code} body={resp.text[:500]}",
        )

    provider_message_id = (resp.json() or {}).get("id")
    logger.info(
        "email_service[resend] sent → to=%s subject=%r id=%s",
        message.to,
        message.subject,
        provider_message_id or "?",
    )
    return _SendOutcome(success=True, provider_message_id=provider_message_id)


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


def render_public_form_email(
    *,
    recipient_email: str,
    recipient_name: Optional[str],
    form_url: str,
    form_title: Optional[str] = None,
) -> EmailMessage:
    """Compose an email that points the recipient at a public intake form."""
    safe_name = html_lib.escape(recipient_name) if recipient_name else None
    safe_email = html_lib.escape(recipient_email)
    safe_url_attr = html_lib.escape(form_url, quote=True)
    safe_url_text = html_lib.escape(form_url)
    title = form_title or "onboarding form"
    safe_title = html_lib.escape(title)
    greeting_text = f"Hi {recipient_name}," if recipient_name else "Hi,"
    greeting_html = f"Hi {safe_name}," if safe_name else "Hi,"
    onboarding = "onboarding" in title.lower()
    subject = (
        "Welcome — complete your onboarding"
        if onboarding
        else f"Please complete your {title}"
    )
    headline = (
        "You're hired — let's finish onboarding"
        if onboarding
        else f"Complete your {safe_title}"
    )
    cta = "Start onboarding" if onboarding else "Open form"
    steps_html = ""
    if onboarding:
        steps_html = (
            '<ul style="margin:0 0 20px 0;padding:0 0 0 18px;color:#4a4a52;font-size:14px;">'
            "<li>Review and sign your employment contract</li>"
            "<li>Confirm your personal details</li>"
            "<li>Complete any remaining HR steps</li>"
            "</ul>"
        )

    text = (
        f"{greeting_text}\n\n"
        + (
            "Congratulations on joining the team. Use the link below to complete "
            "your onboarding (contract, personal details, and HR steps):\n\n"
            if onboarding
            else f"Please complete your {title} using this link:\n\n"
        )
        + f"{form_url}\n\n"
        f"— Integral\n"
    )

    html = f"""<!doctype html>
<html>
  <body style="margin:0;padding:24px;font-family:-apple-system,Segoe UI,Inter,sans-serif;background:#fafafa;color:#0b0b0c;line-height:1.55;">
    <div style="max-width:480px;margin:0 auto;background:#ffffff;border:1px solid #ececef;border-radius:14px;padding:32px;">
      <p style="margin:0 0 8px 0;font-size:12px;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;color:#ff5a1f;">Integral</p>
      <h1 style="font-size:20px;font-weight:600;letter-spacing:-0.018em;margin:0 0 16px 0;">{headline}</h1>
      <p style="margin:0 0 16px 0;color:#4a4a52;font-size:14px;">
        {greeting_html} {"we're excited to have you on board. Use the button below to open your secure onboarding workspace." if onboarding else "please fill in the form using the button below."}
      </p>
      {steps_html}
      <p style="margin:0 0 24px 0;">
        <a href="{safe_url_attr}" style="display:inline-block;background:#ff5a1f;color:#ffffff;padding:10px 18px;border-radius:8px;font-weight:600;text-decoration:none;font-size:14px;">
          {cta}
        </a>
      </p>
      <p style="margin:0 0 8px 0;color:#8a8a93;font-size:12px;">Or copy and paste this URL:</p>
      <p style="margin:0 0 24px 0;color:#4a4a52;font-size:12px;word-break:break-all;">
        <a href="{safe_url_attr}" style="color:#4a4a52;">{safe_url_text}</a>
      </p>
      <p style="margin:0;color:#8a8a93;font-size:12px;">
        This message was sent to {safe_email}.
      </p>
    </div>
  </body>
</html>"""

    return EmailMessage(to=recipient_email, subject=subject, html=html, text=text)


def render_member_provision_email(
    *,
    recipient_email: str,
    recipient_name: str,
    workspace_name: str,
    login_url: str,
    account_email: str,
    temp_password: Optional[str] = None,
    welcome_message: Optional[str] = None,
) -> EmailMessage:
    """Compose hire / member-provision credentials email (new or existing account)."""
    safe_name = html_lib.escape(recipient_name or "")
    safe_email = html_lib.escape(recipient_email)
    safe_account = html_lib.escape(account_email)
    safe_ws = html_lib.escape(workspace_name or "your organization")
    safe_login_attr = html_lib.escape(login_url, quote=True)
    safe_login_text = html_lib.escape(login_url)
    safe_intro = html_lib.escape(welcome_message) if welcome_message else None
    greeting_text = f"Hi {recipient_name}," if recipient_name else "Hi,"
    greeting_html = f"Hi {safe_name}," if safe_name else "Hi,"

    intro_html = ""
    intro_text = ""
    if welcome_message and welcome_message.strip():
        intro_text = f"{welcome_message.strip()}\n\n"
        intro_html = (
            f'<p style="margin:0 0 16px 0;color:#4a4a52;font-size:14px;">'
            f"<em>{safe_intro}</em></p>"
        )

    if temp_password:
        safe_password = html_lib.escape(temp_password)
        subject = "Your Integral account"
        text = (
            f"{intro_text}"
            f"{greeting_text}\n\n"
            f"Welcome — an Integral account has been created for you at {workspace_name}.\n\n"
            f"Sign in here:\n{login_url}\n\n"
            f"Email: {account_email}\n"
            f"Temporary password: {temp_password}\n\n"
            "You will be asked to change your password after your first login.\n\n"
            "— Integral\n"
        )
        creds_html = (
            f'<div style="margin:0 0 24px 0;background:#f8f9fb;border:1px solid #ececef;'
            f'border-radius:10px;padding:16px 18px;">'
            f'<p style="margin:0 0 8px 0;font-size:12px;color:#8a8a93;text-transform:uppercase;'
            f'letter-spacing:0.04em;font-weight:600;">Sign-in details</p>'
            f'<p style="margin:0 0 6px 0;font-size:14px;color:#0b0b0c;">'
            f"<strong>Email</strong> {safe_account}</p>"
            f'<p style="margin:0;font-size:14px;color:#0b0b0c;">'
            f"<strong>Temporary password</strong> "
            f'<span style="font-family:ui-monospace,Menlo,monospace;font-weight:600;">'
            f"{safe_password}</span></p></div>"
        )
        body_html = (
            f"{intro_html}"
            f'<p style="margin:0 0 16px 0;color:#4a4a52;font-size:14px;">'
            f"{greeting_html} welcome to <strong>{safe_ws}</strong> on Integral. "
            f"Use the button below to sign in, then choose a new password when prompted."
            f"</p>"
            f"{creds_html}"
        )
        cta = "Sign in to Integral"
    else:
        subject = f"Welcome to {workspace_name} on Integral"
        text = (
            f"{intro_text}"
            f"{greeting_text}\n\n"
            f"You have been added to {workspace_name} on Integral.\n\n"
            f"Sign in with your existing account:\n{login_url}\n\n"
            f"Work email on file: {account_email}\n\n"
            "— Integral\n"
        )
        body_html = (
            f"{intro_html}"
            f'<p style="margin:0 0 16px 0;color:#4a4a52;font-size:14px;">'
            f"{greeting_html} you have been added to <strong>{safe_ws}</strong> on Integral. "
            f"Sign in with your existing password using the work email "
            f"<strong>{safe_account}</strong>."
            f"</p>"
        )
        cta = "Go to sign in"

    html = f"""<!doctype html>
<html>
  <body style="margin:0;padding:24px;font-family:-apple-system,Segoe UI,Inter,sans-serif;background:#fafafa;color:#0b0b0c;line-height:1.55;">
    <div style="max-width:480px;margin:0 auto;background:#ffffff;border:1px solid #ececef;border-radius:14px;padding:32px;">
      <p style="margin:0 0 8px 0;font-size:12px;font-weight:600;letter-spacing:0.06em;text-transform:uppercase;color:#ff5a1f;">Integral</p>
      <h1 style="font-size:20px;font-weight:600;letter-spacing:-0.018em;margin:0 0 16px 0;">{html_lib.escape(subject)}</h1>
      {body_html}
      <p style="margin:0 0 24px 0;">
        <a href="{safe_login_attr}" style="display:inline-block;background:#ff5a1f;color:#ffffff;padding:10px 18px;border-radius:8px;font-weight:600;text-decoration:none;font-size:14px;">
          {cta}
        </a>
      </p>
      <p style="margin:0 0 8px 0;color:#8a8a93;font-size:12px;">Or copy and paste this URL:</p>
      <p style="margin:0 0 24px 0;color:#4a4a52;font-size:12px;word-break:break-all;">
        <a href="{safe_login_attr}" style="color:#4a4a52;">{safe_login_text}</a>
      </p>
      <p style="margin:0;color:#8a8a93;font-size:12px;">
        This message was sent to {safe_email}. If you were not expecting access, contact your administrator.
      </p>
    </div>
  </body>
</html>"""

    return EmailMessage(to=recipient_email, subject=subject, html=html, text=text)

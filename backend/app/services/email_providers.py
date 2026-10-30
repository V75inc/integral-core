"""Provider-neutral email transports. No graph access or credential decryption."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import smtplib
import socket
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as MIMEMessage
from email.utils import formataddr, parsedate_to_datetime
from typing import Any

import httpx

from app.schemas.workspace_email import EmailConfiguration


@dataclass(frozen=True)
class DeliveryOutcome:
    status: str
    error: str = ""
    provider_message_id: str = ""
    retryable: bool = False
    retry_after: float = 0


def _smtp_send(
    config: EmailConfiguration, message: Any, identity: str
) -> DeliveryOutcome:
    host = config.smtp_host.strip().lower()
    allowed = {
        h.strip().lower()
        for h in os.environ.get("INTEGRAL_SMTP_TRUSTED_HOSTS", "").split(",")
        if h.strip()
    }
    addresses = socket.getaddrinfo(host, config.smtp_port, type=socket.SOCK_STREAM)
    if not addresses or (
        host not in allowed
        and any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses)
    ):
        return DeliveryOutcome("failed", "SMTP destination is not allowed")
    # Pin the validated address while retaining the configured hostname for TLS/SNI.
    target = (addresses[0][4][0], config.smtp_port)

    class PinnedSMTP(smtplib.SMTP):
        def _get_socket(self, host, port, timeout):
            return socket.create_connection(target, timeout)

    class PinnedSMTPSSL(smtplib.SMTP_SSL):
        def _get_socket(self, host, port, timeout):
            sock = socket.create_connection(target, timeout)
            return self.context.wrap_socket(sock, server_hostname=host)

    mime = MIMEMessage()
    mime["From"] = formataddr((config.from_name, config.from_email))
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime["Message-ID"] = f"<{identity}@integral.local>"
    if message.reply_to or config.reply_to:
        mime["Reply-To"] = message.reply_to or config.reply_to
    mime.set_content(message.text)
    mime.add_alternative(message.html, subtype="html")
    client = None
    data_started = False
    try:
        ctx = ssl.create_default_context()
        if config.smtp_tls == "implicit":
            client = PinnedSMTPSSL(host, config.smtp_port, timeout=10, context=ctx)
        else:
            client = PinnedSMTP(host, config.smtp_port, timeout=10)
            client.ehlo()
            client.starttls(context=ctx)
            client.ehlo()
        if config.smtp_username:
            client.login(config.smtp_username, config.smtp_password or "")
        # Mark the uncertainty boundary before entering SMTP DATA/send_message.
        data_started = True
        refused = client.send_message(mime)
        if refused:
            return DeliveryOutcome("failed", "SMTP rejected recipient")
        return DeliveryOutcome("accepted", provider_message_id=identity)
    except smtplib.SMTPResponseException as exc:
        return DeliveryOutcome(
            "failed",
            f"SMTP rejected request ({exc.smtp_code})",
            retryable=400 <= exc.smtp_code < 500,
        )
    except (OSError, smtplib.SMTPException):
        return DeliveryOutcome(
            "unknown" if data_started else "failed",
            "SMTP connection failed",
            retryable=not data_started,
        )
    finally:
        if client:
            client.close()


def validate_message(message: Any) -> None:
    """Enforce the single-recipient and bounded-header transport contract."""
    for value in (message.to, message.reply_to or ""):
        if len(value) > 320:
            raise ValueError("Email address is too long")
        EmailConfiguration.address(value)
    if (
        not message.to
        or len(message.subject) > 998
        or any(c in message.subject for c in "\r\n\x00")
    ):
        raise ValueError("Invalid email recipient or subject")
    if len(message.text.encode()) + len(message.html.encode()) > 1_000_000:
        raise ValueError("Email content exceeds the size limit")


async def deliver(
    config: EmailConfiguration, message: Any, identity: str
) -> DeliveryOutcome:
    """Return acceptance, rejection or uncertainty; never expose provider response bodies."""
    try:
        validate_message(message)
    except ValueError:
        return DeliveryOutcome(
            "failed", "Invalid email address, header or message size"
        )
    if not config.from_email or not message.to or "@" not in message.to:
        return DeliveryOutcome("failed", "A sender and recipient address are required")
    if any(
        "\r" in str(v) or "\n" in str(v)
        for v in (message.to, message.subject, message.reply_to or "")
    ):
        return DeliveryOutcome("failed", "Email headers must not contain line breaks")
    try:
        if config.provider == "smtp":
            if not config.smtp_host or (
                config.smtp_username and not config.smtp_password
            ):
                return DeliveryOutcome(
                    "failed", "SMTP host and credentials are required"
                )
            return await asyncio.to_thread(_smtp_send, config, message, identity)
        if not config.api_key:
            return DeliveryOutcome("failed", "API key is required")
        headers = {"Authorization": f"Bearer {config.api_key}"}
        reply = message.reply_to or config.reply_to
        if config.provider == "resend":
            url = "https://api.resend.com/emails"
            headers["Idempotency-Key"] = identity
            payload = {
                "from": formataddr((config.from_name, config.from_email)),
                "to": [message.to],
                "subject": message.subject,
                "html": message.html,
                "text": message.text,
                "tags": [{"name": "integral_message_id", "value": identity}],
            }
            if reply:
                payload["reply_to"] = reply
        else:
            url = "https://api.sendgrid.com/v3/mail/send"
            payload = {
                "personalizations": [
                    {
                        "to": [{"email": message.to}],
                        "custom_args": {"integral_message_id": identity},
                    }
                ],
                "from": {"email": config.from_email, "name": config.from_name},
                "subject": message.subject,
                "content": [
                    {"type": "text/plain", "value": message.text},
                    {"type": "text/html", "value": message.html},
                ],
            }
            if reply:
                payload["reply_to"] = {"email": reply}
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.post(url, headers=headers, json=payload)
        if not 200 <= response.status_code < 300:
            # 5xx can occur after provider acceptance; only explicit throttling is retried.
            return DeliveryOutcome(
                "unknown" if response.status_code >= 500 else "failed",
                f"Provider returned HTTP {response.status_code}",
                retryable=response.status_code == 429,
                retry_after=_retry_after(response.headers.get("retry-after", "")),
            )
        ref = (
            str(response.json().get("id") or "")
            if config.provider == "resend"
            else response.headers.get("x-message-id", "")
        )
        return DeliveryOutcome("accepted", provider_message_id=ref)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return DeliveryOutcome(
            "failed", "Could not connect to provider", retryable=True
        )
    except (httpx.HTTPError, OSError, ValueError):
        return DeliveryOutcome("unknown", "Provider outcome could not be confirmed")


def _retry_after(raw: str) -> float:
    try:
        return max(0, min(float(raw), 86400))
    except ValueError:
        from datetime import datetime, timezone

        try:
            return max(
                0,
                min(
                    (
                        parsedate_to_datetime(raw) - datetime.now(timezone.utc)
                    ).total_seconds(),
                    86400,
                ),
            )
        except (ValueError, TypeError, OverflowError):
            return 60

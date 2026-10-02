"""Persist transactional mail attempts at send_email (platform operator log)."""

from __future__ import annotations

import logging
from typing import Optional

from app.models.mail_log import MailLog
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

_MAX_ERROR_LEN = 500


async def record_mail_attempt(
    *,
    to: str,
    from_email: str,
    from_name: str,
    reply_to: Optional[str],
    subject: str,
    text: str,
    html: str,
    provider: str,
    status: str,
    error: Optional[str] = None,
    provider_message_id: Optional[str] = None,
) -> Optional[str]:
    """Append one mail attempt. Never raises. Returns record id when saved."""
    try:
        record = MailLog(
            to=(to or "").strip(),
            from_email=(from_email or "").strip(),
            from_name=(from_name or "").strip(),
            reply_to=(reply_to or None),
            subject=subject or "",
            text=text or "",
            html=html or "",
            provider=(provider or "console").strip().lower(),
            status=status if status in ("sent", "failed") else "failed",
            error=(error or None)[:_MAX_ERROR_LEN] if error else None,
            provider_message_id=(provider_message_id or None),
            created_at=utc_now_iso(),
        )
        await record.save()
        return str(record.id or "")
    except Exception:
        logger.exception(
            "mail_log: failed to persist attempt to=%s subject=%r status=%s",
            to,
            subject,
            status,
        )
        return None

"""Pending member assigned-form prompt stored on User.preferences.

Preference key remains ``pending_onboarding_form`` for backward compatibility;
auth responses also expose ``pending_assigned_form``.
"""

from __future__ import annotations

from typing import Dict, Optional
from urllib.parse import quote

from app.models.nodes import User

PENDING_ONBOARDING_FORM_KEY = "pending_onboarding_form"

# Canonical member assigned-form route (substrate-neutral; not HR-specific).
MEMBER_ASSIGNED_FORM_PATH = "/me/assigned-form"
LEGACY_MEMBER_ONBOARDING_PATH = "/hr/employee-onboarding"


def member_assigned_form_relative_url(entry_id: str) -> str:
    """Relative app URL for the authenticated member assigned-form wizard."""
    entry = str(entry_id or "").strip()
    if not entry:
        raise ValueError("entry_id is required")
    return f"{MEMBER_ASSIGNED_FORM_PATH}?entry={quote(entry, safe='')}"


def pending_onboarding_form_url(user: User) -> Optional[str]:
    slot = (user.preferences or {}).get(PENDING_ONBOARDING_FORM_KEY)
    if not isinstance(slot, dict):
        return None
    url = str(slot.get("url") or "").strip()
    return url or None


def pending_onboarding_form_view(user: User) -> Optional[Dict[str, str]]:
    url = pending_onboarding_form_url(user)
    if not url:
        return None
    return {"url": url}


async def set_pending_onboarding_form(user: User, form_url: str) -> None:
    url = str(form_url or "").strip()
    if not url:
        raise ValueError("form_url is required")
    prefs = dict(user.preferences or {})
    prefs[PENDING_ONBOARDING_FORM_KEY] = {"url": url}
    user.preferences = prefs


async def clear_pending_onboarding_form(user: User) -> bool:
    prefs = dict(user.preferences or {})
    if PENDING_ONBOARDING_FORM_KEY not in prefs:
        return False
    prefs.pop(PENDING_ONBOARDING_FORM_KEY, None)
    user.preferences = prefs
    return True


def absolutize_member_onboarding_url(form_url: str) -> str:
    """Turn a relative member onboarding path into an absolute app URL for email."""
    from app.config import settings

    url = str(form_url or "").strip()
    if not url:
        return url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    base = str(getattr(settings, "APP_BASE_URL", "") or "").rstrip("/")
    if not base:
        return url
    if not url.startswith("/"):
        url = f"/{url}"
    return f"{base}{url}"


async def email_member_onboarding_form_link(
    *,
    recipient_email: str,
    form_url: str,
    workspace_id: str,
    actor_user_id: str,
    recipient_name: Optional[str] = None,
    form_title: Optional[str] = None,
    member_user_id: Optional[str] = None,
) -> bool:
    """Email the authenticated onboarding wizard link (member route, not public share)."""
    from app.services.email_service import render_public_form_email, send_email

    email_norm = str(recipient_email or "").strip().lower()
    if not email_norm:
        return False
    link = absolutize_member_onboarding_url(form_url)
    title = (form_title or "").strip() or "employee onboarding form"
    message = render_public_form_email(
        recipient_email=email_norm,
        recipient_name=recipient_name,
        form_url=link,
        form_title=title,
    )
    message.workspace_id = str(workspace_id or "")
    message.source_kind = "member_onboarding"
    message.source_id = str(member_user_id or "")
    message.actor_user_id = str(actor_user_id or "")
    return await send_email(message)

"""Server-managed prompt for a member's assigned form."""

from urllib.parse import quote

from app.models.nodes import User

PENDING_ASSIGNED_FORM_KEY = "pending_assigned_form"
MEMBER_ASSIGNED_FORM_PATH = "/me/assigned-form"


def member_assigned_form_relative_url(entry_id: str) -> str:
    """Build the canonical local link for an assigned record."""
    entry = str(entry_id or "").strip()
    if not entry:
        raise ValueError("entry_id is required")
    return f"{MEMBER_ASSIGNED_FORM_PATH}?entry={quote(entry, safe='')}"


def pending_assigned_form_url(user: User) -> str | None:
    """Read the pending link, if present."""
    slot = (user.preferences or {}).get(PENDING_ASSIGNED_FORM_KEY)
    if not isinstance(slot, dict):
        return None
    return str(slot.get("url") or "").strip() or None


def pending_assigned_form_view(user: User) -> dict[str, str] | None:
    """Get the public projection of the signed-in user's own prompt."""
    url = pending_assigned_form_url(user)
    return {"url": url} if url else None


async def set_pending_assigned_form(user: User, form_url: str) -> None:
    """Store the link in server-managed preferences."""
    url = str(form_url or "").strip()
    if not url:
        raise ValueError("form_url is required")
    user.preferences = {
        **(user.preferences or {}),
        PENDING_ASSIGNED_FORM_KEY: {"url": url},
    }


async def clear_pending_assigned_form(user: User) -> bool:
    """Clear the prompt without modifying other preferences."""
    prefs = dict(user.preferences or {})
    if PENDING_ASSIGNED_FORM_KEY not in prefs:
        return False
    prefs.pop(PENDING_ASSIGNED_FORM_KEY)
    user.preferences = prefs
    return True

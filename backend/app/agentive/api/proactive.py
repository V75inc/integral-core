"""Proactive intelligence endpoints — digests, reminders, and agent push webhook.

D-06: `/proactive/push` is renamed to `/proactive/log-push` and is logging-only —
it does NOT actually deliver. Real delivery (jvagent webhook / channel queue) lands
in Phase 5 or Phase 6. The old path retains a 308 redirect for one release window.
"""

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Request
from jvspatial.api import endpoint

from app.agentive.edges import HAS_CHANNEL_IDENTITY
from app.agentive.services.tool_scope import resolve_scope_from_request
from app.agentive.tooling.dispatch import dispatch_tool
from app.api.errors import InsufficientPermissionsError, MissingAuthenticationError
from app.api.utils import export_node, resolve_principal_id
from app.services.change_event import emit_change_event

logger = logging.getLogger(__name__)

# Profile date fields live in ``Entry.custom_fields`` (there is no
# ``Entry.due_date`` column) — these are the keys a due-date-bearing profile
# conventionally uses. Matched case-insensitively, in this order.
_DUE_FIELD_KEYS = ("due_date", "due", "deadline", "due_at", "due_on")


def _extract_due_value(entry: Any) -> Any:
    """Return the raw due value for ``entry`` (attribute, custom field, or context)."""
    direct = getattr(entry, "due_date", None)
    if direct:
        return direct
    custom = getattr(entry, "custom_fields", None) or {}
    if isinstance(custom, dict) and custom:
        lowered = {str(k).lower(): v for k, v in custom.items()}
        for key in _DUE_FIELD_KEYS:
            val = lowered.get(key)
            if val:
                return val
    context = getattr(entry, "context", None) or {}
    if isinstance(context, dict):
        return context.get("due_date")
    return None


def _parse_due(value: Any) -> Optional[datetime]:
    """Normalise a due value to an aware UTC datetime.

    Accepts ``datetime``/``date`` objects and ISO-8601 strings, including a
    trailing ``Z`` and date-only ``YYYY-MM-DD`` (midnight UTC). Naive values
    are assumed UTC — ``datetime.fromisoformat("2026-09-10")`` is naive and
    comparing it with an aware ``now`` raises ``TypeError``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z") or text.endswith("z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except (ValueError, TypeError):
            try:
                dt = datetime.strptime(text[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@endpoint(
    "/agentive/proactive/digest",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_user_digest(request: Request) -> Dict[str, Any]:
    """Generate an on-demand activity digest for the authenticated user."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    # Bind the active workspace (X-Integral-Scope header) when present, else
    # fail closed to the caller's Personal Workspace — never unconstrained.
    workspace_id: Optional[str] = None
    scope = resolve_scope_from_request(request)
    if scope is not None:
        workspace_id = scope.get("workspace_id")
    if workspace_id is None:
        from app.services.personal_workspace import (
            ensure_personal_workspace_for_user_id,
        )

        personal = await ensure_personal_workspace_for_user_id(user_id)
        if personal is not None:
            workspace_id = personal.id

    result = await dispatch_tool(
        "integral_get_digest",
        {
            "scope": "user",
            "period": "today",
        },
        principal_id=user_id,
        scope=workspace_id,
    )
    if result.is_error:
        return {"error": result.error_code or "error", "message": result.message}
    return result.data


@endpoint(
    "/agentive/proactive/reminders",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_user_reminders(request: Request) -> Dict[str, Any]:
    """Return overdue and upcoming items for the authenticated user."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    from app.services.agent_scope import accessible_tracks_for_scope
    from app.services.permissions import get_user_accessible_entries

    workspace_id: Optional[str] = None
    scope = resolve_scope_from_request(request)
    if scope is not None:
        workspace_id = scope.get("workspace_id")
    if workspace_id is None:
        from app.services.personal_workspace import (
            ensure_personal_workspace_for_user_id,
        )

        personal = await ensure_personal_workspace_for_user_id(user_id)
        if personal is not None:
            workspace_id = personal.id

    tracks = await accessible_tracks_for_scope(user_id, workspace_id=workspace_id)
    overdue = []
    upcoming = []

    for track in tracks[:20]:
        entries = await get_user_accessible_entries(user_id, track.id)
        for entry in entries:
            due = _extract_due_value(entry)
            if not due:
                continue
            status = getattr(entry, "status", "") or (
                getattr(entry, "context", None) or {}
            ).get("status", "")
            if status in ("done", "completed", "cancelled", "archived"):
                continue
            due_dt = _parse_due(due)
            if due_dt is None:
                continue
            now = datetime.now(timezone.utc)
            entry_data = {
                "id": entry.id,
                "title": getattr(entry, "title", ""),
                "track_id": track.id,
                "track_title": track.title,
                "due_date": due if isinstance(due, str) else due_dt.isoformat(),
                "due_at": due_dt.isoformat(),
                "status": status,
            }
            if due_dt < now:
                overdue.append(entry_data)
            else:
                upcoming.append(entry_data)

    overdue.sort(key=lambda e: e.get("due_at", ""))
    upcoming.sort(key=lambda e: e.get("due_at", ""))

    return {
        "reminders": overdue[:10] + upcoming[:10],
        "overdue_count": len(overdue),
        "upcoming_count": min(len(upcoming), 10),
        "total_tracks": len(tracks),
    }


@endpoint(
    "/agentive/proactive/preferences",
    methods=["PATCH"],
    auth=True,
    tags=["Agentive"],
)
async def update_proactive_preferences(
    request: Request,
    digest_enabled: Optional[bool] = None,
    digest_time: Optional[str] = None,
    timezone_pref: Optional[str] = None,
    reminders_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """Update the user's proactive intelligence preferences."""
    user_id = resolve_principal_id(request)
    if not user_id:
        raise MissingAuthenticationError(message="Authentication required")

    from app.models.nodes import User

    user = await User.get(user_id)
    prefs: Dict[str, Any] = {
        "digest_enabled": digest_enabled,
        "digest_time": digest_time,
        "timezone": timezone_pref,
        "reminders_enabled": reminders_enabled,
    }
    if not user:
        return {"message": "User not found", "preferences": prefs}

    prior_snapshot = await export_node(user)  # D-03 before-snapshot
    existing_prefs = getattr(user, "preferences", {}) or {}
    existing_prefs["proactive"] = prefs
    user.preferences = existing_prefs
    user.updated_at = datetime.now(timezone.utc).isoformat()
    await user.save()

    # D-05 single emission path. Sync inline emit before HTTP response (D-06).
    await emit_change_event(
        actor_kind="human",
        actor_id=user_id,
        action="user.update",
        resource_type="User",
        resource_id=user.id,
        before=prior_snapshot,
        after=await export_node(user),
        scope=f"user:{user.id}",
    )

    return {"message": "Preferences updated", "preferences": existing_prefs}


@endpoint(
    "/agentive/proactive/push",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
    status_code=308,
)
async def proactive_push_deprecated_redirect(request: Request) -> None:
    """D-06 deprecated path — 308 permanent redirect to /proactive/log-push.

    Retained for one release window so existing callers can migrate.
    No graph mutation; allow-listed in test_change_event_no_bypass.py.
    """
    from fastapi.responses import RedirectResponse

    return RedirectResponse(  # type: ignore[return-value]
        url="/api/agentive/proactive/log-push",
        status_code=308,
    )


@endpoint(
    "/agentive/proactive/log-push",
    methods=["POST"],
    auth=True,
    tags=["Agentive"],
    status_code=202,
)
async def proactive_log_push(
    request: Request,
    channel: str = "whatsapp",
    message_type: str = "digest",
    payload: Optional[Dict[str, Any]] = None,
    schedule: str = "immediate",
    target_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Log a proactive-message intent and deliver into chat when possible.

    Full Sweep R2: when ``payload.content`` is present, persist onto the
    matching (or most recent) ChatThread and emit a thread WS badge via
    ``persist_proactive_message_on_thread``. Channel adapters (WhatsApp etc.)
    remain separate; in-app delivery is live.
    """
    # Resolve principal — supports both service-auth and regular auth.
    user = getattr(request.state, "user", None)
    user_id: Optional[str]
    if user and hasattr(user, "id"):
        user_id = str(user.id)
    else:
        user_id = resolve_principal_id(request)

    logged_at = datetime.now(timezone.utc).isoformat()
    delivered_thread_id: Optional[str] = None

    logger.info(
        "Proactive log-push accepted: user=%s type=%s channel=%s schedule=%s",
        user_id or "<anonymous>",
        message_type,
        channel,
        schedule,
    )

    if user_id and isinstance(payload, dict):
        content = str(payload.get("content") or "").strip()
        session_id = str(payload.get("session_id") or "").strip()
        if content:
            from app.services.chat_proactive_bridge import (
                persist_proactive_message_on_thread,
            )

            delivered_thread_id = await persist_proactive_message_on_thread(
                user_id=user_id,
                session_id=session_id,
                content=content,
                metadata={"channel": channel, "message_type": message_type},
            )

    return {
        "accepted": True,
        "logged_at": logged_at,
        "delivered": bool(delivered_thread_id),
        "thread_id": delivered_thread_id,
    }


@endpoint(
    "/agentive/proactive/users-needing-digest",
    methods=["GET"],
    auth=True,
    tags=["Agentive"],
)
async def get_users_needing_digest(request: Request) -> Dict[str, Any]:
    """Return list of users who should receive a proactive digest.

    Called by jvagent's IntegralProactiveAction to find users with
    verified channel identities and proactive preferences enabled.
    Requires service auth.
    """
    if not getattr(request.state, "service_auth", False):
        raise InsufficientPermissionsError(message="Service authentication required")

    from app.agentive.nodes import ChannelIdentity
    from app.models.nodes import User

    all_identities = await ChannelIdentity.find(channel="whatsapp")
    verified = [ci for ci in all_identities if getattr(ci, "verified", False)]

    users_needing_digest = []
    seen_users = set()

    for ci in verified:
        uid = getattr(ci, "user_id", "")
        if uid in seen_users:
            continue
        seen_users.add(uid)

        user = await User.get(uid)
        if not user:
            continue

        prefs = getattr(user, "preferences", {}) or {}
        proactive_prefs = prefs.get("proactive", {})
        if not proactive_prefs.get("digest_enabled", True):
            continue

        user_nodes = await ci.nodes(
            edge=[HAS_CHANNEL_IDENTITY], direction="in", node=["User"]
        )
        user_id = user_nodes[0].id if user_nodes else uid

        users_needing_digest.append(
            {
                "user_id": user_id,
                "display_name": getattr(user, "display_name", ""),
                "channel": "whatsapp",
                "channel_user_id": getattr(ci, "channel_user_id", ""),
                "digest_time": proactive_prefs.get("digest_time", "08:00"),
                "timezone": proactive_prefs.get("timezone", "UTC"),
            }
        )

    return {"users": users_needing_digest, "total": len(users_needing_digest)}

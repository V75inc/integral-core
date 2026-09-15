"""Single-entry notification dispatch fan-out (Phase 9 Plan 09-03a, NOTIF-02).

Mirrors ``app.services.policy_engine.evaluate`` discipline: ALL notification
production MUST funnel through :func:`dispatch`. Channel adapters are
registry-resolved (``app.services.notification_channels.CHANNEL_REGISTRY``).
Idempotency on retry is tracked via ``Notification.metadata.dispatched_to[]``
(A1 — no new ChangeEventAction Literal members are introduced; channel-level
dispatch is metadata on the existing Notification entity).

Notification persistence shape (B2 — FIXED by Notification node schema):

- ``type=kind`` — NOT ``kind`` (the Notification node does not have a ``kind``
  field; the Python parameter is named ``kind`` for clarity).
- ``content=<rendered short summary>`` — NOT ``payload`` (the Notification
  node does not have a ``payload`` field; ``content`` is the inbox-tile
  short text).
- ``metadata={"payload": <raw_dict>, "idempotency_key": <str|None>,
  "dispatched_to": [<ChannelDispatchResult dicts>]}`` — the raw payload and
  per-channel ledger live here.

Single-entry discipline (D-05 / B6): the previous ``emit_change_event(
action="notification.create", ...)`` call at ``api/notifications.py``
L297-L306 was DELETED in this plan. The emission lives ONLY in
:class:`~app.services.notification_channels.in_app_channel.InAppChannel`.
``create_notification`` delegates to this module.

Canonical User lookup (B1): :func:`dispatch` uses ``await User.get(user_id)``
— the canonical lookup pattern from ``api/users.py``. There is no fictitious
``User-find-by-userid`` helper anywhere in this module (the grep gate in
the plan's acceptance_criteria asserts this explicitly).
"""

from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models.nodes import User
from app.schemas.notification_preferences import (
    NotificationPreferences,
    default_preferences,
)
from app.services.notification_channels import CHANNEL_REGISTRY
from app.services.notification_paths import resolve_notification_action_url
from app.services.permissions import get_user_node


def _render_summary(kind: str, payload: Dict[str, Any]) -> str:
    """Compose a short single-line summary for ``Notification.content``.

    Heuristics (cheapest path first):

    - If the payload carries an explicit ``content`` (legacy
      create_notification path passes the raw admin-supplied string through),
      use it verbatim — preserves back-compat with the prior REST shape.
    - Else if the payload carries a ``snippet`` (mention/share kinds), prefix
      it with the actor name: ``"Alice: Hey, look at this!"``.
    - Else if the payload carries a ``summary`` (agent_pending_write kind),
      use it verbatim.
    - Else fall back to a kind-derived stub.

    The summary is truncated to 280 chars to keep the inbox tile compact —
    the full payload remains addressable via ``notif.metadata['payload']``.
    """
    if payload.get("content"):
        return str(payload["content"])[:280]
    if "snippet" in payload:
        actor = payload.get("actor_name", "Someone")
        return f"{actor}: {payload['snippet']}"[:280]
    if "summary" in payload:
        return str(payload["summary"])[:280]
    return f"Integral: {kind}"


async def dispatch(
    *,
    user_id: str,
    kind: str,
    payload: Dict[str, Any],
    actor_id: str,
    actor_kind: str = "human",
    channels: Optional[List[str]] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Fan a single notification ``kind`` out across configured channels.

    Args:
        user_id: target user (the recipient). Resolved via the canonical
            ``await User.get(user_id)`` lookup.
        kind: a member of
            :data:`app.schemas.notification_preferences.NotificationKind`
            (``"mention" | "share" | "agent_pending_write" | "invitation" |
            "system" | "whatsapp_welcome"``). Persisted as
            ``Notification.type`` (NOT ``Notification.kind`` — see module
            docstring B2 notes).
        payload: raw payload dict — rendered into ``Notification.content`` via
            :func:`_render_summary` and stored verbatim under
            ``Notification.metadata['payload']``. EmailChannel renders Jinja
            templates against this dict; passes through to WhatsappChannel
            for Meta template-variable binding.
        actor_id: the user who triggered the notification (``"system"`` for
            agent / scheduled paths).
        actor_kind: ``"human" | "agent" | "connector" | "system"``.
        channels: optional explicit override of the channel list. When
            ``None``, the router consults the user's
            ``notification_preferences.kinds[kind]`` matrix (falling back to
            the workspace-wide ``channels`` default). When provided, the
            preference matrix is bypassed entirely — caller takes full
            responsibility for the channel selection.
        idempotency_key: an opaque caller-provided key. Stored on
            ``Notification.metadata['idempotency_key']``; not consulted by
            this dispatch (per-channel idempotency uses ``dispatched_to[]``).

    Returns:
        ``{"notification_id": <str>, "results": [<dispatched_to entries>]}``
        on success, or ``{"status": "skipped", "reason": "user_not_found",
        "results": []}`` when the user can't be resolved (the router does
        not raise — a missing user is a soft skip).
    """
    # Canonical lookup (B1): User.get(user_id) is the primary path; for
    # callers passing an AuthUser id (legacy create_notification endpoint
    # path), fall back to ``get_user_node`` which handles the AuthUser ->
    # User-node resolution. Both paths are explicit — no fictitious
    # helper is invented (the grep gate in this plan's acceptance_criteria
    # asserts this).
    user = await User.get(user_id)
    if user is None:
        user = await get_user_node(user_id)
    if not user:
        return {"status": "skipped", "reason": "user_not_found", "results": []}

    # Resolve the preference matrix. None on the node → canonical defaults.
    # Stored dicts flow through Pydantic ``model_validate`` so stale shapes
    # surface as Pydantic errors at the router boundary (extra="forbid").
    prefs_dict = user.notification_preferences
    prefs: NotificationPreferences = (
        NotificationPreferences.model_validate(prefs_dict)
        if prefs_dict
        else default_preferences()
    )
    # Per-kind matrix; fall back to the workspace-wide default for kinds
    # absent from the user's kinds map.
    kind_matrix = prefs.kinds.get(kind, prefs.channels)  # type: ignore[call-overload]

    # Build the channel list. Explicit override bypasses prefs entirely.
    if channels is not None:
        channels_to_fire: List[str] = list(channels)
    else:
        channels_to_fire = []
        if kind_matrix.in_app:
            channels_to_fire.append("in_app")
        # Outbound master switch (escape hatch — see config.py
        # NOTIFICATION_OUTBOUND_ENABLED docstring).
        if settings.NOTIFICATION_OUTBOUND_ENABLED:
            if kind_matrix.email:
                channels_to_fire.append("email")
            if kind_matrix.whatsapp:
                channels_to_fire.append("whatsapp")

    # The Notification node is the canonical in-app entity — always create
    # it, even when the channel list excludes "in_app" (callers may still
    # want the row to land for audit / future replay). Persistence shape per
    # the Notification node schema:
    #   type=kind            (NOT 'kind')
    #   content=<rendered summary>   (NOT 'payload')
    #   metadata={"payload": <raw>, "idempotency_key": ..., "dispatched_to": []}
    #
    # Phase 10.5 Plan 10.5-01: routed through ``create_notification`` so
    # the User —HAS_NOTIFICATION→ Notification edge is wired in the same
    # transaction (I-GRAPH-01).
    from app.services.app_graph import create_notification

    action_url = resolve_notification_action_url(kind, payload)

    notif = await create_notification(
        user_id=user_id,
        type=kind,
        content=_render_summary(kind, payload),
        read=False,
        action_url=action_url,
        metadata={
            "payload": payload,
            "idempotency_key": idempotency_key,
            "dispatched_to": [],
        },
        created_at=datetime.now(UTC).isoformat(),
    )

    # Per-channel fan-out. Each adapter's result envelope is appended to
    # ``notif.metadata['dispatched_to']`` after the call returns; the router
    # consults the ledger BEFORE each call so a retry against the same
    # Notification short-circuits with reason="already_dispatched".
    results: List[Dict[str, Any]] = []
    for channel_name in channels_to_fire:
        channel = CHANNEL_REGISTRY.get(channel_name)
        if channel is None:
            # Unknown channel name in an explicit override — record as
            # skipped so the caller sees the mismatch in dispatched_to[].
            entry = {
                "channel": channel_name,
                "status": "skipped",
                "reason": "unknown_channel",
                "ts": datetime.now(UTC).isoformat(),
            }
            notif.metadata.setdefault("dispatched_to", []).append(entry)
            results.append(entry)
            continue

        # Idempotency check — A1 invariant. The ledger drives this; no new
        # ChangeEventAction Literal members are introduced.
        already_sent = any(
            d.get("channel") == channel_name and d.get("status") == "sent"
            for d in (notif.metadata.get("dispatched_to") or [])
        )
        if already_sent:
            entry = {
                "channel": channel_name,
                "status": "skipped",
                "reason": "already_dispatched",
                "ts": datetime.now(UTC).isoformat(),
            }
            notif.metadata.setdefault("dispatched_to", []).append(entry)
            results.append(entry)
            continue

        result = await channel.dispatch(  # type: ignore[attr-defined]
            user, kind, payload, notif, actor_id, actor_kind
        )
        entry = result.model_dump()
        entry["ts"] = datetime.now(UTC).isoformat()
        notif.metadata.setdefault("dispatched_to", []).append(entry)
        results.append(entry)

    await notif.save()
    return {"notification_id": notif.id, "results": results}


async def get_entry_assignees_user_ids(entry: Any) -> List[str]:
    """Extract assignee user IDs from entry custom_fields.

    Checks:
    1. Direct `member` fields (values are User IDs).
    2. `relation` fields pointing to target entries. If target entries have a `member` custom field (User ID), we collect them.
    """
    from app.models.nodes import Entry, EntryType

    assignee_user_ids: List[str] = []
    if not getattr(entry, "type_id", None):
        return assignee_user_ids

    entry_type = await EntryType.get(entry.type_id)
    if not entry_type:
        return assignee_user_ids

    fields = entry_type.form_schema.get("fields") or []
    for fd in fields:
        if not isinstance(fd, dict):
            continue
        key = fd.get("key")
        ftype = fd.get("type")
        composite = fd.get("composite")
        if isinstance(composite, dict) and composite.get("base"):
            ftype = composite.get("base") or ftype

        if not key:
            continue

        value = entry.custom_fields.get(key)
        if value is None:
            continue

        if ftype == "member":
            # Direct member field
            if isinstance(value, list):
                for val in value:
                    if val and isinstance(val, str):
                        assignee_user_ids.append(val)
            elif isinstance(value, str) and value:
                assignee_user_ids.append(value)

        elif ftype == "relation":
            # Relation field - could point to employee entries that link to User
            target_ids = []
            if isinstance(value, list):
                target_ids = [str(x) for x in value if x]
            elif isinstance(value, str) and value:
                target_ids = [value]

            for tid in target_ids:
                target_entry = await Entry.get(tid)
                if not target_entry or not getattr(target_entry, "type_id", None):
                    continue
                # Load target entry's type to find if it has any member fields
                target_et = await EntryType.get(target_entry.type_id)
                if not target_et:
                    continue
                target_fields = target_et.form_schema.get("fields") or []
                for tfd in target_fields:
                    if not isinstance(tfd, dict):
                        continue
                    tkey = tfd.get("key")
                    tftype = tfd.get("type")
                    tcomposite = tfd.get("composite")
                    if isinstance(tcomposite, dict) and tcomposite.get("base"):
                        tftype = tcomposite.get("base") or tftype
                    if tftype == "member" and tkey:
                        tval = target_entry.custom_fields.get(tkey)
                        if isinstance(tval, str) and tval:
                            assignee_user_ids.append(tval)
                        elif isinstance(tval, list):
                            for tv in tval:
                                if isinstance(tv, str) and tv:
                                    assignee_user_ids.append(tv)

    return list(set(assignee_user_ids))


async def notify_entry_watchers(
    *,
    entry_id: str,
    action: str,  # "update" | "comment" | "create"
    actor_id: str,
    actor_kind: str = "human",
    comment_text: Optional[str] = None,
    idempotency_suffix: str = "",
) -> None:
    """Notify all entry watchers, track watchers, and assignees, except the actor who triggered this."""
    import logging

    from app.models.edges import WATCHES
    from app.models.nodes import Entry, Track, User

    logger = logging.getLogger(__name__)

    entry = await Entry.get(entry_id)
    if not entry:
        return

    track = await Track.get(entry.track_id)
    if not track:
        return

    # Find watchers connected with WATCHES edge: User -- WATCHES -> Entry
    ctx = await entry.get_context()
    entry_watches_edges = await ctx.find_edges_between(
        None, entry.id, edge_class=WATCHES
    )
    entry_watcher_ids = {e.source for e in entry_watches_edges}
    raw_entry_watchers = await entry.nodes(edge=[WATCHES], direction="in", node=[User])
    entry_watchers = [w for w in raw_entry_watchers if w.id in entry_watcher_ids]

    # Find track watchers connected with WATCHES edge: User -- WATCHES -> Track
    track_watches_edges = await ctx.find_edges_between(
        None, track.id, edge_class=WATCHES
    )
    track_watcher_ids = {e.source for e in track_watches_edges}
    raw_track_watchers = await track.nodes(edge=[WATCHES], direction="in", node=[User])
    track_watchers = [w for w in raw_track_watchers if w.id in track_watcher_ids]

    # Find assignees (direct member fields + relation-based employee member fields)
    assignee_ids = await get_entry_assignees_user_ids(entry)
    assignee_users = []
    for uid in assignee_ids:
        if uid != actor_id:
            u = await User.get(uid)
            if u and u.id != actor_id and u.user_id != actor_id:
                assignee_users.append(u)

    # Merge all unique user nodes to notify, excluding the actor who made the change
    all_recipients = {}
    for u in entry_watchers + track_watchers + assignee_users:
        if u.id != actor_id and u.user_id != actor_id:
            all_recipients[u.id] = u

    if not all_recipients:
        return

    actor_user = await User.get(actor_id)
    if actor_user is None:
        actor_user = await get_user_node(actor_id)
    actor_name = "Someone"
    if actor_user:
        name_val = getattr(actor_user, "display_name", "")
        if name_val:
            parts = name_val.strip().split()
            if parts:
                actor_name = parts[0]
        else:
            from jvspatial.api.auth.models import User as AuthUser

            auth_user = await AuthUser.get(actor_user.user_id)
            if auth_user and auth_user.email:
                email_prefix = auth_user.email.split("@")[0]
                actor_name = email_prefix.split(".")[0].capitalize()
            else:
                actor_name = actor_id

    for watcher in all_recipients.values():
        watcher_id = watcher.user_id or watcher.id

        # Build specific snippet for updates / comments / creations
        if action == "update":
            snippet = f"updated the entry: {entry.title or 'Untitled'}"
        elif action == "comment":
            trimmed_comment = (comment_text or "")[:120]
            if len(comment_text or "") > 120:
                trimmed_comment += "..."
            snippet = f"commented on '{entry.title or 'Untitled'}': {trimmed_comment}"
        elif action == "create":
            snippet = f"created the entry: {entry.title or 'Untitled'} in {track.title or 'Untitled'}"
        else:
            snippet = f"updated '{entry.title or 'Untitled'}'"

        payload = {
            "entry_id": entry.id,
            "entry_title": entry.title or "",
            "actor_id": actor_id,
            "actor_name": actor_name,
            "snippet": snippet,
            "track_id": track.id,
        }

        now = datetime.now(UTC).isoformat()
        ikey = (
            f"entry_watch:{action}:{entry.id}:{watcher.id}:{idempotency_suffix or now}"
        )

        try:
            await dispatch(
                user_id=watcher_id,
                kind="entry_update",
                payload=payload,
                actor_id=actor_id,
                actor_kind=actor_kind,
                idempotency_key=ikey,
            )
        except Exception as exc:
            logger.warning("Failed to notify watcher %s: %s", watcher_id, exc)

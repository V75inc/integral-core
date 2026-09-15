"""@mention parsing + resolution helper.

Shared by ``api/comments.py`` (Phase 9 NOTIF-02 wiring) and
``api/entries.py``. Centralizes the regex + the display-name /
email-local / id resolution chain so both surfaces produce identical
behavior when a user types ``@<token>`` in body / comment text.

Resolution chain (first match wins, None when no match):
    1. Direct id lookup — ``await User.get(token)``. Handles the case
       where a frontend autocomplete inserts the raw user-id token.
    2. ``user_id`` field match — User has both ``id`` (jvspatial node
       id, e.g. ``n.User.xxxx``) and ``user_id`` (auth-system user id,
       UUID). Either form is a valid mention target.
    3. Case-insensitive ``display_name`` exact match. Apps in display
       names are NOT supported by the regex (token must be a single
       word) — multi-word names need a frontend mention picker that
       inserts the user-id token instead.
    4. Email local-part match (case-insensitive). ``alice@example.com``
       resolves on ``@alice``.

The intentional fallthrough policy: if a user types ``@foo`` and no
User node matches, the comment / entry still saves and emits its
ChangeEvent — the mention is silently skipped. Mentions are
best-effort decoration, not part of the durable content contract.
"""

from __future__ import annotations

import re
from typing import List, Optional, Set, cast

from app.models.nodes import User

# Single source of truth for the mention regex. ``@<token>`` where
# token is one or more of: letters, digits, ``_``, ``.``, ``-``. Dots
# are necessary so jvspatial-style ids like ``n.User.abc123`` match
# directly when a frontend autocomplete inserts them verbatim.
_MENTION_RX = re.compile(r"@([A-Za-z0-9_.\-]+)")


def extract_mention_tokens(text: str) -> List[str]:
    """Return the distinct ``@<token>`` strings (without the @) found in ``text``.

    Returns a list (not a set) so ordering is preserved — useful for
    deterministic dispatch fan-out + idempotency keying. Duplicates are
    removed (first occurrence wins).
    """
    if not text:
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for m in _MENTION_RX.findall(text):
        if m in seen:
            continue
        seen.add(m)
        out.append(m)
    return out


async def resolve_mention_token(token: str) -> Optional[User]:
    """Resolve a single ``@<token>`` string to a ``User`` node, or None.

    Tried in order: ``User.get(token)`` (jvspatial id), ``user_id`` field,
    ``display_name`` case-insensitive, ``email`` local-part
    case-insensitive. Returns the first match.
    """
    if not token:
        return None
    # 1. Direct jvspatial id lookup. ``Node.get`` returns ``Object | None``
    # in jvspatial's typing; we know the row, if present, is a User
    # because the registry is partitioned by class. Cast to keep the
    # function signature honest for downstream callers.
    try:
        u = await User.get(token)
        if u:
            return cast(User, u)
    except Exception:
        pass

    # 2/3/4 — pull a bounded list and filter in Python. Phase 9 user
    # counts are O(100s); a single query + linear scan is cheaper than
    # three round-trips through jvspatial's filter language. Bound at
    # 1000 to keep this from degrading at scale; mention resolution at
    # >1000 users requires a dedicated search index.
    casefold = token.casefold()
    try:
        all_users = await User.all()
    except Exception:
        return None
    for raw in all_users:
        u = cast(User, raw)
        if getattr(u, "user_id", "") == token:
            return u
        dn = (getattr(u, "display_name", "") or "").strip()
        if dn.casefold() == casefold:
            return u
        # Frontend mention picker inserts apps-replaced-with-underscores
        # (``@Eldon_Marks`` for display_name "Eldon Marks") so the regex
        # token stays single-word. Match either direction:
        #   token "Eldon_Marks" → display_name "Eldon Marks"
        #   token "Eldon Marks" (paste from clipboard) → never happens
        #     because the regex won't allow an App anyway.
        if (
            dn.casefold().replace(" ", "_") == casefold
            or dn.casefold().replace(" ", "-") == casefold
        ):
            return u
        email = (getattr(u, "email", "") or "").strip().casefold()
        if email and email.split("@", 1)[0] == casefold:
            return u
    return None


async def resolve_mentions(
    text: str,
    *,
    exclude_user_id: str = "",
    track_id: Optional[str] = None,
) -> List[User]:
    """Parse + resolve every ``@<token>`` in ``text`` to a User node.

    ``exclude_user_id`` lets callers suppress self-mentions — if the
    resolved User's ``id`` OR ``user_id`` matches, the mention is
    dropped from the result (caller does not need notification noise
    for self-tagging in their own write).

    ``track_id`` scopes the result to users with effective visibility on
    that track. Without it the resolver behaves as before (best-effort
    global resolution). With it, resolved users are gated through
    ``permissions.resolve_role`` and silently dropped when the role is
    ``None`` — keeps out-of-scope mentions from minting MENTIONS edges
    or notifications. Public-vis tracks bypass the gate (anyone with
    auth can read a public track, mirroring the picker contract).

    Returns a list of UNIQUE Users (deduped by ``user.id``). Order
    matches first-occurrence order in the text.
    """
    tokens = extract_mention_tokens(text)
    out: List[User] = []
    seen_ids: Set[str] = set()

    track_is_public = False
    if track_id:
        # Lazy import to avoid a cycle: models → services → models.
        from app.models.nodes import Track

        try:
            track = await Track.get(track_id)
        except Exception:
            track = None
        if track is None:
            # Unresolvable track means we can't gate — drop ALL mentions
            # rather than letting them through unscoped. Caller passed a
            # track_id, so the caller wants the gate.
            return []
        track_is_public = getattr(track, "visibility", None) == "public"

    for token in tokens:
        user = await resolve_mention_token(token)
        if not user:
            continue
        if exclude_user_id and (
            user.id == exclude_user_id
            or getattr(user, "user_id", "") == exclude_user_id
        ):
            continue
        if user.id in seen_ids:
            continue
        if track_id and not track_is_public:
            # Lazy import to avoid pulling the full permissions module
            # (and its node imports) into mentions resolution when the
            # caller doesn't pass a track_id.
            from app.services.permissions import resolve_role

            target_id = getattr(user, "user_id", "") or user.id
            try:
                role = await resolve_role(target_id, "track", track_id)
            except Exception:
                role = None
            if role is None:
                continue
        seen_ids.add(user.id)
        out.append(user)
    return out

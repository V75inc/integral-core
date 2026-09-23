"""Phase 19 — Gmail SyncConnector (EML-01, EML-02).

Mirror-model: Gmail threads + messages → Integral Entries via the pull-only
``SyncConnector`` ABC. Ingestion is **label-scoped** — the connector pulls
ONLY from labels declared in ``auth_state["labels"]``. An empty labels
array is a structural no-op (I-CON-EML-01) — there is no inbox fallthrough.

Locked decisions:

- **Conflict policy:** ``mirror_only`` — Gmail is source of truth; mail is
  immutable; Integral never writes back. Re-pulling a thread updates the
  mirror in place.
- **Idempotency key:** for threads, SHA-256 of ``gmail:<thread_id>``;
  message-level keys (used by the anchored-children helper) hash
  ``gmail:<thread_id>:<message_id>``.
- **OAuth scope:** ``https://www.googleapis.com/auth/gmail.readonly`` only.
- **Token refresh:** Google does not always rotate refresh_token; the
  connector preserves the existing refresh_token if the response omits one.

Invariants:

- **I-CON-01** — connector never touches ``provenance``; runtime writes it.
- **I-CON-EML-01** (phase-local) — label-scoped ingestion is structural.
  Empty ``labels`` array = zero ingest. No code path issues an unscoped
  ``threads.list``.
- **I-CON-03** — single-slug registration via
  ``@register_sync_connector("gmail")``.
- **D-08 / I-CON-05** — lives in agentive; imported only when
  AGENTIVE_ENABLED=1.

The connector itself yields ONE ExternalRecord per thread; the full
message list rides inside ``record.payload["messages"]``. The sync
runtime materializes the thread; anchored ``email_message`` children
are materialized by a follow-on helper (``materialize_email_messages``)
the calling code invokes after ``sync_one_connector``. (Phase 19 keeps
the anchored-children write OUT of the generic sync runtime — that
runtime is single-track-per-record by design; anchoring is connector
domain knowledge.)
"""

from __future__ import annotations

import base64
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import quote

import httpx

from app.agentive.connectors.drive_native import NativeToolError as GmailToolError
from app.agentive.connectors.drive_native import _google_message as _gmail_error_message
from app.agentive.connectors.drive_native import _headers as _gmail_headers
from app.services.connectors import (
    ConflictPolicy,
    ExternalRecord,
    MaterializedEntry,
    SyncConnector,
    register_sync_connector,
)
from app.services.connectors.gmail_oauth import refresh_tokens

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com"

_REAUTH_REQUIRED_KEY = "reauth_required"
_TOKEN_SKEW_SECONDS = 300


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _decode_b64url(value: str) -> str:
    """Best-effort decode of a base64url-encoded Gmail body part.

    Gmail real responses encode message bodies as base64url; fixtures
    embed plain strings. Tolerate both.
    """
    if not value:
        return ""
    try:
        padded = value + "=" * ((4 - len(value) % 4) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return value


def _headers_to_dict(headers: List[Dict[str, str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for h in headers or []:
        k = (h.get("name") or "").strip().lower()
        v = (h.get("value") or "").strip()
        if k:
            out[k] = v
    return out


def _split_address_list(value: str) -> List[str]:
    """Split a To / Cc header value into normalized lowercase emails."""
    if not value:
        return []
    parts = [p.strip() for p in value.split(",")]
    out: List[str] = []
    for p in parts:
        # Strip the optional "Name <email@host>" wrapping.
        if "<" in p and ">" in p:
            p = p.split("<", 1)[1].split(">", 1)[0]
        p = p.strip().lower()
        if p:
            out.append(p)
    return out


def _extract_body(payload: Dict[str, Any]) -> str:
    """Pull a text body out of a Gmail payload (best-effort)."""
    if not isinstance(payload, dict):
        return ""
    body = payload.get("body") or {}
    data = body.get("data") or ""
    if data:
        return _decode_b64url(data)
    # Walk parts.
    for part in payload.get("parts") or []:
        if not isinstance(part, dict):
            continue
        if (part.get("mimeType") or "") == "text/plain":
            return _decode_b64url((part.get("body") or {}).get("data") or "")
    return ""


@register_sync_connector("gmail")
class GmailConnector(SyncConnector):
    """Pull-only Gmail thread sync, label-scoped (I-CON-EML-01)."""

    slug: str = "gmail"
    conflict_policy: ConflictPolicy = "mirror_only"

    _MAX_PAGES_PER_LABEL: int = 50
    _PAGE_SIZE: int = 100

    # Test seam — production callers leave this None.
    test_http_client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # idempotency
    # ------------------------------------------------------------------
    def idempotency_key_for(self, record: ExternalRecord) -> str:
        """SHA-256(``gmail:<thread_id>``). Thread-level dedup key."""
        thread_id = (record.payload or {}).get("id") or record.external_id or ""
        seed = f"{self.slug}:{thread_id}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    @staticmethod
    def message_idempotency_key(thread_id: str, message_id: str) -> str:
        seed = f"gmail:{thread_id}:{message_id}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------
    # OAuth — token freshness
    # ------------------------------------------------------------------
    async def _ensure_fresh_token(
        self, connector: Any, *, http_client: Optional[httpx.AsyncClient] = None
    ) -> bool:
        from app.api.errors import ConnectorAuthError

        auth_state: Dict[str, Any] = dict(getattr(connector, "auth_state", {}) or {})
        expires_at = _parse_iso(auth_state.get("access_token_expires_at"))
        skew_threshold = datetime.now(timezone.utc) + timedelta(
            seconds=_TOKEN_SKEW_SECONDS
        )
        if expires_at is not None and expires_at > skew_threshold:
            return True
        refresh_token = auth_state.get("refresh_token") or ""
        if not refresh_token:
            logger.warning(
                "gmail _ensure_fresh_token: no refresh_token (connector=%s); reauth required",
                getattr(connector, "id", "<unknown>"),
            )
            auth_state[_REAUTH_REQUIRED_KEY] = True
            connector.auth_state = auth_state
            await connector.save()
            return False
        try:
            refreshed = await refresh_tokens(
                refresh_token,
                http_client=http_client or self.test_http_client,
                client_id=auth_state.get("client_id") or None,
                client_secret=auth_state.get("client_secret") or None,
            )
        except ConnectorAuthError as exc:
            details = getattr(exc, "details", None) or {}
            if details.get("reauth_required"):
                auth_state[_REAUTH_REQUIRED_KEY] = True
                connector.auth_state = auth_state
                await connector.save()
            return False
        new_access = refreshed.get("access_token") or ""
        # Google MAY omit refresh_token on refresh; preserve the existing one.
        new_refresh = refreshed.get("refresh_token") or refresh_token
        expires_in = int(refreshed.get("expires_in") or 3600)
        auth_state["access_token"] = new_access
        auth_state["refresh_token"] = new_refresh
        auth_state["access_token_expires_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat()
        auth_state.pop(_REAUTH_REQUIRED_KEY, None)
        connector.auth_state = auth_state
        await connector.save()
        logger.info(
            "gmail _ensure_fresh_token: rotated access_token for connector %s",
            getattr(connector, "id", "<unknown>"),
        )
        return True

    def _http_client(self) -> httpx.AsyncClient:
        if self.test_http_client is not None:
            return self.test_http_client
        return httpx.AsyncClient(timeout=30.0)

    # ------------------------------------------------------------------
    # Pull
    # ------------------------------------------------------------------
    async def _list_thread_ids_for_label(
        self,
        *,
        client: httpx.AsyncClient,
        access_token: str,
        label_id: str,
    ) -> List[str]:
        """Page threads.list for one label until exhausted or _MAX_PAGES."""
        out: List[str] = []
        url = (
            f"{GMAIL_API_BASE}/gmail/v1/users/me/threads"
            f"?labelIds={quote(label_id)}&maxResults={self._PAGE_SIZE}"
        )
        page_token: Optional[str] = None
        for _ in range(self._MAX_PAGES_PER_LABEL):
            full_url = url + (f"&pageToken={page_token}" if page_token else "")
            resp = await client.get(
                full_url, headers={"Authorization": f"Bearer {access_token}"}
            )
            resp.raise_for_status()
            body = resp.json() or {}
            for thread in body.get("threads") or []:
                tid = str(thread.get("id") or "")
                if tid:
                    out.append(tid)
            page_token = body.get("nextPageToken") or ""
            if not page_token:
                break
        return out

    async def _get_thread(
        self,
        *,
        client: httpx.AsyncClient,
        access_token: str,
        thread_id: str,
    ) -> Dict[str, Any]:
        url = f"{GMAIL_API_BASE}/gmail/v1/users/me/threads/{quote(thread_id)}"
        resp = await client.get(
            url, headers={"Authorization": f"Bearer {access_token}"}
        )
        resp.raise_for_status()
        return resp.json() or {}

    async def sync_pull(self, *, connector: Any) -> AsyncIterator[ExternalRecord]:
        """Yield one ExternalRecord per labeled thread.

        I-CON-EML-01 — when ``auth_state["labels"]`` is empty or missing
        the generator returns immediately (structural no-op). No code
        path falls through to an inbox-wide fetch.
        """
        auth_state: Dict[str, Any] = getattr(connector, "auth_state", None) or {}
        labels = list(auth_state.get("labels") or [])
        if not labels:
            logger.info(
                "gmail sync: connector %s has no labels selected; no-op (I-CON-EML-01)",
                getattr(connector, "id", "<unknown>"),
            )
            return
        client = self._http_client()
        owns_client = self.test_http_client is None
        try:
            ok = await self._ensure_fresh_token(connector, http_client=client)
            if not ok:
                logger.warning(
                    "gmail sync: reauth required for connector %s; aborting",
                    getattr(connector, "id", "<unknown>"),
                )
                return
            access_token = (connector.auth_state or {}).get("access_token") or ""
            seen_thread_ids: set[str] = set()
            for label_id in labels:
                thread_ids = await self._list_thread_ids_for_label(
                    client=client,
                    access_token=access_token,
                    label_id=label_id,
                )
                for tid in thread_ids:
                    if tid in seen_thread_ids:
                        continue
                    seen_thread_ids.add(tid)
                    thread_payload = await self._get_thread(
                        client=client,
                        access_token=access_token,
                        thread_id=tid,
                    )
                    if not thread_payload:
                        continue
                    # Last message's internalDate drives ``updated_at``.
                    messages = thread_payload.get("messages") or []
                    last_internal = (
                        messages[-1].get("internalDate") if messages else None
                    )
                    yield ExternalRecord(
                        external_id=tid,
                        payload=thread_payload,
                        updated_at=str(last_internal) if last_internal else None,
                    )
            connector.sync_cursor = _now_iso()
            await connector.save()
        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # to_entry — Email Thread MaterializedEntry
    # ------------------------------------------------------------------
    def to_entry(self, record: ExternalRecord) -> MaterializedEntry:
        thread = record.payload or {}
        messages = thread.get("messages") or []
        participants: set[str] = set()
        labels: set[str] = set()
        subject = ""
        first_at = None
        last_at = None
        for msg in messages:
            headers = _headers_to_dict((msg.get("payload") or {}).get("headers") or [])
            from_addr = headers.get("from") or ""
            # Strip Name<email> framing.
            if "<" in from_addr and ">" in from_addr:
                from_addr = from_addr.split("<", 1)[1].split(">", 1)[0]
            if from_addr:
                participants.add(from_addr.strip().lower())
            for addr in _split_address_list(headers.get("to") or ""):
                participants.add(addr)
            for addr in _split_address_list(headers.get("cc") or ""):
                participants.add(addr)
            for lid in msg.get("labelIds") or []:
                labels.add(str(lid))
            if not subject:
                subject = headers.get("subject") or ""
            internal = msg.get("internalDate")
            if internal:
                ts = int(internal)
                if first_at is None or ts < first_at:
                    first_at = ts
                if last_at is None or ts > last_at:
                    last_at = ts
        cf = {
            "_source_system": "gmail",
            "_source_version": str(thread.get("historyId") or ""),
            "_last_synced_at": _now_iso(),
            "subject": subject,
            "participant_emails": sorted(participants),
            "message_count": len(messages),
            "first_message_at": (
                datetime.fromtimestamp(first_at / 1000.0, tz=timezone.utc).isoformat()
                if first_at
                else ""
            ),
            "last_message_at": (
                datetime.fromtimestamp(last_at / 1000.0, tz=timezone.utc).isoformat()
                if last_at
                else ""
            ),
            "gmail_thread_id": str(thread.get("id") or ""),
            "gmail_labels": sorted(labels),
            "unmatched_participants": [],
        }
        title = subject or f"Email thread {record.external_id}"
        return MaterializedEntry(
            title=title,
            body="",
            entry_type_key="email_thread",
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

    # ------------------------------------------------------------------
    # to_anchored_children — Email Message MaterializedEntry list
    # ------------------------------------------------------------------
    def to_anchored_children(self, record: ExternalRecord) -> List[Dict[str, Any]]:
        """Project each Gmail message → an ``email_message`` MaterializedEntry payload.

        Returns a list of ``{materialized, idempotency_key, external_id}``
        dicts so the calling code can dedup + materialize each message
        on the anchored messages track. The runtime does NOT walk this
        — Phase 19 keeps the anchor materialization on the connector
        consumer side (a follow-on helper in this module's caller).
        """
        thread = record.payload or {}
        thread_id = str(thread.get("id") or "")
        out: List[Dict[str, Any]] = []
        for msg in thread.get("messages") or []:
            msg_id = str(msg.get("id") or "")
            headers = _headers_to_dict((msg.get("payload") or {}).get("headers") or [])
            from_addr = headers.get("from") or ""
            if "<" in from_addr and ">" in from_addr:
                from_addr = from_addr.split("<", 1)[1].split(">", 1)[0]
            from_addr = from_addr.strip().lower()
            to_list = _split_address_list(headers.get("to") or "")
            cc_list = _split_address_list(headers.get("cc") or "")
            internal = msg.get("internalDate")
            sent_at = (
                datetime.fromtimestamp(
                    int(internal) / 1000.0, tz=timezone.utc
                ).isoformat()
                if internal
                else ""
            )
            body_text = _extract_body(msg.get("payload") or {})
            snippet = (msg.get("snippet") or "")[:160]
            cf = {
                "_source_system": "gmail",
                "_source_version": "",
                "_last_synced_at": _now_iso(),
                "from": from_addr,
                "to": to_list,
                "cc": cc_list,
                "sent_at": sent_at,
                "subject": headers.get("subject") or "",
                "snippet": snippet,
                "body": body_text,
                "attachments_meta": [],
                "gmail_message_id": msg_id,
                "gmail_thread_id": thread_id,
                "gmail_labels": list(msg.get("labelIds") or []),
            }
            materialized = MaterializedEntry(
                title=(headers.get("subject") or f"Message {msg_id}"),
                body=body_text,
                entry_type_key="email_message",
                custom_fields=cf,
                external_updated_at=str(internal) if internal else None,
            )
            out.append(
                {
                    "materialized": materialized,
                    "idempotency_key": self.message_idempotency_key(thread_id, msg_id),
                    "external_id": msg_id,
                }
            )
        return out


# ---------------------------------------------------------------------------
# Live CRUD tools (native parity with drive_native / sheets_native)
# ---------------------------------------------------------------------------
# Registered per workspace-connector as ``native__{short}__{tool}`` and
# invoked through ``native_google_proxy`` (reads run direct, writes stage
# for bless via the ``native_tool_call`` staged kind). The label-scoped
# mirror above is unchanged; these tools act on the whole mailbox the
# OAuth grant covers, so every write is bless-gated.

GMAIL_REST_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

#: Read results are truncated past this many characters (context + host
#: protection). Payloads always carry ``truncated: bool`` where capped.
_GMAIL_READ_CHAR_LIMIT = 100_000

#: Thread reads are capped at this many messages (newest last).
_GMAIL_THREAD_MESSAGE_LIMIT = 20


async def _raise_for_gmail(resp: httpx.Response, *, what: str) -> Dict[str, Any]:
    if resp.status_code < 400:
        try:
            return resp.json() if resp.content else {}
        except Exception:
            return {}
    try:
        body = resp.json()
    except Exception:
        body = None
    detail = _gmail_error_message(body)
    reason = ""
    try:
        errors = ((body or {}).get("error") or {}).get("errors") or []
        if errors and isinstance(errors[0], dict):
            reason = str(errors[0].get("reason") or "")
    except Exception:
        pass
    if resp.status_code == 401:
        raise GmailToolError(
            f"{what}: Google rejected the access token (re-authorize the connector)"
            + (f": {detail}" if detail else ""),
            "auth",
        )
    if resp.status_code == 404:
        raise GmailToolError(
            f"{what}: not found" + (f": {detail}" if detail else ""),
            "not_found",
        )
    if resp.status_code == 403 and reason in (
        "insufficientPermissions",
        "insufficientPermissionsException",
    ):
        # Token is valid but the grant predates mail-action scopes: the
        # connector was authorized read-only. Reconnect with mail actions.
        raise GmailToolError(
            f"{what}: the Google grant is read-only — re-authorize with mail "
            "actions (Settings → Connectors → Gmail → Enable mail actions)"
            + (f": {detail}" if detail else ""),
            "auth",
        )
    raise GmailToolError(
        f"{what}: Gmail API returned {resp.status_code}"
        + (f": {detail}" if detail else ""),
        "remote_error",
    )


def _mime_raw(*, to: str, subject: str, body: str, cc: str = "") -> str:
    """Build a base64url RFC822 payload for messages.send / drafts.create."""

    def _clean(value: Any) -> str:
        return " ".join(str(value or "").split())  # no header injection

    lines = [f"To: {_clean(to)}"]
    if _clean(cc):
        lines.append(f"Cc: {_clean(cc)}")
    lines.append(f"Subject: {_clean(subject)}")
    lines.append("Content-Type: text/plain; charset=utf-8")
    lines.append("Content-Transfer-Encoding: 8bit")
    lines.extend(["", str(body or "")])
    raw = "\n".join(lines).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _prune_message(msg: Dict[str, Any], *, include_body: bool) -> Dict[str, Any]:
    headers = _headers_to_dict((msg.get("payload") or {}).get("headers") or [])
    out: Dict[str, Any] = {
        "id": msg.get("id"),
        "thread_id": msg.get("threadId"),
        "snippet": msg.get("snippet") or "",
        "labels": list(msg.get("labelIds") or []),
        "from": headers.get("from") or "",
        "to": headers.get("to") or "",
        "subject": headers.get("subject") or "",
        "date": headers.get("date") or "",
    }
    if include_body:
        text = _extract_body(msg.get("payload") or {})
        truncated = len(text) > _GMAIL_READ_CHAR_LIMIT
        out["body"] = text[:_GMAIL_READ_CHAR_LIMIT]
        out["truncated"] = truncated
    return out


TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "search_threads",
        "write": False,
        "description": (
            "Search Gmail threads (same query syntax as the Gmail search box). "
            "Returns thread ids with snippets, newest first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "label_ids": {"type": "array", "items": {"type": "string"}},
                "max_results": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_thread",
        "write": False,
        "description": (
            "Read one Gmail thread: labels, snippet, and per-message headers "
            "(up to 20 messages). Pass full=true to include decoded bodies."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string"},
                "full": {"type": "boolean", "default": False},
            },
            "required": ["thread_id"],
        },
    },
    {
        "name": "get_message",
        "write": False,
        "description": "Read one Gmail message (headers + snippet; full=true adds the decoded body).",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
                "full": {"type": "boolean", "default": False},
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "list_labels",
        "write": False,
        "description": "List the mailbox's Gmail labels (ids, names, types).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_drafts",
        "write": False,
        "description": "List Gmail draft ids (newest first).",
        "input_schema": {
            "type": "object",
            "properties": {"max_results": {"type": "integer", "default": 20}},
        },
    },
    {
        "name": "get_draft",
        "write": False,
        "description": "Read one Gmail draft (headers + snippet; full=true adds the body).",
        "input_schema": {
            "type": "object",
            "properties": {
                "draft_id": {"type": "string"},
                "full": {"type": "boolean", "default": False},
            },
            "required": ["draft_id"],
        },
    },
    {
        "name": "create_draft",
        "write": True,
        "description": "Create a Gmail draft (plain text). Send it later with send_draft.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "cc": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "send_message",
        "write": True,
        "description": "Send an email immediately (plain text, no attachments).",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "cc": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "send_draft",
        "write": True,
        "description": "Send an existing Gmail draft.",
        "input_schema": {
            "type": "object",
            "properties": {"draft_id": {"type": "string"}},
            "required": ["draft_id"],
        },
    },
    {
        "name": "delete_draft",
        "write": True,
        "description": "Permanently delete a Gmail draft.",
        "input_schema": {
            "type": "object",
            "properties": {"draft_id": {"type": "string"}},
            "required": ["draft_id"],
        },
    },
    {
        "name": "trash_thread",
        "write": True,
        "description": "Move a Gmail thread to Trash (reversible for 30 days).",
        "input_schema": {
            "type": "object",
            "properties": {"thread_id": {"type": "string"}},
            "required": ["thread_id"],
        },
    },
    {
        "name": "untrash_thread",
        "write": True,
        "description": "Remove a Gmail thread from Trash.",
        "input_schema": {
            "type": "object",
            "properties": {"thread_id": {"type": "string"}},
            "required": ["thread_id"],
        },
    },
    {
        "name": "modify_thread_labels",
        "write": True,
        "description": (
            "Add/remove Gmail labels on a thread: archive (remove INBOX), "
            "mark read/unread (remove/add UNREAD), star, or apply a label."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string"},
                "add_labels": {"type": "array", "items": {"type": "string"}},
                "remove_labels": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["thread_id"],
        },
    },
]


async def _t_search_threads(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise GmailToolError("search_threads: query is required", "bad_args")
    params: Dict[str, Any] = {
        "q": query,
        "maxResults": max(1, min(int(args.get("max_results") or 20), 100)),
        "fields": "threads(id,snippet),resultSizeEstimate,nextPageToken",
    }
    label_ids = args.get("label_ids") or []
    if isinstance(label_ids, list) and label_ids:
        params["labelIds"] = [str(label) for label in label_ids if str(label).strip()]
    resp = await http.get(
        f"{GMAIL_REST_BASE}/threads",
        headers=_gmail_headers(access_token),
        params=params,
    )
    body = await _raise_for_gmail(resp, what="search_threads")
    return {
        "threads": body.get("threads") or [],
        "result_size_estimate": body.get("resultSizeEstimate") or 0,
    }


async def _t_get_thread(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    thread_id = str(args.get("thread_id") or "").strip()
    if not thread_id:
        raise GmailToolError("get_thread: thread_id is required", "bad_args")
    full = args.get("full") is True
    params = {"format": "full" if full else "metadata"}
    if not full:
        params["metadataHeaders"] = ["From", "To", "Subject", "Date"]
    resp = await http.get(
        f"{GMAIL_REST_BASE}/threads/{quote(thread_id)}",
        headers=_gmail_headers(access_token),
        params=params,
    )
    body = await _raise_for_gmail(resp, what="get_thread")
    messages = body.get("messages") or []
    truncated = len(messages) > _GMAIL_THREAD_MESSAGE_LIMIT
    messages = messages[:_GMAIL_THREAD_MESSAGE_LIMIT]
    return {
        "id": body.get("id") or thread_id,
        "snippet": body.get("snippet") or "",
        "history_id": body.get("historyId"),
        "message_count": len(messages),
        "truncated": truncated,
        "messages": [_prune_message(m, include_body=full) for m in messages],
    }


async def _t_get_message(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    message_id = str(args.get("message_id") or "").strip()
    if not message_id:
        raise GmailToolError("get_message: message_id is required", "bad_args")
    full = args.get("full") is True
    params: Dict[str, Any] = {"format": "full" if full else "metadata"}
    if not full:
        params["metadataHeaders"] = ["From", "To", "Subject", "Date"]
    resp = await http.get(
        f"{GMAIL_REST_BASE}/messages/{quote(message_id)}",
        headers=_gmail_headers(access_token),
        params=params,
    )
    body = await _raise_for_gmail(resp, what="get_message")
    return _prune_message(body, include_body=full)


async def _t_list_labels(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    resp = await http.get(
        f"{GMAIL_REST_BASE}/labels",
        headers=_gmail_headers(access_token),
        params={"fields": "labels(id,name,type,messageListVisibility)"},
    )
    body = await _raise_for_gmail(resp, what="list_labels")
    return {"labels": body.get("labels") or []}


async def _t_list_drafts(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    resp = await http.get(
        f"{GMAIL_REST_BASE}/drafts",
        headers=_gmail_headers(access_token),
        params={
            "maxResults": max(1, min(int(args.get("max_results") or 20), 100)),
            "fields": "drafts(id,message(id,threadId))",
        },
    )
    body = await _raise_for_gmail(resp, what="list_drafts")
    return {"drafts": body.get("drafts") or []}


async def _t_get_draft(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    draft_id = str(args.get("draft_id") or "").strip()
    if not draft_id:
        raise GmailToolError("get_draft: draft_id is required", "bad_args")
    full = args.get("full") is True
    params: Dict[str, Any] = {"format": "full" if full else "metadata"}
    if not full:
        params["metadataHeaders"] = ["From", "To", "Subject", "Date"]
    resp = await http.get(
        f"{GMAIL_REST_BASE}/drafts/{quote(draft_id)}",
        headers=_gmail_headers(access_token),
        params=params,
    )
    body = await _raise_for_gmail(resp, what="get_draft")
    message = body.get("message") or {}
    return {
        "id": body.get("id") or draft_id,
        "message": _prune_message(message, include_body=full),
    }


def _require_recipients(args: Dict[str, Any], tool: str) -> Dict[str, str]:
    to = str(args.get("to") or "").strip()
    subject = str(args.get("subject") or "").strip()
    body_text = str(args.get("body") or "")
    if not to:
        raise GmailToolError(f"{tool}: to is required", "bad_args")
    if "@" not in to:
        raise GmailToolError(f"{tool}: to must be an email address", "bad_args")
    if not subject:
        raise GmailToolError(f"{tool}: subject is required", "bad_args")
    if not body_text.strip():
        raise GmailToolError(f"{tool}: body is required", "bad_args")
    return {"to": to, "subject": subject, "body": body_text}


async def _t_create_draft(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    fields = _require_recipients(args, "create_draft")
    raw = _mime_raw(
        to=fields["to"],
        subject=fields["subject"],
        body=fields["body"],
        cc=str(args.get("cc") or ""),
    )
    resp = await http.post(
        f"{GMAIL_REST_BASE}/drafts",
        headers={**_gmail_headers(access_token), "Content-Type": "application/json"},
        json={"message": {"raw": raw}},
        params={"fields": "id,message(id,threadId)"},
    )
    return await _raise_for_gmail(resp, what="create_draft")


async def _t_send_message(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    fields = _require_recipients(args, "send_message")
    raw = _mime_raw(
        to=fields["to"],
        subject=fields["subject"],
        body=fields["body"],
        cc=str(args.get("cc") or ""),
    )
    resp = await http.post(
        f"{GMAIL_REST_BASE}/messages/send",
        headers={**_gmail_headers(access_token), "Content-Type": "application/json"},
        json={"raw": raw},
        params={"fields": "id,threadId,labelIds"},
    )
    return await _raise_for_gmail(resp, what="send_message")


async def _t_send_draft(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    draft_id = str(args.get("draft_id") or "").strip()
    if not draft_id:
        raise GmailToolError("send_draft: draft_id is required", "bad_args")
    resp = await http.post(
        f"{GMAIL_REST_BASE}/drafts/send",
        headers={**_gmail_headers(access_token), "Content-Type": "application/json"},
        json={"id": draft_id},
        params={"fields": "id,threadId,labelIds"},
    )
    return await _raise_for_gmail(resp, what="send_draft")


async def _t_delete_draft(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    draft_id = str(args.get("draft_id") or "").strip()
    if not draft_id:
        raise GmailToolError("delete_draft: draft_id is required", "bad_args")
    resp = await http.delete(
        f"{GMAIL_REST_BASE}/drafts/{quote(draft_id)}",
        headers=_gmail_headers(access_token),
    )
    if resp.status_code == 204:
        return {"draft_id": draft_id, "deleted": True}
    await _raise_for_gmail(resp, what="delete_draft")
    return {"draft_id": draft_id, "deleted": True}  # pragma: no cover


async def _t_trash_thread(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    thread_id = str(args.get("thread_id") or "").strip()
    if not thread_id:
        raise GmailToolError("trash_thread: thread_id is required", "bad_args")
    resp = await http.post(
        f"{GMAIL_REST_BASE}/threads/{quote(thread_id)}/trash",
        headers=_gmail_headers(access_token),
        params={"fields": "id,labelIds"},
    )
    return await _raise_for_gmail(resp, what="trash_thread")


async def _t_untrash_thread(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    thread_id = str(args.get("thread_id") or "").strip()
    if not thread_id:
        raise GmailToolError("untrash_thread: thread_id is required", "bad_args")
    resp = await http.post(
        f"{GMAIL_REST_BASE}/threads/{quote(thread_id)}/untrash",
        headers=_gmail_headers(access_token),
        params={"fields": "id,labelIds"},
    )
    return await _raise_for_gmail(resp, what="untrash_thread")


async def _t_modify_thread_labels(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    thread_id = str(args.get("thread_id") or "").strip()
    if not thread_id:
        raise GmailToolError("modify_thread_labels: thread_id is required", "bad_args")
    add = [
        str(label).strip()
        for label in (args.get("add_labels") or [])
        if str(label).strip()
    ]
    remove = [
        str(label).strip()
        for label in (args.get("remove_labels") or [])
        if str(label).strip()
    ]
    if not add and not remove:
        raise GmailToolError(
            "modify_thread_labels: add_labels and/or remove_labels are required",
            "bad_args",
        )
    resp = await http.post(
        f"{GMAIL_REST_BASE}/threads/{quote(thread_id)}/modify",
        headers={**_gmail_headers(access_token), "Content-Type": "application/json"},
        json={"addLabelIds": add, "removeLabelIds": remove},
        params={"fields": "id,labelIds"},
    )
    return await _raise_for_gmail(resp, what="modify_thread_labels")


_TOOL_FNS = {
    "search_threads": _t_search_threads,
    "get_thread": _t_get_thread,
    "get_message": _t_get_message,
    "list_labels": _t_list_labels,
    "list_drafts": _t_list_drafts,
    "get_draft": _t_get_draft,
    "create_draft": _t_create_draft,
    "send_message": _t_send_message,
    "send_draft": _t_send_draft,
    "delete_draft": _t_delete_draft,
    "trash_thread": _t_trash_thread,
    "untrash_thread": _t_untrash_thread,
    "modify_thread_labels": _t_modify_thread_labels,
}


async def call_tool(
    tool_name: str,
    args: Dict[str, Any],
    *,
    access_token: str,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Invoke one Gmail tool against the Gmail API with a fresh access token."""
    fn = _TOOL_FNS.get(tool_name)
    if fn is None:
        raise GmailToolError(f"unknown Gmail tool {tool_name!r}", "unknown_tool")
    owns = http_client is None
    http = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        return await fn(dict(args or {}), access_token=access_token, http=http)
    finally:
        if owns:
            await http.aclose()

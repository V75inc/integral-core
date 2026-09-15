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

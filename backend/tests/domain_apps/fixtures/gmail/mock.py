"""Phase 19 — Gmail API mock fixtures.

Canned JSON for the Gmail API endpoints the ``GmailConnector`` calls,
plus an ``httpx.MockTransport`` factory so the suite runs offline.

Coverage:
- ``users/me/labels`` — labels.list response.
- ``users/me/threads`` — threads.list paged response (with labelIds filter).
- ``users/me/threads/{id}`` — threads.get full thread payload (messages
  nested inside).
- Google OAuth2 token endpoint (``oauth2.googleapis.com/token``) — exchange
  + refresh + invalid_grant variants.
- Label inventory across two configured labels (`Label_Sales`,
  `Label_Support`) plus an inbox-only thread that MUST NOT be ingested
  (locks I-CON-EML-01 in the connector test).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GOOGLE_OAUTH_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com"

# ---------------------------------------------------------------------------
# Token responses
# ---------------------------------------------------------------------------

TOKEN_EXCHANGE_RESPONSE: Dict[str, Any] = {
    "access_token": "mock-gmail-access-token-exchange",
    "refresh_token": "mock-gmail-refresh-token-exchange",
    "expires_in": 3599,
    "token_type": "Bearer",
    "scope": "https://www.googleapis.com/auth/gmail.readonly",
}

TOKEN_REFRESH_RESPONSE: Dict[str, Any] = {
    # Google does NOT always rotate refresh_token; the new access_token is
    # what matters. Refresh persistence behaviour mirrors that contract.
    "access_token": "mock-gmail-access-token-refreshed",
    "expires_in": 3599,
    "token_type": "Bearer",
    "scope": "https://www.googleapis.com/auth/gmail.readonly",
}

INVALID_GRANT_RESPONSE: Dict[str, Any] = {
    "error": "invalid_grant",
    "error_description": "Token has been expired or revoked.",
}

# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

LABELS_RESPONSE: Dict[str, Any] = {
    "labels": [
        {"id": "INBOX", "name": "INBOX", "type": "system"},
        {"id": "SENT", "name": "SENT", "type": "system"},
        {"id": "Label_Sales", "name": "Sales", "type": "user"},
        {"id": "Label_Support", "name": "Support", "type": "user"},
        {"id": "Label_Personal", "name": "Personal", "type": "user"},
        {"id": "Label_HR", "name": "HR", "type": "user"},
    ],
}


# ---------------------------------------------------------------------------
# Thread fixtures
# ---------------------------------------------------------------------------


def _thread_summary(thread_id: str, history_id: str, snippet: str) -> Dict[str, Any]:
    return {
        "id": thread_id,
        "historyId": history_id,
        "snippet": snippet,
    }


def _message(
    *,
    msg_id: str,
    thread_id: str,
    label_ids: List[str],
    from_addr: str,
    to_addrs: List[str],
    subject: str,
    body: str,
    sent_at_ms: str,
    cc: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a Gmail message payload — minimal but realistic shape."""
    headers = [
        {"name": "From", "value": from_addr},
        {"name": "To", "value": ", ".join(to_addrs)},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": "Sat, 23 May 2026 12:00:00 +0000"},
    ]
    if cc:
        headers.append({"name": "Cc", "value": ", ".join(cc)})
    return {
        "id": msg_id,
        "threadId": thread_id,
        "labelIds": list(label_ids),
        "internalDate": sent_at_ms,
        "snippet": (body or "")[:120],
        "payload": {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {
                "size": len(body or ""),
                # Real Gmail base64url-encodes the body — for fixtures we
                # embed the plain string under ``data`` and the connector
                # treats both forms (decode-or-pass-through).
                "data": body or "",
            },
        },
    }


# Two threads on Sales, one on Support, one inbox-only (NEVER ingested).
THREADS: Dict[str, Dict[str, Any]] = {
    "thread_sales_001": {
        "id": "thread_sales_001",
        "historyId": "1001",
        "messages": [
            _message(
                msg_id="msg_sales_001_a",
                thread_id="thread_sales_001",
                label_ids=["Label_Sales", "INBOX"],
                from_addr="ap@contoso.example",
                to_addrs=["sales@.example"],
                subject="Q3 invoicing question",
                body="Hi , can you send Q3 invoices by July 5?",
                sent_at_ms="1719000000000",
            ),
            _message(
                msg_id="msg_sales_001_b",
                thread_id="thread_sales_001",
                label_ids=["Label_Sales", "SENT"],
                from_addr="sales@.example",
                to_addrs=["ap@contoso.example"],
                subject="Re: Q3 invoicing question",
                body="Yes, sending Friday. Best, Sales.",
                sent_at_ms="1719003600000",
            ),
        ],
    },
    "thread_sales_002": {
        "id": "thread_sales_002",
        "historyId": "1002",
        "messages": [
            _message(
                msg_id="msg_sales_002_a",
                thread_id="thread_sales_002",
                label_ids=["Label_Sales"],
                from_addr="billing@fabrikam.example",
                to_addrs=["sales@.example"],
                subject="Renewal pricing for 2027",
                body="What's the renewal pricing for 2027?",
                sent_at_ms="1719100000000",
            ),
        ],
    },
    "thread_support_001": {
        "id": "thread_support_001",
        "historyId": "1003",
        "messages": [
            _message(
                msg_id="msg_support_001_a",
                thread_id="thread_support_001",
                label_ids=["Label_Support"],
                from_addr="ops@contoso.example",
                to_addrs=["support@.example"],
                subject="Login issue on portal",
                body="Cannot log in to the dashboard since this morning.",
                sent_at_ms="1719200000000",
            ),
        ],
    },
    # INBOX-only — UNLABELED user-facing. MUST NOT be ingested per
    # I-CON-EML-01 even though it lives in the same mailbox.
    "thread_inbox_only_001": {
        "id": "thread_inbox_only_001",
        "historyId": "1004",
        "messages": [
            _message(
                msg_id="msg_inbox_001_a",
                thread_id="thread_inbox_only_001",
                label_ids=["INBOX"],
                from_addr="random@nowhere.example",
                to_addrs=["sales@.example"],
                subject="Private — do not ingest",
                body="This thread is inbox-only and must never enter the substrate.",
                sent_at_ms="1719300000000",
            ),
        ],
    },
}


THREADS_BY_LABEL: Dict[str, List[str]] = {
    "Label_Sales": ["thread_sales_001", "thread_sales_002"],
    "Label_Support": ["thread_support_001"],
    "INBOX": [
        "thread_inbox_only_001",
        "thread_sales_001",
    ],  # also tagged INBOX
    "SENT": ["thread_sales_001"],
    "Label_Personal": [],
    "Label_HR": [],
}


def load(name: str) -> Dict[str, Any]:
    table = {
        "labels": LABELS_RESPONSE,
        "token_exchange": TOKEN_EXCHANGE_RESPONSE,
        "token_refresh": TOKEN_REFRESH_RESPONSE,
        "invalid_grant": INVALID_GRANT_RESPONSE,
        **{f"thread:{tid}": payload for tid, payload in THREADS.items()},
    }
    if name not in table:
        raise KeyError(f"unknown Gmail fixture {name!r}")
    return json.loads(json.dumps(table[name]))


def _extract_label_ids_param(url: str) -> List[str]:
    """Parse labelIds query params from a Gmail threads.list URL."""
    out: List[str] = []
    if "?" not in url:
        return out
    query = url.split("?", 1)[1]
    for part in query.split("&"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        if k == "labelIds":
            out.append(v)
    return out


def gmail_mock_transport(
    *,
    refresh_returns_invalid_grant: bool = False,
) -> httpx.MockTransport:
    """Return a transport that serves Gmail API + OAuth fixtures."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        # OAuth token endpoint (POST).
        if url == GOOGLE_OAUTH_TOKEN_ENDPOINT:
            body = request.content.decode("utf-8", errors="ignore")
            if "grant_type=refresh_token" in body:
                if refresh_returns_invalid_grant:
                    return httpx.Response(400, json=INVALID_GRANT_RESPONSE)
                return httpx.Response(200, json=TOKEN_REFRESH_RESPONSE)
            return httpx.Response(200, json=TOKEN_EXCHANGE_RESPONSE)

        # labels.list
        if url.startswith(f"{GMAIL_API_BASE}/gmail/v1/users/me/labels"):
            return httpx.Response(200, json=LABELS_RESPONSE)

        # threads.list — must pass labelIds; we return only threads in
        # those labels (mirrors Gmail's real semantics).
        if url.startswith(f"{GMAIL_API_BASE}/gmail/v1/users/me/threads") and (
            "/threads?" in url or url.endswith("/threads")
        ):
            label_ids = _extract_label_ids_param(url)
            collected: List[Dict[str, Any]] = []
            seen = set()
            for label_id in label_ids:
                for tid in THREADS_BY_LABEL.get(label_id, []):
                    if tid in seen:
                        continue
                    seen.add(tid)
                    thread = THREADS[tid]
                    last_msg = thread["messages"][-1]
                    collected.append(
                        _thread_summary(
                            tid,
                            thread["historyId"],
                            last_msg["snippet"],
                        )
                    )
            return httpx.Response(
                200,
                json={"threads": collected, "resultSizeEstimate": len(collected)},
            )

        # threads.get — return full thread payload by id.
        if "/users/me/threads/" in url and "/threads/" in url:
            # URL shape: .../v1/users/me/threads/{thread_id}
            path = url.split("?", 1)[0]
            thread_id = path.rsplit("/", 1)[-1]
            thread = THREADS.get(thread_id)
            if thread is None:
                return httpx.Response(404, json={"error": "thread not found"})
            return httpx.Response(200, json=thread)

        return httpx.Response(
            404,
            json={"error": "unmocked Gmail endpoint", "url": url},
        )

    return httpx.MockTransport(handler)


def gmail_invalid_grant_transport() -> httpx.MockTransport:
    return gmail_mock_transport(refresh_returns_invalid_grant=True)

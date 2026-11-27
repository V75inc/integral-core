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
- **I-CON-05** — registered by importing this package. The agentive layer
  is always loaded.

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
from urllib.parse import quote, urlencode

import httpx

from app.services.connectors import (
    ConflictPolicy,
    ExternalRecord,
    MaterializedEntry,
    SyncConnector,
    register_sync_connector,
)
from app.services.connectors.gmail_oauth import refresh_tokens
from app.services.connectors.sync_lease import save_connector_changes

logger = logging.getLogger(__name__)

_REAUTH_REQUIRED_KEY = "reauth_required"
_TOKEN_SKEW_SECONDS = 300


def _gmail_page_size(manifest: Optional[dict] = None) -> int:
    """Return the page size from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def size(source: Optional[dict]) -> Optional[int]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "sync" not in capabilities:
            return None
        sync = capabilities.get("sync")
        if not isinstance(sync, dict) or "page_size" not in sync:
            return None
        raw = sync.get("page_size")
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            return None
        return raw

    found = size(manifest)
    if found is None:
        found = size(admitted_manifest("gmail") or {})
    if found is None:
        raise ValueError("Gmail package does not declare a page size")
    return found


def _gmail_max_pages(manifest: Optional[dict] = None) -> int:
    """Return the page cap from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def pages(source: Optional[dict]) -> Optional[int]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "sync" not in capabilities:
            return None
        sync = capabilities.get("sync")
        if not isinstance(sync, dict) or "max_pages" not in sync:
            return None
        raw = sync.get("max_pages")
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            return None
        return raw

    found = pages(manifest)
    if found is None:
        found = pages(admitted_manifest("gmail") or {})
    if found is None:
        raise ValueError("Gmail package does not declare a page cap")
    return found


def _template_cursor_key(query: Optional[dict]) -> Optional[str]:
    """Return the query key whose value is a cursor template."""
    if not isinstance(query, dict):
        return None
    for key, value in query.items():
        if isinstance(value, str) and "{{cursor}}" in "".join(value.split()):
            return str(key)
    return None


def _template_input_key(query: Optional[dict], name: str) -> Optional[str]:
    """Return the query key whose value templates one input."""
    if not isinstance(query, dict):
        return None
    needle = "{{input." + name + "}}"
    for key, value in query.items():
        if isinstance(value, str) and needle in "".join(value.split()):
            return str(key)
    return None


def _gmail_thread_select(manifest: Optional[dict] = None) -> str:
    """Return the thread-list selector from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def select_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_threads":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("select"):
                return str(output["select"])
            return None
        return None

    found = select_of(manifest)
    if found is None:
        found = select_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a thread list selector")
    return found


def _gmail_page_cursor(manifest: Optional[dict] = None) -> str:
    """Return the next-page path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def next_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_threads":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("next"):
                return str(output["next"])
            return None
        return None

    found = next_of(manifest)
    if found is None:
        found = next_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a next page field")
    return found


def _gmail_thread_id(manifest: Optional[dict] = None) -> str:
    """Return the thread id path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def id_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "list_threads":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("id"):
                return str(output["id"])
            return None
        return None

    found = id_of(manifest)
    if found is None:
        found = id_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a thread id")
    return found


def _gmail_thread_body(manifest: Optional[dict] = None) -> str:
    """Return the thread-read selector from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def select_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("select"):
                return str(output["select"])
            return None
        return None

    found = select_of(manifest)
    if found is None:
        found = select_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a thread body")
    return found


def _gmail_thread_messages(manifest: Optional[dict] = None) -> str:
    """Return the thread message path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def items_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("items"):
                return str(output["items"])
            return None
        return None

    found = items_of(manifest)
    if found is None:
        found = items_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a message field")
    return found


def _gmail_message_updated_at(manifest: Optional[dict] = None) -> str:
    """Return the message timestamp path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def updated_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("updated_at"):
                return str(output["updated_at"])
            return None
        return None

    found = updated_of(manifest)
    if found is None:
        found = updated_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a message timestamp")
    return found


def _gmail_message_id(manifest: Optional[dict] = None) -> str:
    """Return the message id path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def id_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("id"):
                return str(output["id"])
            return None
        return None

    found = id_of(manifest)
    if found is None:
        found = id_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a message id")
    return found


def _gmail_thread_version(manifest: Optional[dict] = None) -> str:
    """Return the history token path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def version_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("version"):
                return str(output["version"])
            return None
        return None

    found = version_of(manifest)
    if found is None:
        found = version_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a history field")
    return found


def _gmail_message_summary(manifest: Optional[dict] = None) -> str:
    """Return the message preview path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def summary_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("summary"):
                return str(output["summary"])
            return None
        return None

    found = summary_of(manifest)
    if found is None:
        found = summary_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a snippet field")
    return found


def _gmail_subject_header(manifest: Optional[dict] = None) -> str:
    """Return the subject header from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def header_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("title_header"):
                return str(output["title_header"])
            return None
        return None

    found = header_of(manifest)
    if found is None:
        found = header_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a subject field")
    return found


def _gmail_from_header(manifest: Optional[dict] = None) -> str:
    """Return the sender header from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def header_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("from_header"):
                return str(output["from_header"])
            return None
        return None

    found = header_of(manifest)
    if found is None:
        found = header_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a from field")
    return found


def _gmail_to_header(manifest: Optional[dict] = None) -> str:
    """Return the recipient header from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def header_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("to_header"):
                return str(output["to_header"])
            return None
        return None

    found = header_of(manifest)
    if found is None:
        found = header_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a to field")
    return found


def _gmail_cc_header(manifest: Optional[dict] = None) -> str:
    """Return the copy header from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def header_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("cc_header"):
                return str(output["cc_header"])
            return None
        return None

    found = header_of(manifest)
    if found is None:
        found = header_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a cc field")
    return found


def _gmail_label_list(manifest: Optional[dict] = None) -> str:
    """Return the label list path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def labels_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("labels"):
                return str(output["labels"])
            return None
        return None

    found = labels_of(manifest)
    if found is None:
        found = labels_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a label field")
    return found


def _gmail_body_type(manifest: Optional[dict] = None) -> str:
    """Return the text body MIME type from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def type_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("body_type"):
                return str(output["body_type"])
            return None
        return None

    found = type_of(manifest)
    if found is None:
        found = type_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a body type")
    return found


def _gmail_body_key(manifest: Optional[dict] = None) -> str:
    """Return the body-part type key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("body_key"):
                return str(output["body_key"])
            return None
        return None

    found = key_of(manifest)
    if found is None:
        found = key_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a body key")
    return found


def _gmail_content_key(manifest: Optional[dict] = None) -> str:
    """Return the encoded-body key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("content_key"):
                return str(output["content_key"])
            return None
        return None

    found = key_of(manifest)
    if found is None:
        found = key_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a content key")
    return found


def _gmail_body_field(manifest: Optional[dict] = None) -> str:
    """Return the body-object key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("body_field"):
                return str(output["body_field"])
            return None
        return None

    found = key_of(manifest)
    if found is None:
        found = key_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a body field")
    return found


def _gmail_parts_key(manifest: Optional[dict] = None) -> str:
    """Return the part-list key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("parts_key"):
                return str(output["parts_key"])
            return None
        return None

    found = key_of(manifest)
    if found is None:
        found = key_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a part list")
    return found


def _gmail_payload_key(manifest: Optional[dict] = None) -> str:
    """Return the message payload key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("payload_key"):
                return str(output["payload_key"])
            return None
        return None

    found = key_of(manifest)
    if found is None:
        found = key_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a message payload")
    return found


def _gmail_headers_key(manifest: Optional[dict] = None) -> str:
    """Return the header-list key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("headers_key"):
                return str(output["headers_key"])
            return None
        return None

    found = key_of(manifest)
    if found is None:
        found = key_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a header list")
    return found


def _gmail_name_key(manifest: Optional[dict] = None) -> str:
    """Return the header-name key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("name_key"):
                return str(output["name_key"])
            return None
        return None

    found = key_of(manifest)
    if found is None:
        found = key_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a header name")
    return found


def _gmail_value_key(manifest: Optional[dict] = None) -> str:
    """Return the header-value key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "get_thread":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("value_key"):
                return str(output["value_key"])
            return None
        return None

    found = key_of(manifest)
    if found is None:
        found = key_of(admitted_manifest("gmail") or {})
    if not found:
        raise ValueError("Gmail package does not declare a header value")
    return found


def _gmail_threads_url(
    base: str,
    label_id: str,
    page_size: int,
    manifest: Optional[dict] = None,
    page_token: Optional[str] = None,
) -> str:
    """Build the thread-list URL from the package path."""
    from app.connectors.package_runtime import package_action_request, render

    request = package_action_request("gmail", "list_threads", manifest)
    path = str(render((request or {}).get("path") or "", {}, {}))
    if (
        not request
        or not path.startswith("/")
        or "://" in path
        or "\\" in path
        or ".." in path
    ):
        raise ValueError("Gmail package does not declare a thread list path")
    extra = request.get("query") if isinstance(request.get("query"), dict) else {}
    label_key = _template_input_key(extra, "label_id")
    if label_key is None and isinstance(manifest, dict):
        admitted = package_action_request("gmail", "list_threads", None)
        admitted_query = admitted.get("query") if isinstance(admitted, dict) else None
        label_key = _template_input_key(
            admitted_query if isinstance(admitted_query, dict) else None,
            "label_id",
        )
    if not label_key:
        raise ValueError("Gmail package does not declare a label filter")
    cursor_key = _template_cursor_key(extra)
    if cursor_key is None and isinstance(manifest, dict):
        admitted = package_action_request("gmail", "list_threads", None)
        admitted_query = admitted.get("query") if isinstance(admitted, dict) else None
        cursor_key = _template_cursor_key(
            admitted_query if isinstance(admitted_query, dict) else None
        )
    if not cursor_key:
        raise ValueError("Gmail package does not declare a cursor field")
    size_key = _template_input_key(extra, "page_size")
    if size_key is None and isinstance(manifest, dict):
        admitted = package_action_request("gmail", "list_threads", None)
        admitted_query = admitted.get("query") if isinstance(admitted, dict) else None
        size_key = _template_input_key(
            admitted_query if isinstance(admitted_query, dict) else None,
            "page_size",
        )
    if not size_key:
        raise ValueError("Gmail package does not declare a page size field")
    rendered = render(extra, {}, {"label_id": "", "page_size": str(page_size)})
    params = {label_key: label_id}
    for key, value in rendered.items():
        if str(key) in {label_key, cursor_key}:
            continue
        params[str(key)] = "" if value is None else str(value)
    if size_key not in params:
        params[size_key] = str(page_size)
    if page_token:
        params[cursor_key] = page_token
    return f"{base}{path}?{urlencode(params, quote_via=quote)}"


def _gmail_thread_url(
    base: str, thread_id: str, manifest: Optional[dict] = None
) -> str:
    """Build one thread-read URL from the package path."""
    from app.connectors.package_runtime import package_action_request, render

    request = package_action_request("gmail", "get_thread", manifest)
    path = str(
        render(
            (request or {}).get("path") or "",
            {},
            {"thread_id": quote(thread_id, safe="")},
        )
    )
    if (
        not request
        or not path.startswith("/")
        or "://" in path
        or "\\" in path
        or ".." in path
    ):
        raise ValueError("Gmail package does not declare a thread read path")
    extra = request.get("query") if isinstance(request.get("query"), dict) else {}
    rendered = render(extra, {}, {"thread_id": thread_id})
    if not rendered:
        return f"{base}{path}"
    params = {
        str(key): "" if value is None else str(value) for key, value in rendered.items()
    }
    return f"{base}{path}?{urlencode(params, quote_via=quote)}"


def _gmail_api_base(manifest: Optional[dict] = None) -> str:
    """Return the stored Gmail base URL, or the admitted package base URL."""
    from app.connectors.package_runtime import package_http

    base = str(package_http("gmail", manifest).get("base_url") or "").rstrip("/")
    if not base.startswith("https://"):
        raise ValueError("Gmail package does not declare an https base URL")
    return base


async def _gmail_package_read(
    action: str,
    manifest: Optional[dict],
    inputs: Dict[str, Any],
    access_token: str,
    client: httpx.AsyncClient,
) -> Any:
    """Send one declared Gmail read through the shared package request."""
    from app.connectors.package_runtime import (
        package_action_request,
        package_http,
        perform_request,
    )

    request = package_action_request("gmail", action, manifest)
    if not isinstance(request, dict) or not request.get("path"):
        raise ValueError(f"Gmail package does not declare {action}")
    sent = dict(request)
    headers = dict(sent.get("headers") or {})
    headers["Authorization"] = f"Bearer {access_token}"
    sent["headers"] = headers
    host = {"http": package_http("gmail", manifest), "auth": {"type": "none"}}
    return await perform_request(host, sent, {}, {}, inputs, client)


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


def _headers_to_dict(
    headers: List[Dict[str, str]], name_key: str, value_key: str
) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not name_key or not value_key:
        return out
    for h in headers or []:
        k = (h.get(name_key) or "").strip().lower()
        v = (h.get(value_key) or "").strip()
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


def _extract_body(
    payload: Dict[str, Any],
    body_type: str,
    body_key: str,
    content_key: str,
    body_field: str,
    parts_key: str,
) -> str:
    """Pull a text body out of a Gmail payload (best-effort)."""
    if (
        not isinstance(payload, dict)
        or not body_type
        or not body_key
        or not content_key
        or not body_field
        or not parts_key
    ):
        return ""
    body = payload.get(body_field) or {}
    data = body.get(content_key) or ""
    if data:
        return _decode_b64url(data)
    for part in payload.get(parts_key) or []:
        if not isinstance(part, dict):
            continue
        if (part.get(body_key) or "") == body_type:
            found = (part.get(body_field) or {}).get(content_key) or ""
            return _decode_b64url(found)
    return ""


@register_sync_connector("gmail")
class GmailConnector(SyncConnector):
    """Pull-only Gmail thread sync, label-scoped (I-CON-EML-01)."""

    slug: str = "gmail"

    # Test seam — production callers leave this None.
    test_http_client: Optional[httpx.AsyncClient] = None

    @property
    def conflict_policy(self) -> ConflictPolicy:  # type: ignore[override]
        """Read the conflict policy from the admitted package."""
        from app.connectors.package_runtime import package_conflict_policy

        return package_conflict_policy(None, self.slug)  # type: ignore[return-value]

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
        from app.agentive.services.connector_registry_node import decrypt_auth_state
        from app.api.errors import ConnectorAuthError

        auth_state: Dict[str, Any] = decrypt_auth_state(
            getattr(connector, "auth_state", {}) or {}
        )
        connector.auth_state = auth_state
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
            await save_connector_changes(connector, fields=("auth_state",))
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
                await save_connector_changes(connector, fields=("auth_state",))
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
        await save_connector_changes(connector, fields=("auth_state",))
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
        manifest: Optional[dict] = None,
    ) -> List[str]:
        """Page threads.list for one label until exhausted or _MAX_PAGES."""
        out: List[str] = []
        page_token: Optional[str] = None
        from app.connectors.package_runtime import select_output

        selector = _gmail_thread_select(manifest)
        cursor_path = _gmail_page_cursor(manifest)
        id_path = _gmail_thread_id(manifest)
        for _ in range(_gmail_max_pages(manifest)):
            body = (
                await _gmail_package_read(
                    "list_threads",
                    manifest,
                    {
                        "label_id": label_id,
                        "page_size": _gmail_page_size(manifest),
                        "cursor": page_token or "",
                    },
                    access_token,
                    client,
                )
                or {}
            )
            selected = select_output(body, selector)
            threads = selected if isinstance(selected, list) else []
            for thread in threads:
                if not isinstance(thread, dict):
                    continue
                found_id = select_output(thread, id_path)
                tid = "" if found_id is None else str(found_id)
                if tid:
                    out.append(tid)
            found = select_output(body, cursor_path)
            page_token = "" if found is None else str(found)
            if not page_token:
                break
        return out

    async def _get_thread(
        self,
        *,
        client: httpx.AsyncClient,
        access_token: str,
        thread_id: str,
        manifest: Optional[dict] = None,
    ) -> Dict[str, Any]:
        from app.connectors.package_runtime import select_output

        selector = _gmail_thread_body(manifest)
        body = (
            await _gmail_package_read(
                "get_thread",
                manifest,
                {"thread_id": thread_id},
                access_token,
                client,
            )
            or {}
        )
        selected = select_output(body, selector)
        return selected if isinstance(selected, dict) else {}

    async def sync_pull(self, *, connector: Any) -> AsyncIterator[ExternalRecord]:
        """Yield one ExternalRecord per labeled thread.

        I-CON-EML-01 — when ``auth_state["labels"]`` is empty or missing
        the generator returns immediately (structural no-op). No code
        path falls through to an inbox-wide fetch.
        """
        auth_state: Dict[str, Any] = getattr(connector, "auth_state", None) or {}
        if getattr(connector, "mapping_profile", None) == "package":
            config = getattr(connector, "package_config", None) or {}
            labels = list(config.get("labels", auth_state.get("labels", [])) or [])
            consent = config.get(
                "consent_acknowledged", auth_state.get("consent_acknowledged", False)
            )
            if consent is not True:
                return
        else:
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
            stored = getattr(connector, "package_manifest", None)
            from app.connectors.package_runtime import select_output

            message_path = _gmail_thread_messages(stored)
            updated_path = _gmail_message_updated_at(stored)
            seen_thread_ids: set[str] = set()
            for label_id in labels:
                thread_ids = await self._list_thread_ids_for_label(
                    client=client,
                    access_token=access_token,
                    label_id=label_id,
                    manifest=stored,
                )
                for tid in thread_ids:
                    if tid in seen_thread_ids:
                        continue
                    seen_thread_ids.add(tid)
                    thread_payload = await self._get_thread(
                        client=client,
                        access_token=access_token,
                        thread_id=tid,
                        manifest=stored,
                    )
                    if not thread_payload:
                        continue
                    # The last message's declared timestamp drives ``updated_at``.
                    selected = select_output(thread_payload, message_path)
                    messages = selected if isinstance(selected, list) else []
                    last = messages[-1] if messages else None
                    found_at = (
                        select_output(last, updated_path)
                        if isinstance(last, dict)
                        else None
                    )
                    yield ExternalRecord(
                        external_id=tid,
                        payload=thread_payload,
                        updated_at=None if not found_at else str(found_at),
                    )
            connector.sync_cursor = _now_iso()
            await save_connector_changes(connector, fields=("sync_cursor",))
        finally:
            if owns_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # to_entry — Email Thread MaterializedEntry
    # ------------------------------------------------------------------
    def to_entry(
        self, record: ExternalRecord, manifest: Optional[dict] = None
    ) -> MaterializedEntry:
        from app.connectors.package_runtime import (
            package_field_name,
            package_record_type,
            package_title_prefix,
            select_output,
        )

        thread = record.payload or {}
        found_version = select_output(thread, _gmail_thread_version(manifest))
        source_version = "" if found_version is None else str(found_version)
        messages = thread.get("messages") or []
        participants: set[str] = set()
        labels: set[str] = set()
        subject = ""
        subject_header = _gmail_subject_header(manifest).strip().lower()
        from_header = _gmail_from_header(manifest).strip().lower()
        to_header = _gmail_to_header(manifest).strip().lower()
        cc_header = _gmail_cc_header(manifest).strip().lower()
        label_path = _gmail_label_list(manifest)
        payload_key = _gmail_payload_key(manifest)
        headers_key = _gmail_headers_key(manifest)
        name_key = _gmail_name_key(manifest)
        value_key = _gmail_value_key(manifest)
        updated_path = _gmail_message_updated_at(manifest)
        first_at = None
        last_at = None
        for msg in messages:
            message_payload = msg.get(payload_key) or {}
            headers = _headers_to_dict(
                message_payload.get(headers_key) or [], name_key, value_key
            )
            from_addr = headers.get(from_header) or ""
            # Strip Name<email> framing.
            if "<" in from_addr and ">" in from_addr:
                from_addr = from_addr.split("<", 1)[1].split(">", 1)[0]
            if from_addr:
                participants.add(from_addr.strip().lower())
            for addr in _split_address_list(headers.get(to_header) or ""):
                participants.add(addr)
            for addr in _split_address_list(headers.get(cc_header) or ""):
                participants.add(addr)
            found_labels = select_output(msg, label_path)
            if isinstance(found_labels, list):
                for lid in found_labels:
                    labels.add(str(lid))
            if not subject:
                subject = headers.get(subject_header) or ""
            internal = select_output(msg, updated_path)
            if internal:
                ts = int(internal)
                if first_at is None or ts < first_at:
                    first_at = ts
                if last_at is None or ts > last_at:
                    last_at = ts
        subject_field = package_field_name(manifest, self.slug, "subject")
        participants_field = package_field_name(manifest, self.slug, "participants")
        count_field = package_field_name(manifest, self.slug, "message_count")
        first_field = package_field_name(manifest, self.slug, "first_message_at")
        last_field = package_field_name(manifest, self.slug, "last_message_at")
        thread_field = package_field_name(manifest, self.slug, "gmail_thread_id")
        labels_field = package_field_name(manifest, self.slug, "gmail_labels")
        source_system_field = package_field_name(manifest, self.slug, "_source_system")
        source_version_field = package_field_name(
            manifest, self.slug, "_source_version"
        )
        synced_field = package_field_name(manifest, self.slug, "_last_synced_at")
        unmatched_field = package_field_name(
            manifest, self.slug, "unmatched_participants"
        )
        cf = {
            source_system_field: "gmail",
            source_version_field: source_version,
            synced_field: _now_iso(),
            subject_field: subject,
            participants_field: sorted(participants),
            count_field: len(messages),
            first_field: (
                datetime.fromtimestamp(first_at / 1000.0, tz=timezone.utc).isoformat()
                if first_at
                else ""
            ),
            last_field: (
                datetime.fromtimestamp(last_at / 1000.0, tz=timezone.utc).isoformat()
                if last_at
                else ""
            ),
            thread_field: str(thread.get("id") or ""),
            labels_field: sorted(labels),
            unmatched_field: [],
        }
        prefix = package_title_prefix(manifest, self.slug)
        title = subject or f"{prefix} {record.external_id}"
        return MaterializedEntry(
            title=title,
            body="",
            entry_type_key=package_record_type(manifest, self.slug),
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

    # ------------------------------------------------------------------
    # to_anchored_children — Email Message MaterializedEntry list
    # ------------------------------------------------------------------
    def to_anchored_children(
        self, record: ExternalRecord, manifest: Optional[dict] = None
    ) -> List[Dict[str, Any]]:
        """Project each Gmail message → an ``email_message`` MaterializedEntry payload.

        Returns a list of ``{materialized, idempotency_key, external_id}``
        dicts so the calling code can dedup + materialize each message
        on the anchored messages track. The runtime does NOT walk this
        — Phase 19 keeps the anchor materialization on the connector
        consumer side (a follow-on helper in this module's caller).
        """
        from app.connectors.package_runtime import (
            package_child_type,
            package_field_name,
            select_output,
        )

        thread = record.payload or {}
        thread_id = str(thread.get("id") or "")
        child_type = package_child_type(manifest, self.slug)
        subject_field = package_field_name(manifest, self.slug, "subject")
        thread_field = package_field_name(manifest, self.slug, "gmail_thread_id")
        labels_field = package_field_name(manifest, self.slug, "gmail_labels")
        source_system_field = package_field_name(manifest, self.slug, "_source_system")
        source_version_field = package_field_name(
            manifest, self.slug, "_source_version"
        )
        synced_field = package_field_name(manifest, self.slug, "_last_synced_at")
        from_field = package_field_name(manifest, self.slug, "from")
        to_field = package_field_name(manifest, self.slug, "to")
        cc_field = package_field_name(manifest, self.slug, "cc")
        sent_field = package_field_name(manifest, self.slug, "sent_at")
        snippet_field = package_field_name(manifest, self.slug, "snippet")
        body_name = package_field_name(manifest, self.slug, "body")
        attachments_field = package_field_name(manifest, self.slug, "attachments_meta")
        message_field = package_field_name(manifest, self.slug, "gmail_message_id")
        updated_path = _gmail_message_updated_at(manifest)
        id_path = _gmail_message_id(manifest)
        summary_path = _gmail_message_summary(manifest)
        subject_header = _gmail_subject_header(manifest).strip().lower()
        from_header = _gmail_from_header(manifest).strip().lower()
        to_header = _gmail_to_header(manifest).strip().lower()
        cc_header = _gmail_cc_header(manifest).strip().lower()
        label_path = _gmail_label_list(manifest)
        body_type = _gmail_body_type(manifest)
        body_key = _gmail_body_key(manifest)
        content_key = _gmail_content_key(manifest)
        body_field = _gmail_body_field(manifest)
        parts_key = _gmail_parts_key(manifest)
        payload_key = _gmail_payload_key(manifest)
        headers_key = _gmail_headers_key(manifest)
        name_key = _gmail_name_key(manifest)
        value_key = _gmail_value_key(manifest)
        selected = select_output(thread, _gmail_thread_messages(manifest))
        messages = selected if isinstance(selected, list) else []
        out: List[Dict[str, Any]] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            found_id = select_output(msg, id_path)
            msg_id = "" if found_id is None else str(found_id)
            found_labels = select_output(msg, label_path)
            message_labels = found_labels if isinstance(found_labels, list) else []
            message_payload = msg.get(payload_key) or {}
            headers = _headers_to_dict(
                message_payload.get(headers_key) or [], name_key, value_key
            )
            from_addr = headers.get(from_header) or ""
            if "<" in from_addr and ">" in from_addr:
                from_addr = from_addr.split("<", 1)[1].split(">", 1)[0]
            from_addr = from_addr.strip().lower()
            to_list = _split_address_list(headers.get(to_header) or "")
            cc_list = _split_address_list(headers.get(cc_header) or "")
            internal = select_output(msg, updated_path)
            sent_at = (
                datetime.fromtimestamp(
                    int(internal) / 1000.0, tz=timezone.utc
                ).isoformat()
                if internal
                else ""
            )
            body_text = _extract_body(
                message_payload,
                body_type,
                body_key,
                content_key,
                body_field,
                parts_key,
            )
            found_summary = select_output(msg, summary_path)
            snippet = ("" if found_summary is None else str(found_summary))[:160]
            cf = {
                source_system_field: "gmail",
                source_version_field: "",
                synced_field: _now_iso(),
                from_field: from_addr,
                to_field: to_list,
                cc_field: cc_list,
                sent_field: sent_at,
                subject_field: headers.get(subject_header) or "",
                snippet_field: snippet,
                body_name: body_text,
                attachments_field: [],
                message_field: msg_id,
                thread_field: thread_id,
                labels_field: [str(lid) for lid in message_labels],
            }
            materialized = MaterializedEntry(
                title=(headers.get(subject_header) or f"Message {msg_id}"),
                body=body_text,
                entry_type_key=child_type,
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

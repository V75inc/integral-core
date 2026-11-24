"""Phase 18 — QuickBooks Online SyncConnector (QB-01, QB-03).

Mirror-model: QuickBooks Online v3 entities (Invoice / Purchase / Customer /
Vendor / Bill) → Integral Entries via the pull-only ``SyncConnector`` ABC.

Locked decisions:

- **Conflict policy:** ``last_write_wins`` (QuickBooks is the source of truth;
  Integral mirrors it read-mostly).
- **Idempotency key:** SHA-256 of ``slug:realm_id:qb_entity:qb_id`` — folds
  the QuickBooks ``realmId`` plus the entity type plus the QBO id, so two
  companies cannot collide and an Invoice/Bill sharing numeric id ``42``
  cannot collide.
- **Token refresh + rotation:** ``_ensure_fresh_token`` rotates within 5
  minutes of expiry and persists the rotated ``refresh_token`` back to
  ``Connector.auth_state`` BEFORE the next API call. Intuit rotates the
  refresh token on every refresh — losing the rotation bricks the connector.
- **OAuth scope:** ``com.intuit.quickbooks.accounting`` only.
  QB-06 (payroll scope) is out of scope per master plan §9 Q2.

Invariants:

- **I-CON-01** — does NOT touch ``provenance`` directly; ``sync_runtime``
  writes the split-shape ``Provenance``. The connector ONLY exposes
  ``MaterializedEntry``.
- **I-CON-03** — registers via ``@register_sync_connector("quickbooks")``.
- **I-CON-04** — per-connector Policy materialized at create time by
  ``connector_registry_node.create_connector``.
- **I-CON-05** — registered by importing this package. The agentive layer
  is always loaded.
"""

from __future__ import annotations

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
from app.services.connectors.quickbooks_oauth import refresh_tokens
from app.services.connectors.sync_lease import save_connector_changes

logger = logging.getLogger(__name__)

# Sentinel injected onto Connector.auth_state by _ensure_fresh_token when the
# refresh-grant returns invalid_grant. The connector aborts its pull and the
# UX shows a Reconnect affordance.
_REAUTH_REQUIRED_KEY = "reauth_required"

# 5-minute skew window for "near expiry" rotation.
_TOKEN_SKEW_SECONDS = 300

# Map QBO entity name → entry_type_key declared in the Finance App manifest.
_QB_ENTITY_TO_ENTRY_TYPE: Dict[str, str] = {
    "Invoice": "qb_invoice",
    "Purchase": "qb_expense",
    "Customer": "qb_customer",
    "Vendor": "qb_vendor",
    "Bill": "qb_bill",
}


def _qbo_entities(manifest: Optional[dict] = None) -> List[str]:
    """Return sync entities from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def listed(source: Optional[dict]) -> Optional[List[str]]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "sync" not in capabilities:
            return None
        sync = capabilities.get("sync")
        if not isinstance(sync, dict) or "entities" not in sync:
            return None
        raw = sync.get("entities")
        if not isinstance(raw, list):
            return None
        names = [str(item).strip() for item in raw if str(item).strip()]
        return names or None

    found = listed(manifest)
    if found is None:
        found = listed(admitted_manifest("quickbooks") or {})
    if not found:
        raise ValueError("QuickBooks package does not declare sync entities")
    return found


def _qbo_page_size(manifest: Optional[dict] = None) -> int:
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
        found = size(admitted_manifest("quickbooks") or {})
    if found is None:
        raise ValueError("QuickBooks package does not declare a page size")
    return found


def _qbo_max_pages(manifest: Optional[dict] = None) -> int:
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
        found = pages(admitted_manifest("quickbooks") or {})
    if found is None:
        raise ValueError("QuickBooks package does not declare a page cap")
    return found


def _template_input_key(query: Optional[dict], name: str) -> Optional[str]:
    """Return the query key whose value templates one input."""
    if not isinstance(query, dict):
        return None
    needle = "{{input." + name + "}}"
    for key, value in query.items():
        if isinstance(value, str) and needle in "".join(value.split()):
            return str(key)
    return None


async def _qbo_package_read(
    action: str,
    manifest: Optional[dict],
    config: Dict[str, Any],
    inputs: Dict[str, Any],
    access_token: str,
    client: httpx.AsyncClient,
    base_url: str,
) -> Any:
    """Send one declared QuickBooks read through the shared package request."""
    from app.connectors.package_runtime import (
        package_action_request,
        package_http,
        perform_request,
    )

    request = package_action_request("quickbooks", action, manifest)
    if not isinstance(request, dict) or not request.get("path"):
        raise ValueError(f"QuickBooks package does not declare {action}")
    sent = dict(request)
    headers = dict(sent.get("headers") or {})
    headers["Authorization"] = f"Bearer {access_token}"
    sent["headers"] = headers
    http = dict(package_http("quickbooks", manifest))
    http["base_url"] = base_url
    host = {"http": http, "auth": {"type": "none"}}
    return await perform_request(host, sent, config, {}, inputs, client)


def _qbo_query_url(
    host: str,
    realm_id: str,
    entity: str,
    start: int,
    page_size: int,
    manifest: Optional[dict] = None,
) -> str:
    """Build the company query URL from the package path and query body."""
    from app.connectors.package_runtime import package_action_request, render

    request = package_action_request("quickbooks", "query_entity", manifest)
    path = str(
        render(
            (request or {}).get("path") or "",
            {"realm_id": quote(realm_id, safe="")},
            {},
        )
    )
    if (
        not request
        or not path.startswith("/")
        or "://" in path
        or "\\" in path
        or ".." in path
    ):
        raise ValueError("QuickBooks package does not declare a query path")
    extra = request.get("query") if isinstance(request.get("query"), dict) else {}
    body_key = _template_input_key(extra, "entity")
    body_template = extra.get(body_key) if body_key else None
    if body_key is None and isinstance(manifest, dict):
        admitted = package_action_request("quickbooks", "query_entity", None)
        admitted_query = admitted.get("query") if isinstance(admitted, dict) else None
        if isinstance(admitted_query, dict):
            body_key = _template_input_key(admitted_query, "entity")
            body_template = admitted_query.get(body_key) if body_key else None
    if not body_key or not isinstance(body_template, str):
        raise ValueError("QuickBooks package does not declare a query body")
    inputs = {
        "entity": entity,
        "start": str(start),
        "page_size": str(page_size),
    }
    rendered = render(extra, {"realm_id": realm_id}, inputs)
    statement = str(render(body_template, {"realm_id": realm_id}, inputs))
    params: Dict[str, str] = {}
    for key, value in rendered.items():
        if str(key) == body_key:
            continue
        params[str(key)] = "" if value is None else str(value)
    params[body_key] = statement
    return f"{host}{path}?{urlencode(params, quote_via=quote)}"


def _qbo_query_select(manifest: Optional[dict] = None) -> str:
    """Return the entity-query selector from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def select_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "query_entity":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("select"):
                return str(output["select"])
            return None
        return None

    found = select_of(manifest)
    if found is None:
        found = select_of(admitted_manifest("quickbooks") or {})
    if not found:
        raise ValueError("QuickBooks package does not declare a query selector")
    return found


def _qbo_record_id(manifest: Optional[dict], action: str) -> str:
    """Return one action's record id path, or the admitted package path."""
    from app.connectors.package_runtime import admitted_manifest

    def id_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("id"):
                return str(output["id"])
            return None
        return None

    found = id_of(manifest)
    if found is None:
        found = id_of(admitted_manifest("quickbooks") or {})
    if not found:
        raise ValueError("QuickBooks package does not declare a record id")
    return found


def _qbo_record_updated_at(manifest: Optional[dict], action: str) -> str:
    """Return one action's record timestamp path, or the admitted package path."""
    from app.connectors.package_runtime import admitted_manifest

    def updated_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("updated_at"):
                return str(output["updated_at"])
            return None
        return None

    found = updated_of(manifest)
    if found is None:
        found = updated_of(admitted_manifest("quickbooks") or {})
    if not found:
        raise ValueError("QuickBooks package does not declare a record timestamp")
    return found


def _qbo_display_name(manifest: Optional[dict] = None) -> str:
    """Return the display-name path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def title_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("title"):
                return str(output["title"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = title_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = title_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a display name")


def _qbo_company_name(manifest: Optional[dict] = None) -> str:
    """Return the company-name path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def name_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("name"):
                return str(output["name"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = name_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = name_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a company name")


def _qbo_document_number(manifest: Optional[dict] = None) -> str:
    """Return the document-number path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def number_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("number"):
                return str(output["number"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = number_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = number_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a document number")


def _qbo_party_name(manifest: Optional[dict] = None) -> str:
    """Return the party-name path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def party_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("party"):
                return str(output["party"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = party_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = party_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a party name")


def _qbo_ref_key(manifest: Optional[dict] = None) -> str:
    """Return the reference name key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("ref_key"):
                return str(output["ref_key"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = key_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = key_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a reference key")


def _qbo_ref_id(manifest: Optional[dict] = None) -> str:
    """Return the reference id key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def id_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("ref_id"):
                return str(output["ref_id"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = id_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = id_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a reference id")


def _qbo_customer_ref(manifest: Optional[dict] = None) -> str:
    """Return the customer-reference key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("customer_ref"):
                return str(output["customer_ref"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = key_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = key_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a customer reference")


def _qbo_expense_ref(manifest: Optional[dict] = None) -> str:
    """Return the expense-payee key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("expense_ref"):
                return str(output["expense_ref"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = key_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = key_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare an expense reference")


def _qbo_vendor_ref(manifest: Optional[dict] = None) -> str:
    """Return the bill-vendor key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("vendor_ref"):
                return str(output["vendor_ref"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = key_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = key_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a vendor reference")


def _qbo_occurred(manifest: Optional[dict] = None) -> str:
    """Return the transaction-date path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def occurred_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("occurred"):
                return str(output["occurred"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = occurred_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = occurred_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a transaction date")


def _qbo_due(manifest: Optional[dict] = None) -> str:
    """Return the due-date path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def due_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("due"):
                return str(output["due"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = due_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = due_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a due date")


def _qbo_amount(manifest: Optional[dict] = None) -> str:
    """Return the total-amount path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def amount_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("amount"):
                return str(output["amount"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = amount_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = amount_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a total amount")


def _qbo_balance(manifest: Optional[dict] = None) -> str:
    """Return the balance path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def balance_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("balance"):
                return str(output["balance"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = balance_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = balance_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a balance")


def _qbo_email(manifest: Optional[dict] = None) -> str:
    """Return the email path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def email_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("email"):
                return str(output["email"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = email_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = email_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare an email")


def _qbo_email_key(manifest: Optional[dict] = None) -> str:
    """Return the email-object key from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def key_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("email_key"):
                return str(output["email_key"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = key_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = key_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare an email key")


def _qbo_phone(manifest: Optional[dict] = None) -> str:
    """Return the phone path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def phone_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("phone"):
                return str(output["phone"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = phone_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = phone_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a phone number")


def _qbo_account_number(manifest: Optional[dict] = None) -> str:
    """Return the account-number path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def account_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("account_number"):
                return str(output["account_number"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = account_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = account_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare an account number")


def _qbo_source_version(manifest: Optional[dict] = None) -> str:
    """Return the source-version path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def version_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("version"):
                return str(output["version"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = version_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = version_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a source version")


def _qbo_payment(manifest: Optional[dict] = None) -> str:
    """Return the payment-type path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def payment_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("payment"):
                return str(output["payment"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = payment_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = payment_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a payment type")


def _qbo_ledger(manifest: Optional[dict] = None) -> str:
    """Return the ledger-account path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def ledger_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("ledger"):
                return str(output["ledger"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = ledger_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = ledger_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare a ledger account")


def _qbo_category(manifest: Optional[dict] = None) -> str:
    """Return the expense-category path from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def category_of(source: Optional[dict], action: str) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for item in capabilities.get("actions") or []:
            if not isinstance(item, dict) or item.get("name") != action:
                continue
            output = item.get("output")
            if isinstance(output, dict) and output.get("category"):
                return str(output["category"])
            return None
        return None

    for action in ("query_entity", "change_data"):
        found = category_of(manifest, action)
        if found:
            return found
    admitted = admitted_manifest("quickbooks") or {}
    for action in ("query_entity", "change_data"):
        found = category_of(admitted, action)
        if found:
            return found
    raise ValueError("QuickBooks package does not declare an expense category")


def _qbo_named_value(payload: Dict[str, Any], path: str) -> str:
    """Read one declared name from a QuickBooks record."""
    from app.connectors.package_runtime import select_output

    found = select_output(payload, path)
    return "" if found is None else str(found)


def _qbo_raw_value(payload: Dict[str, Any], path: str) -> Any:
    """Read one declared value without stringifying it."""
    from app.connectors.package_runtime import select_output

    return select_output(payload, path)


def _qbo_display_value(payload: Dict[str, Any], manifest: Optional[dict]) -> str:
    """Read the declared display name from one QuickBooks record."""
    return _qbo_named_value(payload, _qbo_display_name(manifest))


def _qbo_change_select(manifest: Optional[dict] = None) -> str:
    """Return the change-data selector from the snapshot, or the admitted package."""
    from app.connectors.package_runtime import admitted_manifest

    def select_of(source: Optional[dict]) -> Optional[str]:
        if not isinstance(source, dict):
            return None
        capabilities = source.get("capabilities")
        if not isinstance(capabilities, dict) or "actions" not in capabilities:
            return None
        for action in capabilities.get("actions") or []:
            if not isinstance(action, dict) or action.get("name") != "change_data":
                continue
            output = action.get("output")
            if isinstance(output, dict) and output.get("select"):
                return str(output["select"])
            return None
        return None

    found = select_of(manifest)
    if found is None:
        found = select_of(admitted_manifest("quickbooks") or {})
    if not found:
        raise ValueError("QuickBooks package does not declare a change selector")
    return found


def _qbo_change_rows(
    selected: Any, names: List[str]
) -> Dict[str, List[Dict[str, Any]]]:
    """Group entity rows from a change-data selector result."""
    out: Dict[str, List[Dict[str, Any]]] = {name: [] for name in names}
    responses: List[Dict[str, Any]] = []
    if isinstance(selected, dict):
        responses = [selected]
    elif isinstance(selected, list):
        for item in selected:
            if isinstance(item, dict):
                responses.append(item)
            elif isinstance(item, list):
                responses.extend(row for row in item if isinstance(row, dict))
    for response in responses:
        for entity in names:
            page = response.get(entity) or []
            if isinstance(page, list) and page:
                out[entity].extend(page)
    return out


def _qbo_query_headers(
    access_token: str, manifest: Optional[dict] = None
) -> Dict[str, str]:
    """Build the entity-query headers from the package. The token stays here."""
    from app.connectors.package_runtime import package_action_request, render

    def declared(source: Optional[dict]) -> Optional[dict]:
        if not isinstance(source, dict) or "headers" not in source:
            return None
        headers = source.get("headers")
        return headers if isinstance(headers, dict) else None

    headers = declared(package_action_request("quickbooks", "query_entity", manifest))
    if headers is None and isinstance(manifest, dict):
        headers = declared(package_action_request("quickbooks", "query_entity", None))
    if not isinstance(headers, dict) or "Accept" not in headers:
        raise ValueError("QuickBooks package does not declare a query Accept header")
    rendered = render(headers, {}, {})
    out = {
        str(key): "" if value is None else str(value) for key, value in rendered.items()
    }
    out["Authorization"] = f"Bearer {access_token}"
    return out


def _template_cursor_key(query: Optional[dict]) -> Optional[str]:
    """Return the query key whose value is a cursor template."""
    if not isinstance(query, dict):
        return None
    for key, value in query.items():
        if isinstance(value, str) and "{{cursor}}" in "".join(value.split()):
            return str(key)
    return None


def _qbo_change_url(
    host: str,
    realm_id: str,
    entities: str,
    changed_since: str,
    manifest: Optional[dict] = None,
) -> str:
    """Build the change-data URL from the package path and query."""
    from app.connectors.package_runtime import package_action_request, render

    request = package_action_request("quickbooks", "change_data", manifest)
    path = str(
        render(
            (request or {}).get("path") or "",
            {"realm_id": quote(realm_id, safe="")},
            {},
        )
    )
    if (
        not request
        or not path.startswith("/")
        or "://" in path
        or "\\" in path
        or ".." in path
    ):
        raise ValueError("QuickBooks package does not declare a change-data path")
    extra = request.get("query") if isinstance(request.get("query"), dict) else {}
    cursor_key = _template_cursor_key(extra)
    entity_key = _template_input_key(extra, "entities")
    if cursor_key is None or entity_key is None:
        admitted = package_action_request("quickbooks", "change_data", None)
        admitted_query = admitted.get("query") if isinstance(admitted, dict) else None
        if cursor_key is None:
            cursor_key = _template_cursor_key(
                admitted_query if isinstance(admitted_query, dict) else None
            )
        if entity_key is None:
            entity_key = _template_input_key(
                admitted_query if isinstance(admitted_query, dict) else None,
                "entities",
            )
    if not cursor_key:
        raise ValueError("QuickBooks package does not declare a cursor field")
    if not entity_key:
        raise ValueError("QuickBooks package does not declare an entities field")
    rendered = render(
        extra, {"realm_id": realm_id}, {"cursor": "", "entities": entities}
    )
    params: Dict[str, str] = {}
    for key, value in rendered.items():
        if str(key) == cursor_key:
            continue
        params[str(key)] = "" if value is None else str(value)
    if entity_key not in params:
        params[entity_key] = entities
    if changed_since:
        params[cursor_key] = changed_since
    return f"{host}{path}?{urlencode(params, quote_via=quote)}"


def _qbo_change_headers(
    access_token: str, manifest: Optional[dict] = None
) -> Dict[str, str]:
    """Build the change-data headers from the package. The token stays here."""
    from app.connectors.package_runtime import package_action_request, render

    def declared(source: Optional[dict]) -> Optional[dict]:
        if not isinstance(source, dict) or "headers" not in source:
            return None
        headers = source.get("headers")
        return headers if isinstance(headers, dict) else None

    headers = declared(package_action_request("quickbooks", "change_data", manifest))
    if headers is None and isinstance(manifest, dict):
        headers = declared(package_action_request("quickbooks", "change_data", None))
    if not isinstance(headers, dict) or "Accept" not in headers:
        raise ValueError(
            "QuickBooks package does not declare a change-data Accept header"
        )
    rendered = render(headers, {}, {})
    out = {
        str(key): "" if value is None else str(value) for key, value in rendered.items()
    }
    out["Authorization"] = f"Bearer {access_token}"
    return out


def _qbo_host(environment: str, manifest: Optional[dict] = None) -> str:
    """Return the stored QuickBooks host, or the admitted package host."""
    from app.connectors.package_runtime import package_http

    http = package_http("quickbooks", manifest)
    hosts = http.get("hosts") if isinstance(http.get("hosts"), dict) else {}
    name = "sandbox" if environment == "sandbox" else "production"
    chosen = str(hosts.get(name) or "").rstrip("/")
    if chosen.startswith("https://"):
        return chosen
    if environment != "sandbox":
        base = str(http.get("base_url") or "").rstrip("/")
        if base.startswith("https://"):
            return base
    raise ValueError("QuickBooks package does not declare an https host")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _derive_invoice_status(due: str = "", balance: Any = None) -> str:
    """Derive a UI-friendly status from QuickBooks Invoice fields."""
    try:
        balance_num = float(balance) if balance is not None else None
    except (TypeError, ValueError):
        balance_num = None
    if balance_num is not None and balance_num == 0:
        return "paid"
    if due:
        try:
            due_dt = datetime.fromisoformat(due)
            if due_dt < datetime.now(timezone.utc).replace(tzinfo=None):
                return "overdue"
        except (TypeError, ValueError):
            pass
    return "open"


def _norm_email(value: Any, key: str) -> str:
    """Return a lowercased trimmed email or empty string for missing/non-dict."""
    if isinstance(value, dict):
        addr = value.get(key)
    else:
        addr = value
    if not isinstance(addr, str):
        return ""
    return addr.strip().lower()


@register_sync_connector("quickbooks")
class QuickBooksConnector(SyncConnector):
    """QuickBooks Online v3 pull-only sync.

    See module docstring for the full contract. The connector is invoked
    by ``sync_one_connector`` (``backend/app/services/connectors/sync_runtime.py``);
    that runtime owns Provenance writes (I-CON-01) and ``last_synced_at``
    advance. The connector owns the CDC ``sync_cursor`` and the OAuth
    token rotation.
    """

    slug: str = "quickbooks"

    # Allow tests to inject an httpx.AsyncClient (with a MockTransport) so
    # the suite runs offline. Production callers leave this None.
    test_http_client: Optional[httpx.AsyncClient] = None

    @property
    def conflict_policy(self) -> ConflictPolicy:  # type: ignore[override]
        """Read the conflict policy from the admitted package."""
        from app.connectors.package_runtime import package_conflict_policy

        return package_conflict_policy(None, self.slug)  # type: ignore[return-value]

    def idempotency_key_for(self, record: ExternalRecord) -> str:
        """Fold realm_id + qb_entity + qb_id into the key.

        Two QuickBooks companies (different ``realmId``), or an Invoice
        and a Bill sharing numeric id ``42``, all produce distinct keys.
        Falls back to the default ``slug:external_id`` if the realm or
        entity hint is missing (defensive — should never happen because
        ``sync_pull`` always sets both before yielding).
        """
        payload = record.payload or {}
        realm_id = str(payload.get("_realm_id") or "")
        entity = str(payload.get("_qb_entity") or "")
        if not realm_id or not entity:
            return super().idempotency_key_for(record)
        seed = f"{self.slug}:{realm_id}:{entity}:{record.external_id}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    async def _ensure_fresh_token(
        self, connector: Any, *, http_client: Optional[httpx.AsyncClient] = None
    ) -> bool:
        """Rotate the access token if near expiry. Returns False on reauth-required.

        Persists the rotated ``refresh_token`` and the new ``access_token`` +
        expiries back to ``connector.auth_state`` before any downstream API
        call. On ``invalid_grant`` (refresh token expired or revoked) sets
        ``auth_state[REAUTH_REQUIRED_KEY] = True``, saves the connector, and
        returns False so the caller aborts the pull cleanly.
        """
        from app.agentive.services.connector_registry_node import decrypt_auth_state
        from app.api.errors import ConnectorAuthError

        auth_state: Dict[str, Any] = decrypt_auth_state(
            getattr(connector, "auth_state", {}) or {}
        )
        connector.auth_state = auth_state
        expires_at_str = auth_state.get("access_token_expires_at")
        expires_at = _parse_iso(expires_at_str)
        skew_threshold = datetime.now(timezone.utc) + timedelta(
            seconds=_TOKEN_SKEW_SECONDS
        )
        if expires_at is not None and expires_at > skew_threshold:
            # Token still valid for at least the skew window.
            return True

        refresh_token = auth_state.get("refresh_token") or ""
        if not refresh_token:
            logger.warning(
                "quickbooks _ensure_fresh_token: connector %s has no refresh_token; "
                "marking reauth_required",
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
            reauth = bool(details.get("reauth_required", False))
            logger.warning(
                "quickbooks _ensure_fresh_token: refresh failed for connector %s "
                "reauth_required=%s",
                getattr(connector, "id", "<unknown>"),
                reauth,
            )
            if reauth:
                auth_state[_REAUTH_REQUIRED_KEY] = True
                connector.auth_state = auth_state
                await save_connector_changes(connector, fields=("auth_state",))
            return False

        # Persist the rotated tokens BEFORE any downstream API call.
        new_access = refreshed.get("access_token") or ""
        new_refresh = refreshed.get("refresh_token") or refresh_token
        expires_in = int(refreshed.get("expires_in") or 3600)
        access_exp = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        rt_expires_in = int(refreshed.get("x_refresh_token_expires_in") or 0)
        auth_state["access_token"] = new_access
        auth_state["refresh_token"] = new_refresh
        auth_state["access_token_expires_at"] = access_exp.isoformat()
        if rt_expires_in:
            rt_exp = datetime.now(timezone.utc) + timedelta(seconds=rt_expires_in)
            auth_state["refresh_token_expires_at"] = rt_exp.isoformat()
        auth_state.pop(_REAUTH_REQUIRED_KEY, None)
        connector.auth_state = auth_state
        await save_connector_changes(connector, fields=("auth_state",))
        logger.info(
            "quickbooks _ensure_fresh_token: rotated tokens for connector %s",
            getattr(connector, "id", "<unknown>"),
        )
        return True

    def _http_client(self) -> httpx.AsyncClient:
        """Test seam — return the injected client when set, otherwise build one."""
        if self.test_http_client is not None:
            return self.test_http_client
        return httpx.AsyncClient(timeout=30.0)

    async def _query_entity(
        self,
        *,
        client: httpx.AsyncClient,
        host: str,
        realm_id: str,
        access_token: str,
        entity: str,
        manifest: Optional[dict] = None,
    ) -> List[Dict[str, Any]]:
        """Page through ``SELECT * FROM <entity>`` until exhausted or _MAX_PAGES."""
        out: List[Dict[str, Any]] = []
        start_position = 1
        page_size = _qbo_page_size(manifest)
        from app.connectors.package_runtime import select_output

        selector = _qbo_query_select(manifest)
        for _page in range(_qbo_max_pages(manifest)):
            body = (
                await _qbo_package_read(
                    "query_entity",
                    manifest,
                    {"realm_id": realm_id},
                    {
                        "entity": entity,
                        "start": str(start_position),
                        "page_size": str(page_size),
                    },
                    access_token,
                    client,
                    host,
                )
                or {}
            )
            selected = select_output(body, selector)
            container = selected if isinstance(selected, dict) else {}
            page = container.get(entity) or []
            if not isinstance(page, list):
                page = []
            if not page:
                break
            out.extend(page)
            if len(page) < page_size:
                break
            start_position += page_size
        return out

    async def _cdc_changes(
        self,
        *,
        client: httpx.AsyncClient,
        host: str,
        realm_id: str,
        access_token: str,
        changed_since: str,
        manifest: Optional[dict] = None,
        entities: Optional[List[str]] = None,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Call the package change-data path; return ``{entity: [records]}``."""
        names = entities or _qbo_entities(manifest)
        entities_csv = ",".join(names)
        from app.connectors.package_runtime import select_output

        body = (
            await _qbo_package_read(
                "change_data",
                manifest,
                {"realm_id": realm_id},
                {"entities": entities_csv, "cursor": changed_since},
                access_token,
                client,
                host,
            )
            or {}
        )
        selected = select_output(body, _qbo_change_select(manifest))
        return _qbo_change_rows(selected, names)

    async def sync_pull(self, *, connector: Any) -> AsyncIterator[ExternalRecord]:
        """Yield records for all five QuickBooks entities.

        Backfill (empty cursor) — page each entity via the query endpoint;
        Incremental (cursor set) — call ``/cdc`` once with ``changedSince``.

        On reauth-required the helper returns silently; ``sync_one_connector``
        sees zero records and emits ``connector.sync.complete`` with empty
        stats. The Reauth UX surfaces from ``auth_state.reauth_required``.
        """
        auth_state: Dict[str, Any] = getattr(connector, "auth_state", None) or {}
        realm_id = str(auth_state.get("realm_id") or "")
        if not realm_id:
            logger.warning(
                "quickbooks sync: connector %s has no realm_id in auth_state; "
                "aborting",
                getattr(connector, "id", "<unknown>"),
            )
            return

        client = self._http_client()
        owns_client = self.test_http_client is None
        try:
            ok = await self._ensure_fresh_token(connector, http_client=client)
            if not ok:
                logger.warning(
                    "quickbooks sync: reauth required for connector %s; "
                    "aborting pull",
                    getattr(connector, "id", "<unknown>"),
                )
                return

            access_token = (connector.auth_state or {}).get("access_token") or ""
            environment = str(
                (connector.auth_state or {}).get("environment") or "production"
            )
            stored = getattr(connector, "package_manifest", None)
            query_id = _qbo_record_id(stored, "query_entity")
            change_id = _qbo_record_id(stored, "change_data")
            query_updated = _qbo_record_updated_at(stored, "query_entity")
            change_updated = _qbo_record_updated_at(stored, "change_data")
            host = _qbo_host(environment, stored)
            entities = _qbo_entities(stored)
            cursor = getattr(connector, "sync_cursor", None)

            if cursor:
                # Incremental — one change-data call covers the package entities.
                cdc_result = await self._cdc_changes(
                    client=client,
                    host=host,
                    realm_id=realm_id,
                    access_token=access_token,
                    changed_since=cursor,
                    manifest=stored,
                    entities=entities,
                )
                for entity in entities:
                    for record_dict in cdc_result.get(entity, []):
                        yield self._wrap_record(
                            entity, realm_id, record_dict, change_id, change_updated
                        )
            else:
                # Backfill — page each entity via the query endpoint.
                for entity in entities:
                    page = await self._query_entity(
                        client=client,
                        host=host,
                        realm_id=realm_id,
                        access_token=access_token,
                        entity=entity,
                        manifest=stored,
                    )
                    for record_dict in page:
                        yield self._wrap_record(
                            entity, realm_id, record_dict, query_id, query_updated
                        )

            # Advance the CDC high-water mark. The subclass owns sync_cursor;
            # the runtime owns last_synced_at.
            connector.sync_cursor = _now_iso()
            await save_connector_changes(connector, fields=("sync_cursor",))
        finally:
            if owns_client:
                await client.aclose()

    def _wrap_record(
        self,
        entity: str,
        realm_id: str,
        record_dict: Dict[str, Any],
        id_path: str,
        updated_path: str,
    ) -> ExternalRecord:
        """Wrap a QBO record dict as an ExternalRecord with routing metadata."""
        from app.connectors.package_runtime import select_output

        payload = dict(record_dict)
        payload["_qb_entity"] = entity
        payload["_realm_id"] = realm_id
        found_id = select_output(record_dict, id_path)
        found_at = select_output(record_dict, updated_path)
        return ExternalRecord(
            external_id="" if found_id is None else str(found_id),
            payload=payload,
            updated_at=None if found_at is None else str(found_at),
        )

    def to_entry(
        self, record: ExternalRecord, manifest: Optional[dict] = None
    ) -> MaterializedEntry:
        """Switch on payload["_qb_entity"] and return a typed MaterializedEntry."""
        payload = record.payload or {}
        entity = payload.get("_qb_entity") or ""
        if entity == "Invoice":
            return self._to_entry_invoice(record, manifest)
        if entity == "Purchase":
            return self._to_entry_purchase(record, manifest)
        if entity == "Customer":
            return self._to_entry_customer(record, manifest)
        if entity == "Vendor":
            return self._to_entry_vendor(record, manifest)
        if entity == "Bill":
            return self._to_entry_bill(record, manifest)
        # Unknown entity — surface as a generic entry so the sync run does
        # not crash; the operator notices via the missing entry_type_key.
        logger.warning(
            "quickbooks to_entry: unknown _qb_entity %r on record %s",
            entity,
            record.external_id,
        )
        return MaterializedEntry(
            title=f"QuickBooks {entity} {record.external_id}",
            body="",
            entry_type_key="",
            custom_fields=self._provenance_custom_fields(record, manifest),
        )

    def _destination_fields(
        self, manifest: Optional[dict], *keys: str
    ) -> Dict[str, str]:
        """Resolve destination field names from the snapshot or admitted package."""
        from app.connectors.package_runtime import package_field_name

        return {key: package_field_name(manifest, self.slug, key) for key in keys}

    def _provenance_custom_fields(
        self, record: ExternalRecord, manifest: Optional[dict] = None
    ) -> Dict[str, Any]:
        """Common provenance scalars surfaced into Entry.custom_fields.

        Provenance proper (split shape) is written by sync_runtime per
        I-CON-01; these scalars are an additional fast-path so agents and
        UI surfaces can read source provenance without walking the
        provenance subgraph.
        """
        payload = record.payload or {}
        names = self._destination_fields(
            manifest,
            "_source_system",
            "_source_version",
            "_last_synced_at",
            "_qb_entity",
            "_realm_id",
        )
        return {
            names["_source_system"]: "quickbooks",
            names["_source_version"]: _qbo_named_value(
                payload, _qbo_source_version(manifest)
            ),
            names["_last_synced_at"]: _now_iso(),
            names["_qb_entity"]: payload.get("_qb_entity") or "",
            names["_realm_id"]: payload.get("_realm_id") or "",
        }

    def _to_entry_invoice(
        self, record: ExternalRecord, manifest: Optional[dict] = None
    ) -> MaterializedEntry:
        from app.connectors.package_runtime import (
            package_entity_type,
            package_title_prefix_for,
        )

        inv = record.payload or {}
        title_prefix = package_title_prefix_for(manifest, self.slug, "Invoice")
        customer_ref = inv.get(_qbo_customer_ref(manifest)) or {}
        customer_name = _qbo_named_value(inv, _qbo_party_name(manifest))
        doc_number = _qbo_named_value(inv, _qbo_document_number(manifest))
        due_date = _qbo_named_value(inv, _qbo_due(manifest))
        balance = _qbo_raw_value(inv, _qbo_balance(manifest))
        title = f"{title_prefix} {doc_number} — {customer_name}".strip(" —")
        names = self._destination_fields(
            manifest,
            "invoice_number",
            "qb_customer_id",
            "qb_customer_name",
            "total_amount",
            "balance",
            "txn_date",
            "due_date",
            "status",
        )
        cf = self._provenance_custom_fields(record, manifest)
        cf.update(
            {
                names["invoice_number"]: doc_number,
                names["qb_customer_id"]: str(
                    customer_ref.get(_qbo_ref_id(manifest)) or ""
                ),
                names["qb_customer_name"]: customer_name,
                names["total_amount"]: _qbo_raw_value(inv, _qbo_amount(manifest)),
                names["balance"]: balance,
                names["txn_date"]: _qbo_named_value(inv, _qbo_occurred(manifest)),
                names["due_date"]: due_date,
                names["status"]: _derive_invoice_status(due_date, balance),
            }
        )
        return MaterializedEntry(
            title=title or f"{title_prefix} {record.external_id}",
            body="",
            entry_type_key=package_entity_type(manifest, self.slug, "Invoice"),
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

    def _to_entry_purchase(
        self, record: ExternalRecord, manifest: Optional[dict] = None
    ) -> MaterializedEntry:
        from app.connectors.package_runtime import (
            package_entity_type,
            package_title_prefix_for,
        )

        purchase = record.payload or {}
        title_prefix = package_title_prefix_for(manifest, self.slug, "Purchase")
        vendor_ref = purchase.get(_qbo_expense_ref(manifest)) or {}
        vendor_name = vendor_ref.get(_qbo_ref_key(manifest)) or ""
        found_category = _qbo_raw_value(purchase, _qbo_category(manifest))
        category = ""
        if isinstance(found_category, list):
            for item in found_category:
                if item:
                    category = str(item)
                    break
        elif found_category:
            category = str(found_category)
        names = self._destination_fields(
            manifest,
            "payment_type",
            "qb_vendor_id",
            "qb_vendor_name",
            "total_amount",
            "txn_date",
            "account",
            "category",
        )
        cf = self._provenance_custom_fields(record, manifest)
        cf.update(
            {
                names["payment_type"]: _qbo_named_value(
                    purchase, _qbo_payment(manifest)
                ).lower(),
                names["qb_vendor_id"]: str(vendor_ref.get(_qbo_ref_id(manifest)) or ""),
                names["qb_vendor_name"]: vendor_name,
                names["total_amount"]: _qbo_raw_value(purchase, _qbo_amount(manifest)),
                names["txn_date"]: _qbo_named_value(purchase, _qbo_occurred(manifest)),
                names["account"]: _qbo_named_value(purchase, _qbo_ledger(manifest)),
                names["category"]: category,
            }
        )
        title_bits = [
            title_prefix,
            _qbo_named_value(purchase, _qbo_occurred(manifest)),
            vendor_name,
        ]
        title = " — ".join(b for b in title_bits if b)
        return MaterializedEntry(
            title=title or f"{title_prefix} {record.external_id}",
            body="",
            entry_type_key=package_entity_type(manifest, self.slug, "Purchase"),
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

    def _to_entry_customer(
        self, record: ExternalRecord, manifest: Optional[dict] = None
    ) -> MaterializedEntry:
        from app.connectors.package_runtime import (
            package_entity_type,
            package_title_prefix_for,
        )

        cust = record.payload or {}
        title_prefix = package_title_prefix_for(manifest, self.slug, "Customer")
        company = (
            _qbo_named_value(cust, _qbo_company_name(manifest))
            or _qbo_display_value(cust, manifest)
            or ""
        )
        email = _norm_email(
            _qbo_raw_value(cust, _qbo_email(manifest)),
            _qbo_email_key(manifest),
        )
        phone = _qbo_named_value(cust, _qbo_phone(manifest))
        names = self._destination_fields(
            manifest,
            "company_name",
            "email",
            "phone",
            "balance",
            "crm_link_status",
        )
        cf = self._provenance_custom_fields(record, manifest)
        cf.update(
            {
                names["company_name"]: company,
                names["email"]: email,
                names["phone"]: phone,
                names["balance"]: _qbo_raw_value(cust, _qbo_balance(manifest)),
                names["crm_link_status"]: "unmatched",  # linker updates post-write
            }
        )
        return MaterializedEntry(
            title=company or f"{title_prefix} {record.external_id}",
            body="",
            entry_type_key=package_entity_type(manifest, self.slug, "Customer"),
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

    def _to_entry_vendor(
        self, record: ExternalRecord, manifest: Optional[dict] = None
    ) -> MaterializedEntry:
        from app.connectors.package_runtime import (
            package_entity_type,
            package_title_prefix_for,
        )

        vendor = record.payload or {}
        title_prefix = package_title_prefix_for(manifest, self.slug, "Vendor")
        company = (
            _qbo_named_value(vendor, _qbo_company_name(manifest))
            or _qbo_display_value(vendor, manifest)
            or ""
        )
        names = self._destination_fields(
            manifest,
            "company_name",
            "email",
            "balance",
            "account_number",
        )
        cf = self._provenance_custom_fields(record, manifest)
        cf.update(
            {
                names["company_name"]: company,
                names["email"]: _norm_email(
                    _qbo_raw_value(vendor, _qbo_email(manifest)),
                    _qbo_email_key(manifest),
                ),
                names["balance"]: _qbo_raw_value(vendor, _qbo_balance(manifest)),
                names["account_number"]: _qbo_named_value(
                    vendor, _qbo_account_number(manifest)
                ),
            }
        )
        return MaterializedEntry(
            title=company or f"{title_prefix} {record.external_id}",
            body="",
            entry_type_key=package_entity_type(manifest, self.slug, "Vendor"),
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

    def _to_entry_bill(
        self, record: ExternalRecord, manifest: Optional[dict] = None
    ) -> MaterializedEntry:
        from app.connectors.package_runtime import (
            package_entity_type,
            package_title_prefix_for,
        )

        bill = record.payload or {}
        title_prefix = package_title_prefix_for(manifest, self.slug, "Bill")
        vendor_ref = bill.get(_qbo_vendor_ref(manifest)) or {}
        vendor_name = vendor_ref.get(_qbo_ref_key(manifest)) or ""
        doc_number = _qbo_named_value(bill, _qbo_document_number(manifest))
        title = f"{title_prefix} {doc_number} — {vendor_name}".strip(" —")
        names = self._destination_fields(
            manifest,
            "bill_number",
            "qb_vendor_id",
            "qb_vendor_name",
            "total_amount",
            "balance",
            "txn_date",
            "due_date",
        )
        cf = self._provenance_custom_fields(record, manifest)
        cf.update(
            {
                names["bill_number"]: doc_number,
                names["qb_vendor_id"]: str(vendor_ref.get(_qbo_ref_id(manifest)) or ""),
                names["qb_vendor_name"]: vendor_name,
                names["total_amount"]: _qbo_raw_value(bill, _qbo_amount(manifest)),
                names["balance"]: _qbo_raw_value(bill, _qbo_balance(manifest)),
                names["txn_date"]: _qbo_named_value(bill, _qbo_occurred(manifest)),
                names["due_date"]: _qbo_named_value(bill, _qbo_due(manifest)),
            }
        )
        return MaterializedEntry(
            title=title or f"{title_prefix} {record.external_id}",
            body="",
            entry_type_key=package_entity_type(manifest, self.slug, "Bill"),
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

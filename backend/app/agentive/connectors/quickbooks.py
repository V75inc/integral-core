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
- **D-08 / I-CON-05** — lives in ``agentive/`` and is only imported when
  ``AGENTIVE_ENABLED=1`` via the package ``__init__``.
"""

from __future__ import annotations

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
from app.services.connectors.quickbooks_oauth import refresh_tokens

logger = logging.getLogger(__name__)

# Production: https://quickbooks.api.intuit.com
# Sandbox: https://sandbox-quickbooks.api.intuit.com
_QBO_PROD_HOST = "https://quickbooks.api.intuit.com"
_QBO_SANDBOX_HOST = "https://sandbox-quickbooks.api.intuit.com"

# Sentinel injected onto Connector.auth_state by _ensure_fresh_token when the
# refresh-grant returns invalid_grant. The connector aborts its pull and the
# UX shows a Reconnect affordance.
_REAUTH_REQUIRED_KEY = "reauth_required"

# 5-minute skew window for "near expiry" rotation.
_TOKEN_SKEW_SECONDS = 300

# Entities pulled per sync. Carried in payload["_qb_entity"] for the
# to_entry switch and the multi-track routing in sync_runtime.
_QB_ENTITIES: List[str] = ["Invoice", "Purchase", "Customer", "Vendor", "Bill"]

# Map QBO entity name → entry_type_key declared in the Finance App manifest.
_QB_ENTITY_TO_ENTRY_TYPE: Dict[str, str] = {
    "Invoice": "qb_invoice",
    "Purchase": "qb_expense",
    "Customer": "qb_customer",
    "Vendor": "qb_vendor",
    "Bill": "qb_bill",
}


def _qbo_host(environment: str) -> str:
    return _QBO_SANDBOX_HOST if environment == "sandbox" else _QBO_PROD_HOST


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _derive_invoice_status(invoice: Dict[str, Any]) -> str:
    """Derive a UI-friendly status from QuickBooks Invoice fields."""
    balance = invoice.get("Balance")
    try:
        balance_num = float(balance) if balance is not None else None
    except (TypeError, ValueError):
        balance_num = None
    if balance_num is not None and balance_num == 0:
        return "paid"
    due = invoice.get("DueDate") or ""
    if due:
        try:
            due_dt = datetime.fromisoformat(due)
            if due_dt < datetime.now(timezone.utc).replace(tzinfo=None):
                return "overdue"
        except (TypeError, ValueError):
            pass
    return "open"


def _norm_email(value: Any) -> str:
    """Return a lowercased trimmed email or empty string for missing/non-dict."""
    if isinstance(value, dict):
        addr = value.get("Address")
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
    conflict_policy: ConflictPolicy = "last_write_wins"

    # Safety cap: 100 entities/page × 100 pages × 5 entity types ≈ 50k records
    # per backfill. Prevents a runaway sync against a very large company.
    _MAX_PAGES_PER_ENTITY: int = 100
    _PAGE_SIZE: int = 100

    # Allow tests to inject an httpx.AsyncClient (with a MockTransport) so
    # the suite runs offline. Production callers leave this None.
    test_http_client: Optional[httpx.AsyncClient] = None

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
        from app.api.errors import ConnectorAuthError

        auth_state: Dict[str, Any] = dict(getattr(connector, "auth_state", {}) or {})
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
                await connector.save()
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
        await connector.save()
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
    ) -> List[Dict[str, Any]]:
        """Page through ``SELECT * FROM <entity>`` until exhausted or _MAX_PAGES."""
        out: List[Dict[str, Any]] = []
        start_position = 1
        for _page in range(self._MAX_PAGES_PER_ENTITY):
            query = (
                f"SELECT * FROM {entity} "
                f"STARTPOSITION {start_position} MAXRESULTS {self._PAGE_SIZE}"
            )
            url = (
                f"{host}/v3/company/{realm_id}/query"
                f"?query={quote(query)}&minorversion=65"
            )
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            body = resp.json() or {}
            page = (body.get("QueryResponse") or {}).get(entity) or []
            if not page:
                break
            out.extend(page)
            if len(page) < self._PAGE_SIZE:
                break
            start_position += self._PAGE_SIZE
        return out

    async def _cdc_changes(
        self,
        *,
        client: httpx.AsyncClient,
        host: str,
        realm_id: str,
        access_token: str,
        changed_since: str,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Call ``/cdc`` for all five entities; return ``{entity: [records]}``."""
        entities_csv = ",".join(_QB_ENTITIES)
        url = (
            f"{host}/v3/company/{realm_id}/cdc"
            f"?entities={entities_csv}&changedSince={quote(changed_since)}"
            f"&minorversion=65"
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        body = resp.json() or {}
        out: Dict[str, List[Dict[str, Any]]] = {e: [] for e in _QB_ENTITIES}
        for cdc_block in body.get("CDCResponse") or []:
            for qr in cdc_block.get("QueryResponse") or []:
                for entity in _QB_ENTITIES:
                    page = qr.get(entity) or []
                    if page:
                        out[entity].extend(page)
        return out

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
            host = _qbo_host(environment)
            cursor = getattr(connector, "sync_cursor", None)

            if cursor:
                # Incremental — single /cdc call covers all five entities.
                cdc_result = await self._cdc_changes(
                    client=client,
                    host=host,
                    realm_id=realm_id,
                    access_token=access_token,
                    changed_since=cursor,
                )
                for entity in _QB_ENTITIES:
                    for record_dict in cdc_result.get(entity, []):
                        yield self._wrap_record(entity, realm_id, record_dict)
            else:
                # Backfill — page each entity via the query endpoint.
                for entity in _QB_ENTITIES:
                    page = await self._query_entity(
                        client=client,
                        host=host,
                        realm_id=realm_id,
                        access_token=access_token,
                        entity=entity,
                    )
                    for record_dict in page:
                        yield self._wrap_record(entity, realm_id, record_dict)

            # Advance the CDC high-water mark. The subclass owns sync_cursor;
            # the runtime owns last_synced_at.
            connector.sync_cursor = _now_iso()
            await connector.save()
        finally:
            if owns_client:
                await client.aclose()

    def _wrap_record(
        self, entity: str, realm_id: str, record_dict: Dict[str, Any]
    ) -> ExternalRecord:
        """Wrap a QBO record dict as an ExternalRecord with routing metadata."""
        payload = dict(record_dict)
        payload["_qb_entity"] = entity
        payload["_realm_id"] = realm_id
        meta = record_dict.get("MetaData") or {}
        return ExternalRecord(
            external_id=str(record_dict.get("Id") or ""),
            payload=payload,
            updated_at=meta.get("LastUpdatedTime"),
        )

    def to_entry(self, record: ExternalRecord) -> MaterializedEntry:
        """Switch on payload["_qb_entity"] and return a typed MaterializedEntry."""
        payload = record.payload or {}
        entity = payload.get("_qb_entity") or ""
        if entity == "Invoice":
            return self._to_entry_invoice(record)
        if entity == "Purchase":
            return self._to_entry_purchase(record)
        if entity == "Customer":
            return self._to_entry_customer(record)
        if entity == "Vendor":
            return self._to_entry_vendor(record)
        if entity == "Bill":
            return self._to_entry_bill(record)
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
            custom_fields=self._provenance_custom_fields(record),
        )

    def _provenance_custom_fields(self, record: ExternalRecord) -> Dict[str, Any]:
        """Common provenance scalars surfaced into Entry.custom_fields.

        Provenance proper (split shape) is written by sync_runtime per
        I-CON-01; these scalars are an additional fast-path so agents and
        UI surfaces can read source provenance without walking the
        provenance subgraph.
        """
        payload = record.payload or {}
        return {
            "_source_system": "quickbooks",
            "_source_version": str(payload.get("SyncToken") or ""),
            "_last_synced_at": _now_iso(),
            "_qb_entity": payload.get("_qb_entity") or "",
            "_realm_id": payload.get("_realm_id") or "",
        }

    def _to_entry_invoice(self, record: ExternalRecord) -> MaterializedEntry:
        inv = record.payload or {}
        customer_ref = inv.get("CustomerRef") or {}
        customer_name = customer_ref.get("name") or ""
        doc_number = str(inv.get("DocNumber") or "")
        title = f"Invoice {doc_number} — {customer_name}".strip(" —")
        cf = self._provenance_custom_fields(record)
        cf.update(
            {
                "invoice_number": doc_number,
                "qb_customer_id": str(customer_ref.get("value") or ""),
                "qb_customer_name": customer_name,
                "total_amount": inv.get("TotalAmt"),
                "balance": inv.get("Balance"),
                "txn_date": inv.get("TxnDate") or "",
                "due_date": inv.get("DueDate") or "",
                "status": _derive_invoice_status(inv),
            }
        )
        return MaterializedEntry(
            title=title or f"Invoice {record.external_id}",
            body="",
            entry_type_key="qb_invoice",
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

    def _to_entry_purchase(self, record: ExternalRecord) -> MaterializedEntry:
        purchase = record.payload or {}
        vendor_ref = purchase.get("EntityRef") or {}
        vendor_name = vendor_ref.get("name") or ""
        # Line-item account category — derive a single representative one.
        category = ""
        for line in purchase.get("Line") or []:
            detail = (line.get("AccountBasedExpenseLineDetail") or {}).get("AccountRef")
            if isinstance(detail, dict):
                category = detail.get("name") or ""
                if category:
                    break
        cf = self._provenance_custom_fields(record)
        cf.update(
            {
                "payment_type": (purchase.get("PaymentType") or "").lower(),
                "qb_vendor_id": str(vendor_ref.get("value") or ""),
                "qb_vendor_name": vendor_name,
                "total_amount": purchase.get("TotalAmt"),
                "txn_date": purchase.get("TxnDate") or "",
                "account": (purchase.get("AccountRef") or {}).get("name") or "",
                "category": category,
            }
        )
        title_bits = [
            "Expense",
            purchase.get("TxnDate") or "",
            vendor_name,
        ]
        title = " — ".join(b for b in title_bits if b)
        return MaterializedEntry(
            title=title or f"Expense {record.external_id}",
            body="",
            entry_type_key="qb_expense",
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

    def _to_entry_customer(self, record: ExternalRecord) -> MaterializedEntry:
        cust = record.payload or {}
        company = cust.get("CompanyName") or cust.get("DisplayName") or ""
        email = _norm_email(cust.get("PrimaryEmailAddr"))
        phone = (cust.get("PrimaryPhone") or {}).get("FreeFormNumber") or ""
        cf = self._provenance_custom_fields(record)
        cf.update(
            {
                "company_name": company,
                "email": email,
                "phone": phone,
                "balance": cust.get("Balance"),
                "crm_link_status": "unmatched",  # linker updates post-write
            }
        )
        return MaterializedEntry(
            title=company or f"Customer {record.external_id}",
            body="",
            entry_type_key="qb_customer",
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

    def _to_entry_vendor(self, record: ExternalRecord) -> MaterializedEntry:
        vendor = record.payload or {}
        company = vendor.get("CompanyName") or vendor.get("DisplayName") or ""
        cf = self._provenance_custom_fields(record)
        cf.update(
            {
                "company_name": company,
                "email": _norm_email(vendor.get("PrimaryEmailAddr")),
                "balance": vendor.get("Balance"),
                "account_number": vendor.get("AcctNum") or "",
            }
        )
        return MaterializedEntry(
            title=company or f"Vendor {record.external_id}",
            body="",
            entry_type_key="qb_vendor",
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

    def _to_entry_bill(self, record: ExternalRecord) -> MaterializedEntry:
        bill = record.payload or {}
        vendor_ref = bill.get("VendorRef") or {}
        vendor_name = vendor_ref.get("name") or ""
        doc_number = str(bill.get("DocNumber") or "")
        title = f"Bill {doc_number} — {vendor_name}".strip(" —")
        cf = self._provenance_custom_fields(record)
        cf.update(
            {
                "bill_number": doc_number,
                "qb_vendor_id": str(vendor_ref.get("value") or ""),
                "qb_vendor_name": vendor_name,
                "total_amount": bill.get("TotalAmt"),
                "balance": bill.get("Balance"),
                "txn_date": bill.get("TxnDate") or "",
                "due_date": bill.get("DueDate") or "",
            }
        )
        return MaterializedEntry(
            title=title or f"Bill {record.external_id}",
            body="",
            entry_type_key="qb_bill",
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

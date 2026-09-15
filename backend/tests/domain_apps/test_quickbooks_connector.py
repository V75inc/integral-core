"""Phase 18 — QuickBooksConnector unit tests.

Covers ``sync_pull`` (backfill + incremental), ``to_entry`` for each entity,
``idempotency_key_for`` realm/entity/id folding, ``_ensure_fresh_token``
rotation + persistence, and ``invalid_grant`` clean abort.

All tests use the ``quickbooks_mock`` fixture transport — no test ever hits
Intuit's live API (per the §7.2 mock-only contract).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx
import pytest

from app.agentive.connectors.quickbooks import (
    QuickBooksConnector,
    _derive_invoice_status,
)
from app.services.connectors import ExternalRecord
from tests.domain_apps.fixtures.quickbooks_mock import (
    REALM_ID,
    quickbooks_invalid_grant_transport,
    quickbooks_mock_transport,
)

# ---------------------------------------------------------------------------
# Fixture: an in-memory Connector stand-in (no DB roundtrip)
# ---------------------------------------------------------------------------


class _FakeConnector:
    """Light Connector stand-in for unit tests of the subclass.

    Mirrors the attributes the subclass touches (id, auth_state, sync_cursor,
    save). save() captures call counts so tests can assert rotation persistence.
    """

    def __init__(self, *, auth_state: Dict[str, Any], sync_cursor: str = ""):
        self.id = "fake-conn-1"
        self.auth_state: Dict[str, Any] = dict(auth_state)
        self.sync_cursor: str = sync_cursor
        self.save_call_count = 0

    async def save(self) -> None:
        self.save_call_count += 1


def _fresh_auth_state(**overrides: Any) -> Dict[str, Any]:
    """A non-expired auth_state — _ensure_fresh_token leaves it alone."""
    base = {
        "realm_id": REALM_ID,
        "access_token": "current-access",
        "refresh_token": "current-refresh",
        "access_token_expires_at": (
            datetime.now(timezone.utc) + timedelta(hours=1)
        ).isoformat(),
        "environment": "production",
    }
    base.update(overrides)
    return base


def _near_expiry_auth_state() -> Dict[str, Any]:
    """auth_state whose access_token is within the 5-minute skew window."""
    return _fresh_auth_state(
        access_token_expires_at=(
            datetime.now(timezone.utc) + timedelta(seconds=60)
        ).isoformat()
    )


@pytest.fixture(autouse=True)
def _qbo_env(monkeypatch):
    """Stub deployment secrets so the OAuth helper does not refuse."""
    monkeypatch.setenv("QUICKBOOKS_CLIENT_ID", "mock-client-id")
    monkeypatch.setenv("QUICKBOOKS_CLIENT_SECRET", "mock-client-secret")
    monkeypatch.setenv("SECRET_KEY", "mock-secret-key")
    yield


# ---------------------------------------------------------------------------
# idempotency_key_for
# ---------------------------------------------------------------------------


def test_idempotency_key_folds_realm_entity_and_id():
    conn = QuickBooksConnector()
    inv = ExternalRecord(
        external_id="42",
        payload={"_qb_entity": "Invoice", "_realm_id": "rA"},
    )
    bill = ExternalRecord(
        external_id="42",
        payload={"_qb_entity": "Bill", "_realm_id": "rA"},
    )
    inv_other_realm = ExternalRecord(
        external_id="42",
        payload={"_qb_entity": "Invoice", "_realm_id": "rB"},
    )
    k1 = conn.idempotency_key_for(inv)
    k2 = conn.idempotency_key_for(bill)
    k3 = conn.idempotency_key_for(inv_other_realm)
    assert len({k1, k2, k3}) == 3


def test_idempotency_key_falls_back_when_payload_lacks_realm():
    """Defensive fallback — should never happen in real sync, but stable if it does."""
    conn = QuickBooksConnector()
    rec = ExternalRecord(external_id="42", payload={})
    key = conn.idempotency_key_for(rec)
    # Default base-class hash: SHA-256("quickbooks:42")
    assert isinstance(key, str)
    assert len(key) == 64


def test_conflict_policy_is_last_write_wins():
    assert QuickBooksConnector.conflict_policy == "last_write_wins"


# ---------------------------------------------------------------------------
# to_entry mapping per entity
# ---------------------------------------------------------------------------


def _wrap(entity: str, record_dict: Dict[str, Any]) -> ExternalRecord:
    payload = dict(record_dict)
    payload["_qb_entity"] = entity
    payload["_realm_id"] = REALM_ID
    return ExternalRecord(
        external_id=str(record_dict.get("Id") or ""),
        payload=payload,
        updated_at=(record_dict.get("MetaData") or {}).get("LastUpdatedTime"),
    )


def test_to_entry_invoice_maps_documented_fields():
    conn = QuickBooksConnector()
    # DueDate intentionally far in the future so the derived status is
    # ``open`` regardless of the test wall clock.
    rec = _wrap(
        "Invoice",
        {
            "Id": "1001",
            "SyncToken": "0",
            "DocNumber": "INV-0001",
            "TxnDate": "2030-04-01",
            "DueDate": "2030-05-01",
            "TotalAmt": 5000.00,
            "Balance": 5000.00,
            "CustomerRef": {"value": "201", "name": "Contoso Inc."},
            "MetaData": {"LastUpdatedTime": "2030-04-01T10:00:00Z"},
        },
    )
    m = conn.to_entry(rec)
    assert m.entry_type_key == "qb_invoice"
    assert "Contoso" in m.title
    cf = m.custom_fields
    assert cf["invoice_number"] == "INV-0001"
    assert cf["qb_customer_name"] == "Contoso Inc."
    assert cf["total_amount"] == 5000.0
    assert cf["balance"] == 5000.0
    assert cf["status"] == "open"
    assert cf["_source_system"] == "quickbooks"
    assert cf["_source_version"] == "0"  # SyncToken
    assert cf["_qb_entity"] == "Invoice"
    assert cf["_realm_id"] == REALM_ID


def test_to_entry_invoice_status_paid_when_balance_zero():
    conn = QuickBooksConnector()
    rec = _wrap(
        "Invoice",
        {
            "Id": "1002",
            "Balance": 0.00,
            "TotalAmt": 100.0,
            "CustomerRef": {"value": "201", "name": "Contoso"},
        },
    )
    m = conn.to_entry(rec)
    assert m.custom_fields["status"] == "paid"


def test_to_entry_purchase_maps_vendor_and_line_category():
    conn = QuickBooksConnector()
    rec = _wrap(
        "Purchase",
        {
            "Id": "2001",
            "SyncToken": "0",
            "PaymentType": "Cash",
            "TxnDate": "2026-04-05",
            "TotalAmt": 124.50,
            "AccountRef": {"value": "1", "name": "Checking"},
            "EntityRef": {"value": "301", "name": "Acme Supplies"},
            "Line": [
                {
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": "10", "name": "Office Supplies"},
                    }
                }
            ],
        },
    )
    m = conn.to_entry(rec)
    assert m.entry_type_key == "qb_expense"
    cf = m.custom_fields
    assert cf["payment_type"] == "cash"
    assert cf["qb_vendor_name"] == "Acme Supplies"
    assert cf["account"] == "Checking"
    assert cf["category"] == "Office Supplies"
    assert cf["total_amount"] == 124.5


def test_to_entry_customer_maps_company_and_normalizes_email():
    conn = QuickBooksConnector()
    rec = _wrap(
        "Customer",
        {
            "Id": "201",
            "CompanyName": "Contoso Inc.",
            "PrimaryEmailAddr": {"Address": " AP@Contoso.Example "},
            "PrimaryPhone": {"FreeFormNumber": "+1-555-0100"},
            "Balance": 5000.0,
            "MetaData": {"LastUpdatedTime": "2026-04-01T10:00:00Z"},
        },
    )
    m = conn.to_entry(rec)
    assert m.entry_type_key == "qb_customer"
    assert m.title == "Contoso Inc."
    cf = m.custom_fields
    assert cf["company_name"] == "Contoso Inc."
    assert cf["email"] == "ap@contoso.example"  # normalized
    assert cf["phone"] == "+1-555-0100"
    assert cf["crm_link_status"] == "unmatched"


def test_to_entry_vendor_and_bill_minimal_shapes():
    conn = QuickBooksConnector()
    vendor = conn.to_entry(
        _wrap(
            "Vendor",
            {
                "Id": "301",
                "CompanyName": "Acme Supplies",
                "PrimaryEmailAddr": {"Address": "orders@acme.example"},
                "AcctNum": "ACME-001",
                "Balance": 250.0,
            },
        )
    )
    assert vendor.entry_type_key == "qb_vendor"
    assert vendor.custom_fields["account_number"] == "ACME-001"

    bill = conn.to_entry(
        _wrap(
            "Bill",
            {
                "Id": "4001",
                "DocNumber": "BILL-0001",
                "TotalAmt": 1450.0,
                "Balance": 1450.0,
                "TxnDate": "2026-04-10",
                "DueDate": "2026-05-10",
                "VendorRef": {"value": "301", "name": "Acme Supplies"},
            },
        )
    )
    assert bill.entry_type_key == "qb_bill"
    cf = bill.custom_fields
    assert cf["bill_number"] == "BILL-0001"
    assert cf["qb_vendor_name"] == "Acme Supplies"
    assert cf["total_amount"] == 1450.0


def test_to_entry_unknown_entity_falls_through_with_warning():
    conn = QuickBooksConnector()
    rec = ExternalRecord(
        external_id="x",
        payload={"_qb_entity": "Estimate", "_realm_id": REALM_ID, "Id": "x"},
    )
    m = conn.to_entry(rec)
    assert m.entry_type_key == ""  # Unknown — surfaces as empty


def test_derive_invoice_status_overdue_when_due_date_past():
    assert (
        _derive_invoice_status({"Balance": 500.0, "DueDate": "2020-01-01"}) == "overdue"
    )


# ---------------------------------------------------------------------------
# _ensure_fresh_token rotation + persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_fresh_token_noop_when_current_token_valid():
    conn = _FakeConnector(auth_state=_fresh_auth_state())
    qb = QuickBooksConnector()
    transport = quickbooks_mock_transport()
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await qb._ensure_fresh_token(conn, http_client=client)
    assert ok is True
    # Token unchanged — no save call.
    assert conn.save_call_count == 0
    assert conn.auth_state["access_token"] == "current-access"


@pytest.mark.asyncio
async def test_ensure_fresh_token_rotates_and_persists_within_skew_window():
    """Near-expiry token triggers rotation; persisted refresh_token is the rotated one."""
    conn = _FakeConnector(auth_state=_near_expiry_auth_state())
    qb = QuickBooksConnector()
    transport = quickbooks_mock_transport()
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await qb._ensure_fresh_token(conn, http_client=client)
    assert ok is True
    assert conn.save_call_count == 1
    # Mock returns rotated values (mock-access-token-refresh-v2 etc.).
    assert conn.auth_state["access_token"] == "mock-access-token-refresh-v2"
    assert conn.auth_state["refresh_token"] == "mock-refresh-token-refresh-v2"
    # Rotation cleared any prior reauth marker.
    assert "reauth_required" not in conn.auth_state


@pytest.mark.asyncio
async def test_ensure_fresh_token_invalid_grant_sets_reauth_required():
    """invalid_grant on refresh: no crash, reauth_required marker set."""
    conn = _FakeConnector(auth_state=_near_expiry_auth_state())
    qb = QuickBooksConnector()
    transport = quickbooks_invalid_grant_transport()
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await qb._ensure_fresh_token(conn, http_client=client)
    assert ok is False
    assert conn.save_call_count >= 1
    assert conn.auth_state.get("reauth_required") is True


@pytest.mark.asyncio
async def test_ensure_fresh_token_missing_refresh_token_sets_reauth():
    """No refresh_token present — flag reauth, do not crash."""
    auth = _near_expiry_auth_state()
    auth["refresh_token"] = ""
    conn = _FakeConnector(auth_state=auth)
    qb = QuickBooksConnector()
    transport = quickbooks_mock_transport()
    async with httpx.AsyncClient(transport=transport) as client:
        ok = await qb._ensure_fresh_token(conn, http_client=client)
    assert ok is False
    assert conn.auth_state.get("reauth_required") is True


# ---------------------------------------------------------------------------
# sync_pull — backfill + incremental + cursor advance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_pull_backfill_yields_all_five_entity_types():
    """Backfill (empty cursor): pages each entity, yields all five _qb_entity values."""
    qb = QuickBooksConnector()
    transport = quickbooks_mock_transport()
    async with httpx.AsyncClient(transport=transport) as client:
        qb.test_http_client = client
        conn = _FakeConnector(auth_state=_fresh_auth_state())
        records: List[ExternalRecord] = []
        async for rec in qb.sync_pull(connector=conn):
            records.append(rec)
        qb.test_http_client = None

    seen = {r.payload.get("_qb_entity") for r in records}
    assert seen == {"Invoice", "Purchase", "Customer", "Vendor", "Bill"}
    # Cursor advanced after backfill completed.
    assert conn.sync_cursor


@pytest.mark.asyncio
async def test_sync_pull_incremental_uses_cdc_and_yields_changed_subset():
    """Cursor set: /cdc returns subset; backfill query endpoint is not consulted."""
    qb = QuickBooksConnector()
    transport = quickbooks_mock_transport()
    async with httpx.AsyncClient(transport=transport) as client:
        qb.test_http_client = client
        prior_cursor = "2026-05-01T00:00:00Z"
        conn = _FakeConnector(auth_state=_fresh_auth_state(), sync_cursor=prior_cursor)
        records: List[ExternalRecord] = []
        async for rec in qb.sync_pull(connector=conn):
            records.append(rec)
        qb.test_http_client = None

    # CDC payload has one invoice + one customer (see fixture CDC_RESPONSE).
    seen = {r.payload.get("_qb_entity") for r in records}
    assert seen == {"Invoice", "Customer"}
    # Cursor advanced past the prior value.
    assert conn.sync_cursor and conn.sync_cursor != prior_cursor


@pytest.mark.asyncio
async def test_sync_pull_aborts_silently_when_realm_id_missing():
    """No realm_id in auth_state → log + return without API calls."""
    qb = QuickBooksConnector()
    auth = _fresh_auth_state()
    auth.pop("realm_id")
    conn = _FakeConnector(auth_state=auth)
    records: List[ExternalRecord] = []
    async for rec in qb.sync_pull(connector=conn):
        records.append(rec)
    assert records == []


@pytest.mark.asyncio
async def test_sync_pull_aborts_on_reauth_required():
    """Invalid_grant on refresh → connector flagged + pull aborts cleanly."""
    qb = QuickBooksConnector()
    transport = quickbooks_invalid_grant_transport()
    async with httpx.AsyncClient(transport=transport) as client:
        qb.test_http_client = client
        conn = _FakeConnector(auth_state=_near_expiry_auth_state())
        records: List[ExternalRecord] = []
        async for rec in qb.sync_pull(connector=conn):
            records.append(rec)
        qb.test_http_client = None
    assert records == []
    assert conn.auth_state.get("reauth_required") is True

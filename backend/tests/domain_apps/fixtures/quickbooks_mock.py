"""Phase 18 — Intuit QuickBooks Online v3 mock fixtures.

Provides canned JSON payloads for the QuickBooks Online v3 endpoints the
``QuickBooksConnector`` calls, plus an ``httpx.MockTransport`` factory so the
whole suite runs offline (no test ever hits Intuit's live API).

Coverage:
- Query responses for each of the five entities (Invoice / Purchase /
  Customer / Vendor / Bill) shaped as the real ``QueryResponse`` envelope.
- CDC response covering a subset of changed objects for the
  incremental-sync path.
- Token-exchange response (authorization_code).
- Token-refresh response carrying a ROTATED ``refresh_token`` so the
  rotation-persistence test has a real delta to assert.
- ``invalid_grant`` error response for the expired/revoked-refresh-token
  branch.

Fixture customers include at least one whose email matches a CRM
``Account`` fixture (QB-04 linker hit) and one that matches none
(linker miss / ``unmatched``).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

import httpx

# ---------------------------------------------------------------------------
# Constants — Intuit URL prefixes and the test realm id.
# ---------------------------------------------------------------------------

REALM_ID = "9341454794050000"  # mock Intuit "company" id used across fixtures

# Production: https://quickbooks.api.intuit.com
# Sandbox: https://sandbox-quickbooks.api.intuit.com
QBO_BASE = "https://quickbooks.api.intuit.com"
QBO_TOKEN_ENDPOINT = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

# Token exchange response (authorization_code) — canonical Intuit shape.
TOKEN_EXCHANGE_RESPONSE: Dict[str, Any] = {
    "access_token": "mock-access-token-exchange-v1",
    "refresh_token": "mock-refresh-token-exchange-v1",
    "expires_in": 3600,
    "x_refresh_token_expires_in": 8726400,
    "token_type": "bearer",
}

# Token refresh response — the ROTATED refresh_token is a different value
# (Intuit rotates on every refresh; persisting the rotation is critical).
TOKEN_REFRESH_RESPONSE: Dict[str, Any] = {
    "access_token": "mock-access-token-refresh-v2",
    "refresh_token": "mock-refresh-token-refresh-v2",  # NOTE: rotated
    "expires_in": 3600,
    "x_refresh_token_expires_in": 8726400,
    "token_type": "bearer",
}

# Invalid-grant error response (refresh token expired or revoked).
INVALID_GRANT_RESPONSE: Dict[str, Any] = {
    "error": "invalid_grant",
    "error_description": "Token expired or revoked",
}


# ---------------------------------------------------------------------------
# Entity payloads
# ---------------------------------------------------------------------------

INVOICE_QUERY_RESPONSE: Dict[str, Any] = {
    "QueryResponse": {
        "Invoice": [
            {
                "Id": "1001",
                "SyncToken": "0",
                "DocNumber": "INV-0001",
                "TxnDate": "2026-04-01",
                "DueDate": "2026-05-01",
                "TotalAmt": 5000.00,
                "Balance": 5000.00,
                "CustomerRef": {"value": "201", "name": "Contoso Inc."},
                "MetaData": {
                    "CreateTime": "2026-04-01T10:00:00Z",
                    "LastUpdatedTime": "2026-04-01T10:00:00Z",
                },
            },
            {
                "Id": "1002",
                "SyncToken": "1",
                "DocNumber": "INV-0002",
                "TxnDate": "2026-04-15",
                "DueDate": "2026-05-15",
                "TotalAmt": 8200.50,
                "Balance": 0.00,  # paid
                "CustomerRef": {"value": "201", "name": "Contoso Inc."},
                "MetaData": {
                    "CreateTime": "2026-04-15T10:00:00Z",
                    "LastUpdatedTime": "2026-04-30T16:00:00Z",
                },
            },
        ],
        "startPosition": 1,
        "maxResults": 2,
    },
    "time": "2026-05-23T12:00:00Z",
}

PURCHASE_QUERY_RESPONSE: Dict[str, Any] = {
    "QueryResponse": {
        "Purchase": [
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
                        "DetailType": "AccountBasedExpenseLineDetail",
                        "AccountBasedExpenseLineDetail": {
                            "AccountRef": {"value": "10", "name": "Office Supplies"},
                        },
                    },
                ],
                "MetaData": {
                    "CreateTime": "2026-04-05T10:00:00Z",
                    "LastUpdatedTime": "2026-04-05T10:00:00Z",
                },
            },
        ],
        "startPosition": 1,
        "maxResults": 1,
    },
    "time": "2026-05-23T12:00:00Z",
}

CUSTOMER_QUERY_RESPONSE: Dict[str, Any] = {
    "QueryResponse": {
        "Customer": [
            {
                "Id": "201",
                "SyncToken": "0",
                "CompanyName": "Contoso Inc.",
                "DisplayName": "Contoso Inc.",
                "PrimaryEmailAddr": {"Address": "ap@contoso.example"},
                "PrimaryPhone": {"FreeFormNumber": "+1-555-0100"},
                "Balance": 5000.00,
                "Active": True,
                "MetaData": {
                    "CreateTime": "2025-12-01T10:00:00Z",
                    "LastUpdatedTime": "2026-04-01T10:00:00Z",
                },
            },
            {
                "Id": "202",
                "SyncToken": "0",
                "CompanyName": "Fabrikam LLC",
                "DisplayName": "Fabrikam LLC",
                "PrimaryEmailAddr": {"Address": "billing@fabrikam.example"},
                "Balance": 0.00,
                "Active": True,
                "MetaData": {
                    "CreateTime": "2026-01-15T10:00:00Z",
                    "LastUpdatedTime": "2026-01-15T10:00:00Z",
                },
            },
        ],
        "startPosition": 1,
        "maxResults": 2,
    },
    "time": "2026-05-23T12:00:00Z",
}

VENDOR_QUERY_RESPONSE: Dict[str, Any] = {
    "QueryResponse": {
        "Vendor": [
            {
                "Id": "301",
                "SyncToken": "0",
                "CompanyName": "Acme Supplies",
                "DisplayName": "Acme Supplies",
                "PrimaryEmailAddr": {"Address": "orders@acme.example"},
                "AcctNum": "ACME-001",
                "Balance": 250.00,
                "Active": True,
                "MetaData": {
                    "CreateTime": "2025-11-01T10:00:00Z",
                    "LastUpdatedTime": "2026-04-05T10:00:00Z",
                },
            },
        ],
        "startPosition": 1,
        "maxResults": 1,
    },
    "time": "2026-05-23T12:00:00Z",
}

BILL_QUERY_RESPONSE: Dict[str, Any] = {
    "QueryResponse": {
        "Bill": [
            {
                "Id": "4001",
                "SyncToken": "0",
                "DocNumber": "BILL-0001",
                "TxnDate": "2026-04-10",
                "DueDate": "2026-05-10",
                "TotalAmt": 1450.00,
                "Balance": 1450.00,
                "VendorRef": {"value": "301", "name": "Acme Supplies"},
                "MetaData": {
                    "CreateTime": "2026-04-10T10:00:00Z",
                    "LastUpdatedTime": "2026-04-10T10:00:00Z",
                },
            },
        ],
        "startPosition": 1,
        "maxResults": 1,
    },
    "time": "2026-05-23T12:00:00Z",
}

# CDC response — only the changed subset (one updated invoice + one updated
# customer). Used by the incremental-sync test path.
CDC_RESPONSE: Dict[str, Any] = {
    "CDCResponse": [
        {
            "QueryResponse": [
                {
                    "Invoice": [
                        {
                            "Id": "1001",
                            "SyncToken": "1",
                            "DocNumber": "INV-0001",
                            "TxnDate": "2026-04-01",
                            "DueDate": "2026-05-01",
                            "TotalAmt": 5000.00,
                            "Balance": 2500.00,  # partial payment
                            "CustomerRef": {"value": "201", "name": "Contoso Inc."},
                            "MetaData": {
                                "CreateTime": "2026-04-01T10:00:00Z",
                                "LastUpdatedTime": "2026-05-20T10:00:00Z",
                            },
                        },
                    ],
                    "Customer": [
                        {
                            "Id": "201",
                            "SyncToken": "1",
                            "CompanyName": "Contoso Inc.",
                            "DisplayName": "Contoso Inc.",
                            "PrimaryEmailAddr": {"Address": "ap@contoso.example"},
                            "Balance": 2500.00,
                            "Active": True,
                            "MetaData": {
                                "CreateTime": "2025-12-01T10:00:00Z",
                                "LastUpdatedTime": "2026-05-20T10:00:00Z",
                            },
                        },
                    ],
                },
            ],
        },
    ],
    "time": "2026-05-23T12:00:00Z",
}

# Dispatch table — entity name → query response.
_QUERY_BY_ENTITY: Dict[str, Dict[str, Any]] = {
    "Invoice": INVOICE_QUERY_RESPONSE,
    "Purchase": PURCHASE_QUERY_RESPONSE,
    "Customer": CUSTOMER_QUERY_RESPONSE,
    "Vendor": VENDOR_QUERY_RESPONSE,
    "Bill": BILL_QUERY_RESPONSE,
}


def load(name: str) -> Dict[str, Any]:
    """Load a named canned response (deep copy via json round-trip)."""
    table = {
        "invoice_query": INVOICE_QUERY_RESPONSE,
        "purchase_query": PURCHASE_QUERY_RESPONSE,
        "customer_query": CUSTOMER_QUERY_RESPONSE,
        "vendor_query": VENDOR_QUERY_RESPONSE,
        "bill_query": BILL_QUERY_RESPONSE,
        "cdc": CDC_RESPONSE,
        "token_exchange": TOKEN_EXCHANGE_RESPONSE,
        "token_refresh": TOKEN_REFRESH_RESPONSE,
        "invalid_grant": INVALID_GRANT_RESPONSE,
    }
    if name not in table:
        raise KeyError(f"unknown QuickBooks fixture {name!r}")
    return json.loads(json.dumps(table[name]))


# ---------------------------------------------------------------------------
# httpx mock transport
# ---------------------------------------------------------------------------


def quickbooks_mock_transport(
    *,
    refresh_returns_invalid_grant: bool = False,
) -> httpx.MockTransport:
    """Return an ``httpx.MockTransport`` covering Intuit's token + v3 endpoints.

    Args:
        refresh_returns_invalid_grant: when True, the token-refresh endpoint
            returns the 400 invalid_grant body so the reauth-required path
            can be exercised.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        # Token endpoint (POST).
        if url == QBO_TOKEN_ENDPOINT:
            # Body is form-urlencoded. Distinguish authorization_code from
            # refresh_token by inspecting the body.
            try:
                body = request.content.decode("utf-8", errors="ignore")
            except Exception:
                body = ""
            if "grant_type=refresh_token" in body:
                if refresh_returns_invalid_grant:
                    return httpx.Response(400, json=INVALID_GRANT_RESPONSE)
                return httpx.Response(200, json=TOKEN_REFRESH_RESPONSE)
            # default — authorization_code
            return httpx.Response(200, json=TOKEN_EXCHANGE_RESPONSE)

        # Query endpoint: GET .../v3/company/{realmId}/query?query=SELECT * FROM <Entity>
        if "/query" in url and "SELECT" in url.upper():
            for entity in _QUERY_BY_ENTITY:
                # Case-insensitive entity match.
                if (
                    f"FROM%20{entity}".lower() in url.lower()
                    or f"from {entity.lower()}" in url.lower()
                    or f"from {entity}".lower() in url.lower()
                ):
                    return httpx.Response(200, json=_QUERY_BY_ENTITY[entity])
            return httpx.Response(
                200, json={"QueryResponse": {}, "time": "2026-05-23T12:00:00Z"}
            )

        # CDC endpoint: GET .../v3/company/{realmId}/cdc?entities=...&changedSince=...
        if "/cdc" in url:
            return httpx.Response(200, json=CDC_RESPONSE)

        # Unknown — surface as 404 so tests trip on accidental real-API hits.
        return httpx.Response(
            404,
            json={
                "Fault": {
                    "Error": [
                        {
                            "Message": "Unmocked QuickBooks endpoint",
                            "Detail": url,
                        }
                    ],
                }
            },
        )

    return httpx.MockTransport(handler)


def quickbooks_invalid_grant_transport() -> httpx.MockTransport:
    """Shortcut: a transport whose refresh-grant returns invalid_grant."""
    return quickbooks_mock_transport(refresh_returns_invalid_grant=True)

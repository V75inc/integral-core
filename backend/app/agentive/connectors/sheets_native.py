"""Native Google Sheets connector (sheets_native).

Replaces the hosted ``google_sheets`` MCP mount with direct Sheets v4
(+ Drive v3 for spreadsheet discovery) REST calls using the connector's
own OAuth tokens. Two surfaces in one module:

1. ``SheetsNativeConnector`` — pull-only ``SyncConnector`` mirror
   (spreadsheets → ``spreadsheet`` Entries, ``mirror_only``; Sheets is
   the source of truth).
2. ``TOOL_SPECS`` + ``call_tool`` — live CRUD tools registered per
   workspace-connector and invoked through
   ``app.agentive.connectors.native_google_proxy`` (reads run direct,
   writes stage for bless via the ``native_tool_call`` staged kind).

Invariants: I-CON-01 (no provenance writes), I-CON-03 (single-slug
registration), D-08 / I-CON-05 (agentive-gated import). Tokens never
appear in log lines.

Sheets API: https://sheets.googleapis.com/v4/spreadsheets
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from app.agentive.connectors.drive_native import (
    DRIVE_API_BASE,
    NativeToolError,
    _google_message,
    _headers,
)
from app.services.connectors import (
    ConflictPolicy,
    ExternalRecord,
    MaterializedEntry,
    SyncConnector,
    register_sync_connector,
)

logger = logging.getLogger(__name__)

SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"

_SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"

#: Value reads are capped (context + host protection).
_VALUES_ROW_LIMIT = 1000
_VALUES_COL_LIMIT = 52  # A..Z


class _SheetsToolError(NativeToolError):
    """Sheets-flavored NativeToolError (same mapping contract as Drive)."""


async def _raise_for_google(resp: httpx.Response, *, what: str) -> Dict[str, Any]:
    if resp.status_code < 400:
        try:
            return resp.json() if resp.content else {}
        except Exception:
            return {}
    try:
        body = resp.json()
    except Exception:
        body = None
    detail = _google_message(body)
    if resp.status_code == 401:
        raise _SheetsToolError(
            f"{what}: Google rejected the access token (re-authorize the connector)"
            + (f": {detail}" if detail else ""),
            "auth",
        )
    if resp.status_code == 404:
        raise _SheetsToolError(
            f"{what}: not found" + (f": {detail}" if detail else ""),
            "not_found",
        )
    raise _SheetsToolError(
        f"{what}: Google Sheets API returned {resp.status_code}"
        + (f": {detail}" if detail else ""),
        "remote_error",
    )


# ---------------------------------------------------------------------------
# Live CRUD tools
# ---------------------------------------------------------------------------

TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "list_spreadsheets",
        "write": False,
        "description": "List the user's Google spreadsheets, recently modified first.",
        "input_schema": {
            "type": "object",
            "properties": {"page_size": {"type": "integer", "default": 20}},
        },
    },
    {
        "name": "get_spreadsheet",
        "write": False,
        "description": (
            "Get spreadsheet metadata: title, sheets/tabs, grid properties, "
            "named ranges. Set include_grid_data only for small sheets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "include_grid_data": {"type": "boolean", "default": False},
            },
            "required": ["spreadsheet_id"],
        },
    },
    {
        "name": "get_values",
        "write": False,
        "description": "Read values from a range (e.g. 'Sheet1!A1:D50'). Large ranges are capped.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range": {"type": "string"},
            },
            "required": ["spreadsheet_id", "range"],
        },
    },
    {
        "name": "create_spreadsheet",
        "write": True,
        "description": "Create a new spreadsheet with a title and optional sheet tabs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "sheet_titles": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_values",
        "write": True,
        "description": "Write values into a range (e.g. 'Sheet1!A1:B2'), overwriting what is there.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range": {"type": "string"},
                "values": {"type": "array"},
            },
            "required": ["spreadsheet_id", "range", "values"],
        },
    },
    {
        "name": "append_values",
        "write": True,
        "description": "Append rows after the last row of a table range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range": {"type": "string"},
                "values": {"type": "array"},
            },
            "required": ["spreadsheet_id", "range", "values"],
        },
    },
    {
        "name": "clear_values",
        "write": True,
        "description": "Clear values (keep formatting) from a range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range": {"type": "string"},
            },
            "required": ["spreadsheet_id", "range"],
        },
    },
    {
        "name": "batch_update",
        "write": True,
        "description": (
            "Structural edits via the spreadsheets.batchUpdate API "
            "(insert/delete rows/columns, add sheets, format). Takes the raw "
            "'requests' array — prefer the value tools for cell data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "requests": {"type": "array"},
            },
            "required": ["spreadsheet_id", "requests"],
        },
    },
]


def _require_id(args: Dict[str, Any], tool: str) -> str:
    sid = str(args.get("spreadsheet_id") or "").strip()
    if not sid:
        raise _SheetsToolError(f"{tool}: spreadsheet_id is required", "bad_args")
    return sid


def _cap_values(values: Any) -> tuple[list, bool]:
    rows = list(values or [])
    truncated = len(rows) > _VALUES_ROW_LIMIT
    rows = rows[:_VALUES_ROW_LIMIT]
    capped: list = []
    for row in rows:
        if isinstance(row, list):
            capped.append(row[:_VALUES_COL_LIMIT])
        else:
            capped.append(row)
    return capped, truncated


async def _t_list_spreadsheets(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    from app.agentive.connectors import drive_native as _drive

    page_size = max(1, min(int(args.get("page_size") or 20), 100))
    resp = await http.get(
        f"{DRIVE_API_BASE}/files",
        headers=_headers(access_token),
        params={
            "q": f"mimeType='{_SPREADSHEET_MIME}' and trashed=false",
            "fields": "files(id,name,modifiedTime,webViewLink)",
            "orderBy": "modifiedTime desc",
            "pageSize": page_size,
        },
    )
    body = await _drive._raise_for_google(resp, what="list_spreadsheets")
    return {"spreadsheets": body.get("files") or []}


async def _t_get_spreadsheet(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    sid = _require_id(args, "get_spreadsheet")
    params: Dict[str, Any] = {
        "fields": "spreadsheetId,properties,sheets.properties,namedRanges"
    }
    if args.get("include_grid_data") is True:
        params = {"includeGridData": "true"}
    resp = await http.get(
        f"{SHEETS_API_BASE}/{sid}", headers=_headers(access_token), params=params
    )
    return await _raise_for_google(resp, what="get_spreadsheet")


async def _t_get_values(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    from urllib.parse import quote

    sid = _require_id(args, "get_values")
    rng = str(args.get("range") or "").strip()
    if not rng:
        raise _SheetsToolError("get_values: range is required", "bad_args")
    resp = await http.get(
        f"{SHEETS_API_BASE}/{sid}/values/{quote(rng, safe='')}",
        headers=_headers(access_token),
        params={"majorDimension": "ROWS"},
    )
    body = await _raise_for_google(resp, what="get_values")
    values, truncated = _cap_values(body.get("values"))
    return {"range": body.get("range") or rng, "values": values, "truncated": truncated}


async def _t_create_spreadsheet(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    title = str(args.get("title") or "").strip()
    if not title:
        raise _SheetsToolError("create_spreadsheet: title is required", "bad_args")
    body: Dict[str, Any] = {"properties": {"title": title}}
    tabs = args.get("sheet_titles") or []
    if isinstance(tabs, list) and tabs:
        body["sheets"] = [
            {"properties": {"title": str(t)[:100]}}
            for t in tabs[:25]
            if str(t or "").strip()
        ]
    resp = await http.post(
        SHEETS_API_BASE,
        headers={**_headers(access_token), "Content-Type": "application/json"},
        json=body,
        params={"fields": "spreadsheetId,properties,sheets.properties,spreadsheetUrl"},
    )
    return await _raise_for_google(resp, what="create_spreadsheet")


async def _t_update_values(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    from urllib.parse import quote

    sid = _require_id(args, "update_values")
    rng = str(args.get("range") or "").strip()
    if not rng:
        raise _SheetsToolError("update_values: range is required", "bad_args")
    values = args.get("values")
    if not isinstance(values, list) or not values:
        raise _SheetsToolError(
            "update_values: values must be a non-empty array of rows", "bad_args"
        )
    resp = await http.put(
        f"{SHEETS_API_BASE}/{sid}/values/{quote(rng, safe='')}",
        headers={**_headers(access_token), "Content-Type": "application/json"},
        params={"valueInputOption": "USER_ENTERED"},
        json={"range": rng, "majorDimension": "ROWS", "values": values},
    )
    return await _raise_for_google(resp, what="update_values")


async def _t_append_values(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    from urllib.parse import quote

    sid = _require_id(args, "append_values")
    rng = str(args.get("range") or "").strip()
    if not rng:
        raise _SheetsToolError("append_values: range is required", "bad_args")
    values = args.get("values")
    if not isinstance(values, list) or not values:
        raise _SheetsToolError(
            "append_values: values must be a non-empty array of rows", "bad_args"
        )
    resp = await http.post(
        f"{SHEETS_API_BASE}/{sid}/values/{quote(rng, safe='')}:append",
        headers={**_headers(access_token), "Content-Type": "application/json"},
        params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
        json={"range": rng, "majorDimension": "ROWS", "values": values},
    )
    return await _raise_for_google(resp, what="append_values")


async def _t_clear_values(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    from urllib.parse import quote

    sid = _require_id(args, "clear_values")
    rng = str(args.get("range") or "").strip()
    if not rng:
        raise _SheetsToolError("clear_values: range is required", "bad_args")
    resp = await http.post(
        f"{SHEETS_API_BASE}/{sid}/values/{quote(rng, safe='')}:clear",
        headers={**_headers(access_token), "Content-Type": "application/json"},
        json={},
    )
    return await _raise_for_google(resp, what="clear_values")


async def _t_batch_update(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    sid = _require_id(args, "batch_update")
    requests = args.get("requests")
    if not isinstance(requests, list) or not requests:
        raise _SheetsToolError(
            "batch_update: requests must be a non-empty array", "bad_args"
        )
    if len(requests) > 100:
        raise _SheetsToolError(
            "batch_update: at most 100 requests per call", "bad_args"
        )
    resp = await http.post(
        f"{SHEETS_API_BASE}/{sid}:batchUpdate",
        headers={**_headers(access_token), "Content-Type": "application/json"},
        json={"requests": requests},
    )
    return await _raise_for_google(resp, what="batch_update")


_TOOL_FNS = {
    "list_spreadsheets": _t_list_spreadsheets,
    "get_spreadsheet": _t_get_spreadsheet,
    "get_values": _t_get_values,
    "create_spreadsheet": _t_create_spreadsheet,
    "update_values": _t_update_values,
    "append_values": _t_append_values,
    "clear_values": _t_clear_values,
    "batch_update": _t_batch_update,
}


async def call_tool(
    tool_name: str,
    args: Dict[str, Any],
    *,
    access_token: str,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Invoke one Sheets tool against Sheets v4 with a fresh access token."""
    fn = _TOOL_FNS.get(tool_name)
    if fn is None:
        raise _SheetsToolError(f"unknown Sheets tool {tool_name!r}", "unknown_tool")
    owns = http_client is None
    http = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        return await fn(dict(args or {}), access_token=access_token, http=http)
    finally:
        if owns:
            await http.aclose()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@register_sync_connector("sheets_native")
class SheetsNativeConnector(SyncConnector):
    """Pull-only spreadsheet mirror (Sheets is source of truth)."""

    slug: str = "sheets_native"
    conflict_policy: ConflictPolicy = "mirror_only"

    _MAX_PAGES: int = 10
    _PAGE_SIZE: int = 100

    # Test seam — production callers leave this None.
    test_http_client: Optional[httpx.AsyncClient] = None

    def _http_client(self) -> httpx.AsyncClient:
        if self.test_http_client is not None:
            return self.test_http_client
        return httpx.AsyncClient(timeout=30.0)

    async def sync_pull(self, *, connector: Any) -> AsyncIterator[ExternalRecord]:
        """Yield one ExternalRecord per non-trashed spreadsheet."""
        from app.services.connectors.google_oauth import ensure_fresh_token

        client = self._http_client()
        owns_client = self.test_http_client is None
        try:
            ok = await ensure_fresh_token(connector, http_client=client)
            if not ok:
                logger.warning(
                    "sheets_native sync: reauth required for connector %s; aborting",
                    getattr(connector, "id", "<unknown>"),
                )
                return
            access_token = (connector.auth_state or {}).get("access_token") or ""
            page_token: Optional[str] = None
            for _ in range(self._MAX_PAGES):
                params: Dict[str, Any] = {
                    "q": f"mimeType='{_SPREADSHEET_MIME}' and trashed=false",
                    "fields": "files(id,name,modifiedTime,webViewLink),nextPageToken",
                    "orderBy": "modifiedTime desc",
                    "pageSize": self._PAGE_SIZE,
                }
                if page_token:
                    params["pageToken"] = page_token
                resp = await client.get(
                    f"{DRIVE_API_BASE}/files",
                    headers=_headers(access_token),
                    params=params,
                )
                body = await _raise_for_google(resp, what="sheets_native sync_pull")
                for item in body.get("files") or []:
                    sid = str(item.get("id") or "")
                    if not sid:
                        continue
                    yield ExternalRecord(
                        external_id=sid,
                        payload=item,
                        updated_at=item.get("modifiedTime"),
                    )
                page_token = body.get("nextPageToken") or ""
                if not page_token:
                    break
            connector.sync_cursor = _now_iso()
            await connector.save()
        finally:
            if owns_client:
                await client.aclose()

    def to_entry(self, record: ExternalRecord) -> MaterializedEntry:
        item = record.payload or {}
        name = str(item.get("name") or f"Spreadsheet {record.external_id}")
        cf = {
            "_source_system": "sheets_native",
            "_source_version": str(item.get("modifiedTime") or ""),
            "_last_synced_at": _now_iso(),
            "spreadsheet_id": str(item.get("id") or record.external_id),
            "modified_time": str(item.get("modifiedTime") or ""),
            "web_view_link": str(item.get("webViewLink") or ""),
        }
        return MaterializedEntry(
            title=name,
            body="",
            entry_type_key="spreadsheet",
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

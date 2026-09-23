"""Native Google Drive connector (drive_native).

Replaces the hosted ``google_drive`` MCP mount with direct Drive v3 REST
calls using the connector's own OAuth tokens. Two surfaces in one module:

1. ``DriveNativeConnector`` — pull-only ``SyncConnector`` mirror (files →
   ``drive_file`` Entries, ``mirror_only``; Drive is the source of truth).
2. ``TOOL_SPECS`` + ``call_tool`` — live CRUD tools registered per
   workspace-connector and invoked through
   ``app.agentive.connectors.native_google_proxy`` (reads run direct,
   writes stage for bless via the ``native_tool_call`` staged kind).

Invariants:

- **I-CON-01** — connector never touches ``provenance``; runtime writes it.
- **I-CON-03** — single-slug registration via
  ``@register_sync_connector("drive_native")``.
- **D-08 / I-CON-05** — lives in agentive; imported only when
  AGENTIVE_ENABLED=1.
- Tokens never appear in log lines. API error bodies are summarized
  (status + first Google message), never echoed wholesale into health.

Drive API: https://www.googleapis.com/drive/v3
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from app.services.connectors import (
    ConflictPolicy,
    ExternalRecord,
    MaterializedEntry,
    SyncConnector,
    register_sync_connector,
)

logger = logging.getLogger(__name__)

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_API_BASE = "https://www.googleapis.com/upload/drive/v3/files"

#: Google-native mimeTypes cannot be downloaded with alt=media; they must be
#: exported. Map to a text-first export mimeType.
_EXPORT_MIME_BY_GOOGLE_TYPE = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.drawing": "image/png",
    "application/vnd.google-apps.script": "application/vnd.google-apps.script+json",
}

_DEFAULT_FILE_FIELDS = (
    "id,name,mimeType,modifiedTime,createdTime,size,"
    "parents,trashed,webViewLink,iconLink"
)

#: Read results are truncated past this many characters (context + host
#: protection). The payload always carries ``truncated: bool``.
_READ_CHAR_LIMIT = 200_000

#: Refuse to upload past this many bytes through the tool path.
_UPLOAD_BYTE_LIMIT = 2_000_000


def _multipart_related_body(
    metadata: Dict[str, Any], media_bytes: bytes, media_type: str
) -> tuple[bytes, str]:
    """Build a ``multipart/related`` request body for Drive uploads.

    Drive's ``uploadType=multipart`` endpoint requires ``multipart/related``
    (metadata part first as ``application/json``, then the media part) —
    plain ``multipart/form-data`` is rejected with ``400 Bad Request``.
    Returns ``(body, content_type_header)``.
    """
    import json as _json
    import uuid

    boundary = f"integral-{uuid.uuid4().hex}"
    meta_json = _json.dumps(metadata).encode("utf-8")
    buf = b""
    buf += f"--{boundary}\r\n".encode("ascii")
    buf += b"Content-Type: application/json; charset=UTF-8\r\n\r\n"
    buf += meta_json + b"\r\n"
    buf += f"--{boundary}\r\n".encode("ascii")
    buf += f"Content-Type: {media_type}\r\n\r\n".encode("ascii")
    buf += media_bytes + b"\r\n"
    buf += f"--{boundary}--\r\n".encode("ascii")
    return buf, f'multipart/related; boundary="{boundary}"'


class NativeToolError(Exception):
    """A native Google tool call failed (maps to ToolResult / staged error)."""

    def __init__(self, message: str, error_code: str = "remote_error"):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


def _headers(access_token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _google_message(body: Any) -> str:
    """Best-effort first error message out of a Google error envelope."""
    try:
        err = (body or {}).get("error") or {}
        errors = err.get("errors") or []
        if errors and isinstance(errors[0], dict):
            msg = str(errors[0].get("message") or "").strip()
            if msg:
                return msg[:300]
        msg = str(err.get("message") or "").strip()
        if msg:
            return msg[:300]
    except Exception:
        pass
    return ""


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
        raise NativeToolError(
            f"{what}: Google rejected the access token (re-authorize the connector)"
            + (f": {detail}" if detail else ""),
            "auth",
        )
    if resp.status_code == 404:
        raise NativeToolError(
            f"{what}: not found" + (f": {detail}" if detail else ""),
            "not_found",
        )
    raise NativeToolError(
        f"{what}: Google Drive API returned {resp.status_code}"
        + (f": {detail}" if detail else ""),
        "remote_error",
    )


# ---------------------------------------------------------------------------
# Live CRUD tools
# ---------------------------------------------------------------------------

TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "search_files",
        "write": False,
        "description": (
            "Search Google Drive files by name, type, or Drive query. "
            "Returns id, name, mimeType, modifiedTime, and webViewLink per file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free text matched against file names, or a raw Drive 'q' expression (e.g. \"mimeType='application/pdf'\").",
                },
                "page_size": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "list_recent_files",
        "write": False,
        "description": "List recently modified Drive files, newest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "page_size": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "get_file_metadata",
        "write": False,
        "description": "Get metadata (name, type, times, size, parents, links) for one Drive file.",
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
        },
    },
    {
        "name": "read_file_content",
        "write": False,
        "description": (
            "Read a Drive file's text content. Google-native files (Docs, "
            "Sheets, Slides) are auto-exported to text/CSV. Large files are "
            "truncated (see 'truncated' in the result)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
        },
    },
    {
        "name": "download_file_content",
        "write": False,
        "description": (
            "Download a Drive file's content, optionally choosing the export "
            "format for Google-native files (e.g. 'application/pdf'). "
            "Same truncation contract as read_file_content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "export_mime_type": {"type": "string"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "get_file_permissions",
        "write": False,
        "description": "List sharing permissions (role, type, email) for one Drive file.",
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
        },
    },
    {
        "name": "create_file",
        "write": True,
        "description": (
            "Create a Drive file with optional text content (or an empty "
            "folder via mimeType 'application/vnd.google-apps.folder')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "mime_type": {"type": "string"},
                "content": {"type": "string"},
                "parent_id": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "update_file",
        "write": True,
        "description": "Rename a Drive file and/or replace its content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "name": {"type": "string"},
                "content": {"type": "string"},
                "mime_type": {"type": "string"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "trash_file",
        "write": True,
        "description": "Move a Drive file to trash (reversible).",
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
        },
    },
    {
        "name": "delete_file_permanently",
        "write": True,
        "description": "Permanently delete a Drive file. Irreversible.",
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
        },
    },
    {
        "name": "share_file",
        "write": True,
        "description": (
            "Share a Drive file: grant a role (reader/writer/commenter) to a "
            "user, group, domain, or anyone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "role": {"type": "string"},
                "type": {"type": "string"},
                "email": {"type": "string"},
            },
            "required": ["file_id", "role", "type"],
        },
    },
    {
        "name": "delete_permission",
        "write": True,
        "description": "Remove one sharing permission from a Drive file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "permission_id": {"type": "string"},
            },
            "required": ["file_id", "permission_id"],
        },
    },
]


def _build_drive_q(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return "trashed=false"
    # A raw Drive 'q' expression passes through; anything else is a name match.
    if "=" in q or " in " in q or " contains " in q:
        return q if "trashed" in q else f"({q}) and trashed=false"
    escaped = q.replace("\\", "\\\\").replace("'", "\\'")
    return f"name contains '{escaped}' and trashed=false"


async def _t_search_files(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    page_size = max(1, min(int(args.get("page_size") or 20), 100))
    resp = await http.get(
        f"{DRIVE_API_BASE}/files",
        headers=_headers(access_token),
        params={
            "q": _build_drive_q(str(args.get("query") or "")),
            "fields": f"files({_DEFAULT_FILE_FIELDS})",
            "orderBy": "modifiedTime desc",
            "pageSize": page_size,
        },
    )
    body = await _raise_for_google(resp, what="search_files")
    return {"files": body.get("files") or []}


async def _t_list_recent_files(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    page_size = max(1, min(int(args.get("page_size") or 20), 100))
    resp = await http.get(
        f"{DRIVE_API_BASE}/files",
        headers=_headers(access_token),
        params={
            "q": "trashed=false",
            "fields": f"files({_DEFAULT_FILE_FIELDS})",
            "orderBy": "modifiedTime desc",
            "pageSize": page_size,
        },
    )
    body = await _raise_for_google(resp, what="list_recent_files")
    return {"files": body.get("files") or []}


async def _t_get_file_metadata(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    file_id = str(args.get("file_id") or "").strip()
    if not file_id:
        raise NativeToolError("get_file_metadata: file_id is required", "bad_args")
    resp = await http.get(
        f"{DRIVE_API_BASE}/files/{file_id}",
        headers=_headers(access_token),
        params={"fields": _DEFAULT_FILE_FIELDS},
    )
    return await _raise_for_google(resp, what="get_file_metadata")


async def _fetch_content(
    *,
    file_id: str,
    access_token: str,
    http: httpx.AsyncClient,
    export_mime_type: str = "",
    what: str,
) -> Dict[str, Any]:
    meta_resp = await http.get(
        f"{DRIVE_API_BASE}/files/{file_id}",
        headers=_headers(access_token),
        params={"fields": "id,name,mimeType,size"},
    )
    meta = await _raise_for_google(meta_resp, what=what)
    mime = str(meta.get("mimeType") or "")
    export_mime = (export_mime_type or "").strip() or _EXPORT_MIME_BY_GOOGLE_TYPE.get(
        mime, ""
    )
    if mime.startswith("application/vnd.google-apps.") and not export_mime:
        raise NativeToolError(
            f"{what}: '{meta.get('name')}' is a Google-native {mime}; "
            "pass export_mime_type (e.g. 'application/pdf')",
            "bad_args",
        )
    if export_mime:
        resp = await http.get(
            f"{DRIVE_API_BASE}/files/{file_id}/export",
            headers=_headers(access_token),
            params={"mimeType": export_mime},
        )
    else:
        resp = await http.get(
            f"{DRIVE_API_BASE}/files/{file_id}",
            headers=_headers(access_token),
            params={"alt": "media"},
        )
    if resp.status_code >= 400:
        await _raise_for_google(resp, what=what)
    try:
        text = resp.content.decode("utf-8", errors="replace")
    except Exception:
        raise NativeToolError(f"{what}: content is not decodable text", "bad_args")
    truncated = len(text) > _READ_CHAR_LIMIT
    if truncated:
        text = text[:_READ_CHAR_LIMIT]
    return {
        "file_id": file_id,
        "name": meta.get("name"),
        "mime_type": mime,
        "exported_as": export_mime or None,
        "content": text,
        "truncated": truncated,
    }


async def _t_read_file_content(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    file_id = str(args.get("file_id") or "").strip()
    if not file_id:
        raise NativeToolError("read_file_content: file_id is required", "bad_args")
    return await _fetch_content(
        file_id=file_id, access_token=access_token, http=http, what="read_file_content"
    )


async def _t_download_file_content(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    file_id = str(args.get("file_id") or "").strip()
    if not file_id:
        raise NativeToolError("download_file_content: file_id is required", "bad_args")
    return await _fetch_content(
        file_id=file_id,
        access_token=access_token,
        http=http,
        export_mime_type=str(args.get("export_mime_type") or ""),
        what="download_file_content",
    )


async def _t_get_file_permissions(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    file_id = str(args.get("file_id") or "").strip()
    if not file_id:
        raise NativeToolError("get_file_permissions: file_id is required", "bad_args")
    resp = await http.get(
        f"{DRIVE_API_BASE}/files/{file_id}/permissions",
        headers=_headers(access_token),
        params={"fields": "permissions(id,role,type,emailAddress,domain)"},
    )
    body = await _raise_for_google(resp, what="get_file_permissions")
    return {"file_id": file_id, "permissions": body.get("permissions") or []}


async def _t_create_file(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    name = str(args.get("name") or "").strip()
    if not name:
        raise NativeToolError("create_file: name is required", "bad_args")
    mime_type = str(args.get("mime_type") or "text/plain").strip() or "text/plain"
    content = args.get("content")
    metadata: Dict[str, Any] = {"name": name, "mimeType": mime_type}
    parent_id = str(args.get("parent_id") or "").strip()
    if parent_id:
        metadata["parents"] = [parent_id]
    if content is None or mime_type == "application/vnd.google-apps.folder":
        resp = await http.post(
            f"{DRIVE_API_BASE}/files",
            headers={**_headers(access_token), "Content-Type": "application/json"},
            json=metadata,
            params={"fields": _DEFAULT_FILE_FIELDS},
        )
        return await _raise_for_google(resp, what="create_file")
    raw = str(content).encode("utf-8")
    if len(raw) > _UPLOAD_BYTE_LIMIT:
        raise NativeToolError(
            f"create_file: content exceeds {_UPLOAD_BYTE_LIMIT} bytes",
            "payload_too_large",
        )
    body, content_type = _multipart_related_body(metadata, raw, mime_type)
    resp = await http.post(
        UPLOAD_API_BASE,
        headers={**_headers(access_token), "Content-Type": content_type},
        params={"uploadType": "multipart", "fields": _DEFAULT_FILE_FIELDS},
        content=body,
    )
    return await _raise_for_google(resp, what="create_file")


async def _t_update_file(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    file_id = str(args.get("file_id") or "").strip()
    if not file_id:
        raise NativeToolError("update_file: file_id is required", "bad_args")
    metadata: Dict[str, Any] = {}
    if (args.get("name") or "").strip():
        metadata["name"] = str(args["name"]).strip()
    if (args.get("mime_type") or "").strip():
        metadata["mimeType"] = str(args["mime_type"]).strip()
    content = args.get("content")
    if content is None:
        if not metadata:
            raise NativeToolError(
                "update_file: supply name and/or content to change", "bad_args"
            )
        resp = await http.patch(
            f"{DRIVE_API_BASE}/files/{file_id}",
            headers={**_headers(access_token), "Content-Type": "application/json"},
            json=metadata,
            params={"fields": _DEFAULT_FILE_FIELDS},
        )
        return await _raise_for_google(resp, what="update_file")
    raw = str(content).encode("utf-8")
    if len(raw) > _UPLOAD_BYTE_LIMIT:
        raise NativeToolError(
            f"update_file: content exceeds {_UPLOAD_BYTE_LIMIT} bytes",
            "payload_too_large",
        )
    body, content_type = _multipart_related_body(
        metadata, raw, metadata.get("mimeType", "text/plain")
    )
    resp = await http.patch(
        f"{UPLOAD_API_BASE}/{file_id}",
        headers={**_headers(access_token), "Content-Type": content_type},
        params={"uploadType": "multipart", "fields": _DEFAULT_FILE_FIELDS},
        content=body,
    )
    return await _raise_for_google(resp, what="update_file")


async def _t_trash_file(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    file_id = str(args.get("file_id") or "").strip()
    if not file_id:
        raise NativeToolError("trash_file: file_id is required", "bad_args")
    resp = await http.patch(
        f"{DRIVE_API_BASE}/files/{file_id}",
        headers={**_headers(access_token), "Content-Type": "application/json"},
        json={"trashed": True},
        params={"fields": "id,name,trashed"},
    )
    return await _raise_for_google(resp, what="trash_file")


async def _t_delete_file_permanently(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    file_id = str(args.get("file_id") or "").strip()
    if not file_id:
        raise NativeToolError(
            "delete_file_permanently: file_id is required", "bad_args"
        )
    resp = await http.delete(
        f"{DRIVE_API_BASE}/files/{file_id}", headers=_headers(access_token)
    )
    if resp.status_code == 204:
        return {"file_id": file_id, "deleted": True}
    await _raise_for_google(resp, what="delete_file_permanently")
    return {"file_id": file_id, "deleted": True}  # pragma: no cover


async def _t_share_file(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    file_id = str(args.get("file_id") or "").strip()
    role = str(args.get("role") or "").strip()
    perm_type = str(args.get("type") or "").strip()
    if not file_id or not role or not perm_type:
        raise NativeToolError(
            "share_file: file_id, role, and type are required", "bad_args"
        )
    if role not in ("reader", "commenter", "writer", "owner"):
        raise NativeToolError(f"share_file: unknown role {role!r}", "bad_args")
    if perm_type not in ("user", "group", "domain", "anyone"):
        raise NativeToolError(f"share_file: unknown type {perm_type!r}", "bad_args")
    body: Dict[str, Any] = {"role": role, "type": perm_type}
    email = str(args.get("email") or "").strip()
    if email:
        body["emailAddress"] = email
    resp = await http.post(
        f"{DRIVE_API_BASE}/files/{file_id}/permissions",
        headers={**_headers(access_token), "Content-Type": "application/json"},
        json=body,
        params={"fields": "id,role,type,emailAddress"},
    )
    return await _raise_for_google(resp, what="share_file")


async def _t_delete_permission(
    args: Dict[str, Any], *, access_token: str, http: httpx.AsyncClient
) -> Dict[str, Any]:
    file_id = str(args.get("file_id") or "").strip()
    permission_id = str(args.get("permission_id") or "").strip()
    if not file_id or not permission_id:
        raise NativeToolError(
            "delete_permission: file_id and permission_id are required", "bad_args"
        )
    resp = await http.delete(
        f"{DRIVE_API_BASE}/files/{file_id}/permissions/{permission_id}",
        headers=_headers(access_token),
    )
    if resp.status_code == 204:
        return {"file_id": file_id, "permission_id": permission_id, "deleted": True}
    await _raise_for_google(resp, what="delete_permission")
    return {
        "file_id": file_id,
        "permission_id": permission_id,
        "deleted": True,
    }  # pragma: no cover


_TOOL_FNS = {
    "search_files": _t_search_files,
    "list_recent_files": _t_list_recent_files,
    "get_file_metadata": _t_get_file_metadata,
    "read_file_content": _t_read_file_content,
    "download_file_content": _t_download_file_content,
    "get_file_permissions": _t_get_file_permissions,
    "create_file": _t_create_file,
    "update_file": _t_update_file,
    "trash_file": _t_trash_file,
    "delete_file_permanently": _t_delete_file_permanently,
    "share_file": _t_share_file,
    "delete_permission": _t_delete_permission,
}


async def call_tool(
    tool_name: str,
    args: Dict[str, Any],
    *,
    access_token: str,
    http_client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Invoke one Drive tool against Drive v3 with a fresh access token."""
    fn = _TOOL_FNS.get(tool_name)
    if fn is None:
        raise NativeToolError(f"unknown Drive tool {tool_name!r}", "unknown_tool")
    owns = http_client is None
    http = http_client or httpx.AsyncClient(timeout=30.0)
    try:
        return await fn(dict(args or {}), access_token=access_token, http=http)
    finally:
        if owns:
            await http.aclose()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@register_sync_connector("drive_native")
class DriveNativeConnector(SyncConnector):
    """Pull-only Drive file mirror (Drive is source of truth)."""

    slug: str = "drive_native"
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
        """Yield one ExternalRecord per non-trashed Drive file (newest first)."""
        from app.services.connectors.google_oauth import ensure_fresh_token

        client = self._http_client()
        owns_client = self.test_http_client is None
        try:
            ok = await ensure_fresh_token(connector, http_client=client)
            if not ok:
                logger.warning(
                    "drive_native sync: reauth required for connector %s; aborting",
                    getattr(connector, "id", "<unknown>"),
                )
                return
            access_token = (connector.auth_state or {}).get("access_token") or ""
            page_token: Optional[str] = None
            for _ in range(self._MAX_PAGES):
                params: Dict[str, Any] = {
                    "q": "trashed=false",
                    "fields": f"files({_DEFAULT_FILE_FIELDS}),nextPageToken",
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
                body = await _raise_for_google(resp, what="drive_native sync_pull")
                for item in body.get("files") or []:
                    fid = str(item.get("id") or "")
                    if not fid:
                        continue
                    yield ExternalRecord(
                        external_id=fid,
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
        name = str(item.get("name") or f"Drive file {record.external_id}")
        cf = {
            "_source_system": "drive_native",
            "_source_version": str(item.get("modifiedTime") or ""),
            "_last_synced_at": _now_iso(),
            "file_id": str(item.get("id") or record.external_id),
            "mime_type": str(item.get("mimeType") or ""),
            "created_time": str(item.get("createdTime") or ""),
            "modified_time": str(item.get("modifiedTime") or ""),
            "size_bytes": str(item.get("size") or ""),
            "web_view_link": str(item.get("webViewLink") or ""),
            "parent_ids": list(item.get("parents") or []),
        }
        return MaterializedEntry(
            title=name,
            body="",
            entry_type_key="drive_file",
            custom_fields=cf,
            external_updated_at=record.updated_at,
        )

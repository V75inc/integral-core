"""Workspace document type CRUD — cataloged under DocumentTemplates registry."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.models.nodes import DocumentTemplate, DocumentType, Workspace
from app.services.app_graph import ensure_catalog_edge
from app.services.documents.template_lifecycle import (
    get_or_create_document_templates_registry,
)
from app.services.workspace_permissions import (
    can_access_workspace,
    is_workspace_admin_or_owner,
)
from app.utils.time import utc_now_iso

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _fold(value: str) -> str:
    return (value or "").strip().casefold()


def normalize_document_type_code(code: str) -> str:
    """Stable slug: lowercase letters, digits, underscores."""
    raw = (code or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    if not slug or not _CODE_RE.match(slug):
        raise ValueError(
            "Code must start with a letter and contain only lowercase letters, "
            "digits, and underscores"
        )
    return slug


async def _require_workspace_member(user_id: str, workspace_id: str) -> str:
    role = await can_access_workspace(user_id, workspace_id)
    if not role:
        raise PermissionError("Workspace access denied")
    return role


async def _require_template_admin(user_id: str, workspace_id: str) -> None:
    if not await is_workspace_admin_or_owner(user_id, workspace_id):
        raise PermissionError("Workspace admin required to manage document types")


async def _find_type_by_code(workspace_id: str, code: str) -> Optional[DocumentType]:
    rows = await DocumentType.find(
        {"context.workspace_id": workspace_id, "context.code_fold": _fold(code)}
    )
    if not rows:
        return None
    if not isinstance(rows, list):
        rows = [rows]
    return rows[0]


async def list_document_types(
    *,
    user_id: str,
    workspace_id: str,
    module: Optional[str] = None,
) -> List[DocumentType]:
    await _require_workspace_member(user_id, workspace_id)
    query: Dict[str, Any] = {"context.workspace_id": workspace_id}
    if module:
        mod = module.strip()
        # Empty module on a type means workspace-wide; include those plus scoped rows.
        rows = await DocumentType.find(query) or []
        if not isinstance(rows, list):
            rows = [rows]
        return sorted(
            [
                r
                for r in rows
                if not getattr(r, "module", "") or getattr(r, "module", "") == mod
            ],
            key=lambda r: (getattr(r, "name", "") or getattr(r, "code", "")).casefold(),
        )
    rows = await DocumentType.find(query) or []
    if not isinstance(rows, list):
        rows = [rows]
    return sorted(
        rows,
        key=lambda r: (getattr(r, "name", "") or getattr(r, "code", "")).casefold(),
    )


async def get_document_type(
    *, user_id: str, workspace_id: str, type_id: str
) -> Optional[DocumentType]:
    await _require_workspace_member(user_id, workspace_id)
    row = await DocumentType.get(type_id)
    if not row or getattr(row, "workspace_id", "") != workspace_id:
        return None
    return row


async def create_document_type(
    *,
    user_id: str,
    workspace_id: str,
    code: str,
    name: str,
    description: str = "",
    module: str = "",
) -> DocumentType:
    await _require_template_admin(user_id, workspace_id)
    ws = await Workspace.get(workspace_id)
    if not ws:
        raise ValueError("Workspace not found")
    normalized = normalize_document_type_code(code)
    if await _find_type_by_code(workspace_id, normalized):
        raise ValueError(f"Document type code already exists: {normalized}")
    dreg = await get_or_create_document_templates_registry(ws)
    now = utc_now_iso()
    display = (name or "").strip() or normalized.replace("_", " ").title()
    dt = await DocumentType.create(
        workspace_id=workspace_id,
        code=normalized,
        code_fold=_fold(normalized),
        name=display,
        description=(description or "").strip(),
        module=(module or "").strip(),
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    await ensure_catalog_edge(dreg, dt, cataloged_at=now)
    return dt


async def update_document_type(
    *,
    user_id: str,
    workspace_id: str,
    type_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    module: Optional[str] = None,
) -> DocumentType:
    await _require_template_admin(user_id, workspace_id)
    row = await get_document_type(
        user_id=user_id, workspace_id=workspace_id, type_id=type_id
    )
    if not row:
        raise ValueError("Document type not found")
    now = utc_now_iso()
    if name is not None:
        row.name = (name or "").strip() or row.code.replace("_", " ").title()
    if description is not None:
        row.description = (description or "").strip()
    if module is not None:
        row.module = (module or "").strip()
    row.updated_at = now
    await row.save()
    return row


async def delete_document_type(
    *, user_id: str, workspace_id: str, type_id: str
) -> None:
    await _require_template_admin(user_id, workspace_id)
    row = await get_document_type(
        user_id=user_id, workspace_id=workspace_id, type_id=type_id
    )
    if not row:
        raise ValueError("Document type not found")
    code = getattr(row, "code", "")
    templates = await DocumentTemplate.find(
        {
            "context.workspace_id": workspace_id,
            "context.document_type": code,
        }
    )
    if templates:
        if not isinstance(templates, list):
            templates = [templates]
        if templates:
            raise ValueError(
                f"Cannot delete document type in use by {len(templates)} template(s)"
            )
    await row.delete()

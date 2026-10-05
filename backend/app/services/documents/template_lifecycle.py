"""Document template CRUD + draft/publish version lifecycle."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.edges import CATALOGS, CONTAINS, HAS_TEMPLATE_VERSION
from app.models.nodes import (
    DocumentLayout,
    DocumentTemplate,
    DocumentTemplates,
    DocumentTemplateVersion,
    Workspace,
)
from app.services.app_graph import ensure_catalog_edge
from app.services.workspace_permissions import (
    can_access_workspace,
    is_workspace_admin_or_owner,
)
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)

_EMPTY_DOC: Dict[str, Any] = {
    "type": "doc",
    "content": [{"type": "paragraph", "content": []}],
}


def _fold(name: str) -> str:
    return (name or "").strip().casefold()


def _checksum_version_payload(
    editor_document: Dict[str, Any],
    token_metadata: Dict[str, Any],
    required_inputs: List[Dict[str, Any]],
) -> str:
    payload = {
        "editor_document": editor_document or {},
        "token_metadata": token_metadata or {},
        "required_inputs": required_inputs or [],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def get_or_create_document_templates_registry(
    workspace: Workspace,
) -> DocumentTemplates:
    """Return DocumentTemplates registry under a Workspace (I-GRAPH-01)."""
    children = await workspace.nodes(edge=[CONTAINS], node=["DocumentTemplates"])
    if children:
        return children[0]  # type: ignore[return-value]

    # Deterministic id for idempotent re-resolve.
    node_id = f"n.DocumentTemplates.{workspace.id}"
    existing = await DocumentTemplates.get(node_id)
    if existing:
        ctx = await workspace.get_context()
        edges = await ctx.find_edges_between(
            workspace.id, existing.id, edge_class=CONTAINS
        )
        if not edges:
            await workspace.connect(existing, edge=CONTAINS, added_at=utc_now_iso())
        return existing

    now = utc_now_iso()
    dreg = DocumentTemplates(
        id=node_id,
        workspace_id=workspace.id,
        created_at=now,
    )
    await dreg.save()
    await workspace.connect(dreg, edge=CONTAINS, added_at=now)
    return dreg


async def _require_workspace_member(user_id: str, workspace_id: str) -> str:
    role = await can_access_workspace(user_id, workspace_id)
    if not role:
        raise PermissionError("Workspace access denied")
    return role


async def _require_template_admin(user_id: str, workspace_id: str) -> None:
    if not await is_workspace_admin_or_owner(user_id, workspace_id):
        raise PermissionError("Workspace admin required to manage document templates")


async def list_templates(
    *,
    user_id: str,
    workspace_id: str,
    module: Optional[str] = None,
    context_type: Optional[str] = None,
    document_type: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Any]:
    await _require_workspace_member(user_id, workspace_id)
    from app.services.documents.entry_template_store import (
        is_entry_mode,
        list_template_views,
    )

    if await is_entry_mode(workspace_id):
        return await list_template_views(
            workspace_id=workspace_id,
            module=module,
            context_type=context_type,
            document_type=document_type,
            status=status,
        )
    query: Dict[str, Any] = {"context.workspace_id": workspace_id}
    if module:
        query["context.module"] = module
    if context_type:
        query["context.context_type"] = context_type
    if document_type:
        query["context.document_type"] = document_type
    if status:
        query["context.status"] = status
    rows = await DocumentTemplate.find(query) or []
    if not isinstance(rows, list):
        rows = [rows]
    return list(rows)


async def get_template(
    *, user_id: str, workspace_id: str, template_id: str
) -> Optional[Any]:
    await _require_workspace_member(user_id, workspace_id)
    from app.services.documents.entry_template_store import (
        get_template_view,
        is_entry_mode,
    )

    if await is_entry_mode(workspace_id):
        return await get_template_view(workspace_id=workspace_id, template_id=template_id)
    tmpl = await DocumentTemplate.get(template_id)
    if not tmpl or getattr(tmpl, "workspace_id", "") != workspace_id:
        return None
    return tmpl


async def create_template(
    *,
    user_id: str,
    workspace_id: str,
    name: str,
    module: str,
    document_type: str,
    context_type: str,
    category: str = "",
    app_id: str = "",
    track_id: str = "",
    is_default: bool = False,
    editor_document: Optional[Dict[str, Any]] = None,
    token_metadata: Optional[Dict[str, Any]] = None,
    required_inputs: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    await _require_template_admin(user_id, workspace_id)
    from app.services.documents.entry_template_store import (
        create_template_entry,
        is_entry_mode,
    )

    if await is_entry_mode(workspace_id):
        return await create_template_entry(
            user_id=user_id,
            workspace_id=workspace_id,
            name=name,
            module=module,
            document_type=document_type,
            context_type=context_type,
            is_default=is_default,
            consumer_track_id=track_id,
            editor_document=editor_document,
            token_metadata=token_metadata,
            required_inputs=required_inputs,
        )
    ws = await Workspace.get(workspace_id)
    if not ws:
        raise ValueError("Workspace not found")
    dreg = await get_or_create_document_templates_registry(ws)
    now = utc_now_iso()
    name = (name or "").strip() or "Untitled template"
    tmpl = await DocumentTemplate.create(
        workspace_id=workspace_id,
        name=name,
        name_fold=_fold(name),
        module=(module or "").strip(),
        document_type=(document_type or "").strip(),
        category=(category or "").strip(),
        status="draft",
        is_default=bool(is_default),
        current_version_id="",
        context_type=(context_type or "").strip(),
        app_id=(app_id or "").strip(),
        track_id=(track_id or "").strip(),
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    await ensure_catalog_edge(dreg, tmpl, cataloged_at=now)

    doc = editor_document if isinstance(editor_document, dict) else dict(_EMPTY_DOC)
    meta = token_metadata if isinstance(token_metadata, dict) else {"tokens": []}
    inputs = list(required_inputs or [])
    version = await DocumentTemplateVersion.create(
        template_id=tmpl.id,
        workspace_id=workspace_id,
        version_number=1,
        status="draft",
        editor_document=doc,
        token_metadata=meta,
        required_inputs=inputs,
        checksum=_checksum_version_payload(doc, meta, inputs),
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    await tmpl.connect(version, edge=HAS_TEMPLATE_VERSION, attached_at=now)
    tmpl.current_version_id = version.id
    await tmpl.save()

    if is_default:
        await _clear_other_defaults(
            workspace_id=workspace_id,
            module=tmpl.module,
            document_type=tmpl.document_type,
            keep_id=tmpl.id,
        )
    return tmpl


async def _clear_other_defaults(
    *, workspace_id: str, module: str, document_type: str, keep_id: str
) -> None:
    rows = await DocumentTemplate.find(
        {
            "context.workspace_id": workspace_id,
            "context.module": module,
            "context.document_type": document_type,
            "context.is_default": True,
        }
    ) or []
    if not isinstance(rows, list):
        rows = [rows]
    for row in rows:
        if row.id == keep_id:
            continue
        row.is_default = False
        row.updated_at = utc_now_iso()
        await row.save()


async def update_template(
    *,
    user_id: str,
    workspace_id: str,
    template_id: str,
    name: Optional[str] = None,
    module: Optional[str] = None,
    document_type: Optional[str] = None,
    context_type: Optional[str] = None,
    category: Optional[str] = None,
    app_id: Optional[str] = None,
    track_id: Optional[str] = None,
    status: Optional[str] = None,
    is_default: Optional[bool] = None,
) -> Any:
    await _require_template_admin(user_id, workspace_id)
    from app.models.nodes import DocumentTemplate
    from app.services.documents.entry_template_store import (
        _load_template_entry,
        is_entry_mode,
        update_template_entry,
    )

    if await is_entry_mode(workspace_id):
        entry = await _load_template_entry(template_id, workspace_id)
        if entry:
            return await update_template_entry(
                workspace_id=workspace_id,
                template_id=template_id,
                name=name,
                module=module,
                document_type=document_type,
                context_type=context_type,
                track_id=track_id,
                status=status,
                is_default=is_default,
            )
        tmpl = await DocumentTemplate.get(template_id)
        if not tmpl or getattr(tmpl, "workspace_id", "") != workspace_id:
            raise ValueError("Template not found")
    else:
        tmpl = await DocumentTemplate.get(template_id)
        if not tmpl or getattr(tmpl, "workspace_id", "") != workspace_id:
            raise ValueError("Template not found")
    if name is not None:
        tmpl.name = name.strip() or tmpl.name
        tmpl.name_fold = _fold(tmpl.name)
    if module is not None:
        tmpl.module = module.strip()
    if document_type is not None:
        tmpl.document_type = document_type.strip()
    if context_type is not None:
        tmpl.context_type = context_type.strip()
    if category is not None:
        tmpl.category = category.strip()
    if app_id is not None:
        tmpl.app_id = app_id.strip()
    if track_id is not None:
        tmpl.track_id = track_id.strip()
    if status is not None:
        if status not in ("draft", "active", "inactive", "archived"):
            raise ValueError(f"Invalid status: {status}")
        tmpl.status = status  # type: ignore[assignment]
    if is_default is not None:
        tmpl.is_default = bool(is_default)
    tmpl.updated_at = utc_now_iso()
    await tmpl.save()
    if tmpl.is_default:
        await _clear_other_defaults(
            workspace_id=workspace_id,
            module=tmpl.module,
            document_type=tmpl.document_type,
            keep_id=tmpl.id,
        )
    return tmpl


async def archive_template(
    *, user_id: str, workspace_id: str, template_id: str
) -> DocumentTemplate:
    return await update_template(
        user_id=user_id,
        workspace_id=workspace_id,
        template_id=template_id,
        status="archived",
        is_default=False,
    )


async def delete_template(
    *, user_id: str, workspace_id: str, template_id: str
) -> str:
    """Permanently remove a template and its versions.

    Generated documents stay attached to their context entries.
    """
    await _require_template_admin(user_id, workspace_id)
    from app.services.documents.entry_template_store import (
        delete_template_entry,
        is_entry_mode,
    )

    if await is_entry_mode(workspace_id):
        return await delete_template_entry(
            workspace_id=workspace_id, template_id=template_id
        )
    tmpl = await get_template(
        user_id=user_id, workspace_id=workspace_id, template_id=template_id
    )
    if not tmpl:
        raise ValueError("Template not found")
    versions = await list_versions(
        user_id=user_id, workspace_id=workspace_id, template_id=template_id
    )
    for ver in versions:
        await ver.delete(cascade=False)
    await tmpl.delete(cascade=False)
    return template_id


async def find_open_draft(
    *, user_id: str, workspace_id: str, template_id: str
) -> Optional[DocumentTemplateVersion]:
    versions = await list_versions(
        user_id=user_id, workspace_id=workspace_id, template_id=template_id
    )
    return next((v for v in versions if getattr(v, "status", "") == "draft"), None)


async def duplicate_template(
    *, user_id: str, workspace_id: str, template_id: str
) -> Any:
    src = await get_template(
        user_id=user_id, workspace_id=workspace_id, template_id=template_id
    )
    if not src:
        raise ValueError("Template not found")
    ver = None
    if getattr(src, "current_version_id", ""):
        ver = await get_version(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=str(src.current_version_id),
        )
    category = getattr(src, "category", "") or ""
    return await create_template(
        user_id=user_id,
        workspace_id=workspace_id,
        name=f"{getattr(src, 'name', 'Template')} (copy)",
        module=getattr(src, "module", ""),
        document_type=getattr(src, "document_type", ""),
        context_type=getattr(src, "context_type", ""),
        category=category,
        is_default=False,
        editor_document=dict(ver.editor_document) if ver else None,
        token_metadata=dict(ver.token_metadata) if ver else None,
        required_inputs=list(ver.required_inputs) if ver else None,
    )


async def list_versions(
    *, user_id: str, workspace_id: str, template_id: str
) -> List[Any]:
    tmpl = await get_template(
        user_id=user_id, workspace_id=workspace_id, template_id=template_id
    )
    if not tmpl:
        raise ValueError("Template not found")
    from app.services.documents.entry_template_store import (
        is_entry_mode,
        list_version_views,
    )

    if await is_entry_mode(workspace_id):
        return await list_version_views(
            workspace_id=workspace_id, template_id=template_id
        )
    rows = await DocumentTemplateVersion.find(
        {"context.template_id": template_id, "context.workspace_id": workspace_id}
    ) or []
    if not isinstance(rows, list):
        rows = [rows]
    rows.sort(key=lambda v: int(getattr(v, "version_number", 0) or 0), reverse=True)
    return list(rows)


async def get_version(
    *, user_id: str, workspace_id: str, version_id: str
) -> Optional[Any]:
    await _require_workspace_member(user_id, workspace_id)
    from app.services.documents.entry_template_store import (
        get_version_view,
        is_entry_mode,
    )

    if await is_entry_mode(workspace_id):
        return await get_version_view(workspace_id=workspace_id, version_id=version_id)
    ver = await DocumentTemplateVersion.get(version_id)
    if not ver or getattr(ver, "workspace_id", "") != workspace_id:
        return None
    return ver


async def create_version_draft(
    *, user_id: str, workspace_id: str, template_id: str
) -> Any:
    await _require_template_admin(user_id, workspace_id)
    from app.services.documents.entry_template_store import (
        create_version_draft_entry,
        is_entry_mode,
    )

    if await is_entry_mode(workspace_id):
        return await create_version_draft_entry(
            workspace_id=workspace_id,
            template_id=template_id,
            user_id=user_id,
        )
    tmpl = await get_template(
        user_id=user_id, workspace_id=workspace_id, template_id=template_id
    )
    if not tmpl:
        raise ValueError("Template not found")
    versions = await list_versions(
        user_id=user_id, workspace_id=workspace_id, template_id=template_id
    )
    # Prefer copying the current published/draft version.
    source = None
    if tmpl.current_version_id:
        source = await DocumentTemplateVersion.get(tmpl.current_version_id)
    if source is None and versions:
        source = versions[0]
    next_num = (max((int(v.version_number or 0) for v in versions), default=0)) + 1
    now = utc_now_iso()
    doc = dict(source.editor_document) if source else dict(_EMPTY_DOC)
    meta = dict(source.token_metadata) if source else {"tokens": []}
    inputs = list(source.required_inputs) if source else []
    version = await DocumentTemplateVersion.create(
        template_id=tmpl.id,
        workspace_id=workspace_id,
        version_number=next_num,
        status="draft",
        editor_document=doc,
        token_metadata=meta,
        required_inputs=inputs,
        layout_id=getattr(source, "layout_id", "") or "",
        header_footer=dict(getattr(source, "header_footer", None) or {}),
        checksum=_checksum_version_payload(doc, meta, inputs),
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    await tmpl.connect(version, edge=HAS_TEMPLATE_VERSION, attached_at=now)
    return version


async def update_version_draft(
    *,
    user_id: str,
    workspace_id: str,
    version_id: str,
    editor_document: Optional[Dict[str, Any]] = None,
    token_metadata: Optional[Dict[str, Any]] = None,
    required_inputs: Optional[List[Dict[str, Any]]] = None,
    layout_id: Optional[str] = None,
    header_footer: Optional[Dict[str, Any]] = None,
    render_html: Optional[str] = None,
) -> Any:
    await _require_template_admin(user_id, workspace_id)
    from app.services.documents.entry_template_store import (
        is_entry_mode,
        update_version_draft_entry,
    )

    if await is_entry_mode(workspace_id):
        return await update_version_draft_entry(
            workspace_id=workspace_id,
            version_id=version_id,
            editor_document=editor_document,
            token_metadata=token_metadata,
            required_inputs=required_inputs,
            layout_id=layout_id,
            header_footer=header_footer,
            render_html=render_html,
        )
    ver = await get_version(
        user_id=user_id, workspace_id=workspace_id, version_id=version_id
    )
    if not ver:
        raise ValueError("Version not found")
    if ver.status != "draft":
        raise ValueError("Only draft versions can be edited")
    if editor_document is not None:
        ver.editor_document = editor_document
    if token_metadata is not None:
        ver.token_metadata = token_metadata
    if required_inputs is not None:
        ver.required_inputs = required_inputs
    if layout_id is not None:
        ver.layout_id = layout_id
    if header_footer is not None:
        ver.header_footer = header_footer
    if render_html is not None:
        ver.render_html = render_html
    ver.checksum = _checksum_version_payload(
        ver.editor_document or {},
        ver.token_metadata or {},
        list(ver.required_inputs or []),
    )
    ver.updated_at = utc_now_iso()
    await ver.save()
    return ver


async def publish_version(
    *, user_id: str, workspace_id: str, version_id: str
) -> Any:
    await _require_template_admin(user_id, workspace_id)
    from app.services.documents.entry_template_store import (
        is_entry_mode,
        publish_version_entry,
    )

    if await is_entry_mode(workspace_id):
        return await publish_version_entry(
            workspace_id=workspace_id,
            version_id=version_id,
            user_id=user_id,
        )
    ver = await get_version(
        user_id=user_id, workspace_id=workspace_id, version_id=version_id
    )
    if not ver:
        raise ValueError("Version not found")
    if ver.status != "draft":
        raise ValueError("Only draft versions can be published")
    tmpl = await DocumentTemplate.get(ver.template_id)
    if not tmpl or tmpl.workspace_id != workspace_id:
        raise ValueError("Template not found")

    # Supersede prior published versions.
    prior = await DocumentTemplateVersion.find(
        {
            "context.template_id": tmpl.id,
            "context.workspace_id": workspace_id,
            "context.status": "published",
        }
    ) or []
    if not isinstance(prior, list):
        prior = [prior]
    now = utc_now_iso()
    for old in prior:
        if old.id == ver.id:
            continue
        old.status = "superseded"  # type: ignore[assignment]
        old.updated_at = now
        await old.save()

    from app.services.documents.signature_embed import snapshot_pre_embedded_signatures

    snap_doc, snap_meta = await snapshot_pre_embedded_signatures(
        ver.editor_document or {},
        ver.token_metadata or {},
    )
    ver.editor_document = snap_doc
    ver.token_metadata = snap_meta
    ver.status = "published"  # type: ignore[assignment]
    ver.published_at = now
    ver.published_by = user_id
    ver.checksum = _checksum_version_payload(
        ver.editor_document or {},
        ver.token_metadata or {},
        list(ver.required_inputs or []),
    )
    ver.updated_at = now
    await ver.save()

    tmpl.current_version_id = ver.id
    if tmpl.status in ("draft", "inactive"):
        tmpl.status = "active"  # type: ignore[assignment]
    tmpl.updated_at = now
    await tmpl.save()
    return ver


async def restore_version_as_draft(
    *, user_id: str, workspace_id: str, version_id: str
) -> DocumentTemplateVersion:
    """Fork an old version's content into a new draft."""
    await _require_template_admin(user_id, workspace_id)
    source = await get_version(
        user_id=user_id, workspace_id=workspace_id, version_id=version_id
    )
    if not source:
        raise ValueError("Version not found")
    draft = await create_version_draft(
        user_id=user_id, workspace_id=workspace_id, template_id=source.template_id
    )
    return await update_version_draft(
        user_id=user_id,
        workspace_id=workspace_id,
        version_id=draft.id,
        editor_document=dict(source.editor_document or {}),
        token_metadata=dict(source.token_metadata or {}),
        required_inputs=list(source.required_inputs or []),
        layout_id=source.layout_id or "",
        header_footer=dict(source.header_footer or {}),
    )


async def find_active_template_for_workspace(
    *,
    workspace_id: str,
    document_type: str,
    module: Optional[str] = None,
) -> tuple:
    """Pick active template + published version without membership checks."""
    from app.services.documents.entry_template_store import (
        is_entry_mode,
        resolve_for_generate,
    )

    if await is_entry_mode(workspace_id):
        return await resolve_for_generate(
            workspace_id=workspace_id,
            module=module,
            document_type=document_type,
        )
    from app.models.nodes import DocumentTemplate, DocumentTemplateVersion

    query: Dict[str, Any] = {
        "context.workspace_id": workspace_id,
        "context.status": "active",
        "context.document_type": document_type,
    }
    if module:
        query["context.module"] = module
    rows = await DocumentTemplate.find(query) or []
    if not isinstance(rows, list):
        rows = [rows]
    tmpl = next((r for r in rows if r.is_default), None) or (rows[0] if rows else None)
    if not tmpl:
        raise ValueError("Template not found")
    if not tmpl.current_version_id:
        raise ValueError("Template has no published version")
    ver = await DocumentTemplateVersion.get(tmpl.current_version_id)
    if not ver or ver.status == "draft":
        raise ValueError("Template has no published version")
    return tmpl, ver


async def get_active_template_by_id(
    *,
    workspace_id: str,
    template_id: str,
) -> tuple:
    """Load active template + published version by id."""
    from app.services.documents.entry_template_store import (
        is_entry_mode,
        resolve_for_generate,
    )

    if await is_entry_mode(workspace_id):
        return await resolve_for_generate(
            workspace_id=workspace_id,
            template_id=template_id,
        )
    from app.models.nodes import DocumentTemplate, DocumentTemplateVersion

    tmpl = await DocumentTemplate.get(template_id)
    if not tmpl or str(getattr(tmpl, "workspace_id", "") or "") != workspace_id:
        raise ValueError("Template not found")
    if tmpl.status != "active":
        raise ValueError("Template is not active")
    if not tmpl.current_version_id:
        raise ValueError("Template has no published version")
    ver = await DocumentTemplateVersion.get(tmpl.current_version_id)
    if not ver or ver.status == "draft":
        raise ValueError("Template has no published version")
    return tmpl, ver


async def resolve_template_for_generate(
    *,
    user_id: str,
    workspace_id: str,
    template_id: Optional[str] = None,
    template_version_id: Optional[str] = None,
    module: Optional[str] = None,
    document_type: Optional[str] = None,
) -> tuple[Any, Any]:
    """Pick template + version for generation (active current unless version pinned)."""
    await _require_workspace_member(user_id, workspace_id)
    from app.services.documents.entry_template_store import (
        is_entry_mode,
        resolve_for_generate,
    )

    if await is_entry_mode(workspace_id):
        return await resolve_for_generate(
            workspace_id=workspace_id,
            template_id=template_id,
            template_version_id=template_version_id,
            module=module,
            document_type=document_type,
        )
    tmpl: Optional[DocumentTemplate] = None
    ver: Optional[DocumentTemplateVersion] = None

    if template_version_id:
        ver = await get_version(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=template_version_id,
        )
        if not ver:
            raise ValueError("Template version not found")
        tmpl = await DocumentTemplate.get(ver.template_id)
    elif template_id:
        tmpl = await get_template(
            user_id=user_id, workspace_id=workspace_id, template_id=template_id
        )
    elif module and document_type:
        rows = await list_templates(
            user_id=user_id,
            workspace_id=workspace_id,
            module=module,
            document_type=document_type,
            status="active",
        )
        default = next((r for r in rows if r.is_default), None)
        tmpl = default or (rows[0] if rows else None)

    if not tmpl:
        raise ValueError("Template not found")
    if tmpl.status == "archived":
        raise ValueError("Template is archived")

    if ver is None:
        if not tmpl.current_version_id:
            raise ValueError("Template has no published version")
        ver = await DocumentTemplateVersion.get(tmpl.current_version_id)
    if not ver:
        raise ValueError("Template version not found")
    if ver.status == "draft" and not template_version_id:
        raise ValueError("Template has no published version")
    return tmpl, ver


async def list_document_layouts(
    *,
    user_id: str,
    workspace_id: str,
) -> List[DocumentLayout]:
    await _require_template_admin(user_id, workspace_id)
    rows = await DocumentLayout.find({"context.workspace_id": workspace_id}) or []
    if not isinstance(rows, list):
        rows = [rows]
    layouts = list(rows)
    layouts.sort(key=lambda x: (x.name or "", x.id))
    return layouts


async def get_or_create_layout(
    *,
    user_id: str,
    workspace_id: str,
    name: str,
    logo_attachment_id: str = "",
    header_html: str = "",
    footer_html: str = "",
    page_numbers: bool = True,
    page_size: str = "letter",
    margins: Optional[Dict[str, Any]] = None,
) -> DocumentLayout:
    await _require_template_admin(user_id, workspace_id)
    ws = await Workspace.get(workspace_id)
    if not ws:
        raise ValueError("Workspace not found")
    dreg = await get_or_create_document_templates_registry(ws)
    now = utc_now_iso()
    from app.services.documents.document_theme import normalize_margins, normalize_page_size

    layout = await DocumentLayout.create(
        workspace_id=workspace_id,
        name=(name or "").strip() or "Layout",
        logo_attachment_id=logo_attachment_id or "",
        header_html=header_html or "",
        footer_html=footer_html or "",
        page_numbers=bool(page_numbers),
        page_size=normalize_page_size(page_size),
        margins=normalize_margins(margins),
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    await ensure_catalog_edge(dreg, layout, cataloged_at=now)
    return layout


async def update_document_layout(
    *,
    user_id: str,
    workspace_id: str,
    layout_id: str,
    name: Optional[str] = None,
    logo_attachment_id: Optional[str] = None,
    header_html: Optional[str] = None,
    footer_html: Optional[str] = None,
    page_numbers: Optional[bool] = None,
    page_size: Optional[str] = None,
    margins: Optional[Dict[str, Any]] = None,
) -> DocumentLayout:
    await _require_template_admin(user_id, workspace_id)
    layout = await DocumentLayout.get(layout_id)
    if not layout or getattr(layout, "workspace_id", "") != workspace_id:
        raise ValueError("Layout not found")
    from app.services.documents.document_theme import normalize_margins, normalize_page_size

    now = utc_now_iso()
    if name is not None:
        layout.name = (name or "").strip() or layout.name
    if logo_attachment_id is not None:
        layout.logo_attachment_id = logo_attachment_id or ""
    if header_html is not None:
        layout.header_html = header_html or ""
    if footer_html is not None:
        layout.footer_html = footer_html or ""
    if page_numbers is not None:
        layout.page_numbers = bool(page_numbers)
    if page_size is not None:
        layout.page_size = normalize_page_size(page_size)
    if margins is not None:
        layout.margins = normalize_margins(margins)
    layout.updated_at = now
    await layout.save()
    return layout

"""Preview + generate orchestration for the Document Template Platform."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.models.edges import HAS_ATTACHMENT, HAS_GENERATED_DOCUMENT, REFERENCES
from app.models.nodes import Attachment, Entry, GeneratedDocument
from app.services.attachment_storage import get_attachment_storage_service
from app.services.documents.context_resolver import (
    batch_resolve,
    collect_field_keys_from_document,
    token_meta_from_document,
)
from app.services.documents.field_registry import list_fields_for_track
from app.services.documents.output import checksum_bytes, render_output
from app.services.documents.render import merge_document_to_html
from app.services.documents.template_lifecycle import (
    get_version,
    resolve_template_for_generate,
)
from app.services.permissions import resolve_role
from app.utils.time import utc_now_iso

logger = logging.getLogger(__name__)


async def _layout_parts(
    layout_id: str, header_footer: Dict[str, Any]
) -> Dict[str, Any]:
    from app.services.documents.entry_template_store import resolve_layout_parts

    return await resolve_layout_parts(layout_id, header_footer or {})


async def preview_document(
    *,
    user_id: str,
    workspace_id: str,
    template_version_id: Optional[str] = None,
    template_id: Optional[str] = None,
    mode: str = "placeholder",
    context_entry_id: Optional[str] = None,
    input_values: Optional[Dict[str, Any]] = None,
    editor_document: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if template_version_id:
        ver = await get_version(
            user_id=user_id,
            workspace_id=workspace_id,
            version_id=template_version_id,
        )
        if not ver:
            raise ValueError("Template version not found")
        from app.services.documents.entry_template_store import get_template_view

        tmpl = await get_template_view(
            workspace_id=workspace_id, template_id=str(ver.template_id)
        )
        if not tmpl:
            raise ValueError("Template not found")
    else:
        tmpl, ver = await resolve_template_for_generate(
            user_id=user_id,
            workspace_id=workspace_id,
            template_id=template_id,
        )

    if mode == "live":
        if not context_entry_id:
            raise ValueError("context_entry_id required for live preview")
        role = await resolve_role(user_id, "entry", context_entry_id)
        if not role:
            raise PermissionError("Access denied to context entry")
    else:
        role = None

    doc = (
        editor_document
        if isinstance(editor_document, dict) and editor_document
        else (ver.editor_document or {})
    )
    keys = collect_field_keys_from_document(doc, ver.token_metadata or {})
    track_id = str(getattr(tmpl, "track_id", "") or "")
    if track_id:
        await list_fields_for_track(
            workspace_id, track_id, module=str(getattr(tmpl, "module", "") or "")
        )
    context_entry = None
    if mode == "live" and context_entry_id:
        context_entry = await Entry.get(context_entry_id)
    resolved = await batch_resolve(
        workspace_id=workspace_id,
        field_keys=keys,
        context_entry=context_entry,
        context_entry_id=context_entry_id,
        anchor_entry=context_entry,
        caller_role=role,
        input_values=input_values or {},
        mode=mode,
        token_meta=token_meta_from_document(doc, ver.token_metadata),
    )
    layout = await _layout_parts(ver.layout_id or "", ver.header_footer or {})
    html = merge_document_to_html(
        doc,
        resolved["formatted"],
        header_html=layout["header_html"],
        footer_html=layout["footer_html"],
        page_size=layout["page_size"],
        margins=layout["margins"],
        page_numbers=layout["page_numbers"],
        title=tmpl.name or "Document",
        highlight_field_tokens=mode == "placeholder",
    )
    return {
        "html": html,
        "warnings": resolved["warnings"],
        "formatted": resolved["formatted"],
        "values": resolved["values"],
        "template_id": tmpl.id,
        "template_version_id": ver.id,
        "mode": mode,
    }


async def generate_document(
    *,
    user_id: str,
    workspace_id: str,
    context_type: str,
    context_entry_id: str,
    template_id: Optional[str] = None,
    template_version_id: Optional[str] = None,
    module: Optional[str] = None,
    document_type: Optional[str] = None,
    output_format: str = "pdf",
    input_values: Optional[Dict[str, Any]] = None,
) -> GeneratedDocument:
    role = await resolve_role(user_id, "entry", context_entry_id)
    if role not in ("owner", "editor", "commenter", "viewer"):
        raise PermissionError("Access denied to context entry")
    # Generation requires editor+ so viewers cannot mint official docs.
    if role not in ("owner", "editor"):
        raise PermissionError("Editor role required to generate documents")

    entry = await Entry.get(context_entry_id)
    if not entry:
        raise ValueError("Context entry not found")

    tmpl, ver = await resolve_template_for_generate(
        user_id=user_id,
        workspace_id=workspace_id,
        template_id=template_id,
        template_version_id=template_version_id,
        module=module,
        document_type=document_type,
    )
    tmpl_track_id = str(getattr(tmpl, "track_id", "") or "")
    if tmpl_track_id:
        if str(getattr(entry, "track_id", "") or "") != tmpl_track_id:
            raise ValueError("Context entry is not on the template's selected track")
    elif tmpl.context_type and context_type and tmpl.context_type != context_type:
        raise ValueError(
            f"Template context_type {tmpl.context_type!r} does not match "
            f"{context_type!r}"
        )

    # Validate required inputs.
    missing_inputs: List[str] = []
    for inp in ver.required_inputs or []:
        if not isinstance(inp, dict):
            continue
        if not inp.get("required", True):
            continue
        key = str(inp.get("key") or "").strip()
        if key and (input_values or {}).get(key) in (None, ""):
            missing_inputs.append(key)
    if missing_inputs:
        raise ValueError(f"Missing required inputs: {', '.join(missing_inputs)}")

    keys = collect_field_keys_from_document(
        ver.editor_document or {}, ver.token_metadata or {}
    )
    track_id = str(getattr(tmpl, "track_id", "") or "")
    if track_id:
        await list_fields_for_track(
            workspace_id, track_id, module=str(getattr(tmpl, "module", "") or "")
        )
    resolved = await batch_resolve(
        workspace_id=workspace_id,
        field_keys=keys,
        context_entry=entry,
        context_entry_id=context_entry_id,
        anchor_entry=entry,
        caller_role=role,
        input_values=input_values or {},
        mode="live",
        token_meta=token_meta_from_document(
            ver.editor_document or {}, ver.token_metadata
        ),
    )
    if any(w.startswith("block:") for w in resolved["warnings"]):
        raise ValueError(
            "Generation blocked: required field values missing ("
            + ", ".join(w for w in resolved["warnings"] if w.startswith("block:"))
            + ")"
        )

    layout = await _layout_parts(ver.layout_id or "", ver.header_footer or {})
    embed_pre_sigs = output_format != "pdf"
    html = merge_document_to_html(
        ver.editor_document or {},
        resolved["formatted"],
        header_html=layout["header_html"],
        footer_html=layout["footer_html"],
        page_size=layout["page_size"],
        margins=layout["margins"],
        page_numbers=layout["page_numbers"],
        title=tmpl.name or "Document",
        embed_pre_signatures=embed_pre_sigs,
        highlight_field_tokens=False,
    )
    data, mime, ext = render_output(html, output_format, title=tmpl.name or "Document")
    token_meta = ver.token_metadata or {}
    if output_format == "pdf" and data:
        from app.services.documents.signature_embed import apply_pre_embedded_signatures
        from app.services.documents.signature_places import resolve_signature_places

        signature_places = resolve_signature_places(
            html=html,
            editor_document=ver.editor_document or {},
            pdf_bytes=data,
            token_metadata=token_meta,
        )
        data = await apply_pre_embedded_signatures(
            data,
            html=html,
            editor_document=ver.editor_document or {},
            token_metadata=token_meta,
        )
    else:
        signature_places = []
    digest = checksum_bytes(data)
    now = utc_now_iso()

    gd = await GeneratedDocument.create(
        workspace_id=workspace_id,
        template_id=tmpl.id,
        template_version_id=ver.id,
        module=tmpl.module,
        context_type=context_type or tmpl.context_type,
        context_entry_id=context_entry_id,
        generated_by=user_id,
        generated_at=now,
        output_format=output_format,  # type: ignore[arg-type]
        attachment_id="",
        input_values=dict(input_values or {}),
        resolved_snapshot={
            **dict(resolved["formatted"]),
            "signature_places": signature_places,
        },
        checksum=digest,
        status="generated",
        created_at=now,
    )

    safe_name = (tmpl.name or "document").replace("/", "-").strip() or "document"
    filename = f"{safe_name}.{ext}"
    attachment = await Attachment.create(
        filename=filename,
        mime_type=mime,
        size=len(data),
        storage_key="",
        source_type="file",
        external_url="",
        uploaded_by=user_id,
        scan_status="skipped",
        metadata_status="pending",
        owner_kind="entry",
        content_hash=digest,
        created_at=now,
    )
    storage = get_attachment_storage_service()
    try:
        stored = await storage.save_attachment(
            entry_id=context_entry_id,
            attachment_id=attachment.id,
            filename=filename,
            content=data,
        )
    except Exception:
        await attachment.delete()
        await gd.delete()
        raise
    attachment.storage_key = str(stored.get("path") or "")
    await attachment.save()

    await gd.connect(
        attachment,
        edge=HAS_ATTACHMENT,
        attached_at=now,
        attached_by=user_id,
    )
    # Also attach to the context entry for visibility in entry attachments.
    await entry.connect(
        attachment,
        edge=HAS_ATTACHMENT,
        attached_at=now,
        attached_by=user_id,
    )
    if attachment.id not in (entry.attachment_ids or []):
        entry.attachment_ids = list(entry.attachment_ids or []) + [attachment.id]
        await entry.save()

    from app.services.documents.entry_template_store import (
        create_generated_document_entry,
        is_entry_mode,
    )

    if getattr(tmpl, "entry_backed", False) and await is_entry_mode(workspace_id):
        await create_generated_document_entry(
            workspace_id=workspace_id,
            template_id=tmpl.id,
            template_version_id=ver.id,
            module=tmpl.module,
            context_type=context_type or tmpl.context_type,
            context_entry_id=context_entry_id,
            generated_by=user_id,
            output_format=output_format,
            attachment_id=attachment.id,
            input_values=dict(input_values or {}),
            resolved_snapshot={
                **dict(resolved["formatted"]),
                "signature_places": signature_places,
            },
            checksum=digest,
            title=tmpl.name or "Document",
        )
    else:
        await tmpl.connect(gd, edge=HAS_GENERATED_DOCUMENT, generated_at=now)
        try:
            await entry.connect(gd, edge=REFERENCES, field_key="generated_document")
        except Exception:
            logger.exception("generate_document: REFERENCES wire failed")

    gd.attachment_id = attachment.id
    await gd.save()
    return gd


async def list_generated_documents(
    *,
    user_id: str,
    workspace_id: str,
    context_entry_id: Optional[str] = None,
    template_id: Optional[str] = None,
) -> List[GeneratedDocument]:
    from app.services.workspace_permissions import can_access_workspace

    if not await can_access_workspace(user_id, workspace_id):
        raise PermissionError("Workspace access denied")
    query: Dict[str, Any] = {"context.workspace_id": workspace_id}
    if context_entry_id:
        query["context.context_entry_id"] = context_entry_id
        role = await resolve_role(user_id, "entry", context_entry_id)
        if not role:
            raise PermissionError("Access denied to context entry")
    if template_id:
        query["context.template_id"] = template_id
    rows = await GeneratedDocument.find(query) or []
    if not isinstance(rows, list):
        rows = [rows]
    rows.sort(key=lambda g: str(getattr(g, "generated_at", "") or ""), reverse=True)
    return list(rows)

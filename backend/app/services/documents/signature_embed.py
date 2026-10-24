"""Pre-embedded template signatures — snapshot at publish, apply at generate."""

from __future__ import annotations

import asyncio
import base64
import copy
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.services.documents.signature_overlay import overlay_signatures
from app.services.documents.signature_places import (
    pre_embedded_signature_blocks,
    resolve_signature_places,
)

logger = logging.getLogger(__name__)


async def load_png_for_attachment(attachment_id: str) -> Optional[bytes]:
    if not attachment_id:
        return None
    from app.models.nodes import Attachment
    from app.services.attachment_storage import get_attachment_storage_service

    att = await Attachment.get(attachment_id)
    if not att or not att.storage_key:
        return None
    storage = get_attachment_storage_service()
    try:
        return await storage.read_attachment(att.storage_key)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to read signature attachment %s", attachment_id)
        return None


async def snapshot_pre_embedded_signatures(
    editor_document: Dict[str, Any],
    token_metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Copy pre-embedded PNG bytes into token_metadata at publish time."""
    meta = copy.deepcopy(token_metadata or {})
    signatures_meta: List[Dict[str, Any]] = list(meta.get("signatures") or [])
    meta_by_role = {
        str(s.get("role") or ""): s for s in signatures_meta if isinstance(s, dict)
    }

    async def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "signaturePlaceholder":
            attrs = dict(node.get("attrs") or {})
            mode = str(attrs.get("mode") or "runtime").strip().lower()
            role = str(attrs.get("role") or "signature").strip()
            if mode != "pre_embedded":
                return
            spec = dict(meta_by_role.get(role) or {})
            png_b64 = str(
                attrs.get("embeddedPngB64")
                or attrs.get("embedded_png_b64")
                or spec.get("embedded_png_b64")
                or ""
            ).strip()
            att_id = str(
                attrs.get("embeddedAttachmentId")
                or attrs.get("embedded_attachment_id")
                or spec.get("embedded_attachment_id")
                or ""
            ).strip()
            if not png_b64 and att_id:
                png_bytes = await load_png_for_attachment(att_id)
                if png_bytes:
                    png_b64 = base64.b64encode(png_bytes).decode("ascii")
            if png_b64:
                spec.update(
                    {
                        "role": role,
                        "mode": "pre_embedded",
                        "label": str(attrs.get("label") or spec.get("label") or role),
                        "width": attrs.get("width") or spec.get("width") or 220,
                        "height": attrs.get("height") or spec.get("height") or 48,
                        "embedded_png_b64": png_b64,
                        "embedded_attachment_id": att_id,
                    }
                )
                meta_by_role[role] = spec
                attrs["embeddedPngB64"] = png_b64
                if att_id:
                    attrs["embeddedAttachmentId"] = att_id
                node["attrs"] = attrs
        for child in node.get("content") or []:
            await walk(child)

    doc = copy.deepcopy(editor_document or {"type": "doc", "content": []})
    await walk(doc)
    meta["signatures"] = list(meta_by_role.values())
    return doc, meta


def png_bytes_from_block(block: Dict[str, Any]) -> Optional[bytes]:
    raw = str(block.get("embedded_png_b64") or "").strip()
    if not raw:
        return None
    try:
        return base64.b64decode(raw, validate=True)
    except Exception:  # noqa: BLE001
        return None


def _meta_by_role(
    token_metadata: Optional[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    meta_by_role: Dict[str, Dict[str, Any]] = {}
    for spec in (token_metadata or {}).get("signatures") or []:
        if isinstance(spec, dict):
            role = str(spec.get("role") or "").strip()
            if role:
                meta_by_role[role] = spec
    return meta_by_role


def _attachment_id_from_document(editor_document: Dict[str, Any], role: str) -> str:
    found = ""

    def walk(node: Any) -> None:
        nonlocal found
        if found or not isinstance(node, dict):
            return
        if node.get("type") == "signaturePlaceholder":
            attrs = node.get("attrs") or {}
            if str(attrs.get("role") or "").strip() == role:
                found = str(
                    attrs.get("embeddedAttachmentId")
                    or attrs.get("embedded_attachment_id")
                    or ""
                ).strip()
                return
        for child in node.get("content") or []:
            walk(child)

    walk(editor_document or {})
    return found


def published_pre_embedded_blocks(
    editor_document: Dict[str, Any],
    token_metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Pre-embedded blocks with inline PNG bytes already on the published version."""
    meta_by_role = _meta_by_role(token_metadata)
    out: List[Dict[str, Any]] = []
    for block in pre_embedded_signature_blocks(editor_document, token_metadata):
        role = str(block.get("role") or "").strip()
        meta = meta_by_role.get(role) or {}
        png_b64 = str(
            meta.get("embedded_png_b64")
            or meta.get("embeddedPngB64")
            or block.get("embedded_png_b64")
            or ""
        ).strip()
        if not png_b64:
            continue
        out.append({**block, "embedded_png_b64": png_b64, "mode": "pre_embedded"})
    return out


async def resolve_published_pre_embedded_blocks(
    editor_document: Dict[str, Any],
    token_metadata: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Pre-embedded blocks for PDF overlay at generate time.

    Uses ``embedded_png_b64`` snapshotted on publish. If bytes are missing but
    ``embedded_attachment_id`` was stored, loads that attachment once (frozen
    publish reference — not the caller's live E-Sign vault).
    """
    meta_by_role = _meta_by_role(token_metadata)
    out: List[Dict[str, Any]] = []
    for block in pre_embedded_signature_blocks(editor_document, token_metadata):
        role = str(block.get("role") or "").strip()
        meta = meta_by_role.get(role) or {}
        png_b64 = str(
            meta.get("embedded_png_b64")
            or meta.get("embeddedPngB64")
            or block.get("embedded_png_b64")
            or ""
        ).strip()
        att_id = str(
            meta.get("embedded_attachment_id")
            or meta.get("embeddedAttachmentId")
            or _attachment_id_from_document(editor_document, role)
            or ""
        ).strip()
        if not png_b64 and att_id:
            png_bytes = await load_png_for_attachment(att_id)
            if png_bytes:
                png_b64 = base64.b64encode(png_bytes).decode("ascii")
        if not png_b64:
            logger.warning(
                "Pre-embedded signature role=%s has no published PNG or attachment; "
                "save and publish the template after adding My signature",
                role,
            )
            continue
        out.append(
            {
                **block,
                "embedded_png_b64": png_b64,
                "embedded_attachment_id": att_id,
                "mode": "pre_embedded",
            }
        )
    return out


async def render_pdf_with_pre_embedded_signatures(
    html: str,
    *,
    title: str,
    editor_document: Dict[str, Any],
    token_metadata: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Render template HTML to PDF and stamp pre-embedded signature PNGs.

    ReportLab fallback PDFs drop inline images; this overlay path is the generic
    substrate contract for any caller (App draw tools, legacy generate paths).
    """
    from app.services.documents.output import render_output

    data, mime, _ext = await asyncio.to_thread(render_output, html, "pdf", title=title)
    if mime != "application/pdf":
        raise ValueError("PDF rendering failed")
    return await apply_pre_embedded_signatures(
        data,
        html=html,
        editor_document=editor_document,
        token_metadata=token_metadata,
    )


async def apply_pre_embedded_signatures(
    pdf_bytes: bytes,
    *,
    html: str,
    editor_document: Dict[str, Any],
    token_metadata: Optional[Dict[str, Any]] = None,
) -> bytes:
    """Overlay pre-embedded signature PNGs onto a generated PDF."""
    blocks = await resolve_published_pre_embedded_blocks(
        editor_document, token_metadata
    )
    if not blocks:
        return pdf_bytes

    all_places = await asyncio.to_thread(
        resolve_signature_places,
        html=html,
        editor_document=editor_document,
        pdf_bytes=pdf_bytes,
        token_metadata=token_metadata,
        runtime_only=False,
    )
    place_by_role = {str(p.get("role") or ""): p for p in all_places if p.get("role")}

    signatures: List[Dict[str, Any]] = []
    overlay_places: List[Dict[str, Any]] = []
    for block in blocks:
        role = str(block.get("role") or "")
        png = png_bytes_from_block(block)
        place = place_by_role.get(role)
        if not png or place is None:
            continue
        merged_place = dict(place)
        merged_place["mode"] = "pre_embedded"
        overlay_places.append(merged_place)
        signatures.append({"role": role, "png_bytes": png})

    if not signatures:
        return pdf_bytes
    return await asyncio.to_thread(
        overlay_signatures,
        pdf_bytes,
        signatures,
        places=overlay_places,
        allow_place_fallback=False,
    )
